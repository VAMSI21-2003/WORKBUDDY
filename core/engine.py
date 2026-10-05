"""Engine — drives the Eyes -> Brain -> Think -> Planner -> Delegate ->
Hands -> Inspector -> Voice cycle end to end.
"""

import logging
from dataclasses import dataclass
from typing import Protocol

from buddy.state import BuddyState, StageFailure

logger = logging.getLogger(__name__)


class Stage(Protocol):
    """A single step in Buddy's cognitive cycle.

    A stage receives the current ``BuddyState``, does its work against it,
    and returns the resulting state. A stage may mutate the state it is
    given and return the same instance, or return a new one — the Engine
    only cares about the value a stage returns, never how a stage produces
    it.
    """

    def __call__(self, state: BuddyState) -> BuddyState:
        """Run this stage against ``state`` and return the resulting state."""
        ...


@dataclass(frozen=True)
class CognitiveCycle:
    """The ordered set of stage dependencies the Engine runs.

    Each attribute is an injected ``Stage`` implementation. The Engine
    holds no knowledge of what any stage actually does — only the order in
    which they run. See ``docs/WORKBUDDY_ARCHITECTURE.md`` Section 4 for
    what each stage is responsible for, and Section 11 for the
    Dispatcher's role. The Dispatcher (``core/dispatcher.py``) is
    deliberately **not** one of these stages — Section 11 is explicit that
    it is not a cognitive stage and never reads or writes ``BuddyState``.
    It is a Core Engine component that ``hands`` calls internally,
    exactly once it's ready to act on a delegated task.

    Attributes:
        eyes: Perceives the raw input for the current turn.
        brain: Interprets observations into user intent.
        think: Reasons about intent to determine what needs to happen.
        planner: Converts reasoning into an ordered sequence of steps.
        delegate: Converts plan steps into capability requests.
        hands: Asks the Dispatcher to select a capable Skill for each
            delegated request, invokes it, and collects the results.
        inspector: Checks results against the original intent.
        voice: Produces the reply sent back to the user.
    """

    eyes: Stage
    brain: Stage
    think: Stage
    planner: Stage
    delegate: Stage
    hands: Stage
    inspector: Stage
    voice: Stage

    def stages(self) -> tuple[Stage, ...]:
        """Return the stages in the order the Engine runs them."""
        return (
            self.eyes,
            self.brain,
            self.think,
            self.planner,
            self.delegate,
            self.hands,
            self.inspector,
            self.voice,
        )

    def named_stages(self) -> tuple[tuple[str, Stage], ...]:
        """Return ``(slot name, stage)`` pairs in the order the Engine runs them."""
        return tuple(zip(_STAGE_NAMES, self.stages(), strict=True))


# The CognitiveCycle slot names, in run order. A ``StageFailure.stage`` is
# always one of these, so the set of stage labels Voice can see is closed.
_STAGE_NAMES = (
    "eyes",
    "brain",
    "think",
    "planner",
    "delegate",
    "hands",
    "inspector",
    "voice",
)
VOICE_STAGE = "voice"


class Engine:
    """Orchestrates Buddy's cognitive cycle over a ``BuddyState``.

    The Engine contains no reasoning, routing, or domain logic of its own.
    Its only responsibility is to run each stage of the cognitive cycle, in
    order, against a shared ``BuddyState``, threading the state returned by
    one stage into the next. Every stage is supplied to the Engine as a
    dependency (see ``CognitiveCycle``), so the Engine can be exercised in
    tests with stub or fake stages, and real stage implementations can be
    built, changed, or replaced without ever changing the Engine.
    """

    def __init__(self, cycle: CognitiveCycle) -> None:
        """Initialize the engine.

        Args:
            cycle: The stage dependencies to run, in order.
        """
        self._cycle = cycle

    def run(self, state: BuddyState) -> BuddyState:
        """Run one full pass of the cognitive cycle over ``state``.

        A stage that raises doesn't end the turn without a reply
        (``docs/WORKBUDDY_ARCHITECTURE.md`` Section 17): the Engine logs it
        with its traceback, records a ``StageFailure`` on ``state.failure``,
        skips every remaining stage except Voice, and still runs Voice,
        which gives a fixed failure reply that claims nothing
        (``buddy.voice.Voice``). The exception's message is never put on
        the state.

        Args:
            state: The ``BuddyState`` to run the cycle against.

        Returns:
            The state after the cycle. ``state.failure`` is set if a stage
            other than Voice raised.

        Raises:
            Exception: Only what Voice itself raises. With no stage left to
                give a reply, that is the caller's to handle. ``state.failure``
                is set first (to Voice's, unless an earlier stage had already
                failed), so the state left behind never looks like a verified
                delivery.
        """
        logger.info("Starting cognitive cycle for session %r", state.session_id)
        for name, stage in self._cycle.named_stages():
            if state.failure is not None and name != VOICE_STAGE:
                logger.debug("Skipping stage %r after a failure in %r", name, state.failure.stage)
                continue
            stage_name = getattr(stage, "__name__", type(stage).__name__)
            logger.debug("Running stage %r for session %r", stage_name, state.session_id)
            try:
                state = stage(state)
            except Exception as exc:
                logger.exception(
                    "Stage %r failed for session %r", stage_name, state.session_id
                )
                if name == VOICE_STAGE:
                    # No stage is left to give a reply, so this goes to the
                    # caller. The state still describes itself: whatever it
                    # holds, it isn't a verified delivery (review M1). An
                    # earlier stage's failure is kept.
                    if state.failure is None:
                        state.failure = StageFailure(stage=name, error_type=type(exc).__name__)
                    raise
                state.failure = StageFailure(stage=name, error_type=type(exc).__name__)
        logger.info("Completed cognitive cycle for session %r", state.session_id)
        return state

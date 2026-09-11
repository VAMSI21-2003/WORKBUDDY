"""Delegate — assigns planned steps to the appropriate skills."""

import logging
from dataclasses import dataclass
from typing import Protocol

from buddy.models import CapabilityRequest, PlanningStep
from buddy.state import BuddyState

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DelegationRequest:
    """The inputs available for turning planned steps into capability requests.

    A plain, self-contained bundle of everything a ``Delegator`` needs to
    work from — assembled mechanically from ``BuddyState`` by the Delegate
    stage, so a delegator never has to depend on ``BuddyState`` directly.

    Attributes:
        planning: The ordered sequence of ``PlanningStep``s produced by
            Plan.
        uploaded_files: Files currently available in the workspace.
        active_file: Reference to the file the user was last focused on,
            if any, from the working context.
    """

    planning: tuple[PlanningStep, ...]
    uploaded_files: tuple[str, ...]
    active_file: str | None


class Delegator(Protocol):
    """Produces ``CapabilityRequest``s from a ``DelegationRequest``.

    This is the seam where the mapping from planned steps to capability
    requests is plugged in. The Delegate stage itself never decides how a
    step's target is resolved; it only calls whichever ``Delegator`` it
    was given.
    """

    def __call__(self, request: DelegationRequest) -> tuple[CapabilityRequest, ...]:
        """Delegate ``request`` and return the resulting capability requests."""
        ...


class NullDelegator:
    """A ``Delegator`` that produces no capability requests.

    Never invents a target to act on. Serves as a safe default so
    ``Delegate`` can be constructed and exercised — in tests, or wired
    into the Engine — before a real delegator is plugged in.
    """

    def __call__(self, request: DelegationRequest) -> tuple[CapabilityRequest, ...]:
        """Return no capability requests for any request.

        Args:
            request: The request that would otherwise be delegated.

        Returns:
            An empty tuple — no delegator has been configured.
        """
        return ()


class MechanicalDelegator:
    """A ``Delegator`` that mechanically turns each step into one request.

    Produces one ``CapabilityRequest`` per ``PlanningStep``, using the
    step's own ``capability`` and resolving ``target`` from the working
    context (the active file, or the most recent upload) rather than from
    the step itself — ``PlanningStep`` carries no target field, the same
    way Understand and Think already pull file references straight from
    state rather than threading them through every model.

    Never references a skill by name: ``capability`` is always the
    domain-language string a ``PlanningStep`` already carries (for
    example ``"spreadsheet.deduplicate"``), so which registered Skill
    actually satisfies it is left entirely to the Dispatcher.
    """

    def __call__(self, request: DelegationRequest) -> tuple[CapabilityRequest, ...]:
        """Delegate ``request`` and return the resulting capability requests.

        Args:
            request: The request to delegate.

        Returns:
            One ``CapabilityRequest`` per planned step, using a resolved
            target file. If no target file can be resolved, returns an
            empty tuple rather than guessing.
        """
        target_file = request.active_file or (
            request.uploaded_files[-1] if request.uploaded_files else None
        )
        if target_file is None:
            return ()

        return tuple(
            CapabilityRequest(
                capability=step.capability,
                target=target_file,
                parameters={},
            )
            for step in request.planning
        )


class Delegate:
    """The Delegate stage of Buddy's cognitive cycle.

    Delegate converts Plan's ordered steps into capability requests — what
    capability is needed and what it should be applied to — for the
    Dispatcher to resolve to a concrete Skill (see
    ``docs/WORKBUDDY_ARCHITECTURE.md`` Sections 4 and 11). As documented
    there, Delegate does not itself resolve a request to a specific Skill
    instance; that resolution belongs entirely to the Dispatcher, called
    by Execute.

    Delegate contains no delegation logic of its own. All of the actual
    decision-making — how a step's target is resolved — is delegated to
    an injected ``Delegator``. Delegate's own responsibility is purely
    mechanical: assemble the delegator's input from ``BuddyState``, call
    the delegator, and record what it returns. This keeps Delegate a
    stable, swappable framework — any implementation conforming to
    ``Delegator`` can be substituted without ever changing this stage.
    """

    def __init__(self, delegator: Delegator | None = None) -> None:
        """Initialize the stage.

        Args:
            delegator: The delegator to delegate to. Defaults to
                ``NullDelegator``, which produces no capability requests
                rather than guessing.
        """
        if delegator is None:
            logger.debug("No delegator provided; falling back to NullDelegator")
        self._delegator = delegator if delegator is not None else NullDelegator()

    def __call__(self, state: BuddyState) -> BuddyState:
        """Delegate for ``state`` and record the resulting capability requests.

        Args:
            state: The current ``BuddyState`` for this turn.

        Returns:
            The same state, with the full sequence of
            ``CapabilityRequest``s recorded in ``state.delegation`` and
            mirrored as plain task records in the legacy ``state.tasks``.
        """
        request = self._build_request(state)
        capability_requests = self._delegator(request)
        self._apply(state, capability_requests)
        logger.debug(
            "Delegate recorded %d capability request(s) for session %r",
            len(capability_requests),
            state.session_id,
        )
        return state

    @staticmethod
    def _build_request(state: BuddyState) -> DelegationRequest:
        """Assemble the delegator's input from the current state.

        This step is purely mechanical: it gathers what is already
        present on ``state`` and ``state.context`` into a plain,
        self-contained bundle. It performs no delegation of its own. If
        Plan has not recorded any steps yet, an empty sequence is used
        rather than guessing at what it might have contained.

        Args:
            state: The current ``BuddyState`` for this turn.

        Returns:
            The request to hand to the configured delegator.
        """
        return DelegationRequest(
            planning=state.planning or (),
            uploaded_files=tuple(state.uploaded_files),
            active_file=state.context.active_file,
        )

    @staticmethod
    def _apply(state: BuddyState, capability_requests: tuple[CapabilityRequest, ...]) -> None:
        """Record the delegator's output onto ``state``.

        ``state.delegation`` is the source of truth. Plain task records
        are additionally mirrored into the legacy ``state.tasks`` list,
        consistent with how the earlier stages keep their legacy fields
        populated alongside the typed ones.

        Args:
            state: The state to update.
            capability_requests: The capability requests to record.
        """
        state.delegation = capability_requests
        state.tasks = [
            {
                "capability": request.capability,
                "target": request.target,
                "parameters": dict(request.parameters),
            }
            for request in capability_requests
        ]

"""Think — reasons about intent to determine what needs to happen."""

import json
import logging
from dataclasses import dataclass, replace
from typing import Protocol

from buddy.context import ConversationTurn
from buddy.llm import LLMMessage, LLMProvider
from buddy.models import Understanding
from buddy.state import BuddyState

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ThinkingRequest:
    """The inputs available for reasoning about how to approach a request.

    A plain, self-contained bundle of everything a ``Thinker`` needs to
    work from — assembled mechanically from ``BuddyState`` and its
    ``Context`` by the Think stage, so a thinker never has to depend on
    either of those types directly.

    Attributes:
        understanding: The structured understanding produced by
            Understand.
        observations: Observations recorded during the Observe stage.
        uploaded_files: Files currently available in the workspace.
        active_task: Identifier of the task the user was last focused on,
            if any, from the working context.
        recent_conversation: Recent conversation turns, oldest first.
        user_message: The current turn's message, in the user's own
            words. The classified intent alone doesn't say which actions a
            compound request asks for.
        turn_id: The turn this reasoning is for, only so a failure can be logged against it.
        file_settled: Whether which file this turn works on is settled
            (``BuddyState.file_used`` and ``FileChoice``); the model is told so, so it doesn't
            ask. Its name is not sent: nothing about a copy but that one exists reaches a model.
    """

    understanding: Understanding
    observations: tuple[str, ...]
    uploaded_files: tuple[str, ...]
    active_task: str | None
    recent_conversation: tuple[ConversationTurn, ...]
    user_message: str = ""
    turn_id: str = ""
    file_settled: bool = False


# Why Think asks for clarification: a closed vocabulary. Think chooses one; the reply shows a
# fixed question for it (``buddy.voice_catalog``), never Think's own words. Anything else is
# ``CLARIFY_OTHER``. ``CLARIFY_FAILED`` is set by code, never by the model, when Think itself
# couldn't produce a reading: that isn't a question for the user.
CLARIFY_WHICH_ACTION = "which_action"
CLARIFY_WHICH_FILE = "which_file"
CLARIFY_OTHER = "other"
CLARIFY_FAILED = "failed"
CLARIFY_MODEL_REASONS = frozenset({CLARIFY_WHICH_ACTION, CLARIFY_WHICH_FILE, CLARIFY_OTHER})


@dataclass(frozen=True)
class Reasoning:
    """The result of reasoning about how to approach a request.

    Attributes:
        objective: Plain-language statement of the overall objective.
        summary: Plain-language summary of the reasoning behind the
            recommended strategy.
        assumptions: Assumptions made in reaching this conclusion.
        constraints: Constraints that limit the possible approaches.
        risks: Risks identified in pursuing the recommended strategy.
        recommended_strategy: Plain-language description of the strategy
            recommended for handling the request.
        clarification_required: Whether the request is too ambiguous or
            incomplete to proceed without asking the user something first.
        confidence: Confidence in the recommended strategy, from 0.0 (no
            confidence) to 1.0 (full confidence). Distinct from
            ``Understanding.confidence``, which reflects confidence in the
            inferred intent rather than in the strategy for handling it.
        clarification_reason: Why clarification is required, one of the
            ``CLARIFY_*`` codes; ``""`` when it isn't. Only the code is kept:
            the reply's question is fixed text chosen for it.
    """

    objective: str = ""
    summary: str = ""
    assumptions: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()
    recommended_strategy: str = ""
    clarification_required: bool = False
    confidence: float = 0.0
    clarification_reason: str = ""


# How many times Think asks the model before it gives up (one retry).
ATTEMPTS = 2
# The kinds of failure, as logged. None carries any of the reply's words.
FAILURE_CALL = "the call raised"
FAILURE_NOT_JSON = "the reply was not valid JSON"
FAILURE_SHAPE = "the reply had an unexpected shape"


class Thinker(Protocol):
    """Produces a ``Reasoning`` result from a ``ThinkingRequest``.

    This is the seam where real reasoning is plugged in — a rule-based
    thinker, or one backed by an LLM. The Think stage itself never reasons
    about a request; it only calls whichever ``Thinker`` it was given.
    """

    def __call__(self, request: ThinkingRequest) -> Reasoning:
        """Reason about ``request`` and return the resulting strategy."""
        ...


class NullThinker:
    """A ``Thinker`` that performs no reasoning.

    Never invents information. Simply reports that no ``Thinker``
    implementation has been configured, rather than guessing at a
    strategy. Serves as a safe default so ``Think`` can be constructed and
    exercised — in tests, or wired into the Engine — before a real thinker
    is plugged in.
    """

    def __call__(self, request: ThinkingRequest) -> Reasoning:
        """Return an empty ``Reasoning`` for any request.

        Args:
            request: The request that would otherwise be reasoned about.

        Returns:
            A ``Reasoning`` with no objective or recommended strategy,
            flagged as requiring clarification, noting that no thinker
            has been configured.
        """
        return Reasoning(
            risks=("no thinker has been configured",),
            clarification_required=True,
            confidence=0.0,
            clarification_reason=CLARIFY_FAILED,
        )


class Think:
    """The Think stage of Buddy's cognitive cycle.

    Think evaluates the structured ``Understanding`` produced by
    Understand and decides *how* Buddy should approach the request: the
    overall objective, the reasoning behind it, the assumptions,
    constraints, and risks involved, a recommended strategy, and whether
    the request is clear enough to proceed without asking the user
    something first. It is not responsible for creating executable steps,
    dispatching Skills, executing work, or verifying results — those
    belong to Plan, Delegate, Execute, and Verify respectively (see
    ``docs/WORKBUDDY_ARCHITECTURE.md`` Section 4).

    Think contains no reasoning logic of its own. All of the actual
    judgment — evaluating the ``Understanding``, weighing assumptions,
    constraints, and risks, deciding on a strategy — is delegated to an
    injected ``Thinker``. Think's own responsibility is purely mechanical:
    assemble the thinker's input from ``BuddyState`` and its ``Context``,
    call the thinker, and store what it returns. This keeps Think a
    stable, swappable framework — any implementation conforming to
    ``Thinker`` can be substituted without ever changing this stage.
    """

    def __init__(self, thinker: Thinker | None = None) -> None:
        """Initialize the stage.

        Args:
            thinker: The thinker to delegate reasoning to. Defaults to
                ``NullThinker``, which performs no reasoning and never
                invents information — it simply reports that no thinker
                has been configured.
        """
        if thinker is None:
            logger.debug("No thinker provided; falling back to NullThinker")
        self._thinker = thinker if thinker is not None else NullThinker()

    def __call__(self, state: BuddyState) -> BuddyState:
        """Reason about ``state`` and store the resulting strategy.

        Args:
            state: The current ``BuddyState`` for this turn.

        Returns:
            The same state, with the full ``Reasoning`` result recorded in
            ``state.reasoning`` and its plain-language summary appended to
            the legacy ``state.reasoning_trace``.
        """
        request = self._build_request(state)
        reasoning = settle_file_question(state, self._thinker(request))
        self._apply(state, reasoning)
        logger.debug(
            "Think recorded objective=%r clarification_required=%s for session %r",
            reasoning.objective,
            reasoning.clarification_required,
            state.session_id,
        )
        return state

    @staticmethod
    def _build_request(state: BuddyState) -> ThinkingRequest:
        """Assemble the thinker's input from the current state.

        This step is purely mechanical: it gathers what is already
        present on ``state`` and ``state.context`` into a plain,
        self-contained bundle. It performs no reasoning of its own. If
        Understand has not recorded an ``Understanding`` yet, an empty one
        is used rather than guessing at what it might have contained.

        Args:
            state: The current ``BuddyState`` for this turn.

        Returns:
            The request to hand to the configured thinker.
        """
        context = state.context
        understanding = state.understanding or Understanding()
        return ThinkingRequest(
            understanding=understanding,
            observations=tuple(state.observations),
            uploaded_files=tuple(state.uploaded_files),
            active_task=context.active_task,
            recent_conversation=tuple(context.recent_conversation()),
            user_message=state.user_message or "",
            turn_id=state.turn_id or "",
            file_settled=_file_settled(state),
        )

    @staticmethod
    def _apply(state: BuddyState, reasoning: Reasoning) -> None:
        """Store the thinker's output on ``state``.

        ``state.reasoning`` is the source of truth for the full result —
        objective, assumptions, constraints, risks, and recommended
        strategy — so downstream stages can inspect it directly rather
        than through a plain dict. The plain-language summary is
        additionally appended to ``state.reasoning_trace``, the legacy
        trace of Think's reasoning steps. Think does not touch
        ``state.confidence``: that field already holds Understand's
        confidence in the inferred intent, which is a distinct value from
        Think's confidence in its recommended strategy.

        Args:
            state: The state to update.
            reasoning: The reasoning result to record.
        """
        state.reasoning = reasoning
        if reasoning.summary:
            state.add_reasoning_step(reasoning.summary)


def _file_settled(state: BuddyState) -> bool:
    """Whether which file this turn works on is settled.

    Settled means Brain's turn picked one (``BuddyState.file_used``) and the message doesn't
    name a file that couldn't be found or told apart (``FileChoice.unclear``).
    """
    choice = state.file_choice
    return state.file_used is not None and not (choice is not None and choice.unclear)


def settle_file_question(state: BuddyState, reasoning: Reasoning) -> Reasoning:
    """Make Think's "which file?" agree with what code knows about the turn's file.

    The file is code's to settle, not the model's (owner's decision, ``docs/design/
    conversation-repair.md``): the latest copy handed back, or the one or active upload, and the
    reply says which was used. So a "which file?" from the model is dropped when the file is
    settled, and is asked, in the fixed words, when the message names a file that can't be
    found or told apart. A Think that failed stays failed: that isn't a question for the user.
    """
    if reasoning.clarification_reason == CLARIFY_FAILED:
        return reasoning
    asks_file = (
        reasoning.clarification_required and reasoning.clarification_reason == CLARIFY_WHICH_FILE
    )
    choice = state.file_choice
    if choice is not None and choice.unclear:
        if asks_file:
            return reasoning
        return replace(
            reasoning, clarification_required=True, clarification_reason=CLARIFY_WHICH_FILE
        )
    if asks_file and state.file_used is not None:
        logger.info("Turn %s: Think asked which file; the file is settled", state.turn_id)
        return replace(reasoning, clarification_required=False, clarification_reason="")
    return reasoning


_SYSTEM_PROMPT = """You are the reasoning stage for WorkBuddy, an AI coworker.

Given a structured understanding of what the user wants, decide how Buddy
should approach the request: the overall objective, a short plain-language
summary of your reasoning, the assumptions you're making, any constraints,
any risks, a recommended strategy, whether the request is clear enough to
proceed without asking the user something first, and your confidence in
that recommendation.

You are shown the user's own message, the already-classified intent, and
the names of uploaded files.
You are never shown the contents of any uploaded file, so never assume
anything about what data a file contains. Work out which actions the user
asked for from their own message.

Buddy carries out the work itself, through its skills — it opens the file,
changes it, and hands back the result. The user never runs anything.
Your strategy must never tell the user to run code, write a script, use a
library, or follow manual steps; describe what Buddy will do.

Buddy's available capabilities, and their real limits (the two that change
a spreadsheet work only on a workbook with a single sheet: they refuse one
with more than one sheet rather than clean part of it, write their result
to a new file, and never modify the original; the analysis reads only the
first sheet). When a request needs more than one of
them, they run in the order the user stated, each working on the previous
one's result, and the user gets one final file; if any step fails, the
rest don't run:
- spreadsheet.deduplicate: removes rows that are identical across every
  column, keeping the first occurrence. It cannot choose which columns
  define a duplicate, cannot match near-duplicates or differently-formatted
  values, and cannot choose which occurrence to keep.
- spreadsheet.remove_empty_rows: removes rows in which every cell is
  empty. It cannot remove partially-empty rows.
- spreadsheet.analyze: reads the spreadsheet and reports possible issues —
  missing values, numeric outliers, mixed date formats, rows that share an
  identifier but disagree on other values, and likely near-duplicates. It
  changes nothing; it only reports. Buddy runs it automatically before other
  work, so never plan for it or ask about it. A request for a report, summary
  or overview of the file is answered with its findings; it needs no question
  either.

Set "clarification_required" to true only when the user's answer would
change what one of these capabilities actually does. If the user asks for
something no capability can do (for example, choosing which columns define
a duplicate), don't ask for details Buddy can't use: say plainly, in your
summary and constraints, what Buddy can and can't do, and recommend the
closest thing it can do.

These capabilities take no options, so their behavior is fixed. Never set
"clarification_required" to true, and never recommend asking the user, about
any of these — no answer to them can change what Buddy does:
- which columns define a duplicate, which occurrence to keep, or whether
  near-duplicates or partial matches should count;
- what counts as an empty row;
- whether the user is happy with a capability's fixed behavior (don't ask
  for confirmation).
Vague qualifiers such as "only the ones that matter" or "the important
ones" are not a reason to ask either. In all of these cases set
"clarification_required" to false, recommend running the matching
capability with its fixed behavior, and state its limits plainly in your
summary and constraints.

Respond with a single JSON object and nothing else, matching this shape:
{
  "objective": "<plain-language statement of the goal>",
  "summary": "<plain-language summary of your reasoning>",
  "assumptions": [<assumption strings>],
  "constraints": [<constraint strings>],
  "risks": [<risk strings>],
  "recommended_strategy": "<plain-language description of the approach>",
  "clarification_required": <true|false>,
  "clarification_reason": "which_action" | "which_file" | "other" | "",
  "confidence": <number between 0.0 and 1.0>
}

When a line says which file this turn works on is already settled, never ask which file.

"clarification_reason" is empty unless "clarification_required" is true. Then it says what
the user must tell you, and only that: "which_action" when you cannot tell which of the
capabilities above they want, "which_file" when you cannot tell which file they mean, "other"
for anything else. Do not write the question: the user is shown a fixed one for the reason.
"""


class ClaudeThinker:
    """A ``Thinker`` backed by a Claude model, via ``LLMProvider``.

    Builds a prompt from the ``ThinkingRequest``'s already-classified
    ``Understanding`` and asks the model to reason about how to approach
    the request. Only the user's own message, the classified intent, file
    *references* (never file contents), and recent conversation text are
    ever sent to the model —
    this stage never opens an uploaded file, so no cell or row data can
    appear in any prompt it builds.

    Never raises: a malformed reply or a provider failure is logged and
    turned into a conservative result flagged as requiring clarification,
    rather than propagated — the same honesty contract ``NullThinker``
    follows.
    """

    def __init__(self, provider: LLMProvider) -> None:
        """Initialize the thinker.

        Args:
            provider: The ``LLMProvider`` used to reason about the request.
        """
        self._provider = provider

    def __call__(self, request: ThinkingRequest) -> Reasoning:
        """Reason about ``request`` and return the resulting strategy.

        A failed attempt (the call raised, the reply wasn't JSON, or it had the wrong shape) is
        tried once more: the call has no side effects, and a model that misfires once usually
        doesn't twice. Each failure is logged at WARNING with the turn and the kind of failure,
        never the reply, the message or a key.

        Args:
            request: The request to reason about.

        Returns:
            The resulting ``Reasoning``, or, when both attempts failed, a conservative result
            flagged as requiring clarification for reason ``CLARIFY_FAILED``.
        """
        user_prompt = self._build_user_prompt(request)
        failure = FAILURE_CALL
        for attempt in range(1, ATTEMPTS + 1):
            reasoning, failure = self._attempt(user_prompt)
            if reasoning is not None:
                if attempt > 1:
                    logger.info(
                        "Turn %s: Think succeeded on attempt %d", request.turn_id or "-", attempt
                    )
                return reasoning
            logger.warning(
                "Turn %s: Think failed (%s), attempt %d of %d",
                request.turn_id or "-",
                failure,
                attempt,
                ATTEMPTS,
            )
        return _failed_reasoning(failure)

    def _attempt(self, user_prompt: str) -> tuple[Reasoning | None, str]:
        """One call and parse: the reasoning, or ``None`` and the kind of failure."""
        try:
            raw_reply = self._provider.complete(
                system=_SYSTEM_PROMPT,
                messages=(LLMMessage(role="user", content=user_prompt),),
            )
        except Exception as exc:
            # The exception's class says what failed (a timeout, a rate limit); its message
            # isn't logged, and the logging filter masks any key that got into it.
            logger.debug("ClaudeThinker: LLM call raised")
            return None, f"{FAILURE_CALL}: {type(exc).__name__}"
        return _parse(raw_reply)

    @staticmethod
    def _build_user_prompt(request: ThinkingRequest) -> str:
        """Build the user-turn prompt from ``request``.

        Only the user's own message, the classified intent, file
        *references* (never file contents), and recent conversation text
        are included — the same message ``ClaudePlanner`` already sees.

        Args:
            request: The request to build a prompt from.

        Returns:
            The plain-text user prompt to send to the model.
        """
        understanding = request.understanding
        lines = []
        if request.user_message:
            lines.append(f"The user's own words: \"{request.user_message}\"")
        lines.append(
            f"Classified intent: {understanding.intent!r} "
            f"(confidence={understanding.confidence:.2f})"
        )
        if understanding.referenced_files:
            lines.append(
                "Referenced files: " + ", ".join(understanding.referenced_files)
            )
        if understanding.missing_information:
            lines.append(
                "Missing information: " + "; ".join(understanding.missing_information)
            )
        if understanding.ambiguity_flags:
            lines.append("Ambiguity flags: " + "; ".join(understanding.ambiguity_flags))
        if request.uploaded_files:
            lines.append("Uploaded files (names only): " + ", ".join(request.uploaded_files))
        else:
            lines.append("Uploaded files: none")
        if request.file_settled:
            lines.append("Which file this turn works on is already settled.")
        if request.active_task:
            lines.append(f"Currently active task: {request.active_task}")
        if request.recent_conversation:
            lines.append("Recent conversation:")
            for turn in request.recent_conversation:
                lines.append(f"  {turn.role.value}: {turn.message}")
        return "\n".join(lines)

    @staticmethod
    def _parse_reply(raw_reply: str) -> Reasoning:
        """Parse the model's raw JSON reply into a ``Reasoning``.

        Args:
            raw_reply: The model's raw text reply.

        Returns:
            The parsed ``Reasoning``, or a conservative fallback if
            ``raw_reply`` is not valid JSON or doesn't match the expected
            shape.
        """
        reasoning, failure = _parse(raw_reply)
        return reasoning if reasoning is not None else _failed_reasoning(failure)


def _failed_reasoning(failure: str) -> Reasoning:
    """The conservative result for a Think that produced no reading: not a question for the user."""
    risks = {
        FAILURE_NOT_JSON: "the model's reply was not valid JSON",
        FAILURE_SHAPE: "the model's reply had an unexpected shape",
    }
    return Reasoning(
        risks=(risks.get(failure, "the language model call failed"),),
        clarification_required=True,
        confidence=0.0,
        clarification_reason=CLARIFY_FAILED,
    )


def _parse(raw_reply: str) -> tuple[Reasoning | None, str]:
    """``raw_reply`` as a ``Reasoning``, or ``None`` and the kind of failure."""
    try:
        data = json.loads(raw_reply)
    except (json.JSONDecodeError, TypeError):
        return None, FAILURE_NOT_JSON
    try:
        clarification_required = bool(data.get("clarification_required", False))
        reason = data.get("clarification_reason")
        return (
            Reasoning(
                objective=data.get("objective") or "",
                summary=data.get("summary") or "",
                assumptions=tuple(data.get("assumptions") or ()),
                constraints=tuple(data.get("constraints") or ()),
                risks=tuple(data.get("risks") or ()),
                recommended_strategy=data.get("recommended_strategy") or "",
                clarification_required=clarification_required,
                confidence=float(data.get("confidence", 0.0)),
                clarification_reason=_clarification_reason(clarification_required, reason),
            ),
            "",
        )
    except (AttributeError, TypeError, ValueError):
        return None, FAILURE_SHAPE


def _clarification_reason(clarification_required: bool, raw_reason: object) -> str:
    """The reason code for a parsed reply: empty if no clarification, else a known code.

    Whatever the model wrote outside the closed vocabulary becomes ``CLARIFY_OTHER``, so the
    reply never carries its words.
    """
    if not clarification_required:
        return ""
    if isinstance(raw_reason, str) and raw_reason.strip().casefold() in CLARIFY_MODEL_REASONS:
        return raw_reason.strip().casefold()
    return CLARIFY_OTHER

"""Think — reasons about intent to determine what needs to happen."""

import json
import logging
from dataclasses import dataclass
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
    """

    understanding: Understanding
    observations: tuple[str, ...]
    uploaded_files: tuple[str, ...]
    active_task: str | None
    recent_conversation: tuple[ConversationTurn, ...]


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
    """

    objective: str = ""
    summary: str = ""
    assumptions: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()
    recommended_strategy: str = ""
    clarification_required: bool = False
    confidence: float = 0.0


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
        reasoning = self._thinker(request)
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


_SYSTEM_PROMPT = """You are the reasoning stage for WorkBuddy, an AI coworker.

Given a structured understanding of what the user wants, decide how Buddy
should approach the request: the overall objective, a short plain-language
summary of your reasoning, the assumptions you're making, any constraints,
any risks, a recommended strategy, whether the request is clear enough to
proceed without asking the user something first, and your confidence in
that recommendation.

You are never shown the contents of any uploaded file — only its name and
the already-classified intent — so never assume anything about what data a
file contains.

Buddy carries out the work itself, through its skills — it opens the file,
changes it, and hands back the result. The user never runs anything.
Your strategy must never tell the user to run code, write a script, use a
library, or follow manual steps; describe what Buddy will do.

Buddy's available capabilities, and their real limits (each reads only the
first sheet of an uploaded spreadsheet, writes its result to a new file,
and never modifies the original):
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
  work, so never plan for it or ask about it.

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
  "confidence": <number between 0.0 and 1.0>
}
"""


class ClaudeThinker:
    """A ``Thinker`` backed by a Claude model, via ``LLMProvider``.

    Builds a prompt from the ``ThinkingRequest``'s already-classified
    ``Understanding`` and asks the model to reason about how to approach
    the request. Only the classified intent, file *references* (never file
    contents), and recent conversation text are ever sent to the model —
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

        Args:
            request: The request to reason about.

        Returns:
            The resulting ``Reasoning``, or a conservative fallback if the
            model call fails or its reply can't be parsed.
        """
        user_prompt = self._build_user_prompt(request)
        try:
            raw_reply = self._provider.complete(
                system=_SYSTEM_PROMPT,
                messages=(LLMMessage(role="user", content=user_prompt),),
            )
        except Exception:
            logger.exception("ClaudeThinker: LLM call failed")
            return Reasoning(
                risks=("the language model call failed",),
                clarification_required=True,
                confidence=0.0,
            )
        return self._parse_reply(raw_reply)

    @staticmethod
    def _build_user_prompt(request: ThinkingRequest) -> str:
        """Build the user-turn prompt from ``request``.

        Only the classified intent, file *references* (never file
        contents), and recent conversation text are included.

        Args:
            request: The request to build a prompt from.

        Returns:
            The plain-text user prompt to send to the model.
        """
        understanding = request.understanding
        lines = [
            f"Classified intent: {understanding.intent!r} "
            f"(confidence={understanding.confidence:.2f})"
        ]
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
        try:
            data = json.loads(raw_reply)
        except json.JSONDecodeError:
            logger.warning("ClaudeThinker: reply was not valid JSON: %r", raw_reply)
            return Reasoning(
                risks=("the model's reply was not valid JSON",),
                clarification_required=True,
                confidence=0.0,
            )

        try:
            return Reasoning(
                objective=data.get("objective") or "",
                summary=data.get("summary") or "",
                assumptions=tuple(data.get("assumptions") or ()),
                constraints=tuple(data.get("constraints") or ()),
                risks=tuple(data.get("risks") or ()),
                recommended_strategy=data.get("recommended_strategy") or "",
                clarification_required=bool(data.get("clarification_required", False)),
                confidence=float(data.get("confidence", 0.0)),
            )
        except (TypeError, ValueError):
            logger.warning("ClaudeThinker: reply JSON had an unexpected shape: %r", data)
            return Reasoning(
                risks=("the model's reply had an unexpected shape",),
                clarification_required=True,
                confidence=0.0,
            )

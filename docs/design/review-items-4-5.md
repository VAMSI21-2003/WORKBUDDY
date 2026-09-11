# Review record — Step 3, items #4 and #5

**Full test suite: `34 passed in 2.52s`** (run via `python -m pytest tests/`
from the repo root, after the fixes below — this includes the pre-existing
`test_llm.py`/`test_state.py` suites from items #1–3, so this is the whole
suite green, not just the two new test files).

Two stale docstrings were found and fixed while re-reading these files for
this doc — both `Understand.__call__` and `Think.__call__` still described
the old metadata-based behavior after the dead-field fix. Included below.

---

## `buddy/understand.py` (modified)

```python
"""Understand — interprets observations into user intent."""

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
class UnderstandingRequest:
    """The inputs available for interpreting a single user request.

    A plain, self-contained bundle of everything an ``Interpreter`` needs
    to work from — assembled mechanically from ``BuddyState`` and its
    ``Context`` by the Understand stage, so an interpreter never has to
    depend on either of those types directly.

    Attributes:
        user_message: The current turn's raw message from the user.
        observations: Observations recorded during the Observe stage.
        uploaded_files: Files currently available in the workspace.
        active_task: Identifier of the task the user was last focused on,
            if any, from the working context.
        active_file: Reference to the file the user was last focused on,
            if any, from the working context.
        recent_conversation: Recent conversation turns, oldest first,
            available for resolving references such as "it" or "that
            file" against what was just said.
    """

    user_message: str
    observations: tuple[str, ...]
    uploaded_files: tuple[str, ...]
    active_task: str | None
    active_file: str | None
    recent_conversation: tuple[ConversationTurn, ...]


class Interpreter(Protocol):
    """Produces a structured ``Understanding`` from an ``UnderstandingRequest``.

    ``Understanding`` is the shared model defined in ``buddy.models`` — the
    same type Think reads from and every other stage that needs to inspect
    what was understood relies on. This is the seam where real language
    understanding is plugged in — a rule-based interpreter, an LLM-backed
    interpreter, or anything else. The Understand stage itself never
    interprets a request; it only calls whichever ``Interpreter`` it was
    given.
    """

    def __call__(self, request: UnderstandingRequest) -> Understanding:
        """Interpret ``request`` and return the resulting understanding."""
        ...


class NullInterpreter:
    """An ``Interpreter`` that performs no interpretation.

    Honestly reports that nothing has been determined yet, rather than
    guessing. Serves as a safe default so ``Understand`` can be
    constructed and exercised — in tests, or wired into the Engine — before
    a real interpreter is plugged in.
    """

    def __call__(self, request: UnderstandingRequest) -> Understanding:
        """Return an empty ``Understanding`` for any request.

        Args:
            request: The request that would otherwise be interpreted.

        Returns:
            An ``Understanding`` with no inferred intent and zero
            confidence, noting that no interpreter has been configured.
        """
        return Understanding(
            intent=None,
            confidence=0.0,
            missing_information=("no interpreter has been configured",),
        )


class Understand:
    """The Understand stage of Buddy's cognitive cycle.

    Understand turns this turn's observations into a structured
    understanding of what the user wants: an inferred intent, a
    confidence score, which files and work items the request refers to,
    what information is missing, and where the request is ambiguous. It
    does not reason about what to do next, does not build a plan, and
    does not execute, verify, or modify anything — those belong to Think,
    Plan, and later stages respectively (see
    ``docs/WORKBUDDY_ARCHITECTURE.md`` Section 4).

    Understand performs no interpretation itself. All of the actual
    language understanding — inferring intent, resolving references such
    as "that file" against the working context, judging completeness and
    ambiguity — is delegated to an injected ``Interpreter``. Understand's
    own responsibility is purely mechanical: assemble the interpreter's
    input from ``BuddyState`` and its ``Context``, call the interpreter,
    and record what it returns. This keeps Understand a stable, swappable
    framework — a rule-based interpreter, an LLM-backed interpreter, or
    anything else conforming to ``Interpreter`` can be substituted without
    ever changing this stage.
    """

    def __init__(self, interpreter: Interpreter | None = None) -> None:
        """Initialize the stage.

        Args:
            interpreter: The interpreter to delegate understanding to.
                Defaults to ``NullInterpreter``, which performs no
                interpretation and honestly reports nothing understood
                rather than guessing.
        """
        if interpreter is None:
            logger.debug("No interpreter provided; falling back to NullInterpreter")
        self._interpreter = interpreter if interpreter is not None else NullInterpreter()

    def __call__(self, state: BuddyState) -> BuddyState:
        """Interpret ``state`` and record a structured understanding.

        Args:
            state: The current ``BuddyState`` for this turn.

        Returns:
            The same state, with the full ``Understanding`` recorded in
            ``state.understanding`` and ``intent``/``confidence`` updated
            on their legacy fields.
        """
        request = self._build_request(state)
        understanding = self._interpreter(request)
        self._apply(state, understanding)
        logger.debug(
            "Understand recorded intent=%r confidence=%.2f for session %r",
            understanding.intent,
            understanding.confidence,
            state.session_id,
        )
        return state

    @staticmethod
    def _build_request(state: BuddyState) -> UnderstandingRequest:
        """Assemble the interpreter's input from the current state.

        This step is purely mechanical: it gathers what is already
        present on ``state`` and ``state.context`` into a plain,
        self-contained bundle. It performs no interpretation of its own.

        Args:
            state: The current ``BuddyState`` for this turn.

        Returns:
            The request to hand to the configured interpreter.
        """
        context = state.context
        return UnderstandingRequest(
            user_message=state.user_message,
            observations=tuple(state.observations),
            uploaded_files=tuple(state.uploaded_files),
            active_task=context.active_task,
            active_file=context.active_file,
            recent_conversation=tuple(context.recent_conversation()),
        )

    @staticmethod
    def _apply(state: BuddyState, understanding: Understanding) -> None:
        """Record the interpreter's output onto ``state``.

        ``state.understanding`` is the source of truth for everything the
        interpreter concluded — including the fields legacy state has no
        dedicated slot for, such as ``referenced_files`` and
        ``ambiguity_flags``. ``intent`` and ``confidence`` are additionally
        copied onto their own legacy ``BuddyState`` fields, since they
        coexist with the richer typed field during the transition
        described in ``BuddyState``'s docstring.

        Args:
            state: The state to update.
            understanding: The structured understanding to record.
        """
        state.understanding = understanding
        state.intent = understanding.intent
        state.confidence = understanding.confidence


_SYSTEM_PROMPT = """You are the intent classifier for WorkBuddy, an AI coworker.

Classify the user's request into exactly one of the following intents:
- "remove_duplicate_rows": the user wants duplicate rows removed from a spreadsheet.
- "unclear": the request does not clearly match a known intent, or is too ambiguous to act on.

You are never shown the contents of any uploaded file — only its name — so
never assume anything about what data a file contains.

Respond with a single JSON object and nothing else, matching this shape:
{
  "intent": "remove_duplicate_rows" | "unclear",
  "confidence": <number between 0.0 and 1.0>,
  "referenced_files": [<file names the request refers to, if any>],
  "referenced_work_items": [<task or work item identifiers referred to, if any>],
  "missing_information": [<plain-language description of what's missing, if anything>],
  "ambiguity_flags": [<plain-language description of ambiguity, if any>],
  "notes": "<any other brief note, or an empty string>"
}
"""


class ClaudeInterpreter:
    """An ``Interpreter`` backed by a Claude model, via ``LLMProvider``.

    Builds a prompt from the ``UnderstandingRequest``, asks the model to
    classify the user's intent from a small closed vocabulary, and parses
    its JSON reply into an ``Understanding``. Only the user's message, file
    *references* (never file contents), and recent conversation text are
    ever sent to the model — this stage never opens an uploaded file, so no
    cell or row data can appear in any prompt it builds.

    Never raises: a malformed reply or a provider failure is logged and
    turned into a conservative, low-confidence ``Understanding`` rather
    than propagated — the same honesty contract ``NullInterpreter``
    follows.
    """

    def __init__(self, provider: LLMProvider) -> None:
        """Initialize the interpreter.

        Args:
            provider: The ``LLMProvider`` used to classify intent.
        """
        self._provider = provider

    def __call__(self, request: UnderstandingRequest) -> Understanding:
        """Classify ``request`` into a structured ``Understanding``.

        Args:
            request: The request to interpret.

        Returns:
            The classified ``Understanding``, or a conservative fallback if
            the model call fails or its reply can't be parsed.
        """
        user_prompt = self._build_user_prompt(request)
        try:
            raw_reply = self._provider.complete(
                system=_SYSTEM_PROMPT,
                messages=(LLMMessage(role="user", content=user_prompt),),
            )
        except Exception:
            logger.exception("ClaudeInterpreter: LLM call failed")
            return Understanding(
                intent=None,
                confidence=0.0,
                missing_information=("the language model call failed",),
            )
        return self._parse_reply(raw_reply)

    @staticmethod
    def _build_user_prompt(request: UnderstandingRequest) -> str:
        """Build the user-turn prompt from ``request``.

        Only the user's message text, file *references* (never file
        contents), and recent conversation text are included.

        Args:
            request: The request to build a prompt from.

        Returns:
            The plain-text user prompt to send to the model.
        """
        lines = [f'User message: "{request.user_message}"']
        if request.uploaded_files:
            lines.append("Uploaded files (names only): " + ", ".join(request.uploaded_files))
        else:
            lines.append("Uploaded files: none")
        if request.active_file:
            lines.append(f"Currently active file: {request.active_file}")
        if request.active_task:
            lines.append(f"Currently active task: {request.active_task}")
        if request.recent_conversation:
            lines.append("Recent conversation:")
            for turn in request.recent_conversation:
                lines.append(f"  {turn.role.value}: {turn.message}")
        return "\n".join(lines)

    @staticmethod
    def _parse_reply(raw_reply: str) -> Understanding:
        """Parse the model's raw JSON reply into an ``Understanding``.

        Args:
            raw_reply: The model's raw text reply.

        Returns:
            The parsed ``Understanding``, or a conservative fallback if
            ``raw_reply`` is not valid JSON or doesn't match the expected
            shape.
        """
        try:
            data = json.loads(raw_reply)
        except json.JSONDecodeError:
            logger.warning("ClaudeInterpreter: reply was not valid JSON: %r", raw_reply)
            return Understanding(
                intent=None,
                confidence=0.0,
                missing_information=("the model's reply was not valid JSON",),
            )

        try:
            return Understanding(
                intent=data.get("intent") or None,
                confidence=float(data.get("confidence", 0.0)),
                referenced_files=tuple(data.get("referenced_files") or ()),
                referenced_work_items=tuple(data.get("referenced_work_items") or ()),
                missing_information=tuple(data.get("missing_information") or ()),
                ambiguity_flags=tuple(data.get("ambiguity_flags") or ()),
                notes=data.get("notes") or "",
            )
        except (TypeError, ValueError):
            logger.warning("ClaudeInterpreter: reply JSON had an unexpected shape: %r", data)
            return Understanding(
                intent=None,
                confidence=0.0,
                missing_information=("the model's reply had an unexpected shape",),
            )
```

---

## `buddy/think.py` (modified)

```python
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
```

---

## `tests/buddy/test_understand.py` (new)

```python
"""Unit tests for buddy.understand."""

from __future__ import annotations

import json

import pytest

from buddy.llm import LLMMessage
from buddy.models import Understanding
from buddy.state import BuddyState
from buddy.understand import ClaudeInterpreter, NullInterpreter, Understand, UnderstandingRequest


class _FakeLLMProvider:
    """A minimal ``LLMProvider`` stand-in that returns a canned reply."""

    def __init__(self, reply: str | None = None, *, raises: Exception | None = None) -> None:
        self._reply = reply
        self._raises = raises
        self.last_system: str | None = None
        self.last_messages: tuple[LLMMessage, ...] | None = None

    def complete(
        self, *, system: str, messages: tuple[LLMMessage, ...], max_tokens: int = 1024
    ) -> str:
        self.last_system = system
        self.last_messages = messages
        if self._raises is not None:
            raise self._raises
        assert self._reply is not None
        return self._reply


def _make_state(user_message: str = "remove duplicate rows") -> BuddyState:
    state = BuddyState.new(session_id="session-1", workspace_id="workspace-1")
    state.user_message = user_message
    state.context.add_uploaded_file("report.xlsx")
    return state


def _empty_request(**overrides: object) -> UnderstandingRequest:
    fields: dict[str, object] = {
        "user_message": "remove duplicate rows",
        "observations": (),
        "uploaded_files": (),
        "active_task": None,
        "active_file": None,
        "recent_conversation": (),
    }
    fields.update(overrides)
    return UnderstandingRequest(**fields)  # type: ignore[arg-type]


# -- Understand stage: dead-field fix ----------------------------------------


def test_understand_sets_typed_understanding_field() -> None:
    state = _make_state()
    understanding = Understanding(intent="remove_duplicate_rows", confidence=0.9)

    class _FixedInterpreter:
        def __call__(self, request: UnderstandingRequest) -> Understanding:
            return understanding

    stage = Understand(interpreter=_FixedInterpreter())
    stage(state)

    assert state.understanding == understanding
    assert state.intent == "remove_duplicate_rows"
    assert state.confidence == 0.9


def test_understand_no_longer_writes_metadata_understanding() -> None:
    state = _make_state()
    stage = Understand(interpreter=NullInterpreter())

    stage(state)

    assert "understanding" not in state.metadata
    assert state.understanding is not None  # NullInterpreter still returns a real Understanding


def test_null_interpreter_reports_nothing_understood() -> None:
    state = _make_state()
    stage = Understand(interpreter=NullInterpreter())

    stage(state)

    assert state.understanding is not None
    assert state.understanding.intent is None
    assert state.understanding.confidence == 0.0


# -- ClaudeInterpreter --------------------------------------------------------


def test_claude_interpreter_parses_valid_json_reply() -> None:
    reply = json.dumps(
        {
            "intent": "remove_duplicate_rows",
            "confidence": 0.87,
            "referenced_files": ["report.xlsx"],
            "referenced_work_items": [],
            "missing_information": [],
            "ambiguity_flags": [],
            "notes": "clear request",
        }
    )
    provider = _FakeLLMProvider(reply=reply)
    interpreter = ClaudeInterpreter(provider)
    request = _empty_request(uploaded_files=("report.xlsx",))

    understanding = interpreter(request)

    assert understanding.intent == "remove_duplicate_rows"
    assert understanding.confidence == pytest.approx(0.87)
    assert understanding.referenced_files == ("report.xlsx",)
    assert understanding.notes == "clear request"


def test_claude_interpreter_prompt_contains_only_message_and_file_references() -> None:
    """The prompt must be built solely from the request's fields — never
    from opening the uploaded file — since ``UnderstandingRequest`` itself
    has no field capable of carrying cell or row data in the first place.
    An exact match proves nothing extra (e.g. file content) was added.
    """
    provider = _FakeLLMProvider(reply=json.dumps({"intent": "unclear", "confidence": 0.1}))
    interpreter = ClaudeInterpreter(provider)
    request = _empty_request(
        uploaded_files=("report.xlsx",),
        active_file="report.xlsx",
    )

    interpreter(request)

    assert provider.last_messages is not None
    prompt = provider.last_messages[0].content
    assert prompt == (
        'User message: "remove duplicate rows"\n'
        "Uploaded files (names only): report.xlsx\n"
        "Currently active file: report.xlsx"
    )


def test_claude_interpreter_prompt_reports_no_uploaded_files_when_none() -> None:
    provider = _FakeLLMProvider(reply=json.dumps({"intent": "unclear", "confidence": 0.1}))
    interpreter = ClaudeInterpreter(provider)
    request = _empty_request(uploaded_files=())

    interpreter(request)

    assert provider.last_messages is not None
    prompt = provider.last_messages[0].content
    assert "Uploaded files: none" in prompt


def test_claude_interpreter_falls_back_on_invalid_json() -> None:
    provider = _FakeLLMProvider(reply="not json at all")
    interpreter = ClaudeInterpreter(provider)

    understanding = interpreter(_empty_request())

    assert understanding.intent is None
    assert understanding.confidence == 0.0
    assert understanding.missing_information


def test_claude_interpreter_falls_back_on_unexpected_shape() -> None:
    provider = _FakeLLMProvider(reply=json.dumps({"intent": "x", "confidence": "not-a-number"}))
    interpreter = ClaudeInterpreter(provider)

    understanding = interpreter(_empty_request())

    assert understanding.intent is None
    assert understanding.confidence == 0.0
    assert understanding.missing_information


def test_claude_interpreter_falls_back_on_provider_failure() -> None:
    provider = _FakeLLMProvider(raises=RuntimeError("network error"))
    interpreter = ClaudeInterpreter(provider)

    understanding = interpreter(_empty_request())

    assert understanding.intent is None
    assert understanding.confidence == 0.0
    assert understanding.missing_information


def test_claude_interpreter_wired_into_understand_stage() -> None:
    state = _make_state()
    reply = json.dumps({"intent": "remove_duplicate_rows", "confidence": 0.95})
    provider = _FakeLLMProvider(reply=reply)
    stage = Understand(interpreter=ClaudeInterpreter(provider))

    stage(state)

    assert state.understanding is not None
    assert state.understanding.intent == "remove_duplicate_rows"
    assert state.intent == "remove_duplicate_rows"
```

---

## `tests/buddy/test_think.py` (new)

```python
"""Unit tests for buddy.think."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from buddy.context import ConversationRole, ConversationTurn
from buddy.llm import LLMMessage
from buddy.models import Understanding
from buddy.state import BuddyState
from buddy.think import ClaudeThinker, NullThinker, Reasoning, Think, ThinkingRequest


class _FakeLLMProvider:
    """A minimal ``LLMProvider`` stand-in that returns a canned reply."""

    def __init__(self, reply: str | None = None, *, raises: Exception | None = None) -> None:
        self._reply = reply
        self._raises = raises
        self.last_system: str | None = None
        self.last_messages: tuple[LLMMessage, ...] | None = None

    def complete(
        self, *, system: str, messages: tuple[LLMMessage, ...], max_tokens: int = 1024
    ) -> str:
        self.last_system = system
        self.last_messages = messages
        if self._raises is not None:
            raise self._raises
        assert self._reply is not None
        return self._reply


def _make_state() -> BuddyState:
    state = BuddyState.new(session_id="session-1", workspace_id="workspace-1")
    state.context.add_uploaded_file("report.xlsx")
    return state


def _empty_request(**overrides: object) -> ThinkingRequest:
    fields: dict[str, object] = {
        "understanding": Understanding(),
        "observations": (),
        "uploaded_files": (),
        "active_task": None,
        "recent_conversation": (),
    }
    fields.update(overrides)
    return ThinkingRequest(**fields)  # type: ignore[arg-type]


# -- Think stage: dead-field fix + repointed read ----------------------------


def test_think_sets_typed_reasoning_field() -> None:
    state = _make_state()
    reasoning = Reasoning(objective="dedupe the file", confidence=0.8)

    class _FixedThinker:
        def __call__(self, request: ThinkingRequest) -> Reasoning:
            return reasoning

    stage = Think(thinker=_FixedThinker())
    stage(state)

    assert state.reasoning == reasoning


def test_think_no_longer_writes_metadata_reasoning_result() -> None:
    state = _make_state()
    stage = Think(thinker=NullThinker())

    stage(state)

    assert "reasoning_result" not in state.metadata
    assert state.reasoning is not None  # NullThinker still returns a real Reasoning


def test_think_reads_understanding_from_typed_field_not_metadata() -> None:
    state = _make_state()
    state.understanding = Understanding(intent="remove_duplicate_rows", confidence=0.9)
    # Deliberately do NOT populate state.metadata["understanding"] — if Think
    # still read from metadata, this would regress to an empty Understanding.
    seen: list[Understanding] = []

    class _RecordingThinker:
        def __call__(self, request: ThinkingRequest) -> Reasoning:
            seen.append(request.understanding)
            return Reasoning()

    stage = Think(thinker=_RecordingThinker())
    stage(state)

    assert seen == [state.understanding]


def test_think_falls_back_to_empty_understanding_when_none_set() -> None:
    state = _make_state()
    seen: list[Understanding] = []

    class _RecordingThinker:
        def __call__(self, request: ThinkingRequest) -> Reasoning:
            seen.append(request.understanding)
            return Reasoning()

    stage = Think(thinker=_RecordingThinker())
    stage(state)

    assert seen == [Understanding()]


# -- ClaudeThinker -------------------------------------------------------------


def test_claude_thinker_parses_valid_json_reply() -> None:
    reply = json.dumps(
        {
            "objective": "Remove duplicate rows from report.xlsx",
            "summary": "The request is clear and a file is available.",
            "assumptions": ["duplicates means exact full-row duplicates"],
            "constraints": [],
            "risks": [],
            "recommended_strategy": "Use the Excel skill to deduplicate rows.",
            "clarification_required": False,
            "confidence": 0.9,
        }
    )
    provider = _FakeLLMProvider(reply=reply)
    thinker = ClaudeThinker(provider)
    request = _empty_request(
        understanding=Understanding(intent="remove_duplicate_rows", confidence=0.9),
        uploaded_files=("report.xlsx",),
    )

    reasoning = thinker(request)

    assert reasoning.objective == "Remove duplicate rows from report.xlsx"
    assert reasoning.clarification_required is False
    assert reasoning.confidence == pytest.approx(0.9)


def test_claude_thinker_prompt_contains_only_intent_and_file_references() -> None:
    """The prompt must be built solely from the classified ``Understanding``
    and file references — never from opening the uploaded file — since
    neither ``Understanding`` nor ``ThinkingRequest`` has a field capable of
    carrying cell or row data in the first place. An exact match proves
    nothing extra (e.g. file content) was added.
    """
    provider = _FakeLLMProvider(
        reply=json.dumps({"objective": "", "confidence": 0.0, "clarification_required": True})
    )
    thinker = ClaudeThinker(provider)
    request = _empty_request(
        understanding=Understanding(intent="remove_duplicate_rows", confidence=0.9),
        uploaded_files=("report.xlsx",),
    )

    thinker(request)

    assert provider.last_messages is not None
    prompt = provider.last_messages[0].content
    assert prompt == (
        "Classified intent: 'remove_duplicate_rows' (confidence=0.90)\n"
        "Uploaded files (names only): report.xlsx"
    )


def test_claude_thinker_prompt_includes_recent_conversation_text_only() -> None:
    provider = _FakeLLMProvider(
        reply=json.dumps({"objective": "", "confidence": 0.0, "clarification_required": True})
    )
    thinker = ClaudeThinker(provider)
    request = _empty_request(
        recent_conversation=(
            ConversationTurn(
                role=ConversationRole.USER,
                message="remove duplicate rows",
                timestamp=datetime.now(timezone.utc),
            ),
        ),
    )

    thinker(request)

    assert provider.last_messages is not None
    prompt = provider.last_messages[0].content
    assert "Recent conversation:" in prompt
    assert "user: remove duplicate rows" in prompt


def test_claude_thinker_falls_back_on_invalid_json() -> None:
    provider = _FakeLLMProvider(reply="not json")
    thinker = ClaudeThinker(provider)

    reasoning = thinker(_empty_request())

    assert reasoning.clarification_required is True
    assert reasoning.confidence == 0.0
    assert reasoning.risks


def test_claude_thinker_falls_back_on_unexpected_shape() -> None:
    provider = _FakeLLMProvider(reply=json.dumps({"confidence": "not-a-number"}))
    thinker = ClaudeThinker(provider)

    reasoning = thinker(_empty_request())

    assert reasoning.clarification_required is True
    assert reasoning.confidence == 0.0


def test_claude_thinker_falls_back_on_provider_failure() -> None:
    provider = _FakeLLMProvider(raises=RuntimeError("network error"))
    thinker = ClaudeThinker(provider)

    reasoning = thinker(_empty_request())

    assert reasoning.clarification_required is True
    assert reasoning.confidence == 0.0


def test_claude_thinker_wired_into_think_stage() -> None:
    state = _make_state()
    state.understanding = Understanding(intent="remove_duplicate_rows", confidence=0.9)
    reply = json.dumps(
        {"objective": "dedupe", "confidence": 0.8, "clarification_required": False}
    )
    provider = _FakeLLMProvider(reply=reply)
    stage = Think(thinker=ClaudeThinker(provider))

    stage(state)

    assert state.reasoning is not None
    assert state.reasoning.objective == "dedupe"
```

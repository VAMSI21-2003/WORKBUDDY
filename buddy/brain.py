"""Brain — interprets observations into user intent."""

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
    ``Context`` by the Brain stage, so an interpreter never has to
    depend on either of those types directly.

    Attributes:
        user_message: The current turn's raw message from the user.
        observations: Observations recorded during the Eyes stage.
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
    interpreter, or anything else. The Brain stage itself never
    interprets a request; it only calls whichever ``Interpreter`` it was
    given.
    """

    def __call__(self, request: UnderstandingRequest) -> Understanding:
        """Interpret ``request`` and return the resulting understanding."""
        ...


class NullInterpreter:
    """An ``Interpreter`` that performs no interpretation.

    Honestly reports that nothing has been determined yet, rather than
    guessing. Serves as a safe default so ``Brain`` can be
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


class Brain:
    """The Brain stage of Buddy's cognitive cycle.

    Brain turns this turn's observations into a structured
    understanding of what the user wants: an inferred intent, a
    confidence score, which files and work items the request refers to,
    what information is missing, and where the request is ambiguous. It
    does not reason about what to do next, does not build a plan, and
    does not execute, verify, or modify anything — those belong to Think,
    Planner, and later stages respectively (see
    ``docs/WORKBUDDY_ARCHITECTURE.md`` Section 4).

    Brain performs no interpretation itself. All of the actual
    language understanding — inferring intent, resolving references such
    as "that file" against the working context, judging completeness and
    ambiguity — is delegated to an injected ``Interpreter``. Brain's
    own responsibility is purely mechanical: assemble the interpreter's
    input from ``BuddyState`` and its ``Context``, call the interpreter,
    and record what it returns. This keeps Brain a stable, swappable
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
            "Brain recorded intent=%r confidence=%.2f for session %r",
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


# Every intent the classifier may return. Anything else in a reply is
# recorded as "unclear" rather than passed on as an unknown label.
INTENT_VOCABULARY = frozenset(
    {"remove_duplicate_rows", "remove_empty_rows", "multi_step_request", "unclear"}
)

_SYSTEM_PROMPT = """You are the intent classifier for WorkBuddy, an AI coworker.

Classify the user's request into exactly one of the following intents:
- "remove_duplicate_rows": the user wants duplicate rows removed from a spreadsheet.
- "remove_empty_rows": the user wants empty rows removed from a spreadsheet.
- "multi_step_request": the request clearly names more than one distinct action, but is not itself ambiguous.
- "unclear": the request does not clearly match a known intent, or is too ambiguous to act on.

You are never shown the contents of any uploaded file — only its name — so
never assume anything about what data a file contains.

Respond with a single JSON object and nothing else, matching this shape:
{
  "intent": "remove_duplicate_rows" | "remove_empty_rows" | "multi_step_request"
            | "unclear",
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
            intent = data.get("intent")
            if intent not in INTENT_VOCABULARY:
                logger.warning("ClaudeInterpreter: intent outside the vocabulary: %r", intent)
                intent = "unclear"
            return Understanding(
                intent=intent,
                confidence=float(data.get("confidence", 0.0)),
                referenced_files=tuple(data.get("referenced_files") or ()),
                referenced_work_items=tuple(data.get("referenced_work_items") or ()),
                missing_information=tuple(data.get("missing_information") or ()),
                ambiguity_flags=tuple(data.get("ambiguity_flags") or ()),
                notes=data.get("notes") or "",
            )
        except (AttributeError, TypeError, ValueError):
            logger.warning("ClaudeInterpreter: reply JSON had an unexpected shape: %r", data)
            return Understanding(
                intent=None,
                confidence=0.0,
                missing_information=("the model's reply had an unexpected shape",),
            )

"""Unit tests for buddy.brain."""

from __future__ import annotations

import json
import re

import pytest

from buddy.actions import ACTION_VOCABULARY, UNSUPPORTED, brain_ledger
from buddy.brain import (
    _SYSTEM_PROMPT,
    INTENT_VOCABULARY,
    Brain,
    ClaudeInterpreter,
    MentionCrossCheck,
    NullInterpreter,
    UnderstandingRequest,
)
from buddy.llm import LLMMessage
from buddy.models import (
    LEDGER_FROM_BRAIN,
    LEDGER_FROM_INTENT,
    LEDGER_FROM_MENTION,
    RequestedAction,
    Understanding,
)
from buddy.state import BuddyState


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


# -- Brain stage: dead-field fix ----------------------------------------


def test_brain_sets_typed_understanding_field() -> None:
    state = _make_state()
    understanding = Understanding(intent="remove_duplicate_rows", confidence=0.9)

    class _FixedInterpreter:
        def __call__(self, request: UnderstandingRequest) -> Understanding:
            return understanding

    stage = Brain(interpreter=_FixedInterpreter())
    stage(state)

    assert state.understanding == understanding
    assert state.intent == "remove_duplicate_rows"
    assert state.confidence == 0.9


def test_brain_no_longer_writes_metadata_understanding() -> None:
    state = _make_state()
    stage = Brain(interpreter=NullInterpreter())

    stage(state)

    assert "understanding" not in state.metadata
    assert state.understanding is not None  # NullInterpreter still returns a real Understanding


def test_null_interpreter_reports_nothing_understood() -> None:
    state = _make_state()
    stage = Brain(interpreter=NullInterpreter())

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


def test_claude_interpreter_wired_into_brain_stage() -> None:
    state = _make_state()
    reply = json.dumps({"intent": "remove_duplicate_rows", "confidence": 0.95})
    provider = _FakeLLMProvider(reply=reply)
    stage = Brain(interpreter=ClaudeInterpreter(provider))

    stage(state)

    assert state.understanding is not None
    assert state.understanding.intent == "remove_duplicate_rows"
    assert state.intent == "remove_duplicate_rows"


def test_claude_interpreter_accepts_multi_step_request_intent() -> None:
    """"multi_step_request" is a new legal value in the closed vocabulary
    (see docs/design/multi-step-planner-design.md), added so ClaudePlanner
    has an explicit signal distinct from "unclear" — confirm it round-trips
    through parsing exactly like any other intent string, with no special
    handling needed in ClaudeInterpreter itself.
    """
    reply = json.dumps(
        {
            "intent": "multi_step_request",
            "confidence": 0.9,
            "notes": "describes multiple distinct actions",
        }
    )
    provider = _FakeLLMProvider(reply=reply)
    interpreter = ClaudeInterpreter(provider)

    understanding = interpreter(_empty_request())

    assert understanding.intent == "multi_step_request"
    assert understanding.confidence == pytest.approx(0.9)


# -- Review F1: the intent is validated against the vocabulary ------------------------


@pytest.mark.parametrize(
    "intent",
    ["remove_duplicate_rows", "remove_empty_rows", "multi_step_request", "unclear"],
)
def test_claude_interpreter_keeps_every_intent_in_the_vocabulary(intent: str) -> None:
    provider = _FakeLLMProvider(reply=json.dumps({"intent": intent, "confidence": 0.9}))

    assert ClaudeInterpreter(provider)(_empty_request()).intent == intent


@pytest.mark.parametrize(
    "reply",
    [
        {"intent": "deduplicate", "confidence": 0.9},
        {"intent": "Remove_Duplicate_Rows", "confidence": 0.9},
        {"intent": "remove_duplicates", "confidence": 0.9},
        {"intent": "", "confidence": 0.9},
        {"intent": None, "confidence": 0.9},
        {"confidence": 0.9},
    ],
    ids=["invented", "wrong-case", "near-miss", "empty", "null", "missing"],
)
def test_claude_interpreter_records_any_other_intent_as_unclear(reply: dict) -> None:
    provider = _FakeLLMProvider(reply=json.dumps(reply))

    understanding = ClaudeInterpreter(provider)(_empty_request())

    assert understanding.intent == "unclear"
    assert understanding.confidence == pytest.approx(0.9)


def test_the_prompt_offers_exactly_the_vocabulary() -> None:
    offered = set(re.findall(r'^- "([a-z_]+)":', _SYSTEM_PROMPT, re.MULTILINE))
    assert offered == INTENT_VOCABULARY


def test_claude_interpreter_falls_back_when_the_reply_is_not_a_json_object() -> None:
    provider = _FakeLLMProvider(reply=json.dumps(["remove_duplicate_rows"]))

    understanding = ClaudeInterpreter(provider)(_empty_request())

    assert understanding.intent is None
    assert understanding.missing_information


# -- The requested-actions ledger (verified-outcome design, Section 3) -----------------


def _interpret(reply: dict, message: str) -> Understanding:
    provider = _FakeLLMProvider(reply=json.dumps(reply))
    return ClaudeInterpreter(provider)(_empty_request(user_message=message))


def test_requested_actions_are_parsed_in_order_with_the_users_own_words() -> None:
    understanding = _interpret(
        {
            "intent": "multi_step_request",
            "confidence": 0.9,
            "requested_actions": [
                {"action": "remove_empty_rows", "source_phrase": "delete the blank rows"},
                {"action": "remove_duplicate_rows", "source_phrase": "remove duplicates"},
            ],
        },
        "Delete the blank rows, then remove duplicates",
    )

    assert understanding.requested_actions == (
        RequestedAction("remove_empty_rows", "Delete the blank rows", LEDGER_FROM_BRAIN, True),
        RequestedAction("remove_duplicate_rows", "remove duplicates", LEDGER_FROM_BRAIN, True),
    )


@pytest.mark.parametrize(
    "action", ["Remove_Duplicate_Rows", " remove_duplicate_rows ", "REMOVE_DUPLICATE_ROWS\n"]
)
def test_a_casing_or_whitespace_slip_in_an_action_name_is_still_that_action(action: str) -> None:
    understanding = _interpret(
        {
            "intent": "multi_step_request",
            "confidence": 0.9,
            "requested_actions": [{"action": action, "source_phrase": "remove duplicates"}],
        },
        "remove duplicates and sort by amount",
    )

    assert understanding.requested_actions == (
        RequestedAction("remove_duplicate_rows", "remove duplicates", LEDGER_FROM_BRAIN, True),
    )


@pytest.mark.parametrize("action", ["sort_rows", "remove_duplicates", "report"])
def test_an_action_outside_the_vocabulary_becomes_unsupported_and_keeps_its_phrase(
    action: str,
) -> None:
    understanding = _interpret(
        {
            "intent": "multi_step_request",
            "confidence": 0.9,
            "requested_actions": [{"action": action, "source_phrase": "sort by amount"}],
        },
        "remove duplicates and sort by amount",
    )

    assert understanding.requested_actions == (
        RequestedAction(UNSUPPORTED, "sort by amount", LEDGER_FROM_BRAIN, True),
    )


def test_a_phrase_not_in_the_message_is_kept_but_marked_not_found() -> None:
    understanding = _interpret(
        {
            "intent": "remove_duplicate_rows",
            "confidence": 0.9,
            "requested_actions": [
                {"action": "remove_duplicate_rows", "source_phrase": "deduplicate the rows"}
            ],
        },
        "get rid of the repeats",
    )

    (entry,) = understanding.requested_actions
    assert (entry.action, entry.source_phrase, entry.phrase_found) == (
        "remove_duplicate_rows",
        "deduplicate the rows",
        False,
    )


@pytest.mark.parametrize(
    "raw",
    [None, "remove_duplicate_rows", {"action": "remove_duplicate_rows"}, 3],
    ids=["missing", "string", "object", "number"],
)
def test_a_missing_or_malformed_requested_actions_gives_no_entries(raw: object) -> None:
    reply: dict = {"intent": "remove_duplicate_rows", "confidence": 0.9}
    if raw is not None:
        reply["requested_actions"] = raw

    understanding = _interpret(reply, "remove duplicate rows")

    assert understanding.intent == "remove_duplicate_rows"
    assert understanding.requested_actions == ()
    # The ledger falls back to Brain's single-action intent.
    assert brain_ledger(understanding) == (
        RequestedAction("remove_duplicate_rows", "", LEDGER_FROM_INTENT, False),
    )


def test_entries_with_no_action_are_skipped_and_the_rest_kept() -> None:
    understanding = _interpret(
        {
            "intent": "remove_empty_rows",
            "confidence": 0.9,
            "requested_actions": [
                {"source_phrase": "remove empty rows"},
                {"action": "", "source_phrase": "x"},
                7,
                "remove_empty_rows",
            ],
        },
        "remove empty rows",
    )

    assert understanding.requested_actions == (
        RequestedAction("remove_empty_rows", "", LEDGER_FROM_BRAIN, False),
    )


def test_the_prompt_offers_exactly_the_action_vocabulary() -> None:
    line = next(ln for ln in _SYSTEM_PROMPT.splitlines() if '{"action":' in ln)
    assert set(re.findall(r'"([a-z_]+)"', line.split(":", 1)[1])) == ACTION_VOCABULARY
    assert "remove_empty_rows" in INTENT_VOCABULARY  # a Brain intent as well as an action


class _Fixed:
    def __init__(self, understanding: Understanding) -> None:
        self._understanding = understanding

    def __call__(self, request: UnderstandingRequest) -> Understanding:
        return self._understanding


def _cross_check(understanding: Understanding, message: str) -> Understanding:
    return MentionCrossCheck(_Fixed(understanding))(_empty_request(user_message=message))


def test_mention_cross_check_adds_only_what_brain_left_out() -> None:
    """N1: Brain read a dedupe request as empty-row removal."""
    understanding = _cross_check(
        Understanding(intent="remove_empty_rows", confidence=0.9),
        "remove the duplicate rows please",
    )

    assert understanding.intent == "remove_empty_rows"  # routing unchanged
    assert understanding.requested_actions == (
        RequestedAction("remove_empty_rows", "", LEDGER_FROM_INTENT, False),
        RequestedAction("remove_duplicate_rows", "duplicate rows", LEDGER_FROM_MENTION, True),
    )


def test_mention_cross_check_adds_nothing_brain_already_listed() -> None:
    brain = (
        RequestedAction("remove_duplicate_rows", "remove duplicates", LEDGER_FROM_BRAIN, True),
        RequestedAction("remove_empty_rows", "delete the empty rows", LEDGER_FROM_BRAIN, True),
    )
    understanding = _cross_check(
        Understanding(intent="multi_step_request", requested_actions=brain),
        "remove duplicates and also delete the empty rows",
    )

    assert understanding.requested_actions == brain


def test_mention_cross_check_adds_a_negated_mention_as_a_mention() -> None:
    """A false positive the design accepts: it can only add a true "I didn't
    run …" sentence, never a claim."""
    brain = (
        RequestedAction("remove_empty_rows", "remove the empty rows", LEDGER_FROM_BRAIN, True),
    )
    understanding = _cross_check(
        Understanding(intent="remove_empty_rows", requested_actions=brain),
        "remove the empty rows but keep the duplicates",
    )

    assert understanding.requested_actions == (
        *brain,
        RequestedAction("remove_duplicate_rows", "duplicates", LEDGER_FROM_MENTION, True),
    )


def test_mention_cross_check_runs_when_brain_failed() -> None:
    understanding = _cross_check(Understanding(intent=None), "remove the blank lines")

    assert understanding.intent is None
    assert understanding.requested_actions == (
        RequestedAction("remove_empty_rows", "blank lines", LEDGER_FROM_MENTION, True),
    )


def test_mention_cross_check_misses_a_paraphrase() -> None:
    """The known limit (design 3.2): a paraphrase isn't detected, so the
    reply is silent about it rather than wrong."""
    understanding = _cross_check(
        Understanding(intent="remove_empty_rows"), "get rid of the repeated entries"
    )

    assert [e.action for e in understanding.requested_actions] == ["remove_empty_rows"]

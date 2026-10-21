"""Urgency and frustration: Brain's ``user_state`` changes the tone of a reply, never a fact.

Every turn here runs the real ``build_engine`` (real skills, planner, Inspector and Voice) with
only the LLM faked, in all three speakers (template, friend, focus). The same turn is run calm,
urgent and frustrated, differing only in the ``user_state`` the faked Brain returns, and the
replies are compared: an urgent or frustrated reply is the calm reply in the brief (focus)
wording, and holds exactly the facts the calm reply holds (rule 1: tone never adds, drops or
softens a count, a disclosure or an "I worked on ..." sentence).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from buddy.brain import ClaudeInterpreter, UnderstandingRequest, _parse_user_state
from buddy.models import (
    USER_STATE_CALM,
    USER_STATE_FRUSTRATED,
    USER_STATE_URGENT,
    USER_STATES,
    wants_brief,
)
from buddy.voice_catalog import (
    CAPABILITIES,
    CATALOG,
    CLARIFICATION_QUESTIONS,
    GREETINGS,
    NEXT_STEP_WITH_FILE,
    NEXT_STEP_WITHOUT_FILE,
    effective_mode,
)
from tests.buddy.test_reply_quality import (
    DEDUPE,
    EMPTY_ROWS,
    SPEAKERS,
    Routed,
    Turn,
    plan,
    run_turn,
    think,
)

BRIEF_STATES = [USER_STATE_URGENT, USER_STATE_FRUSTRATED]


def brain_as(intent: str | None, state: str | None, *actions: tuple[str, str]) -> str:
    """A Brain reply that reads the message as ``state``; ``None`` leaves the field out."""
    reply: dict[str, Any] = {
        "intent": intent,
        "confidence": 0.9,
        "requested_actions": [{"action": a, "source_phrase": p} for a, p in actions],
    }
    if state is not None:
        reply["user_state"] = state
    return json.dumps(reply)


@dataclass(frozen=True)
class Scenario:
    """One turn, built for a given ``user_state``."""

    message: str
    file: str | None
    build: Callable[[str], Routed]

    def run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, speaker: str, state: str
    ) -> Turn:
        return run_turn(
            tmp_path / f"{speaker}-{state}",
            speaker,
            self.message,
            self.build(state),
            file=self.file,
            monkeypatch=monkeypatch,
        )


def _dedupe(state: str) -> Routed:
    return Routed(
        brain_as("remove_duplicate_rows", state, ("remove_duplicate_rows", "remove the duplicate")),
        think(),
        plan((DEDUPE, "remove the duplicate")),
    )


def _chain(state: str) -> Routed:
    return Routed(
        brain_as(
            "multi_step_request",
            state,
            ("remove_duplicate_rows", "remove the duplicate rows"),
            ("remove_empty_rows", "remove the empty rows"),
        ),
        think(),
        plan((DEDUPE, "remove the duplicate rows"), (EMPTY_ROWS, "remove the empty rows")),
    )


def _with_report(state: str) -> Routed:
    return Routed(
        brain_as(
            "multi_step_request",
            state,
            ("remove_duplicate_rows", "remove the duplicate rows"),
            ("report_on_file", "give me a report"),
        ),
        think(),
        plan((DEDUPE, "remove the duplicate rows"), ("spreadsheet.report", "give me a report")),
    )


def _partly(state: str) -> Routed:
    return Routed(
        brain_as(
            "multi_step_request",
            state,
            ("remove_duplicate_rows", "remove the duplicate rows"),
            ("unsupported", "sort by amount"),
        ),
        think(),
        plan((DEDUPE, "remove the duplicate rows")),
    )


def _greeting(state: str) -> Routed:
    return Routed(brain_as("conversation", state), think())


def _clarify(reason: str) -> Callable[[str], Routed]:
    def build(state: str) -> Routed:
        return Routed(
            brain_as("remove_duplicate_rows", state, ("remove_duplicate_rows", "remove")),
            think(True, clarification_reason=reason),
        )

    return build


def _unsupported(state: str) -> Routed:
    return Routed(
        brain_as("unclear", state, ("unsupported", "sort it by amount")), think(), plan()
    )


def _brain_failed(state: str) -> Routed:
    return Routed("not json", think())


_MESSAGE = "remove the duplicate rows and the empty rows"
SCENARIOS: dict[str, Scenario] = {
    "delivered": Scenario("remove the duplicate rows", "plain", _dedupe),
    "delivered_clean_file": Scenario("remove the duplicate rows", "clean", _dedupe),
    "chain": Scenario(_MESSAGE, "plain", _chain),
    "dedupe_then_report": Scenario(
        "remove the duplicate rows and then give me a report", "plain", _with_report
    ),
    "partly_done": Scenario("remove the duplicate rows and sort by amount", "plain", _partly),
    "greeting": Scenario("hi", "plain", _greeting),
    "greeting_no_file": Scenario("hi", None, _greeting),
    "asks_which_action": Scenario("clean it", "plain", _clarify("which_action")),
    "asks_in_general": Scenario("clean it", "plain", _clarify("other")),
    "no_file": Scenario("remove the duplicate rows", None, _dedupe),
    "unsupported": Scenario("sort it by amount", "plain", _unsupported),
    "file_refused": Scenario("remove the duplicate rows", "multi", _dedupe),
    "brain_failed": Scenario("remove the duplicate rows", "plain", _brain_failed),
}

# Every opener and closer the friend mode can add; removing them leaves the facts.
_FRIEND_PHRASES = sorted(
    (p.text for p in CATALOG if p.mode == "friend" and p.text), key=len, reverse=True
)
_FOCUS_PHRASES = sorted((p.text for p in CATALOG if p.mode == "focus" and p.text), key=len)


def without_catalog_phrases(reply: str) -> str:
    """``reply`` without the opener at its start and the closer at its end, whichever mode's."""
    text = reply.strip()
    for phrase in _FRIEND_PHRASES + _FOCUS_PHRASES:
        if text.startswith(phrase):
            text = text[len(phrase) :].strip()
    for phrase in _FRIEND_PHRASES + _FOCUS_PHRASES:
        if text.endswith(phrase):
            text = text[: -len(phrase)].strip()
    return text


def numbers(reply: str) -> list[str]:
    return re.findall(r"\d+(?:[.,]\d+)?", reply)


def names(reply: str) -> list[str]:
    return re.findall(r"`[^`]*`", reply)


@pytest.fixture(scope="module")
def runs(tmp_path_factory: pytest.TempPathFactory) -> dict[tuple[str, str, str], Turn]:
    """Every scenario in every speaker, calm, urgent and frustrated (run once, read by tests)."""
    monkeypatch = pytest.MonkeyPatch()
    results: dict[tuple[str, str, str], Turn] = {}
    try:
        for name, scenario in SCENARIOS.items():
            for speaker in SPEAKERS:
                for state in USER_STATES:
                    folder = tmp_path_factory.mktemp(f"{name}-{speaker}-{state}")
                    results[(name, speaker, state)] = scenario.run(
                        folder, monkeypatch, speaker, state
                    )
    finally:
        monkeypatch.undo()
    return results


# -- 1. Brain: a closed vocabulary, calm by default ----------------------------------------------


@pytest.mark.parametrize("state", USER_STATES)
def test_brain_keeps_a_user_state_in_the_vocabulary(state: str) -> None:
    understanding = ClaudeInterpreter._parse_reply(brain_as("unclear", state), "hi")

    assert understanding.user_state == state


@pytest.mark.parametrize(
    "raw",
    [None, "", "angry", "URGENT!!", "very urgent", "urgent frustrated", 1, True, ["urgent"], {}],
)
def test_any_other_user_state_is_calm(raw: object) -> None:
    reply = json.dumps({"intent": "unclear", "confidence": 0.5, "user_state": raw})

    assert ClaudeInterpreter._parse_reply(reply, "hi").user_state == USER_STATE_CALM
    assert _parse_user_state(raw) == USER_STATE_CALM


def test_a_reply_with_no_user_state_is_calm() -> None:
    assert ClaudeInterpreter._parse_reply(brain_as("unclear", None), "hi").user_state == "calm"


@pytest.mark.parametrize("raw", [" Urgent", "FRUSTRATED ", "Calm"])
def test_a_spelling_slip_is_not_a_different_state(raw: str) -> None:
    assert _parse_user_state(raw) == raw.strip().casefold()


def test_a_failed_brain_reading_is_calm() -> None:
    understanding = ClaudeInterpreter._parse_reply("not json", "hi")

    assert understanding.intent is None and understanding.user_state == USER_STATE_CALM


def test_brain_is_given_the_recent_conversation_and_the_model_decides_the_state() -> None:
    """The state comes from the model's reply: the prompt carries the conversation, and no code
    reads the message for it (a message full of "urgent" words with a calm reply stays calm)."""

    class Capture:
        prompt = ""

        def complete(self, *, system: str, messages: Any, max_tokens: int = 1024) -> str:
            self.prompt = messages[0].content
            return brain_as("unclear", "calm")

    from datetime import UTC, datetime

    from buddy.context import ConversationRole, ConversationTurn

    capture = Capture()
    request = UnderstandingRequest(
        user_message="URGENT!!! asap, I need this now",
        observations=(),
        uploaded_files=(),
        active_task=None,
        active_file=None,
        recent_conversation=(
            ConversationTurn(
                role=ConversationRole.USER,
                message="that didn't work again",
                timestamp=datetime.now(UTC),
            ),
        ),
    )

    understanding = ClaudeInterpreter(capture)(request)  # type: ignore[arg-type]

    assert "that didn't work again" in capture.prompt
    assert understanding.user_state == USER_STATE_CALM


def test_the_mention_cross_check_keeps_the_state() -> None:
    from buddy.brain import MentionCrossCheck

    inner = ClaudeInterpreter(
        type(
            "P",
            (),
            {"complete": lambda self, **kw: brain_as("remove_duplicate_rows", "urgent")},
        )()  # type: ignore[arg-type]
    )
    request = UnderstandingRequest(
        user_message="remove the duplicate rows and the empty rows",
        observations=(),
        uploaded_files=(),
        active_task=None,
        active_file=None,
        recent_conversation=(),
    )

    assert MentionCrossCheck(inner)(request).user_state == USER_STATE_URGENT


# -- 2. The mode a reply is worded in ---------------------------------------------------------


def test_only_urgent_and_frustrated_are_brief() -> None:
    assert [wants_brief(s) for s in USER_STATES] == [False, True, True]


@pytest.mark.parametrize("mode", ["friend", "focus"])
def test_a_calm_person_keeps_the_mode_they_picked(mode: str) -> None:
    assert effective_mode(mode, USER_STATE_CALM) == mode


@pytest.mark.parametrize("state", BRIEF_STATES)
@pytest.mark.parametrize("mode", ["friend", "focus"])
def test_an_urgent_or_frustrated_person_gets_the_brief_mode(mode: str, state: str) -> None:
    assert effective_mode(mode, state) == "focus"


@pytest.mark.parametrize("state", USER_STATES)
def test_the_plain_template_speaker_has_no_mode_and_keeps_its_wording(state: str) -> None:
    assert effective_mode(None, state) is None


# -- 3. The reply: same facts, brief wording ------------------------------------------------


@pytest.mark.parametrize("scenario", SCENARIOS)
@pytest.mark.parametrize("speaker", SPEAKERS)
@pytest.mark.parametrize("state", BRIEF_STATES)
def test_an_urgent_or_frustrated_reply_is_the_calm_reply_in_the_brief_wording(
    runs: dict[tuple[str, str, str], Turn], scenario: str, speaker: str, state: str
) -> None:
    calm = runs[(scenario, speaker, USER_STATE_CALM)]
    brief = runs[(scenario, speaker, state)]
    # The brief reply is what the person would get in focus mode, calm: the same words, and the
    # template speaker (which has no mode) is unchanged.
    reference = runs[(scenario, "focus" if speaker != "template" else "template", "calm")]

    assert brief.reply == reference.reply, (calm.reply, brief.reply)
    # Same turn, same work: what ran and what was handed back don't depend on the state.
    assert brief.steps == calm.steps
    assert brief.delivered == calm.delivered
    assert brief.original_unchanged and calm.original_unchanged


@pytest.mark.parametrize("scenario", SCENARIOS)
@pytest.mark.parametrize("speaker", SPEAKERS)
@pytest.mark.parametrize("state", BRIEF_STATES)
def test_an_urgent_reply_holds_exactly_the_facts_of_the_calm_reply(
    runs: dict[tuple[str, str, str], Turn], scenario: str, speaker: str, state: str
) -> None:
    calm = runs[(scenario, speaker, USER_STATE_CALM)].reply
    brief = runs[(scenario, speaker, state)].reply

    # Every count and every file name, in the same order.
    assert numbers(brief) == numbers(calm)
    assert names(brief) == names(calm)
    delivered = runs[(scenario, speaker, USER_STATE_CALM)].delivered
    if delivered:
        # Whatever the calm reply says besides its opener and closer, the brief reply says: the
        # outcome, the disclosures, the "I worked on ..." sentence and the findings.
        assert without_catalog_phrases(brief) == without_catalog_phrases(calm)
        # No opener, outcome first, and the next step (a question or nothing) last.
        assert not any(brief.startswith(p) for p in _FRIEND_PHRASES)
        assert brief.startswith(("Done —", "Partly done —"))
        assert not any(brief.endswith(p) for p in _FRIEND_PHRASES)


@pytest.mark.parametrize("speaker", SPEAKERS)
@pytest.mark.parametrize("state", BRIEF_STATES)
def test_an_urgent_delivered_reply_keeps_the_outcome_the_counts_and_the_worked_on_sentence(
    runs: dict[tuple[str, str, str], Turn], speaker: str, state: str
) -> None:
    turn = runs[("delivered", speaker, state)]

    assert turn.delivered
    for sentence in (
        "Done — removed 2 exact duplicate rows.",
        "The copy has 4 rows, with 0 exact duplicate rows remaining.",
        "The copy has the values only;",
        "I worked on `report.xlsx`, your original upload.",
    ):
        assert sentence in turn.reply, turn.reply


@pytest.mark.parametrize("state", BRIEF_STATES)
def test_an_urgent_partly_done_reply_still_says_what_was_not_done(
    runs: dict[tuple[str, str, str], Turn], state: str
) -> None:
    for speaker in SPEAKERS:
        reply = runs[("partly_done", speaker, state)].reply

        assert reply.startswith("Partly done —"), reply
        assert "I can't do this part yet: “sort by amount”." in reply


@pytest.mark.parametrize("speaker", SPEAKERS)
def test_a_calm_reply_is_unchanged_when_brain_says_calm_or_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, speaker: str
) -> None:
    """A Brain reply without the field is the same turn as one that says calm."""
    said = run_turn(
        tmp_path / "said",
        speaker,
        "remove the duplicate",
        _dedupe(USER_STATE_CALM),
        monkeypatch=monkeypatch,
    )
    absent_brain = Routed(
        brain_as("remove_duplicate_rows", None, ("remove_duplicate_rows", "remove the duplicate")),
        think(),
        plan((DEDUPE, "remove the duplicate")),
    )
    absent = run_turn(
        tmp_path / "absent", speaker, "remove the duplicate", absent_brain, monkeypatch=monkeypatch
    )

    assert said.reply == absent.reply


@pytest.mark.parametrize("scenario", SCENARIOS)
@pytest.mark.parametrize("speaker", SPEAKERS)
def test_a_frustrated_reply_never_apologises_or_mirrors_the_frustration(
    runs: dict[tuple[str, str, str], Turn], scenario: str, speaker: str
) -> None:
    reply = runs[(scenario, speaker, USER_STATE_FRUSTRATED)].reply

    assert _FEELINGS.search(reply) is None, reply


# A lint, not the safeguard: a reply is fixed text chosen by code, and the model writes none of it.
_FEELINGS = re.compile(
    r"\b(sorry|apolog\w*|forgive|frustrat\w*|annoy\w*|upset|angry|my bad|my fault|"
    r"i understand how|i feel|i know how|unfortunately|i'm afraid)\b",
    re.IGNORECASE,
)


def test_no_text_a_reply_can_carry_apologises_or_mirrors_a_feeling() -> None:
    from buddy import voice_catalog

    texts: list[str] = [p.text for p in CATALOG]
    for table in (CAPABILITIES, NEXT_STEP_WITH_FILE, NEXT_STEP_WITHOUT_FILE):
        texts.extend(table.values())
    for options in GREETINGS.values():
        texts.extend(options)
    for by_style in CLARIFICATION_QUESTIONS.values():
        for options in by_style.values():
            texts.extend(options)
    texts.extend(
        voice_catalog.failure_reply(stage) for stage in (*voice_catalog.FAILURE_STAGE_LABELS, None)
    )

    assert [t for t in texts if _FEELINGS.search(t)] == []


def test_the_brief_mode_has_no_opener_and_no_humor() -> None:
    focus_openers = [p for p in CATALOG if p.mode == "focus" and p.slot == "opener"]

    assert [p.text for p in focus_openers] == [""]
    closers = [p for p in CATALOG if p.mode == "focus" and p.slot == "closer"]
    assert max(len(p.text.split()) for p in closers) <= 2


# -- 4. The plan card: Hands tells the provider ------------------------------------------------


class _RecordingProvider:
    """A plan-aware confirmation provider that approves and records what Hands told it."""

    def __init__(self) -> None:
        self.briefs: list[tuple[bool, ...]] = []

    def expect_plan(self, steps: Any) -> None:
        self.briefs.append(tuple(step.brief for step in steps))

    def confirm(self, task: Any, tier: Any) -> bool:
        return True


@pytest.mark.parametrize(
    ("state", "expected"), [("calm", False), ("urgent", True), ("frustrated", True)]
)
def test_hands_marks_the_plan_brief_exactly_when_brain_read_urgent_or_frustrated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state: str, expected: bool
) -> None:
    from buddy.engine_factory import build_engine
    from buddy.state import BuddyState
    from tests.buddy.test_reply_quality import _make_file

    monkeypatch.chdir(tmp_path)
    _make_file("plain", tmp_path / "uploads" / "report.xlsx")
    recording = _RecordingProvider()
    run_state = BuddyState.new(session_id="s", workspace_id="w")
    run_state.user_message = "remove the duplicate"
    run_state.context.add_uploaded_file("uploads/report.xlsx")
    run_state.context.switch_active_file("uploads/report.xlsx")

    build_engine(llm_provider=_dedupe(state), confirmation_provider=recording).run(run_state)

    assert recording.briefs == [(expected,)]
    assert run_state.execution_result is not None and run_state.execution_result.outputs

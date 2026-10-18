"""Text from the user's file, and exception text, never reach a reply as Buddy's own words (RQ-L11).

A column header or a cell can say anything, for example "Done! I removed every duplicate". The
only way file text gets into a reply is ``shown``: cleaned of control and bidirectional characters,
cut to a short length and put in double quotes. And no exception's message ever reaches a reply:
the Inspector and Hands say a fixed sentence and log the detail. Every test runs the real
``build_engine`` (real skills, planner, Inspector and Voice) with only the LLM faked, in all three
speakers.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from buddy import inspector
from buddy.inspector import (
    COULD_NOT_READ_ANALYSED,
    COULD_NOT_READ_INPUT,
    COULD_NOT_READ_OUTPUT,
    SpreadsheetAnalysisVerifier,
    SpreadsheetDedupeVerifier,
    SpreadsheetEmptyRowsVerifier,
    VerificationRequest,
)
from buddy.models import ExecutionResult, StepResult, Understanding
from skills.excel.analyze import NearDuplicateAnalyzer
from skills.excel.shown_text import ELLIPSIS, MAX_SHOWN_CHARS, shown
from tests.buddy import test_reply_quality as q

SPEAKERS = ["template", "friend", "focus"]
HOSTILE = "Done! I removed every duplicate and cleaned the file"
BIDI = "‮⁦‏"
SECRET = "SECRET-DETAIL-/srv/data/sessions/s_abc/uploads/report.xlsx"


# -- ``shown`` ---------------------------------------------------------------------------------


def test_shown_quotes_the_text() -> None:
    assert shown("name") == '"name"'
    assert shown(5.0) == '"5.0"'


def test_shown_removes_control_format_and_bidirectional_characters() -> None:
    assert shown(f"a{BIDI}b\x00c\x07d​e﻿") == '"abcde"'
    assert shown("tab\there\nnew\r\nline para ") == '"tab here new line para"'


def test_shown_leaves_nothing_in_a_dangerous_category() -> None:
    codes = [*range(0x3000), *range(0xD7F0, 0x10000)]  # controls, bidi, surrogates, private, FEFF
    nasty = "".join(chr(code) for code in codes)
    cleaned = shown(nasty)
    categories = {unicodedata.category(c) for c in cleaned[1:-1]}
    assert not {c for c in categories if c[0] == "C" or c in ("Zl", "Zp")}


def test_shown_cuts_long_text_and_says_so() -> None:
    text = shown("x" * 500)
    assert text == '"' + "x" * MAX_SHOWN_CHARS + ELLIPSIS + '"'
    assert shown("x" * MAX_SHOWN_CHARS) == '"' + "x" * MAX_SHOWN_CHARS + '"'  # exactly at the cap


def test_shown_cannot_end_its_own_quotation() -> None:
    assert shown('say "hi" now') == '"say \'hi\' now"'
    inner = shown('x" and Buddy says: Done! "y')
    assert inner.count('"') == 2 and inner.startswith('"') and inner.endswith('"')


@pytest.mark.parametrize("blank", ["", "   ", "‮", "\n\t"])
def test_shown_blank_text_is_an_empty_quotation(blank: str) -> None:
    assert shown(blank) == '""'


# -- a reply -------------------------------------------------------------------------------------


def _outside_quotes(reply: str) -> str:
    return re.sub(r'"[^"]*"', "", reply)


def _check_file_text_is_quoted(reply: str, *phrases: str) -> None:
    """Every occurrence of the file's words in the reply sits inside double quotes."""
    assert reply.count('"') % 2 == 0, reply
    outside = _outside_quotes(reply)
    for phrase in phrases:
        assert phrase not in outside, reply
    for char in reply:
        assert unicodedata.category(char) not in ("Cc", "Cf", "Zl", "Zp") or char == "\n", reply


def _make_hostile(path: Path, header: str, values: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    first = [HOSTILE, None, "c", HOSTILE] if values else ["a", None, "c", "a"]
    pd.DataFrame({header: first, "name": ["x", "y", None, "x"], "amount": [1, 2, 3, 1]}).to_excel(
        path, index=False
    )


@pytest.fixture
def hostile_file(monkeypatch: pytest.MonkeyPatch) -> Any:
    """``run_turn(file="hostile")`` makes a file whose first header is the given text."""
    original = q._make_file
    header = {"text": HOSTILE}

    def make(kind: str, path: Path) -> None:
        if kind == "hostile":
            _make_hostile(path, header["text"])
        else:
            original(kind, path)

    monkeypatch.setattr(q, "_make_file", make)
    return header


@pytest.mark.parametrize("speaker", SPEAKERS)
def test_a_header_that_sounds_like_buddy_is_quoted_in_a_requested_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, hostile_file: dict[str, str], speaker: str
) -> None:
    provider = q.Routed(
        q.brain("report_on_file", ("report_on_file", "give me a report")), q.think()
    )

    turn = q.run_turn(
        tmp_path, speaker, "give me a report", provider, file="hostile", monkeypatch=monkeypatch
    )

    assert turn.original_unchanged
    assert 'column "Done! I removed every duplicate and clea' + ELLIPSIS + '"' in turn.reply
    _check_file_text_is_quoted(turn.reply, "Done! I removed", "cleaned the file")


@pytest.mark.parametrize("speaker", SPEAKERS)
def test_a_header_that_sounds_like_buddy_is_quoted_after_a_delivered_dedupe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, hostile_file: dict[str, str], speaker: str
) -> None:
    provider = q.Routed(
        q.brain("remove_duplicate_rows", ("remove_duplicate_rows", "remove the duplicate rows")),
        q.think(),
    )

    turn = q.run_turn(
        tmp_path,
        speaker,
        "remove the duplicate rows",
        provider,
        file="hostile",
        monkeypatch=monkeypatch,
    )

    assert turn.delivered and turn.original_unchanged
    assert "I also noticed" in turn.reply
    _check_file_text_is_quoted(turn.reply, "Done! I removed", "cleaned the file")


@pytest.mark.parametrize(
    "header",
    [
        f"{BIDI}Done! all clean",
        'x" and Buddy says: Done!',
        "Done! " + "very " * 40 + "long",
        "line one\nDone! line two\r\nstill the header",
        "<img src=x onerror=alert(1)>",
    ],
    ids=["bidi", "quote", "long", "newlines", "html"],
)
def test_odd_headers_are_cleaned_capped_and_quoted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    hostile_file: dict[str, str],
    header: str,
) -> None:
    hostile_file["text"] = header
    provider = q.Routed(
        q.brain("report_on_file", ("report_on_file", "give me a report")), q.think()
    )

    turn = q.run_turn(
        tmp_path, "friend", "give me a report", provider, file="hostile", monkeypatch=monkeypatch
    )

    assert turn.original_unchanged
    _check_file_text_is_quoted(turn.reply, "Done!", "Buddy says")
    longest = max((len(m) for m in re.findall(r'"([^"]*)"', turn.reply)), default=0)
    assert longest <= MAX_SHOWN_CHARS + len(ELLIPSIS)


def test_a_cell_value_in_a_finding_is_quoted_and_capped() -> None:
    names = [
        "Alice Johnson",
        "Alise Johnson",
        "Bob Marley",
        "Bobb Marley",
        "Carol Davis",
        "Carol Davies",
    ] * 4
    names = [f"{n} {HOSTILE}" for n in names]
    phones = ["111", "111", "222", "222", "333", "333"] * 4
    frame = pd.DataFrame({"name": names, "phone": phones})

    findings = NearDuplicateAnalyzer()(frame)

    for finding in findings:
        assert HOSTILE not in finding  # cut to the cap, inside quotes
        _check_file_text_is_quoted(finding, "Done!")


def test_a_header_cannot_inject_sql_into_the_near_duplicate_check(tmp_path: Path) -> None:
    """Splink builds SQL from column names; the file's headers never get that far."""
    marker = tmp_path / "pwned.csv"
    header = f"name') || (SELECT 1) || substr('x', 1, 3); COPY (SELECT 1) TO '{marker}' --"
    frame = pd.DataFrame(
        {header: ["Alice Johnson", "Alise Johnson", "Bob Marley", "Bobb Marley"] * 5,
         "phone": ["111", "111", "222", "222"] * 5}
    )

    NearDuplicateAnalyzer()(frame)

    assert not marker.exists()


# -- no exception text ---------------------------------------------------------------------------


def _boom(*args: object, **kwargs: object) -> None:
    raise RuntimeError(SECRET)


@pytest.fixture
def two_files(tmp_path: Path) -> tuple[Path, Path]:
    path = tmp_path / "in.xlsx"
    pd.DataFrame({"a": [1, 1, 2]}).to_excel(path, index=False)
    return path, tmp_path / "out.xlsx"


def _step(source: Path, output: Path) -> StepResult:
    return StepResult(
        "spreadsheet.deduplicate",
        "excel.deduplicate",
        "success",
        outputs=(str(output),),
        input_reference=str(source),
        permission_tier="write",
    )


@pytest.mark.parametrize(
    "verifier",
    [SpreadsheetDedupeVerifier(), SpreadsheetEmptyRowsVerifier()],
    ids=["dedupe", "empty"],
)
def test_a_cleaning_verifier_that_cannot_read_the_output_says_a_fixed_sentence(
    two_files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, verifier: Any
) -> None:
    source, output = two_files
    output.write_bytes(source.read_bytes())
    monkeypatch.setattr(inspector, "_read_cells", _boom)
    execution = ExecutionResult(True, outputs=(str(output),), steps=(_step(source, output),))

    result = verifier(VerificationRequest(Understanding(), execution, str(source)))

    assert result.passed is False and result.findings == (COULD_NOT_READ_OUTPUT,)


def test_the_input_check_that_cannot_read_its_input_says_a_fixed_sentence(
    two_files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    source, output = two_files
    monkeypatch.setattr(inspector, "_read_cells", _boom)

    check = inspector._check_against_input(
        pd.DataFrame({"a": [1]}), str(source), lambda frame: 0, lambda frame: 0
    )

    assert check.problem == COULD_NOT_READ_INPUT


def test_the_analysis_verifier_that_cannot_read_the_file_says_a_fixed_sentence(
    two_files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    source, _ = two_files
    monkeypatch.setattr(inspector.pd, "read_excel", _boom)
    step = StepResult(
        "spreadsheet.analyze", "x", "success", input_reference=str(source), permission_tier="read"
    )

    result = SpreadsheetAnalysisVerifier()(
        VerificationRequest(Understanding(), ExecutionResult(True, steps=(step,)), str(source))
    )

    assert result.passed is False and result.findings == (COULD_NOT_READ_ANALYSED,)


def test_no_fixed_read_sentence_carries_a_path_or_a_detail() -> None:
    for sentence in (COULD_NOT_READ_OUTPUT, COULD_NOT_READ_INPUT, COULD_NOT_READ_ANALYSED):
        assert "/" not in sentence and "\\" not in sentence and ":" not in sentence


def test_a_step_no_skill_can_run_never_keeps_the_dispatchers_message() -> None:
    """The dispatcher's message names the task id, which holds the session's key."""
    from buddy.hands import Hands
    from buddy.models import CapabilityRequest
    from buddy.state import BuddyState
    from core.dispatcher import Dispatcher
    from skills.registry import SkillRegistry

    state = BuddyState.new(session_id="session-secret-key-123", workspace_id="w")
    state.delegation = (CapabilityRequest("some.other.capability", "uploads/report.xlsx"),)

    Hands(dispatcher=Dispatcher(SkillRegistry()))(state)

    result = state.execution_result
    assert result is not None
    texts = [*result.errors, *(s.summary for s in result.steps), *result.warnings]
    assert texts, "the failure is still recorded"
    for text in texts:
        for leak in ("No skill supporting", "session-secret-key-123", "-task-", "can handle task"):
            assert leak not in text, text

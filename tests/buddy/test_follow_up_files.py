"""Follow-up turns through ``build_engine``: a turn builds on the latest copy handed back.

The pipeline half of ``docs/design/follow-up-turns.md``. A caller that runs several turns in one
session offers a turn the upload and the latest copy handed back from it
(``BuddyState.file_choice``); Brain's reading decides which is used (``file_to_use``, a closed
vocabulary), and the reply says which in one fixed sentence. Every skill, verifier and speaker is
real; only the LLM is faked. The numbers in every reply are checked against an independent read
of the file the turn actually used, with openpyxl.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

import openpyxl
import pandas as pd
import pytest

from buddy.engine_factory import build_engine
from buddy.models import FILE_KIND_COPY, FILE_KIND_ORIGINAL, FileChoice
from buddy.state import BuddyState
from buddy.voice import _COMPLETION_CLAIM, PersonalityVoice, is_verified_delivery
from buddy.voice_catalog import (
    NO_FILE_SENTENCE,
    WORKED_ON_ORIGINAL_SENTENCE,
    WORKED_ON_ORIGINAL_SKIPPING_COPY_SENTENCE,
)
from tests.buddy.test_engine_factory import _chain_replies, _QueuedLLMProvider

SPEAKERS = ["template", "friend", "focus"]

# 6 rows: Alice and Bob each appear twice (2 duplicate rows), one fully-empty row.
ROWS = [
    ["Alice", 10],
    ["Bob", 20],
    ["Alice", 10],
    [None, None],
    ["Carol", 30],
    ["Bob", 20],
]


def _sha(path: Path | str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@dataclass(frozen=True)
class Facts:
    """What an independent read of a workbook finds."""

    rows: int
    duplicates: int
    empties: int


def facts(path: Path | str) -> Facts:
    """Rows, rows that repeat an earlier row exactly, and fully-empty rows, read with openpyxl."""
    sheet = openpyxl.load_workbook(path, read_only=True).worksheets[0]
    body = [tuple(row) for row in sheet.iter_rows(min_row=2, values_only=True)]
    seen: set[tuple[object, ...]] = set()
    duplicates = 0
    for row in body:
        duplicates += row in seen
        seen.add(row)
    return Facts(
        rows=len(body),
        duplicates=duplicates,
        empties=sum(all(cell is None for cell in row) for row in body),
    )


def write_upload(folder: Path, name: str = "report.xlsx", rows: list[list[object]] | None = None):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    pd.DataFrame(ROWS if rows is None else rows, columns=["name", "amount"]).to_excel(
        path, index=False
    )
    return path


def brain(intent: str, *, file_to_use: object = "latest", phrase: str | None = None) -> list[str]:
    """Brain's and Think's canned replies for a single-action turn."""
    entry = [{"action": intent, "source_phrase": phrase or ""}]
    understand = {
        "intent": intent,
        "confidence": 0.95,
        "missing_information": [],
        "notes": "",
        "requested_actions": entry,
        "file_to_use": file_to_use,
    }
    think = {
        "objective": "Clean up the spreadsheet.",
        "summary": "Clear enough to proceed.",
        "clarification_required": False,
        "confidence": 0.9,
    }
    return [json.dumps(understand), json.dumps(think)]


class Session:
    """A few turns on one upload, each offered the latest copy handed back, as the API does."""

    def __init__(self, tmp_path: Path, speaker: str = "template") -> None:
        self.tmp = tmp_path
        self.speaker = speaker
        self.upload = write_upload(tmp_path / "uploads")
        self.copy: Path | None = None
        self.turns = 0

    def turn(
        self,
        message: str,
        replies: list[str],
        *,
        pinned: bool = False,
        upload: Path | None = None,
        offer_copy: bool = True,
        copy_unavailable: bool = False,
    ) -> BuddyState:
        self.turns += 1
        target = upload or self.upload
        state = BuddyState.new(session_id="session-1", workspace_id="workspace-1")
        state.user_message = message
        state.context.add_uploaded_file(str(target))
        state.context.switch_active_file(str(target))
        copy = self.copy if offer_copy and upload is None else None
        state.file_choice = FileChoice(
            original=str(target),
            copy=str(copy) if copy else None,
            copy_name=copy.name if copy else None,
            copy_unavailable=copy_unavailable,
            pinned=pinned,
        )
        out = self.tmp / "outputs" / f"t{self.turns:04d}"
        out.mkdir(parents=True)
        state.turn_output_dir = str(out)
        speaker = None
        if self.speaker != "template":
            state.context.set_mode(self.speaker)
            speaker = PersonalityVoice()
        build_engine(llm_provider=_QueuedLLMProvider(replies), speaker=speaker).run(state)
        return state

    def delivered(self, state: BuddyState) -> Path | None:
        if state.execution_result is None or not state.execution_result.outputs:
            return None
        return Path(state.execution_result.outputs[0]) if is_verified_delivery(state) else None


def numbers_in(reply: str) -> set[int]:
    """The numbers in a reply, not counting a file name (``...-task-1.xlsx``) or a percentage
    (the analysis's own "1 missing value (33.3%)", which is not a row count)."""
    text = re.sub(r"[\w\-. ]*[\w\-]\.xlsx", "", reply)
    return {int(n) for n in re.findall(r"\d+", re.sub(r"\d+(?:\.\d+)?%", "", text))}


def allowed_numbers(used: Facts, result: Facts | None) -> set[int]:
    """Every number a reply may carry: counts read from the file used and the file delivered."""
    allowed = {used.rows, used.duplicates, used.empties}
    if result is not None:
        allowed |= {result.rows, result.duplicates, result.empties}
        allowed |= {used.rows - result.rows, used.duplicates - result.duplicates}
        allowed |= {used.empties - result.empties}
    return allowed


# -- Two and three requests adding up -----------------------------------------------------------


@pytest.mark.parametrize("speaker", SPEAKERS)
def test_a_follow_up_builds_on_the_latest_copy_so_two_requests_add_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, speaker: str
) -> None:
    monkeypatch.chdir(tmp_path)
    session = Session(tmp_path, speaker)
    original_hash = _sha(session.upload)

    first = session.turn("remove the duplicate rows", brain("remove_duplicate_rows"))
    c1 = session.delivered(first)
    assert c1 is not None
    assert facts(session.upload) == Facts(rows=6, duplicates=2, empties=1)
    assert facts(c1) == Facts(rows=4, duplicates=0, empties=1)
    assert first.file_used is not None and first.file_used.kind == FILE_KIND_ORIGINAL
    assert WORKED_ON_ORIGINAL_SENTENCE in (first.reply or "")
    session.copy = c1
    c1_hash = _sha(c1)

    second = session.turn(
        "now remove the empty rows too", brain("remove_empty_rows", phrase="remove the empty rows")
    )
    c2 = session.delivered(second)

    assert c2 is not None
    assert second.file_used is not None and second.file_used.kind == FILE_KIND_COPY
    assert second.file_used.path == str(c1)
    # Both are done in the one file: no duplicate and no empty row.
    assert facts(c2) == Facts(rows=3, duplicates=0, empties=0)
    reply = second.reply or ""
    assert f"`{c1.name}`, the latest copy I handed back" in reply
    assert WORKED_ON_ORIGINAL_SENTENCE not in reply
    # Every number matches a read of the file the turn used (the copy), not of the upload.
    assert numbers_in(reply) <= allowed_numbers(facts(c1), facts(c2))
    assert 6 not in numbers_in(reply)
    assert _sha(session.upload) == original_hash and _sha(c1) == c1_hash


@pytest.mark.parametrize("speaker", SPEAKERS)
def test_three_requests_in_a_row_each_work_on_the_latest_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, speaker: str
) -> None:
    """Empty rows first, then duplicates, then a request with nothing left to do."""
    monkeypatch.chdir(tmp_path)
    session = Session(tmp_path, speaker)

    first = session.turn("remove the empty rows", brain("remove_empty_rows"))
    c1 = session.delivered(first)
    assert c1 is not None and facts(c1) == Facts(rows=5, duplicates=2, empties=0)
    session.copy = c1

    second = session.turn("and remove the duplicates", brain("remove_duplicate_rows"))
    c2 = session.delivered(second)
    assert c2 is not None and facts(c2) == Facts(rows=3, duplicates=0, empties=0)
    assert second.file_used is not None and second.file_used.path == str(c1)
    assert numbers_in(second.reply or "") <= allowed_numbers(facts(c1), facts(c2))
    session.copy = c2

    third = session.turn("clean it again", brain("remove_duplicate_rows"))

    assert third.file_used is not None and third.file_used.path == str(c2)
    assert session.delivered(third) is None
    assert not is_verified_delivery(third)


# -- Start over, a new upload, a picked file ----------------------------------------------------


@pytest.mark.parametrize("speaker", SPEAKERS)
def test_start_over_uses_the_original_upload_when_brain_reads_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, speaker: str
) -> None:
    monkeypatch.chdir(tmp_path)
    session = Session(tmp_path, speaker)
    first = session.turn("remove the duplicate rows", brain("remove_duplicate_rows"))
    c1 = session.delivered(first)
    assert c1 is not None
    session.copy = c1

    again = session.turn(
        "start over and remove the empty rows",
        brain("remove_empty_rows", file_to_use="original", phrase="remove the empty rows"),
    )
    out = session.delivered(again)

    assert out is not None
    assert again.file_used is not None
    assert again.file_used.kind == FILE_KIND_ORIGINAL
    assert again.file_used.path == str(session.upload)
    assert again.file_used.copy_skipped and again.file_used.start_over
    # Only the empty row is gone: the upload's duplicates are still there.
    assert facts(out) == Facts(rows=5, duplicates=2, empties=0)
    reply = again.reply or ""
    assert WORKED_ON_ORIGINAL_SKIPPING_COPY_SENTENCE in reply
    assert numbers_in(reply) <= allowed_numbers(facts(session.upload), facts(out))


def test_brain_is_the_only_thing_that_starts_over(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The words "start over" in the message change nothing unless Brain's field says so."""
    monkeypatch.chdir(tmp_path)
    session = Session(tmp_path)
    first = session.turn("remove the duplicate rows", brain("remove_duplicate_rows"))
    session.copy = session.delivered(first)

    second = session.turn(
        "start over from my original file and remove the empty rows",
        brain("remove_empty_rows", file_to_use="latest"),
    )

    assert second.file_used is not None and second.file_used.kind == FILE_KIND_COPY


@pytest.mark.parametrize("value", ["both", "ORIGINAL ", 5, None, ["original"], "", "the original"])
def test_a_file_to_use_outside_the_vocabulary_builds_on_the_latest_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: object
) -> None:
    """Only the exact words of the closed vocabulary (any casing) start over."""
    monkeypatch.chdir(tmp_path)
    session = Session(tmp_path)
    first = session.turn("remove the duplicate rows", brain("remove_duplicate_rows"))
    session.copy = session.delivered(first)

    second = session.turn("remove the empty rows", brain("remove_empty_rows", file_to_use=value))

    assert second.understanding is not None
    expected = "original" if value == "ORIGINAL " else "latest"
    assert second.understanding.file_to_use == expected
    assert second.file_used is not None
    kind = FILE_KIND_ORIGINAL if expected == "original" else FILE_KIND_COPY
    assert second.file_used.kind == kind


def test_a_new_upload_is_used_as_itself(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    session = Session(tmp_path)
    first = session.turn("remove the duplicate rows", brain("remove_duplicate_rows"))
    session.copy = session.delivered(first)
    other = write_upload(
        tmp_path / "uploads2", "other.xlsx", [["x", 1], ["x", 1], [None, None], ["y", 2]]
    )

    third = session.turn("remove the duplicate rows", brain("remove_duplicate_rows"), upload=other)
    out = session.delivered(third)

    assert out is not None
    assert third.file_used is not None
    assert third.file_used.path == str(other) and third.file_used.kind == FILE_KIND_ORIGINAL
    assert not third.file_used.copy_skipped
    assert WORKED_ON_ORIGINAL_SENTENCE in (third.reply or "")
    assert facts(out) == Facts(rows=3, duplicates=0, empties=1)
    assert numbers_in(third.reply or "") <= allowed_numbers(facts(other), facts(out))


def test_a_file_the_person_picked_is_used_as_itself(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    session = Session(tmp_path)
    first = session.turn("remove the duplicate rows", brain("remove_duplicate_rows"))
    session.copy = session.delivered(first)

    picked = session.turn("remove the empty rows", brain("remove_empty_rows"), pinned=True)

    assert picked.file_used is not None
    assert picked.file_used.path == str(session.upload)
    assert picked.file_used.copy_skipped
    assert WORKED_ON_ORIGINAL_SKIPPING_COPY_SENTENCE in (picked.reply or "")


def test_a_copy_that_cant_be_offered_is_said_not_silently_dropped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    session = Session(tmp_path)

    turn = session.turn(
        "remove the empty rows",
        brain("remove_empty_rows"),
        offer_copy=False,
        copy_unavailable=True,
    )

    assert turn.file_used is not None and turn.file_used.copy_skipped
    assert WORKED_ON_ORIGINAL_SKIPPING_COPY_SENTENCE in (turn.reply or "")


# -- Nothing left to do -------------------------------------------------------------------------


@pytest.mark.parametrize("speaker", SPEAKERS)
def test_a_copy_that_already_satisfies_the_request_says_so_and_makes_no_new_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, speaker: str
) -> None:
    monkeypatch.chdir(tmp_path)
    session = Session(tmp_path, speaker)
    first = session.turn("remove the duplicate rows", brain("remove_duplicate_rows"))
    c1 = session.delivered(first)
    assert c1 is not None
    session.copy = c1
    before = _sha(c1)

    second = session.turn("remove the duplicate rows", brain("remove_duplicate_rows"))

    reply = second.reply or ""
    assert not is_verified_delivery(second)
    assert session.delivered(second) is None
    assert facts(c1).duplicates == 0  # the independent read agrees: nothing was left to remove
    assert (
        f"The latest copy, `{c1.name}`, already has no duplicate rows, so there "
        "was nothing to remove and I handed back no new file."
    ) in reply
    assert "No changes were made to any file this turn." in reply
    assert not numbers_in(reply)
    assert "Saved as" not in reply and "is ready" not in reply
    assert _sha(c1) == before and _sha(session.upload) == _sha(session.upload)


def test_nothing_left_for_two_requests_names_both(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    session = Session(tmp_path)
    first = session.turn("remove the empty rows", brain("remove_empty_rows"))
    session.copy = session.delivered(first)
    second = session.turn("remove the duplicate rows", brain("remove_duplicate_rows"))
    session.copy = session.delivered(second)
    assert session.copy is not None and facts(session.copy) == Facts(3, 0, 0)

    replies = _chain_replies(
        ("spreadsheet.deduplicate", "remove duplicate rows"),
        ("spreadsheet.remove_empty_rows", "and empty rows"),
    )
    third = session.turn("remove duplicate rows and empty rows", replies)

    assert not is_verified_delivery(third)
    assert "already has no duplicate rows or fully-empty rows" in (third.reply or "")
    assert session.delivered(third) is None


def test_a_follow_up_that_changes_something_is_still_delivered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A chain on a copy where only the first step has nothing left still hands back the file."""
    monkeypatch.chdir(tmp_path)
    session = Session(tmp_path)
    first = session.turn("remove the duplicate rows", brain("remove_duplicate_rows"))
    session.copy = session.delivered(first)

    replies = _chain_replies(
        ("spreadsheet.deduplicate", "remove duplicate rows"),
        ("spreadsheet.remove_empty_rows", "and empty rows"),
    )
    second = session.turn("remove duplicate rows and empty rows", replies)

    out = session.delivered(second)
    assert out is not None
    assert facts(out) == Facts(rows=3, duplicates=0, empties=0)
    reply = second.reply or ""
    assert "There were no duplicate rows to remove." in reply
    assert "handed back no new file" not in reply
    assert numbers_in(reply) <= allowed_numbers(facts(session.copy), facts(out))


def test_the_original_with_nothing_to_remove_is_still_delivered_as_before(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The rule is about a copy handed back earlier; a clean upload is delivered as before."""
    monkeypatch.chdir(tmp_path)
    session = Session(tmp_path)
    session.upload = write_upload(tmp_path / "clean", "clean.xlsx", [["a", 1], ["b", 2]])

    turn = session.turn("remove the duplicate rows", brain("remove_duplicate_rows"))

    assert session.delivered(turn) is not None
    assert "there were no duplicate rows to remove." in (turn.reply or "")


# -- A report on a copy -------------------------------------------------------------------------


def test_a_report_on_a_copy_is_labelled_as_the_file_worked_on_not_the_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    session = Session(tmp_path)
    first = session.turn("remove the duplicate rows", brain("remove_duplicate_rows"))
    session.copy = session.delivered(first)

    report = session.turn(
        "give me a report", brain("report_on_file", phrase="give me a report")
    )

    reply = report.reply or ""
    assert report.file_used is not None and report.file_used.kind == FILE_KIND_COPY
    assert session.delivered(report) is None
    assert "Report on the file I worked on: " in reply
    assert "Report on your original file" not in reply
    # The analysis was of the copy (4 rows, 0 repeats), not of the upload.
    assert "repeat an earlier row" not in reply


# -- Smaller guards -----------------------------------------------------------------------------


def test_a_caller_that_offers_no_choice_gets_the_upload_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    upload = write_upload(tmp_path / "uploads")
    state = BuddyState.new(session_id="s", workspace_id="w")
    state.user_message = "remove the duplicate rows"
    state.context.add_uploaded_file(str(upload))
    state.context.switch_active_file(str(upload))

    build_engine(llm_provider=_QueuedLLMProvider(brain("remove_duplicate_rows"))).run(state)

    assert state.file_used is not None and state.file_used.path == str(upload)
    assert WORKED_ON_ORIGINAL_SENTENCE in (state.reply or "")


def test_a_turn_with_no_file_says_nothing_about_a_file_worked_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    state = BuddyState.new(session_id="s", workspace_id="w")
    state.user_message = "remove the duplicate rows"

    build_engine(llm_provider=_QueuedLLMProvider(brain("remove_duplicate_rows"))).run(state)

    assert state.file_used is None
    assert (state.reply or "").startswith(NO_FILE_SENTENCE)
    assert "worked on" not in (state.reply or "")


def test_the_model_is_told_a_copy_exists_but_never_its_name_or_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    session = Session(tmp_path)
    first = session.turn("remove the duplicate rows", brain("remove_duplicate_rows"))
    session.copy = session.delivered(first)
    provider = _QueuedLLMProvider(brain("remove_empty_rows"))
    state = BuddyState.new(session_id="s", workspace_id="w")
    state.user_message = "remove the empty rows"
    state.context.add_uploaded_file(str(session.upload))
    state.context.switch_active_file(str(session.upload))
    state.file_choice = FileChoice(
        original=str(session.upload), copy=str(session.copy), copy_name="secret_name.xlsx"
    )
    out = tmp_path / "outputs" / "x"
    out.mkdir(parents=True)
    state.turn_output_dir = str(out)

    build_engine(llm_provider=provider).run(state)

    prompts = "\n".join(m.content for _, messages in provider.calls for m in messages)
    assert "A copy of the file that Buddy handed back earlier is available." in prompts
    assert "secret_name" not in prompts and str(session.copy) not in prompts


def test_the_new_sentences_claim_nothing_and_carry_no_number() -> None:
    from buddy import voice_catalog as catalog

    for text in (
        catalog.WORKED_ON_ORIGINAL_SENTENCE,
        catalog.WORKED_ON_ORIGINAL_SKIPPING_COPY_SENTENCE,
        catalog.WORKED_ON_COPY_TEMPLATE.format(file="`x.xlsx`"),
        catalog.NOTHING_LEFT_TEMPLATE.format(file="`x.xlsx`", rows="duplicate rows"),
    ):
        assert _COMPLETION_CLAIM.search(text) is None, text
        assert not any(c.isdigit() for c in text)

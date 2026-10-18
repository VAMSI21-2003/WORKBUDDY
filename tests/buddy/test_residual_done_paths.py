"""The residual false "Done —" paths of the verified-outcome review (z31, z18, MEDIUM-u).

Each runs the real ``build_engine`` (real skills, planner, Inspector and Voice) with only the LLM
faked, in all three speakers, on a fixture with 2 exact duplicate rows and 1 empty row.

- z31: Brain files words the skills can't act on ("delete the file") under duplicate removal.
- z18: Brain flags "don't keep the empty rows" (a request to remove them) as unsupported.
  Both used to end "Done —". Neither can now: the reply says that part wasn't done.
- MEDIUM-u, near-miss action names ("dedupe", "remove_duplicates"): outside Brain's closed
  vocabulary, so nothing is run and nothing is claimed. This cannot reach a "Partly done".
- MEDIUM-u, a whole-message ``unsupported`` flag on a request that was fully carried out: still
  possible after a Brain double-listing; the reply is the flat "Partly done", never "Done —"
  about the flagged part. Absorbing it would absorb z31 too.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.buddy.test_reply_quality import SPEAKERS, Routed, brain, run_turn, think

RD, RE, UNSUPPORTED = "remove_duplicate_rows", "remove_empty_rows", "unsupported"
DONE_LEAD = "Done —"


def _run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    speaker: str,
    message: str,
    intent: str,
    *actions: tuple[str, str],
) -> tuple[str, bool]:
    provider = Routed(brain(intent, *actions), think())
    turn = run_turn(tmp_path, speaker, message, provider, monkeypatch=monkeypatch)
    assert turn.original_unchanged
    return turn.reply, turn.delivered


def _no_bare_done(reply: str) -> None:
    assert not reply.startswith(DONE_LEAD), reply
    assert " Done — " not in reply and not reply.startswith("Okay, here's the rundown. Done —")


Z31 = [
    "remove duplicates and delete the file",
    "remove duplicates and rename the file",
    "remove duplicates and archive it",
    "Remove Duplicates And DELETE The File",
    "remove duplicates and delete the sheet",
]


@pytest.mark.parametrize("speaker", SPEAKERS)
@pytest.mark.parametrize("message", Z31)
def test_z31_words_that_dont_name_duplicates_filed_under_them_are_not_called_done(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, speaker: str, message: str
) -> None:
    unsupported = message.split(" and ", 1)[1] if " and " in message else message
    unsupported = message[message.lower().index(unsupported.lower()) :]
    reply, delivered = _run(
        tmp_path, monkeypatch, speaker, message, RD, (RD, unsupported), (UNSUPPORTED, unsupported)
    )

    assert delivered  # the dedupe itself ran and was verified: the reply says so, only so far
    _no_bare_done(reply)
    assert "Partly done" in reply, reply
    # Said not done, in the generic sentence: a quote of the flagged words could hold the words
    # of the duplicate removal that did run.
    assert "I read part of your request as something I can't do yet." in reply, reply


@pytest.mark.parametrize("speaker", SPEAKERS)
def test_z31_a_phrase_that_names_the_action_is_still_absorbed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, speaker: str
) -> None:
    """Control: Brain double-listing words that do name duplicate removal still reads as done."""
    message = "Please, just remove the DUPLICATES."
    reply, delivered = _run(
        tmp_path,
        monkeypatch,
        speaker,
        message,
        RD,
        (RD, "remove the DUPLICATES"),
        (UNSUPPORTED, message),
    )

    assert delivered and "Partly done" not in reply and "can't do" not in reply, reply


Z18 = [
    "remove duplicates and don't keep rows with nothing in them",
    "remove duplicates and don't leave the blank lines",
]


@pytest.mark.parametrize("speaker", SPEAKERS)
@pytest.mark.parametrize("message", Z18)
def test_z18_a_paraphrased_double_negation_flagged_unsupported_is_not_called_done(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, speaker: str, message: str
) -> None:
    reply, delivered = _run(
        tmp_path,
        monkeypatch,
        speaker,
        message,
        RD,
        (RD, "remove duplicates"),
        (UNSUPPORTED, "don't keep the empty rows"),
    )

    assert delivered
    _no_bare_done(reply)
    # Said not done, by Brain's flag or by the mention check, whichever the message reaches.
    assert "Partly done" in reply or "didn't run the empty-row removal" in reply, reply


NEAR_MISS = [
    ("de-dupe the sheet", "dedupe"),
    ("remove the duplicates from my excel file", "remove_duplicates"),
    ("removing duplicates please", "remove_duplicates"),
    ("could you please remove the duplicate rows for me", "remove_duplicates"),
    ("dedupe this sheet", "dedupe"),
]


@pytest.mark.parametrize("speaker", SPEAKERS)
@pytest.mark.parametrize(("message", "name"), NEAR_MISS)
def test_medium_u_a_near_miss_action_name_runs_nothing_and_claims_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, speaker: str, message: str, name: str
) -> None:
    reply, delivered = _run(tmp_path, monkeypatch, speaker, message, name, (name, message))

    assert not delivered
    _no_bare_done(reply)
    assert "Partly done" not in reply, reply  # nothing was done, so nothing is "partly" done
    assert "removed" not in reply.lower(), reply


WHOLE_MESSAGE = [
    "remove and delete the duplicates",
    "Don’t leave any duplicates",
    "delete the duplicates of my file",
    "find the duplicates and remove them",
]


@pytest.mark.parametrize("speaker", SPEAKERS)
@pytest.mark.parametrize("message", WHOLE_MESSAGE)
def test_medium_u_a_whole_message_flag_is_reported_flat_never_as_done(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, speaker: str, message: str
) -> None:
    reply, delivered = _run(
        tmp_path, monkeypatch, speaker, message, RD, (RD, message), (UNSUPPORTED, message)
    )

    assert delivered
    _no_bare_done(reply)
    assert "Partly done" in reply and "can't do" in reply, reply

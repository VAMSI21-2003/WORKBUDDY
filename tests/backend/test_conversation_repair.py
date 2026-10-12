"""Conversation repair: the owner's live transcript (Friend mode, two uploads), end to end.

Every test runs the real app and pipeline with only the LLM faked. The numbered scenarios are the
turns of that transcript:

1. "remove duplicate rows" -> "I couldn't work out what you asked for just now" (Think failed)
2. "remove duplicate rows" -> "Before I start: which file do you mean?"
3. "messy-data.xlsx" -> a greeting (the pending request was lost)
5. "now remove the empty rows too in mess-data.xlsx" -> "which file?" (a typo)
6. "messy-data.xslx" -> a greeting again
8. "start over from my original file and remove the empty rows" -> correct file, but no
   "I also noticed" line although the copy still has an exact duplicate and an Email linked to
   two Names
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.main import create_app
from backend.settings import Settings
from buddy.voice_catalog import (
    CLARIFICATION_QUESTIONS,
    RESUMED_SENTENCE,
    UNDERSTANDING_FAILED_SENTENCE,
    WORKED_ON_COPY_TEMPLATE,
)
from tests.backend.factories import PipelineFactory, QueuedProvider, raise_in
from tests.backend.helpers import xlsx_bytes
from tests.backend.test_follow_up_turns import Chat, facts
from tests.file_sentences import worked_on_original

FRIEND_QUESTIONS = CLARIFICATION_QUESTIONS["which_file"]["friend"]

# messy-data.xlsx: 6 rows; row 4 repeats row 1 exactly, row 3 is empty, and one Email (c@x.com)
# is linked to two Names.
MESSY = {
    "Name": ["Alice", "Bob", None, "Alice", "Carol", "Dan"],
    "Email": ["a@x.com", "b@x.com", None, "a@x.com", "c@x.com", "c@x.com"],
    "Amount": [10, 20, None, 10, 30, 40],
}
# other.xlsx: a second upload, the active one when it is uploaded last.
OTHER = {"Name": ["x", "x", "y"], "Email": ["x@x.com", "x@x.com", "y@x.com"], "Amount": [1, 1, 2]}


def brain(
    intent: str = "remove_duplicate_rows",
    *,
    phrase: str | None = None,
    answers_pending: bool | None = None,
    file_to_use: str | None = None,
) -> str:
    """Brain's reply. ``phrase`` lists the action with that source phrase."""
    reply: dict[str, Any] = {"intent": intent, "confidence": 0.9}
    if phrase is not None:
        reply["requested_actions"] = [{"action": intent, "source_phrase": phrase}]
    if answers_pending is not None:
        reply["answers_pending"] = answers_pending
    if file_to_use is not None:
        reply["file_to_use"] = file_to_use
    return json.dumps(reply)


def think(reason: str | None = None) -> str:
    """Think's reply: clear enough to go on, or asking for ``reason``."""
    reply: dict[str, Any] = {
        "objective": "Clean up the spreadsheet.",
        "summary": "Clear enough to proceed.",
        "clarification_required": reason is not None,
        "confidence": 0.9,
    }
    if reason is not None:
        reply["clarification_reason"] = reason
    return json.dumps(reply)


class ByStage(QueuedProvider):
    """Replies by who is asking, each list in order, the last one repeating.

    For a test where a stage may or may not be reached, so the number of calls isn't fixed."""

    def __init__(self, brain_replies: list[str], think_replies: list[str]) -> None:
        super().__init__([])
        self._by_stage = {"intent classifier": brain_replies, "reasoning stage": think_replies}

    def complete(self, *, system: str, messages: Any, max_tokens: int = 1024) -> str:
        with self._lock:
            self.calls.append((system, messages))
            for marker, replies in self._by_stage.items():
                if marker in system:
                    return replies.pop(0) if len(replies) > 1 else replies[0]
        raise AssertionError("a model call that is not Brain or Think")


@pytest.fixture
def make_chat(settings: Settings):  # type: ignore[no-untyped-def]
    def make(
        replies: list[str] | QueuedProvider, speaker: str = "friend", **factory_args: Any
    ) -> Chat:
        provider = replies if isinstance(replies, QueuedProvider) else QueuedProvider(replies)
        factory = PipelineFactory(provider, speaker=speaker, **factory_args)
        client = TestClient(create_app(settings, factory))
        chat = Chat(client, factory, speaker)
        chat.settings = settings  # type: ignore[attr-defined]
        return chat

    return make


def two_uploads(chat: Chat) -> tuple[dict[str, Any], dict[str, Any]]:
    """messy-data.xlsx first, other.xlsx second (so other.xlsx is the active upload)."""
    messy = chat.upload(xlsx_bytes(MESSY), "messy-data.xlsx")
    other = chat.upload(xlsx_bytes(OTHER), "other.xlsx")
    return messy, other


def upload_hashes(chat: Chat) -> dict[str, str]:
    root = chat.settings.uploads_dir  # type: ignore[attr-defined]
    return {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob("*.xlsx")
    }


def session_json(chat: Chat) -> dict[str, Any]:
    (path,) = chat.settings.sessions_dir.glob("*/session.json")  # type: ignore[attr-defined]
    return dict(json.loads(path.read_text(encoding="utf-8")))


# -- 2: "which file?" is not asked when the answer is settled --------------------------------


def test_think_asking_which_file_is_dropped_when_the_active_upload_settles_it(make_chat) -> None:  # type: ignore[no-untyped-def]
    chat = make_chat([brain(), think("which_file")])
    two_uploads(chat)
    before = upload_hashes(chat)

    turn = chat.turn("remove duplicate rows")

    assert turn["outcome"] == "delivered", turn["reply"]
    assert turn["reply"] not in FRIEND_QUESTIONS
    assert worked_on_original("other.xlsx") in turn["reply"]  # says which file was used
    (copy,) = turn["delivered_files"]
    assert facts(chat.download(copy)) == (2, 0, 0)  # other.xlsx: 3 rows, 1 repeat
    assert upload_hashes(chat) == before
    assert session_json(chat)["pending_request"] is None


def test_the_latest_copy_settles_it_and_the_reply_names_the_copy(make_chat) -> None:  # type: ignore[no-untyped-def]
    chat = make_chat([brain(), think(), brain("remove_empty_rows"), think("which_file")])
    chat.upload(xlsx_bytes(MESSY), "messy-data.xlsx")
    first = chat.turn("remove duplicate rows")
    (copy_1,) = first["delivered_files"]

    second = chat.turn("now remove the empty rows too")

    assert second["outcome"] == "delivered", second["reply"]
    assert WORKED_ON_COPY_TEMPLATE.format(file=f"`{copy_1['name']}`") in second["reply"]
    (copy_2,) = second["delivered_files"]
    assert facts(chat.download(copy_2)) == (4, 0, 0)


def test_a_think_that_asked_which_file_with_one_upload_just_runs(make_chat) -> None:  # type: ignore[no-untyped-def]
    chat = make_chat([brain(), think("which_file")])
    chat.upload(xlsx_bytes(MESSY), "messy-data.xlsx")

    turn = chat.turn("remove duplicate rows")

    assert turn["outcome"] == "delivered"
    assert worked_on_original("messy-data.xlsx") in turn["reply"]


# -- 5 and 6: a file named with a slip is still found ------------------------------------------


@pytest.mark.parametrize(
    "message",
    [
        "now remove the empty rows too in mess-data.xlsx",
        "remove the empty rows in messy-data.xslx",
        "remove the empty rows in MESSY-DATA.XLSX",
        "remove the empty rows in 'messy-data.xlsx'.",
    ],
)
def test_a_file_named_with_a_slip_is_matched_to_the_session_file(make_chat, message: str) -> None:  # type: ignore[no-untyped-def]
    chat = make_chat([brain(), think(), brain("remove_empty_rows"), think()])
    chat.upload(xlsx_bytes(MESSY), "messy-data.xlsx")
    chat.upload(xlsx_bytes(OTHER), "other.xlsx")  # the active one
    first = chat.turn("remove duplicate rows in messy-data.xlsx")
    (copy_1,) = first["delivered_files"]

    turn = chat.turn(message)

    assert turn["outcome"] == "delivered", turn["reply"]
    assert turn["reply"] not in FRIEND_QUESTIONS
    # The named file's latest copy, so both removals are in the one file.
    assert WORKED_ON_COPY_TEMPLATE.format(file=f"`{copy_1['name']}`") in turn["reply"]
    (copy_2,) = turn["delivered_files"]
    assert facts(chat.download(copy_2)) == (4, 0, 0)
    assert chat.state()["next_request_file"]["file_id"] == copy_2["file_id"]


def test_a_delivered_files_name_is_matched_too(make_chat) -> None:  # type: ignore[no-untyped-def]
    chat = make_chat([brain(), think(), brain("remove_empty_rows"), think()])
    chat.upload(xlsx_bytes(MESSY), "messy-data.xlsx")
    chat.upload(xlsx_bytes(OTHER), "other.xlsx")
    first = chat.turn("remove duplicate rows in messy-data.xlsx")
    (copy_1,) = first["delivered_files"]

    turn = chat.turn(f"remove the empty rows from {copy_1['name']}")

    assert turn["outcome"] == "delivered", turn["reply"]
    assert WORKED_ON_COPY_TEMPLATE.format(file=f"`{copy_1['name']}`") in turn["reply"]


# -- 1-3: a name that is not a session file asks, and the answer resumes the request -----------


def test_an_unknown_file_name_asks_and_the_next_message_resumes_the_request(make_chat) -> None:  # type: ignore[no-untyped-def]
    chat = make_chat(
        [
            brain(phrase="remove duplicate rows"),  # turn 1 (asks)
            think(),
            brain(phrase="remove duplicate rows", answers_pending=True),  # turn 2 (resumes)
            think(),
        ]
    )
    two_uploads(chat)
    before = upload_hashes(chat)

    asked = chat.turn("remove duplicate rows in sales.xlsx")

    assert asked["outcome"] == "not_delivered" and asked["delivered_files"] == []
    assert asked["reply"] in FRIEND_QUESTIONS
    assert session_json(chat)["pending_request"] == {
        "message": "remove duplicate rows in sales.xlsx",
        "turn_id": asked["turn_id"],
    }

    resumed = chat.turn("messy-data.xlsx")

    assert resumed["outcome"] == "delivered", resumed["reply"]
    assert worked_on_original("messy-data.xlsx") in resumed["reply"]
    (copy,) = resumed["delivered_files"]
    assert facts(chat.download(copy)) == (5, 0, 1)  # the duplicates of messy-data.xlsx are gone
    assert session_json(chat)["pending_request"] is None
    assert upload_hashes(chat) == before


def test_brain_decides_a_message_naming_a_file_can_be_a_request_of_its_own(make_chat) -> None:  # type: ignore[no-untyped-def]
    """The pending request is offered to Brain, not applied: Brain reads this as its own request."""
    chat = make_chat(
        [
            brain(phrase="remove duplicate rows"),
            think(),
            brain("remove_empty_rows", phrase="remove the empty rows", answers_pending=False),
            think(),
        ]
    )
    two_uploads(chat)
    chat.turn("remove duplicate rows in sales.xlsx")

    turn = chat.turn("remove the empty rows in messy-data.xlsx")

    assert turn["outcome"] == "delivered", turn["reply"]
    assert "empty row" in turn["reply"].lower() and "duplicate row" not in turn["reply"]
    (copy,) = turn["delivered_files"]
    assert facts(chat.download(copy)) == (5, 1, 0)  # messy-data.xlsx: only the empty row is gone


def test_a_pending_request_goes_to_brain_only_when_the_message_names_a_file(make_chat) -> None:  # type: ignore[no-untyped-def]
    provider_replies = [
        brain(phrase="remove duplicate rows"),
        think(),
        brain("conversation"),
        think(),
    ]
    chat = make_chat(provider_replies)
    two_uploads(chat)
    chat.turn("remove duplicate rows in sales.xlsx")

    chat.turn("hello")

    prompts = [m.content for _, messages in chat.factory.provider.calls for m in messages]
    assert not any("waiting for the user to say which file" in prompt for prompt in prompts)
    assert session_json(chat)["pending_request"] is None  # one turn later, it is forgotten


def test_the_model_is_told_about_the_waiting_request_when_a_file_is_named(make_chat) -> None:  # type: ignore[no-untyped-def]
    chat = make_chat(
        [
            brain(phrase="remove duplicate rows"),
            think(),
            brain(phrase="remove duplicate rows", answers_pending=True),
            think(),
        ]
    )
    two_uploads(chat)
    chat.turn("remove duplicate rows in sales.xlsx")

    chat.turn("messy-data.xlsx")

    brain_prompt = chat.factory.provider.calls[2][1][0].content
    assert 'waiting for the user to say which file it is about: "remove duplicate rows in' in (
        brain_prompt
    )


def test_a_pending_request_survives_a_restart(settings: Settings) -> None:
    replies = [brain(phrase="remove duplicate rows"), think()]
    factory = PipelineFactory(QueuedProvider(replies), speaker="friend")
    chat = Chat(TestClient(create_app(settings, factory)), factory)
    two_uploads(chat)
    chat.turn("remove duplicate rows in sales.xlsx")
    resumed_replies = [
        brain(phrase="remove duplicate rows", answers_pending=True),
        think(),
    ]

    # A new app on the same data: the server was restarted.
    factory_2 = PipelineFactory(QueuedProvider(resumed_replies), speaker="friend")
    client_2 = TestClient(create_app(settings, factory_2))
    chat_2 = Chat(client_2, factory_2)
    chat_2.session_id = chat.session_id
    turn = chat_2.turn("messy-data.xlsx")

    assert turn["outcome"] == "delivered", turn["reply"]
    assert worked_on_original("messy-data.xlsx") in turn["reply"]


def test_two_files_a_slip_cant_tell_apart_ask_instead_of_guessing(make_chat) -> None:  # type: ignore[no-untyped-def]
    chat = make_chat([brain(), think()])
    chat.upload(xlsx_bytes(MESSY), "report_a.xlsx")
    chat.upload(xlsx_bytes(OTHER), "report_b.xlsx")
    before = upload_hashes(chat)

    turn = chat.turn("remove duplicate rows in report_x.xlsx")

    assert turn["outcome"] == "not_delivered" and turn["reply"] in FRIEND_QUESTIONS
    assert upload_hashes(chat) == before


def test_think_not_asking_is_overridden_when_the_named_file_is_unclear(make_chat) -> None:  # type: ignore[no-untyped-def]
    """Which file is code's to settle: a file that can't be found is asked about even when the
    model, which is never shown the session's files by name in this check, didn't ask."""
    chat = make_chat([brain(), think()])
    two_uploads(chat)

    turn = chat.turn("remove duplicate rows in sales.xlsx")

    assert turn["outcome"] == "not_delivered" and turn["reply"] in FRIEND_QUESTIONS


def test_a_message_that_names_no_spreadsheet_file_is_never_unclear(make_chat) -> None:  # type: ignore[no-untyped-def]
    chat = make_chat([brain(), think()])
    two_uploads(chat)

    turn = chat.turn("remove duplicate rows, e.g. the ones at the end, etc.")

    assert turn["outcome"] == "delivered", turn["reply"]


def test_the_model_is_not_shown_the_file_name_only_that_it_is_settled(make_chat) -> None:  # type: ignore[no-untyped-def]
    chat = make_chat([brain(), think()])
    two_uploads(chat)

    chat.turn("remove duplicate rows")

    think_prompt = chat.factory.provider.calls[1][1][0].content
    assert "Which file this turn works on is already settled." in think_prompt


# -- 1: a failed call is tried again --------------------------------------------------------------


@pytest.mark.parametrize("bad", ["I could not do JSON today", "[1, 2]", ""])
def test_a_think_that_fails_once_then_works_delivers(make_chat, bad: str) -> None:  # type: ignore[no-untyped-def]
    chat = make_chat([brain(), bad, think()])
    chat.upload(xlsx_bytes(MESSY), "messy-data.xlsx")

    turn = chat.turn("remove duplicate rows")

    assert turn["outcome"] == "delivered", turn["reply"]
    assert UNDERSTANDING_FAILED_SENTENCE not in turn["reply"]


def test_a_brain_that_fails_once_then_works_delivers(make_chat) -> None:  # type: ignore[no-untyped-def]
    chat = make_chat(["not json", brain(), think()])
    chat.upload(xlsx_bytes(MESSY), "messy-data.xlsx")

    turn = chat.turn("remove duplicate rows")

    assert turn["outcome"] == "delivered", turn["reply"]


def test_two_think_failures_in_a_row_give_the_existing_honest_reply(make_chat) -> None:  # type: ignore[no-untyped-def]
    chat = make_chat([brain(), "nope", "still nope"])
    chat.upload(xlsx_bytes(MESSY), "messy-data.xlsx")
    before = upload_hashes(chat)

    turn = chat.turn("remove duplicate rows")

    assert turn["outcome"] == "not_delivered" and turn["delivered_files"] == []
    assert UNDERSTANDING_FAILED_SENTENCE in turn["reply"]
    assert upload_hashes(chat) == before
    assert session_json(chat)["pending_request"] is None  # a failure is not a question


def test_two_brain_failures_in_a_row_give_the_existing_honest_reply(make_chat) -> None:  # type: ignore[no-untyped-def]
    chat = make_chat(["nope", "still nope", think()])
    chat.upload(xlsx_bytes(MESSY), "messy-data.xlsx")

    turn = chat.turn("remove duplicate rows")

    assert turn["outcome"] == "not_delivered" and turn["delivered_files"] == []
    assert UNDERSTANDING_FAILED_SENTENCE in turn["reply"]


def test_a_failure_is_logged_with_the_turn_and_never_the_message(  # type: ignore[no-untyped-def]
    make_chat, caplog: pytest.LogCaptureFixture
) -> None:
    chat = make_chat([brain(), "nope: remove duplicate rows secret-cell-4111", think()])
    chat.upload(xlsx_bytes(MESSY), "messy-data.xlsx")

    with caplog.at_level("INFO"):
        turn = chat.turn("remove duplicate rows")

    assert turn["outcome"] == "delivered"
    failures = [r.getMessage() for r in caplog.records if "failed (" in r.getMessage()]
    assert len(failures) == 1 and turn["turn_id"] in failures[0] and "Think" in failures[0]
    assert "secret-cell-4111" not in caplog.text


# -- 8: findings on the file handed back, after a start-over -------------------------------------


def test_a_start_over_reports_what_the_new_file_still_has(make_chat) -> None:  # type: ignore[no-untyped-def]
    chat = make_chat(
        [
            brain(),
            think(),
            brain("remove_empty_rows", file_to_use="original"),
            think(),
        ]
    )
    chat.upload(xlsx_bytes(MESSY), "messy-data.xlsx")
    first = chat.turn("remove duplicate rows")
    assert "I also noticed" in first["reply"]

    again = chat.turn("start over from my original file and remove the empty rows")

    assert again["outcome"] == "delivered", again["reply"]
    (copy,) = again["delivered_files"]
    assert facts(chat.download(copy)) == (5, 1, 0)  # one exact duplicate is still in the copy
    assert "I also noticed" in again["reply"]
    assert "1 row repeats an earlier row exactly" in again["reply"]
    assert "1 value of 'Email' is linked to more than one 'Name'" in again["reply"]


def test_a_follow_up_on_the_copy_still_reports_on_the_new_copy(make_chat) -> None:  # type: ignore[no-untyped-def]
    chat = make_chat([brain("remove_empty_rows"), think(), brain("remove_duplicate_rows"), think()])
    chat.upload(xlsx_bytes(MESSY), "messy-data.xlsx")
    chat.turn("remove the empty rows")

    second = chat.turn("and the duplicates")

    assert "1 value of 'Email' is linked to more than one 'Name'" in second["reply"]


def test_a_turn_that_delivers_nothing_does_not_repeat_the_findings_next_time(make_chat) -> None:  # type: ignore[no-untyped-def]
    """The once-per-file rule still holds until a file is handed back: a declined plan showed the
    findings already, so the retry that delivers does not say them again."""
    chat = make_chat([brain(), think(), brain(), think()])
    chat.upload(xlsx_bytes(MESSY), "messy-data.xlsx")
    declined = chat.turn("remove duplicate rows", decision="reject")
    assert declined["outcome"] == "not_delivered"
    assert "linked to more than one 'Name'" in declined["reply"]

    again = chat.turn("remove duplicate rows")

    assert again["outcome"] == "delivered"
    assert "I also noticed" not in again["reply"]


# -- 5 (honesty): "exact duplicate rows", and the trim/case finding -------------------------------


def test_the_dedupe_reply_says_exact_duplicate_rows(make_chat) -> None:  # type: ignore[no-untyped-def]
    chat = make_chat([brain(), think()])
    chat.upload(xlsx_bytes(MESSY), "messy-data.xlsx")

    turn = chat.turn("remove duplicate rows")

    assert "removed 1 exact duplicate row" in turn["reply"]
    assert "0 exact duplicate rows remaining" in turn["reply"]


def test_rows_that_differ_only_by_spaces_or_case_are_reported_not_removed(make_chat) -> None:  # type: ignore[no-untyped-def]
    rows = {
        "Name": ["Alice", "alice ", "Bob", "Bob", "Carol"],
        "Amount": [10, 10, 20, 20, 30],
    }
    chat = make_chat([brain(), think()])
    chat.upload(xlsx_bytes(rows), "names.xlsx")

    turn = chat.turn("remove duplicate rows")

    assert turn["outcome"] == "delivered", turn["reply"]
    (copy,) = turn["delivered_files"]
    assert facts(chat.download(copy)) == (4, 0, 0)  # only the exact repeat (Bob) was removed
    assert (
        "I also noticed: 1 row matches an earlier row once spaces around the text and capital "
        "letters are ignored, but isn't an exact repeat" in turn["reply"]
    )
    assert "alice" not in turn["reply"].lower().replace("matches an earlier", "")


def test_a_saved_uploads_hash_is_unchanged_by_all_of_it(make_chat, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    chat = make_chat([brain(), think("which_file"), brain("remove_empty_rows"), think()])
    two_uploads(chat)
    before = upload_hashes(chat)

    chat.turn("remove duplicate rows")
    chat.turn("remove the empty rows in mess-data.xlsx")

    assert upload_hashes(chat) == before


# -- Review round 1 --------------------------------------------------------------------------------


def asks_then_waits(chat: Chat) -> dict[str, Any]:
    asked = chat.turn("remove duplicate rows in sales.xlsx")
    assert asked["reply"] in FRIEND_QUESTIONS
    return asked


def test_naming_an_unknown_file_twice_still_resumes_the_original_request(make_chat) -> None:  # type: ignore[no-untyped-def]
    """F1: the second wrong name used to replace the waiting request with itself."""
    chat = make_chat(
        [
            brain(phrase="remove duplicate rows"),
            think(),
            brain(phrase="remove duplicate rows", answers_pending=True),  # "sales_2024.xlsx"
            think(),
            brain(phrase="remove duplicate rows", answers_pending=True),  # "messy-data.xlsx"
            think(),
        ]
    )
    two_uploads(chat)
    asks_then_waits(chat)

    again = chat.turn("sales_2024.xlsx")

    assert again["reply"] in FRIEND_QUESTIONS
    assert session_json(chat)["pending_request"]["message"] == (
        "remove duplicate rows in sales.xlsx"
    )

    resumed = chat.turn("messy-data.xlsx")

    assert resumed["outcome"] == "delivered", resumed["reply"]
    assert worked_on_original("messy-data.xlsx") in resumed["reply"]
    assert RESUMED_SENTENCE in resumed["reply"]


@pytest.mark.parametrize("stage", ["brain", "think", "planner", "hands", "inspector"])
def test_a_stage_failure_on_the_answering_turn_keeps_the_waiting_request(  # type: ignore[no-untyped-def]
    make_chat, stage: str
) -> None:
    """F2: a failed answering turn used to clear the waiting request, so the retry got nothing."""
    failing = {"on": False}

    def wrap(cycle: Any) -> Any:
        return raise_in(stage, after=False)(cycle) if failing["on"] else cycle

    chat = make_chat(
        ByStage(
            [
                brain(phrase="remove duplicate rows"),
                brain(phrase="remove duplicate rows", answers_pending=True),
            ],
            [think()],
        ),
        wrap=wrap,
    )
    two_uploads(chat)
    asks_then_waits(chat)
    failing["on"] = True
    failed = chat.turn("messy-data.xlsx")
    failing["on"] = False
    assert failed["outcome"] == "failed"
    assert session_json(chat)["pending_request"] is not None

    retry = chat.turn("messy-data.xlsx")

    assert retry["outcome"] == "delivered", retry["reply"]
    assert worked_on_original("messy-data.xlsx") in retry["reply"]


def test_two_failed_reads_of_the_answer_keep_the_waiting_request(make_chat) -> None:  # type: ignore[no-untyped-def]
    chat = make_chat(
        [
            brain(phrase="remove duplicate rows"),
            think(),
            brain(phrase="remove duplicate rows", answers_pending=True),
            "nope",
            "still nope",  # Think fails twice on the answering turn
            brain(phrase="remove duplicate rows", answers_pending=True),
            think(),
        ]
    )
    two_uploads(chat)
    asks_then_waits(chat)

    failed = chat.turn("messy-data.xlsx")

    assert UNDERSTANDING_FAILED_SENTENCE in failed["reply"]
    assert session_json(chat)["pending_request"] is not None
    retry = chat.turn("messy-data.xlsx")
    assert retry["outcome"] == "delivered", retry["reply"]


def test_a_brain_that_answers_for_a_request_of_its_own_is_visible_and_cross_checked(  # type: ignore[no-untyped-def]
    make_chat,
) -> None:
    """F3: the old request ran and nothing said the new one hadn't."""
    chat = make_chat(
        [
            brain(phrase="remove duplicate rows"),
            think(),
            brain(phrase="remove duplicate rows", answers_pending=True),  # a misreading
            think(),
        ]
    )
    two_uploads(chat)
    asks_then_waits(chat)

    turn = chat.turn("actually just remove the empty rows in messy-data.xlsx")

    assert turn["outcome"] == "delivered"
    assert RESUMED_SENTENCE in turn["reply"]  # a wrong resumption is visible
    assert "mentions empty rows, but I didn't run the empty-row removal" in turn["reply"]


# -- A name that is not a session file, with one upload and with several ---------------------------


@pytest.mark.parametrize(
    "message",
    [
        "remove duplicate rows and save it as cleaned.xlsx",
        "remove duplicate rows, export to results.csv",
        "remove duplicate rows (thanks for budget.xlsx)",
    ],
)
def test_with_one_upload_a_name_that_is_no_file_is_not_a_question(make_chat, message: str) -> None:  # type: ignore[no-untyped-def]
    """F4: with one upload there is nothing to choose, and the reply names the file it used."""
    chat = make_chat([brain(), think()])
    chat.upload(xlsx_bytes(MESSY), "messy-data.xlsx")

    turn = chat.turn(message)

    assert turn["outcome"] == "delivered", turn["reply"]
    assert worked_on_original("messy-data.xlsx") in turn["reply"]


def test_with_several_uploads_a_name_that_is_not_a_file_still_asks(make_chat) -> None:  # type: ignore[no-untyped-def]
    chat = make_chat([brain(), think()])
    two_uploads(chat)

    turn = chat.turn("remove duplicate rows and save it as cleaned.xlsx")

    assert turn["outcome"] == "not_delivered" and turn["reply"] in FRIEND_QUESTIONS


def test_a_name_close_to_a_file_and_its_copy_is_not_ambiguous(make_chat) -> None:  # type: ignore[no-untyped-def]
    """The upload and the copy made from it are one lineage: naming either is not a question."""
    chat = make_chat([brain(), think(), brain("remove_empty_rows"), think()])
    chat.upload(xlsx_bytes(MESSY), "report.xlsx")
    first = chat.turn("remove duplicate rows")
    (copy,) = first["delivered_files"]

    turn = chat.turn(f"remove the empty rows in {copy['name'].replace('_dedup', '_dedop')}")

    assert turn["outcome"] == "delivered", turn["reply"]
    assert turn["reply"] not in FRIEND_QUESTIONS


def test_a_greeting_that_mentions_an_unknown_file_remembers_no_request(make_chat) -> None:  # type: ignore[no-untyped-def]
    chat = make_chat([brain("conversation"), think()])
    two_uploads(chat)

    turn = chat.turn("thanks for sales.xlsx")

    assert turn["reply"] not in FRIEND_QUESTIONS  # a greeting, not a question
    assert session_json(chat)["pending_request"] is None


def test_a_resumed_reply_says_so_in_every_speaker(make_chat) -> None:  # type: ignore[no-untyped-def]
    for speaker in ("template", "focus"):
        chat = make_chat(
            [
                brain(phrase="remove duplicate rows"),
                think(),
                brain(phrase="remove duplicate rows", answers_pending=True),
                think(),
            ],
            speaker,
        )
        two_uploads(chat)
        chat.turn("remove duplicate rows in sales.xlsx")

        resumed = chat.turn("messy-data.xlsx")

        assert resumed["outcome"] == "delivered" and RESUMED_SENTENCE in resumed["reply"], speaker


# -- Review round 2 -----------------------------------------------------------------------------


def unsupported_answer() -> str:
    """Brain says the message is only the answer, but reads the waiting request as no action."""
    return json.dumps({"intent": "unclear", "confidence": 0.4, "answers_pending": True})


def test_think_failing_twice_on_the_answer_does_not_claim_the_request_went_on(make_chat) -> None:  # type: ignore[no-untyped-def]
    """F5: the sentence "went on with your earlier request" sat beside "I couldn't work out..."."""
    chat = make_chat(
        ByStage(
            [
                brain(phrase="remove duplicate rows"),
                brain(phrase="remove duplicate rows", answers_pending=True),
            ],
            [think(), "nope", "still nope"],
        )
    )
    two_uploads(chat)
    asks_then_waits(chat)

    failed = chat.turn("messy-data.xlsx")

    assert UNDERSTANDING_FAILED_SENTENCE in failed["reply"]
    assert RESUMED_SENTENCE not in failed["reply"]


def test_a_brain_that_reads_the_answer_as_no_action_does_not_claim_the_request_went_on(  # type: ignore[no-untyped-def]
    make_chat,
) -> None:
    chat = make_chat(
        ByStage([brain(phrase="remove duplicate rows"), unsupported_answer()], [think()])
    )
    two_uploads(chat)
    asks_then_waits(chat)

    turn = chat.turn("messy-data.xlsx")

    assert turn["outcome"] == "not_delivered"
    assert RESUMED_SENTENCE not in turn["reply"]


def test_the_resumed_sentence_is_on_a_nothing_left_reply(make_chat) -> None:  # type: ignore[no-untyped-def]
    """The request was carried out (it found nothing to remove), so the sentence is true there."""
    chat = make_chat(
        ByStage(
            [
                brain(phrase="remove duplicate rows"),
                brain(phrase="remove duplicate rows"),
                brain(phrase="remove duplicate rows", answers_pending=True),
            ],
            [think()],
        )
    )
    two_uploads(chat)
    chat.turn("remove duplicate rows in messy-data.xlsx")  # delivers a copy with none left
    asks = chat.turn("remove duplicate rows in sales.xlsx")
    assert asks["reply"] in FRIEND_QUESTIONS

    turn = chat.turn("messy-data.xlsx")

    assert turn["outcome"] == "not_delivered"
    assert "already has no exact duplicate rows" in turn["reply"]
    assert RESUMED_SENTENCE in turn["reply"]


def test_the_resumed_sentence_is_on_a_report_that_was_given(make_chat) -> None:  # type: ignore[no-untyped-def]
    report = brain("report_on_file", phrase="give me a report")
    chat = make_chat(ByStage([report, report_answer(report)], [think()]))
    two_uploads(chat)
    chat.turn("give me a report on sales.xlsx")

    turn = chat.turn("messy-data.xlsx")

    assert "Report on" in turn["reply"] and RESUMED_SENTENCE in turn["reply"]


def report_answer(reply: str) -> str:
    return json.dumps({**json.loads(reply), "answers_pending": True})


def test_two_brain_failures_on_the_answer_keep_the_waiting_request(make_chat) -> None:  # type: ignore[no-untyped-def]
    chat = make_chat(
        ByStage(
            [
                brain(phrase="remove duplicate rows"),
                "nope",
                "still nope",
                brain(phrase="remove duplicate rows", answers_pending=True),
            ],
            [think()],
        )
    )
    two_uploads(chat)
    asks_then_waits(chat)

    failed = chat.turn("messy-data.xlsx")

    assert UNDERSTANDING_FAILED_SENTENCE in failed["reply"]
    assert session_json(chat)["pending_request"] is not None
    retry = chat.turn("messy-data.xlsx")
    assert retry["outcome"] == "delivered", retry["reply"]


def test_the_turn_after_a_resumed_one_does_not_say_it_resumed(make_chat) -> None:  # type: ignore[no-untyped-def]
    chat = make_chat(
        ByStage(
            [
                brain(phrase="remove duplicate rows"),
                brain(phrase="remove duplicate rows", answers_pending=True),
                brain("remove_empty_rows"),
            ],
            [think()],
        )
    )
    two_uploads(chat)
    asks_then_waits(chat)
    assert RESUMED_SENTENCE in chat.turn("messy-data.xlsx")["reply"]

    next_turn = chat.turn("now remove the empty rows")

    assert next_turn["outcome"] == "delivered" and RESUMED_SENTENCE not in next_turn["reply"]


@pytest.mark.parametrize("name", ["duplicates.xlsx", "empty rows jan.xlsx"])
def test_a_file_name_with_a_request_word_in_it_is_not_a_request(make_chat, name: str) -> None:  # type: ignore[no-untyped-def]
    """F6: answering with the name of a file called "duplicates.xlsx" is not asking for them."""
    chat = make_chat(
        ByStage(
            [
                brain("remove_empty_rows", phrase="remove the empty rows"),
                brain("remove_empty_rows", phrase="remove the empty rows", answers_pending=True),
            ],
            [think()],
        )
    )
    chat.upload(xlsx_bytes(OTHER), name)
    chat.upload(xlsx_bytes(MESSY), "messy-data.xlsx")
    asked = chat.turn("remove the empty rows in budget.xlsx")
    assert asked["reply"] in FRIEND_QUESTIONS

    turn = chat.turn(name)

    assert turn["outcome"] == "delivered", turn["reply"]
    assert "Your message mentions" not in turn["reply"]


def test_names_between_a_files_two_copies_are_not_a_question(make_chat) -> None:  # type: ignore[no-untyped-def]
    """The name is equally close to both copies, which are the one lineage: not ambiguous."""
    chat = make_chat(
        ByStage([brain(), brain("remove_empty_rows"), brain("remove_empty_rows")], [think()])
    )
    chat.upload(xlsx_bytes(MESSY), "report.xlsx")
    chat.turn("remove duplicate rows")
    second = chat.turn("now remove the empty rows too")
    assert second["outcome"] == "delivered"
    names = chat.delivered_names()
    assert names == ["report_deduplicated.xlsx", "report_deduplicated_cleaned.xlsx"]

    turn = chat.turn("remove the empty rows in report_deduplicated_cle.xlsx")

    assert turn["reply"] not in FRIEND_QUESTIONS

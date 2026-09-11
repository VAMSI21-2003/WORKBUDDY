"""Unit tests for buddy.context."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from itertools import count

import pytest

from buddy.context import ConversationRole, Context


def _make_clock():
    """A deterministic, strictly-increasing clock for tests."""
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    ticks = count(1)

    def clock() -> datetime:
        return start + timedelta(seconds=next(ticks))

    return clock


def _make_context() -> Context:
    return Context(active_workspace="workspace-1", current_session="session-1", clock=_make_clock())


# -- construction / defaults -----------------------------------------------------


def test_context_defaults() -> None:
    context = _make_context()

    assert context.active_task is None
    assert context.active_file is None
    assert context.active_document is None
    assert context.active_sheet is None
    assert context.uploaded_files == []
    assert context.current_topic is None
    assert context.mode == "friend"
    assert context.metadata == {}


def test_context_stamps_created_and_updated_at_from_the_clock() -> None:
    context = _make_context()

    assert context.created_at == context.last_updated_at


# -- active task / file switching -------------------------------------------------


def test_switch_active_task() -> None:
    context = _make_context()

    context.switch_active_task("task-1")

    assert context.active_task == "task-1"


def test_switch_active_task_to_none_clears_it() -> None:
    context = _make_context()
    context.switch_active_task("task-1")

    context.switch_active_task(None)

    assert context.active_task is None


def test_switch_active_file_sets_document_and_sheet() -> None:
    context = _make_context()

    context.switch_active_file("report.xlsx", document="Sheet1", sheet="Data")

    assert context.active_file == "report.xlsx"
    assert context.active_document == "Sheet1"
    assert context.active_sheet == "Data"


def test_switch_active_file_resets_document_and_sheet_when_not_given() -> None:
    """Switching to a different file with no new document/sheet clears the
    stale pointers from the previous file — a leftover sheet name from
    file A is meaningless once file B is active.
    """
    context = _make_context()
    context.switch_active_file("report.xlsx", document="Sheet1", sheet="Data")

    context.switch_active_file("other.docx")

    assert context.active_file == "other.docx"
    assert context.active_document is None
    assert context.active_sheet is None


def test_switch_active_file_touches_last_updated_at() -> None:
    context = _make_context()
    before = context.last_updated_at

    context.switch_active_file("report.xlsx")

    assert context.last_updated_at > before


# -- mode -----------------------------------------------------------------------------


def test_mode_defaults_to_friend() -> None:
    context = _make_context()

    assert context.mode == "friend"


def test_set_mode_switches_to_focus() -> None:
    context = _make_context()

    context.set_mode("focus")

    assert context.mode == "focus"


def test_set_mode_switches_back_to_friend() -> None:
    context = _make_context()
    context.set_mode("focus")

    context.set_mode("friend")

    assert context.mode == "friend"


def test_set_mode_rejects_invalid_values() -> None:
    context = _make_context()

    with pytest.raises(ValueError):
        context.set_mode("sarcastic")

    # Rejected attempt leaves the existing mode untouched.
    assert context.mode == "friend"


def test_set_mode_touches_last_updated_at() -> None:
    context = _make_context()
    before = context.last_updated_at

    context.set_mode("focus")

    assert context.last_updated_at > before


def test_mode_is_a_per_session_setting_not_cleared_by_clear_completed_work() -> None:
    """``mode`` is a session-level preference, not per-message transient
    state — confirm it survives operations that clear per-turn state.
    """
    context = _make_context()
    context.set_mode("focus")
    context.switch_active_task("task-1")

    context.clear_completed_work()

    assert context.mode == "focus"


# -- uploaded files -----------------------------------------------------------------


def test_add_uploaded_file() -> None:
    context = _make_context()

    context.add_uploaded_file("report.xlsx")

    assert context.uploaded_files == ["report.xlsx"]


def test_add_uploaded_file_is_idempotent() -> None:
    context = _make_context()
    context.add_uploaded_file("report.xlsx")
    before = context.last_updated_at

    context.add_uploaded_file("report.xlsx")

    assert context.uploaded_files == ["report.xlsx"]
    assert context.last_updated_at == before  # no-op: _touch() not called again


def test_add_uploaded_file_tracks_multiple_files_in_order() -> None:
    context = _make_context()

    context.add_uploaded_file("a.xlsx")
    context.add_uploaded_file("b.xlsx")

    assert context.uploaded_files == ["a.xlsx", "b.xlsx"]


# -- metadata -------------------------------------------------------------------------


def test_set_and_get_metadata() -> None:
    context = _make_context()

    context.set_metadata("key", "value")

    assert context.get_metadata("key") == "value"


def test_get_metadata_returns_default_when_missing() -> None:
    context = _make_context()

    assert context.get_metadata("missing", "fallback") == "fallback"
    assert context.get_metadata("missing") is None


# -- conversation history -----------------------------------------------------------


def test_record_user_message_and_buddy_reply() -> None:
    context = _make_context()

    context.record_user_message("hello")
    context.record_buddy_reply("hi there")

    turns = context.recent_conversation()
    assert [t.role for t in turns] == [ConversationRole.USER, ConversationRole.BUDDY]
    assert [t.message for t in turns] == ["hello", "hi there"]


def test_recent_conversation_is_oldest_first_and_respects_limit() -> None:
    context = _make_context()
    for i in range(5):
        context.record_user_message(f"message-{i}")

    turns = context.recent_conversation(limit=2)

    assert [t.message for t in turns] == ["message-3", "message-4"]


def test_recent_user_messages_filters_out_buddy_replies() -> None:
    context = _make_context()
    context.record_user_message("question")
    context.record_buddy_reply("answer")
    context.record_user_message("follow-up")

    assert context.recent_user_messages() == ["question", "follow-up"]


def test_recent_buddy_replies_filters_out_user_messages() -> None:
    context = _make_context()
    context.record_user_message("question")
    context.record_buddy_reply("answer")

    assert context.recent_buddy_replies() == ["answer"]


def test_recent_user_messages_respects_limit() -> None:
    context = _make_context()
    for i in range(5):
        context.record_user_message(f"message-{i}")

    assert context.recent_user_messages(limit=2) == ["message-3", "message-4"]


def test_conversation_history_has_no_trimming_limit_on_write() -> None:
    """Context keeps the *full* history internally; only the read-side
    ``recent_*`` accessors are limited. Confirm there is no silent
    trimming on the write path that would lose older turns.
    """
    context = _make_context()
    for i in range(20):
        context.record_user_message(f"message-{i}")

    all_turns = context.recent_conversation(limit=1000)
    assert len(all_turns) == 20
    assert all_turns[0].message == "message-0"


# -- execution history -----------------------------------------------------------------


def test_record_and_recent_execution() -> None:
    context = _make_context()

    context.record_execution("removed 2 duplicate rows")

    records = context.recent_execution()
    assert [r.description for r in records] == ["removed 2 duplicate rows"]


def test_recent_execution_respects_limit() -> None:
    context = _make_context()
    for i in range(5):
        context.record_execution(f"did thing {i}")

    records = context.recent_execution(limit=2)

    assert [r.description for r in records] == ["did thing 3", "did thing 4"]


# -- active_work_item ----------------------------------------------------------------


def test_active_work_item_prefers_task_over_file_over_document() -> None:
    context = _make_context()
    context.switch_active_file("report.xlsx", document="Sheet1")
    context.switch_active_task("task-1")

    assert context.active_work_item() == "task-1"


def test_active_work_item_falls_back_to_file_when_no_task() -> None:
    context = _make_context()
    context.switch_active_file("report.xlsx")

    assert context.active_work_item() == "report.xlsx"


def test_active_work_item_falls_back_to_document_when_no_task_or_file() -> None:
    context = _make_context()
    context.switch_active_file(None, document="Sheet1")

    assert context.active_work_item() == "Sheet1"


def test_active_work_item_is_none_when_nothing_is_active() -> None:
    context = _make_context()

    assert context.active_work_item() is None


# -- snapshot -------------------------------------------------------------------------


def test_snapshot_reflects_current_state() -> None:
    context = _make_context()
    context.switch_active_file("report.xlsx")
    context.add_uploaded_file("report.xlsx")
    context.set_metadata("key", "value")
    context.set_mode("focus")

    snapshot = context.snapshot()

    assert snapshot["active_workspace"] == "workspace-1"
    assert snapshot["current_session"] == "session-1"
    assert snapshot["active_file"] == "report.xlsx"
    assert snapshot["uploaded_files"] == ["report.xlsx"]
    assert snapshot["metadata"] == {"key": "value"}
    assert snapshot["mode"] == "focus"


def test_snapshot_uploaded_files_is_a_copy_not_a_live_reference() -> None:
    context = _make_context()
    context.add_uploaded_file("report.xlsx")

    snapshot = context.snapshot()
    snapshot["uploaded_files"].append("mutated.xlsx")

    assert context.uploaded_files == ["report.xlsx"]


# -- clear_completed_work --------------------------------------------------------------


def test_clear_completed_work_clears_only_active_task() -> None:
    context = _make_context()
    context.switch_active_task("task-1")
    context.switch_active_file("report.xlsx", document="Sheet1", sheet="Data")

    context.clear_completed_work()

    assert context.active_task is None
    assert context.active_file == "report.xlsx"
    assert context.active_document == "Sheet1"
    assert context.active_sheet == "Data"

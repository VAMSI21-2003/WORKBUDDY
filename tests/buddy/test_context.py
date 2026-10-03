"""Unit tests for buddy.context."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone
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


# -- analyzed files -----------------------------------------------------------------


def test_analyzed_files_defaults_to_empty() -> None:
    context = _make_context()

    assert context.analyzed_files == frozenset()


def test_mark_analyzed() -> None:
    context = _make_context()

    context.mark_analyzed("report.xlsx")

    assert context.analyzed_files == frozenset({"report.xlsx"})


def test_mark_analyzed_is_idempotent() -> None:
    context = _make_context()
    context.mark_analyzed("report.xlsx")
    before = context.last_updated_at

    context.mark_analyzed("report.xlsx")

    assert context.analyzed_files == frozenset({"report.xlsx"})
    assert context.last_updated_at == before  # no-op: _touch() not called again


def test_mark_analyzed_tracks_multiple_files() -> None:
    context = _make_context()

    context.mark_analyzed("a.xlsx")
    context.mark_analyzed("b.xlsx")

    assert context.analyzed_files == frozenset({"a.xlsx", "b.xlsx"})


def test_mark_analyzed_is_independent_of_uploaded_files() -> None:
    """A file can be uploaded without being analyzed yet, and marking one
    analyzed doesn't remove it from (or require it to be present in)
    uploaded_files — the two sets are tracked independently.
    """
    context = _make_context()
    context.add_uploaded_file("report.xlsx")

    context.mark_analyzed("report.xlsx")

    assert context.uploaded_files == ["report.xlsx"]
    assert context.analyzed_files == frozenset({"report.xlsx"})


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


# -- to_dict / from_dict ------------------------------------------------------


def _populated_context() -> Context:
    context = _make_context()
    context.add_uploaded_file("uploads/a.xlsx")
    context.add_uploaded_file("uploads/b.xlsx")
    context.mark_analyzed("uploads/b.xlsx")
    context.mark_analyzed("uploads/a.xlsx")
    context.switch_active_file("uploads/a.xlsx", document="doc", sheet="Sheet1")
    context.switch_active_task("task-7")
    context.set_current_topic("cleanup")
    context.set_mode("focus")
    context.set_metadata("source", {"kind": "upload", "ids": [1, 2]})
    context.record_user_message("remove duplicate rows")
    context.record_buddy_reply("Done — removed 2 duplicate row(s).")
    context.record_user_message("now the empty rows")
    context.record_execution("ran spreadsheet.deduplicate")
    return context


def _history(context: Context) -> tuple[list[object], list[object]]:
    return context.recent_conversation(limit=1000), context.recent_execution(limit=1000)


def test_to_dict_from_dict_round_trip_through_json_keeps_everything() -> None:
    original = _populated_context()

    data = json.loads(json.dumps(original.to_dict()))
    restored = Context.from_dict(data)

    assert restored == original  # every compared field
    assert restored.analyzed_files == frozenset({"uploads/a.xlsx", "uploads/b.xlsx"})
    assert _history(restored) == _history(original)  # roles, text and timestamps
    assert restored.created_at == original.created_at
    assert restored.last_updated_at == original.last_updated_at
    assert restored.buddy_reply_count() == 1
    assert restored.to_dict() == original.to_dict()


def test_from_dict_uses_the_injected_clock_only_after_the_restore() -> None:
    original = _populated_context()
    later = datetime(2030, 5, 5, tzinfo=UTC)

    restored = Context.from_dict(original.to_dict(), clock=lambda: later)

    assert _history(restored) == _history(original)  # replayed with the stored times
    assert restored.last_updated_at == original.last_updated_at
    restored.record_buddy_reply("a new reply")
    assert restored.recent_conversation()[-1].timestamp == later
    assert restored.last_updated_at == later


def test_a_restored_context_is_independent_of_the_dict_it_came_from() -> None:
    data = _populated_context().to_dict()

    restored = Context.from_dict(data)
    data["uploaded_files"].append("uploads/c.xlsx")
    data["metadata"]["source"]["ids"].append(3)

    assert restored.uploaded_files == ["uploads/a.xlsx", "uploads/b.xlsx"]
    assert restored.metadata == {"source": {"kind": "upload", "ids": [1, 2]}}


def test_an_empty_context_round_trips() -> None:
    original = _make_context()

    restored = Context.from_dict(json.loads(json.dumps(original.to_dict())))

    assert restored == original
    assert _history(restored) == ([], [])
    assert restored.analyzed_files == frozenset()


def test_from_dict_rejects_an_unknown_format_version() -> None:
    data = _populated_context().to_dict()
    data["version"] = 99

    with pytest.raises(ValueError, match="version"):
        Context.from_dict(data)


def test_from_dict_rejects_an_invalid_mode() -> None:
    data = _populated_context().to_dict()
    data["mode"] = "chaos"

    with pytest.raises(ValueError):
        Context.from_dict(data)


def test_from_dict_rejects_an_unknown_role() -> None:
    data = _populated_context().to_dict()
    data["conversation_history"][0]["role"] = "system"

    with pytest.raises(ValueError):
        Context.from_dict(data)


def test_from_dict_rejects_a_missing_key() -> None:
    data = _populated_context().to_dict()
    del data["analyzed_files"]

    with pytest.raises(KeyError):
        Context.from_dict(data)

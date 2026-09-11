"""Unit tests for buddy.eyes."""

from __future__ import annotations

from buddy.eyes import Eyes
from buddy.state import BuddyState


def _make_state() -> BuddyState:
    return BuddyState.new(session_id="session-1", workspace_id="workspace-1")


def test_eyes_records_session_and_workspace_ids() -> None:
    state = _make_state()

    Eyes()(state)

    assert "session_id: session-1" in state.observations
    assert "workspace_id: workspace-1" in state.observations


def test_eyes_records_user_message_when_present() -> None:
    state = _make_state()
    state.user_message = "remove duplicate rows"

    Eyes()(state)

    assert "user_message: 'remove duplicate rows'" in state.observations


def test_eyes_records_placeholder_for_empty_user_message() -> None:
    state = _make_state()

    Eyes()(state)

    assert "user_message: <empty>" in state.observations


def test_eyes_records_each_uploaded_file_individually() -> None:
    state = _make_state()
    state.context.add_uploaded_file("report.xlsx")
    state.context.add_uploaded_file("notes.docx")

    Eyes()(state)

    assert "uploaded_file: report.xlsx" in state.observations
    assert "uploaded_file: notes.docx" in state.observations


def test_eyes_records_none_placeholder_when_no_uploaded_files() -> None:
    state = _make_state()

    Eyes()(state)

    assert "uploaded_files: none" in state.observations


def test_eyes_records_metadata_entries() -> None:
    state = _make_state()
    state.context.set_metadata("source", "email")

    Eyes()(state)

    assert "metadata: source='email'" in state.observations


def test_eyes_records_nothing_extra_when_no_metadata() -> None:
    state = _make_state()

    Eyes()(state)

    assert not any(o.startswith("metadata:") for o in state.observations)


def test_eyes_records_exactly_the_expected_observations_for_a_fresh_upload() -> None:
    """A full, exact accounting of what one Eyes pass records for a
    single fresh upload with a message — proves nothing extra (no file
    type classification, no intent guess) sneaks in alongside the
    mechanical records.
    """
    state = _make_state()
    state.user_message = "remove duplicate rows"
    state.context.add_uploaded_file("report.xlsx")

    Eyes()(state)

    assert state.observations == [
        "session_id: session-1",
        "workspace_id: workspace-1",
        "user_message: 'remove duplicate rows'",
        "uploaded_file: report.xlsx",
    ]


def test_eyes_never_infers_intent_or_understanding() -> None:
    """Eyes has no dependency on any AI model and performs no
    interpretation (per its own docstring) — confirm that holds even for
    a message and file that make the intent obvious to a human reader.
    """
    state = _make_state()
    state.user_message = "remove duplicate rows"
    state.context.add_uploaded_file("report.xlsx")

    Eyes()(state)

    assert state.intent is None
    assert state.understanding is None


def test_eyes_records_follow_up_message_against_existing_context() -> None:
    """A second turn in the same session: Eyes records the new message
    and the files already tracked in context — it doesn't decide anything
    about how the new message relates to the existing file.
    """
    state = _make_state()
    state.context.add_uploaded_file("report.xlsx")
    state.user_message = "actually, keep the header row"

    Eyes()(state)

    assert "user_message: 'actually, keep the header row'" in state.observations
    assert "uploaded_file: report.xlsx" in state.observations


def test_eyes_returns_the_same_state_instance() -> None:
    state = _make_state()

    result = Eyes()(state)

    assert result is state


def test_eyes_appends_rather_than_clearing_prior_observations() -> None:
    """Clearing observations between turns is ``BuddyState.reset_transient``'s
    job, not Eyes's — Eyes only ever appends.
    """
    state = _make_state()
    state.add_observation("a pre-existing observation")

    Eyes()(state)

    assert "a pre-existing observation" in state.observations

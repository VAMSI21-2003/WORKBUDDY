"""Unit tests for buddy.state."""

from __future__ import annotations

from buddy.models import CapabilityRequest
from buddy.state import BuddyState


def _make_state() -> BuddyState:
    return BuddyState.new(session_id="session-1", workspace_id="workspace-1")


def test_new_state_has_no_delegation_by_default() -> None:
    state = _make_state()

    assert state.delegation is None


def test_delegation_can_be_set_and_read_back() -> None:
    state = _make_state()
    requests = (
        CapabilityRequest(capability="spreadsheet.deduplicate", target="uploads/report.xlsx"),
    )

    state.delegation = requests

    assert state.delegation == requests


def test_reset_transient_clears_delegation() -> None:
    state = _make_state()
    state.delegation = (
        CapabilityRequest(capability="spreadsheet.deduplicate", target="uploads/report.xlsx"),
    )

    state.reset_transient()

    assert state.delegation is None


def test_reset_transient_preserves_context_but_clears_delegation() -> None:
    state = _make_state()
    state.context.add_uploaded_file("uploads/report.xlsx")
    state.delegation = (
        CapabilityRequest(capability="spreadsheet.deduplicate", target="uploads/report.xlsx"),
    )

    state.reset_transient()

    # Context (session-level) survives a reset — only per-cycle fields clear.
    assert state.uploaded_files == ["uploads/report.xlsx"]
    assert state.delegation is None


def test_new_state_has_none_for_all_typed_stage_fields() -> None:
    state = _make_state()

    assert state.understanding is None
    assert state.reasoning is None
    assert state.planning is None
    assert state.delegation is None
    assert state.execution_result is None
    assert state.verification_result is None

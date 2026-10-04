"""Delivery: the one function that decides what's handed back, file listing, and download.

Design Sections 1.6, 1.7 and 5.3, and items 1 and 3 of "What a PR-2 author
must do" (``docs/reviews/pipeline-failure-and-output-dir-review.md``).
"""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from backend.delivery import check_turn_file, delivered_files, uploads_unchanged
from backend.main import create_app
from backend.settings import Settings
from buddy.engine_factory import build_engine
from buddy.models import VerificationResult
from buddy.state import BuddyState, StageFailure
from buddy.voice import PersonalityVoice
from tests.backend.factories import (
    DEDUPE_THEN_EMPTY,
    PipelineFactory,
    QueuedProvider,
    chain,
    raise_in,
    single,
)
from tests.backend.helpers import new_session, start_turn, upload, wait_finished, xlsx_bytes

_NOW = datetime(2026, 10, 3, tzinfo=UTC)


# -- delivered_files: the table of design Section 7.2 ------------------------------------


@pytest.fixture(scope="module")
def delivered_run(tmp_path_factory: pytest.TempPathFactory) -> tuple[BuddyState, Path]:
    """A real, verified, delivered turn: its final state and its turn folder."""
    root = tmp_path_factory.mktemp("delivered")
    upload_path = root / "uploads" / "report.xlsx"
    upload_path.parent.mkdir(parents=True)
    upload_path.write_bytes(xlsx_bytes())
    turn_dir = root / "outputs" / "t0001"
    turn_dir.mkdir(parents=True)
    provider = QueuedProvider(single("remove_duplicate_rows"))
    state = BuddyState.new(session_id="k" * 32, workspace_id="k" * 32)
    state.context.add_uploaded_file(str(upload_path))
    state.context.switch_active_file(str(upload_path))
    state.user_message = "remove duplicate rows"
    state.turn_output_dir = str(turn_dir)
    build_engine(llm_provider=provider, speaker=PersonalityVoice()).run(state)
    assert state.failure is None and state.verification_result is not None
    assert state.verification_result.passed and len(state.execution_result.outputs) == 1  # type: ignore[union-attr]
    return state, turn_dir


def _row(
    delivered_run: tuple[BuddyState, Path], tmp_path: Path
) -> tuple[BuddyState, Path, Path]:
    """A private copy of the delivered turn: (state, turn folder, its output)."""
    base_state, base_dir = delivered_run
    turn_dir = tmp_path / "t0001"
    shutil.copytree(base_dir, turn_dir)
    state = copy.deepcopy(base_state)
    output = turn_dir / Path(base_state.execution_result.outputs[0]).name  # type: ignore[union-attr]
    state.execution_result = dataclasses.replace(
        state.execution_result, outputs=(str(output),)  # type: ignore[arg-type]
    )
    return state, turn_dir, output


def _deliver(state: BuddyState, turn_dir: Path) -> tuple[Any, ...]:
    return delivered_files(state, turn_dir=turn_dir, turn_id="t0001", now=_NOW)


def test_the_all_true_row_delivers(delivered_run: tuple[BuddyState, Path], tmp_path: Path) -> None:
    state, turn_dir, output = _row(delivered_run, tmp_path)

    (delivered,) = _deliver(state, turn_dir)

    assert delivered.file_id == "d_t0001_1"
    assert delivered.path == output
    assert delivered.name == output.name
    assert delivered.sha256 == hashlib.sha256(output.read_bytes()).hexdigest()
    assert delivered.size == output.stat().st_size
    assert delivered.turn_id == "t0001" and delivered.delivered_at == _NOW


def test_failure_set_with_outputs_verified_and_inside_delivers_nothing(
    delivered_run: tuple[BuddyState, Path], tmp_path: Path
) -> None:
    """The Inspector-after case (review item 1): every other rule holds."""
    state, turn_dir, output = _row(delivered_run, tmp_path)
    state.failure = StageFailure(stage="inspector", error_type="RuntimeError")
    assert state.verification_result.passed and output.is_file()  # type: ignore[union-attr]

    assert _deliver(state, turn_dir) == ()


@pytest.mark.parametrize("broken", ["failure", "unverified", "not_passed"])
def test_each_rule_holds_on_its_own_even_if_the_reply_predicate_said_yes(
    delivered_run: tuple[BuddyState, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    broken: str,
) -> None:
    """``delivered_files`` checks failure and verification itself, not only via Voice."""
    monkeypatch.setattr("backend.delivery.is_verified_delivery", lambda state: True)
    state, turn_dir, _ = _row(delivered_run, tmp_path)
    assert len(_deliver(state, turn_dir)) == 1  # control
    if broken == "failure":
        state.failure = StageFailure(stage="inspector", error_type="RuntimeError")
    elif broken == "unverified":
        state.verification_result = None
    else:
        state.verification_result = dataclasses.replace(
            state.verification_result, passed=False  # type: ignore[arg-type]
        )

    assert _deliver(state, turn_dir) == ()


def test_the_reply_predicate_is_a_rule_of_its_own(
    delivered_run: tuple[BuddyState, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("backend.delivery.is_verified_delivery", lambda state: False)
    state, turn_dir, _ = _row(delivered_run, tmp_path)

    assert _deliver(state, turn_dir) == ()


def test_no_outputs_delivers_nothing(
    delivered_run: tuple[BuddyState, Path], tmp_path: Path
) -> None:
    state, turn_dir, _ = _row(delivered_run, tmp_path)
    state.execution_result = dataclasses.replace(state.execution_result, outputs=())  # type: ignore[arg-type]

    assert _deliver(state, turn_dir) == ()


def test_only_execution_result_outputs_counts_never_legacy_or_step_outputs(
    delivered_run: tuple[BuddyState, Path], tmp_path: Path
) -> None:
    """Review item 3: ``state.outputs`` and each step's outputs are never read."""
    state, turn_dir, output = _row(delivered_run, tmp_path)
    state.execution_result = dataclasses.replace(state.execution_result, outputs=())  # type: ignore[arg-type]
    state.outputs = [{"outputs": [str(output)], "status": "success"}]
    assert any(step.outputs for step in state.execution_result.steps)  # type: ignore[union-attr]

    assert _deliver(state, turn_dir) == ()


@pytest.mark.parametrize("verification", [None, VerificationResult(passed=False)])
def test_unverified_delivers_nothing(
    delivered_run: tuple[BuddyState, Path], tmp_path: Path, verification: Any
) -> None:
    state, turn_dir, _ = _row(delivered_run, tmp_path)
    state.verification_result = verification

    assert _deliver(state, turn_dir) == ()


def test_a_stopped_chain_delivers_nothing(
    delivered_run: tuple[BuddyState, Path], tmp_path: Path
) -> None:
    state, turn_dir, _ = _row(delivered_run, tmp_path)
    state.execution_result = dataclasses.replace(
        state.execution_result, stopped_at="spreadsheet.deduplicate"  # type: ignore[arg-type]
    )

    assert _deliver(state, turn_dir) == ()


def test_a_path_outside_the_turn_folder_delivers_nothing(
    delivered_run: tuple[BuddyState, Path], tmp_path: Path
) -> None:
    state, turn_dir, output = _row(delivered_run, tmp_path)
    outside = tmp_path / output.name
    shutil.copy(output, outside)
    state.execution_result = dataclasses.replace(
        state.execution_result, outputs=(str(outside),)  # type: ignore[arg-type]
    )

    assert _deliver(state, turn_dir) == ()


def test_a_path_that_climbs_out_of_the_turn_folder_delivers_nothing(
    delivered_run: tuple[BuddyState, Path], tmp_path: Path
) -> None:
    state, turn_dir, output = _row(delivered_run, tmp_path)
    shutil.copy(output, tmp_path / "escaped.xlsx")
    climbing = f"{turn_dir}/../escaped.xlsx"
    state.execution_result = dataclasses.replace(state.execution_result, outputs=(climbing,))  # type: ignore[arg-type]

    assert _deliver(state, turn_dir) == ()


def test_a_relative_path_delivers_nothing(
    delivered_run: tuple[BuddyState, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state, turn_dir, output = _row(delivered_run, tmp_path)
    monkeypatch.chdir(turn_dir)
    state.execution_result = dataclasses.replace(
        state.execution_result, outputs=(output.name,)  # type: ignore[arg-type]
    )

    assert _deliver(state, turn_dir) == ()


def test_a_file_in_a_subfolder_delivers_nothing(
    delivered_run: tuple[BuddyState, Path], tmp_path: Path
) -> None:
    state, turn_dir, output = _row(delivered_run, tmp_path)
    nested = turn_dir / "sub" / output.name
    nested.parent.mkdir()
    shutil.copy(output, nested)
    state.execution_result = dataclasses.replace(
        state.execution_result, outputs=(str(nested),)  # type: ignore[arg-type]
    )

    assert _deliver(state, turn_dir) == ()


def test_a_symlink_delivers_nothing_even_to_a_file_inside(
    delivered_run: tuple[BuddyState, Path], tmp_path: Path
) -> None:
    state, turn_dir, output = _row(delivered_run, tmp_path)
    real = turn_dir / "real.xlsx"
    output.rename(real)
    output.symlink_to(real)

    assert _deliver(state, turn_dir) == ()


def test_a_path_through_a_symlinked_sibling_folder_delivers_nothing(
    delivered_run: tuple[BuddyState, Path], tmp_path: Path
) -> None:
    """Review L6: it resolves into the turn folder, but isn't in it by name."""
    state, turn_dir, output = _row(delivered_run, tmp_path)
    sibling = tmp_path / "sibling"
    sibling.symlink_to(turn_dir)
    state.execution_result = dataclasses.replace(
        state.execution_result, outputs=(str(sibling / output.name),)  # type: ignore[arg-type]
    )
    assert (sibling / output.name).resolve().parent == turn_dir.resolve()

    assert _deliver(state, turn_dir) == ()


def test_a_symlinked_turn_folder_delivers_nothing(
    delivered_run: tuple[BuddyState, Path], tmp_path: Path
) -> None:
    state, turn_dir, output = _row(delivered_run, tmp_path)
    linked = tmp_path / "linked"
    linked.symlink_to(turn_dir)
    state.execution_result = dataclasses.replace(
        state.execution_result, outputs=(str(linked / output.name),)  # type: ignore[arg-type]
    )

    assert _deliver(state, linked) == ()


def test_a_missing_file_delivers_nothing(
    delivered_run: tuple[BuddyState, Path], tmp_path: Path
) -> None:
    state, turn_dir, output = _row(delivered_run, tmp_path)
    output.unlink()

    assert _deliver(state, turn_dir) == ()


def test_an_empty_file_delivers_nothing(
    delivered_run: tuple[BuddyState, Path], tmp_path: Path
) -> None:
    state, turn_dir, output = _row(delivered_run, tmp_path)
    output.write_bytes(b"")

    assert _deliver(state, turn_dir) == ()


def test_a_directory_named_like_the_output_delivers_nothing(
    delivered_run: tuple[BuddyState, Path], tmp_path: Path
) -> None:
    state, turn_dir, output = _row(delivered_run, tmp_path)
    output.unlink()
    output.mkdir()

    assert _deliver(state, turn_dir) == ()


def test_one_bad_output_among_several_delivers_nothing(
    delivered_run: tuple[BuddyState, Path], tmp_path: Path
) -> None:
    state, turn_dir, output = _row(delivered_run, tmp_path)
    state.execution_result = dataclasses.replace(
        state.execution_result,  # type: ignore[arg-type]
        outputs=(str(output), str(turn_dir / "missing.xlsx")),
    )

    assert _deliver(state, turn_dir) == ()


def test_check_turn_file_returns_the_bytes_it_checked(tmp_path: Path) -> None:
    path = tmp_path / "a.xlsx"
    path.write_bytes(b"abc")

    assert check_turn_file(path, tmp_path) == (path, b"abc")


def test_uploads_unchanged_catches_a_change_a_deletion_and_a_symlink(tmp_path: Path) -> None:
    path = tmp_path / "u.xlsx"
    path.write_bytes(b"one")
    sha = hashlib.sha256(b"one").hexdigest()
    assert uploads_unchanged({"f": (path, sha)}) is True

    path.write_bytes(b"two")
    assert uploads_unchanged({"f": (path, sha)}) is False
    path.unlink()
    assert uploads_unchanged({"f": (path, sha)}) is False
    target = tmp_path / "target"
    target.write_bytes(b"one")
    path.symlink_to(target)
    assert uploads_unchanged({"f": (path, sha)}) is False


# -- Listing and download through the API -----------------------------------------------


def _delivered_turn(settings: Settings, replies: list[str] | None = None) -> tuple[
    TestClient, str, dict[str, Any], PipelineFactory
]:
    factory = PipelineFactory(QueuedProvider(replies or single("remove_duplicate_rows")))
    client = TestClient(create_app(settings, factory))
    session_id = new_session(client)
    assert upload(client, session_id, xlsx_bytes()).status_code == 201
    turn_id = start_turn(client, session_id, "remove duplicate rows").json()["turn_id"]
    return client, session_id, wait_finished(client, session_id, turn_id), factory


def test_a_delivered_file_downloads_byte_for_byte_with_safe_headers(settings: Settings) -> None:
    client, session_id, turn, factory = _delivered_turn(settings)
    (delivered,) = turn["delivered_files"]
    on_disk = Path(factory.runs[0].outputs[0])

    response = client.get(delivered["download_url"])

    assert response.status_code == 200
    assert response.content == on_disk.read_bytes()
    assert hashlib.sha256(response.content).hexdigest() == delivered["sha256"]
    assert response.headers["Content-Type"] == (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert response.headers["Content-Disposition"] == (
        f"attachment; filename*=UTF-8''{delivered['name']}"
    )
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Cache-Control"] == "no-store"
    assert os.stat(on_disk).st_mode & 0o222 == 0  # marked read-only
    downloaded = pd.read_excel(on_disk)
    assert len(downloaded) == 4


def test_the_file_list_shows_uploads_as_not_downloadable_and_deliveries(
    settings: Settings,
) -> None:
    client, session_id, turn, _ = _delivered_turn(settings)

    files = client.get(f"/api/v1/sessions/{session_id}/files").json()["files"]

    upload_entry, delivered_entry = files
    assert upload_entry["kind"] == "upload" and upload_entry["downloadable"] is False
    assert delivered_entry == {
        "file_id": turn["delivered_files"][0]["file_id"],
        "name": turn["delivered_files"][0]["name"],
        "size": turn["delivered_files"][0]["size"],
        "kind": "delivered",
        "downloadable": True,
        "uploaded_at": None,
        "turn_id": turn["turn_id"],
        "delivered_at": turn["finished_at"],
    }


def test_an_upload_can_never_be_downloaded(settings: Settings) -> None:
    client, session_id, _, _ = _delivered_turn(settings)
    upload_id = client.get(f"/api/v1/sessions/{session_id}").json()["files"][0]["file_id"]

    response = client.get(f"/api/v1/sessions/{session_id}/files/{upload_id}/download")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "file_not_found"


def test_another_session_cant_download_or_list_a_delivered_file(settings: Settings) -> None:
    client, session_id, turn, _ = _delivered_turn(settings)
    other = new_session(client)
    file_id = turn["delivered_files"][0]["file_id"]

    assert client.get(f"/api/v1/sessions/{other}/files/{file_id}/download").status_code == 404
    assert client.get(f"/api/v1/sessions/{other}/files").json()["files"] == []
    assert client.get(f"/api/v1/sessions/s_nope/files/{file_id}/download").status_code == 404


@pytest.mark.parametrize(
    "file_id",
    ["d_t0001_2", "d_t0002_1", "..%2F..%2Fuploads", "report.xlsx", "x" * 65, "d_t0001_1.."],
)
def test_ids_that_arent_delivered_files_are_404(settings: Settings, file_id: str) -> None:
    client, session_id, _, _ = _delivered_turn(settings)

    response = client.get(f"/api/v1/sessions/{session_id}/files/{file_id}/download")

    assert response.status_code == 404


def test_a_failed_turns_file_and_a_chains_intermediate_file_are_unreachable(
    settings: Settings,
) -> None:
    factory = PipelineFactory(
        QueuedProvider(chain(*DEDUPE_THEN_EMPTY)), wrap=raise_in("inspector", after=True)
    )
    client = TestClient(create_app(settings, factory))
    session_id = new_session(client)
    upload(client, session_id, xlsx_bytes())
    turn_id = start_turn(client, session_id, "remove duplicates then empty rows").json()["turn_id"]
    turn = wait_finished(client, session_id, turn_id)
    written = list((Path(factory.runs[0].turn_output_dir or "")).iterdir())

    assert turn["outcome"] == "failed" and len(written) == 2  # final and intermediate on disk
    for n in (1, 2, 3):
        response = client.get(f"/api/v1/sessions/{session_id}/files/d_{turn_id}_{n}/download")
        assert response.status_code == 404
    kinds = [f["kind"] for f in client.get(f"/api/v1/sessions/{session_id}/files").json()["files"]]
    assert kinds == ["upload"]


def test_a_delivered_file_that_changed_is_410(settings: Settings) -> None:
    client, session_id, turn, factory = _delivered_turn(settings)
    on_disk = Path(factory.runs[0].outputs[0])
    on_disk.chmod(0o644)
    on_disk.write_bytes(on_disk.read_bytes() + b"tampered")

    response = client.get(turn["delivered_files"][0]["download_url"])

    assert response.status_code == 410
    assert response.json()["error"]["code"] == "file_unavailable"


def test_a_delivered_file_that_is_gone_is_410(settings: Settings) -> None:
    client, session_id, turn, factory = _delivered_turn(settings)
    Path(factory.runs[0].outputs[0]).unlink()

    response = client.get(turn["delivered_files"][0]["download_url"])

    assert response.status_code == 410


def test_a_delivered_file_replaced_by_a_symlink_is_410(settings: Settings, tmp_path: Path) -> None:
    client, session_id, turn, factory = _delivered_turn(settings)
    on_disk = Path(factory.runs[0].outputs[0])
    copy_of = tmp_path / "copy.xlsx"
    shutil.copy(on_disk, copy_of)
    on_disk.unlink()
    on_disk.symlink_to(copy_of)

    response = client.get(turn["delivered_files"][0]["download_url"])

    assert response.status_code == 410

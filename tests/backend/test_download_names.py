"""What a person downloads a delivered file as: no internal ids, and no two the same.

A skill writes ``report_deduplicated_<session>-task-1.xlsx`` so two files never share a
path on disk.
The session id and task number are internal; the download response names the file
``report_deduplicated.xlsx`` and the file on disk keeps its unique name.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from backend.main import create_app
from backend.names import display_names, download_name
from backend.sessions import DeliveredFile
from backend.settings import Settings
from tests.backend.factories import (
    DEDUPE_THEN_EMPTY,
    PipelineFactory,
    QueuedProvider,
    chain,
    single,
)
from tests.backend.helpers import new_session, start_turn, upload, wait_finished, xlsx_bytes

KEY = "a1b2c3d4e5f6"


def _delivered(file_id: str, name: str) -> DeliveredFile:
    return DeliveredFile(
        file_id=file_id,
        name=name,
        size=1,
        sha256="0" * 64,
        path=Path("/x") / name,
        turn_id="t0001",
        delivered_at=datetime(2026, 10, 9, tzinfo=UTC),
    )


@pytest.mark.parametrize(
    ("stored", "shown"),
    [
        (f"report_deduplicated_{KEY}-task-1.xlsx", "report_deduplicated.xlsx"),
        (f"report_no_empty_rows_{KEY}-task-1.xlsx", "report_cleaned.xlsx"),
        # A chain's file carries one id per step.
        (
            f"report_deduplicated_{KEY}-task-1_no_empty_rows_{KEY}-task-2.xlsx",
            "report_deduplicated_cleaned.xlsx",
        ),
        (f"my data_deduplicated_{KEY}-task-12.xlsx", "my data_deduplicated.xlsx"),
        # Not a skill's name: left as it is.
        ("report.xlsx", "report.xlsx"),
        ("report_deduplicated_other-task-1.xlsx", "report_deduplicated_other-task-1.xlsx"),
    ],
)
def test_the_internal_ids_are_dropped_from_the_download_name(stored: str, shown: str) -> None:
    assert download_name(stored, KEY) == shown


@pytest.mark.parametrize(
    ("stored", "shown"),
    [
        # The person's own words are kept, even when they look like a skill's.
        (
            f"my_no_empty_rows_data_no_empty_rows_{KEY}-task-1.xlsx",
            "my_no_empty_rows_data_cleaned.xlsx",
        ),
        (
            f"deduplicated_report_deduplicated_{KEY}-task-1.xlsx",
            "deduplicated_report_deduplicated.xlsx",
        ),
        # An id in a shape no skill writes today is still dropped.
        (f"report_somethingnew_{KEY}-task-3.xlsx", "report_somethingnew.xlsx"),
    ],
)
def test_only_the_generated_part_of_the_name_is_rewritten(stored: str, shown: str) -> None:
    assert download_name(stored, KEY) == shown


def test_a_long_name_is_cut_in_its_stem_so_the_operation_still_shows() -> None:
    stem = "x" * 95
    first = download_name(f"{stem}_deduplicated_{KEY}-task-1.xlsx", KEY)
    chain = download_name(f"{stem}_deduplicated_{KEY}-task-1_no_empty_rows_{KEY}-task-2.xlsx", KEY)

    assert first.endswith("_deduplicated.xlsx") and len(first) <= 100
    assert chain.endswith("_deduplicated_cleaned.xlsx") and len(chain) <= 100
    assert first != chain


def test_names_that_differ_only_in_case_are_numbered_too() -> None:
    files = [
        _delivered("d1", f"Report_deduplicated_{KEY}-task-1.xlsx"),
        _delivered("d2", f"report_deduplicated_{KEY}-task-1.xlsx"),
    ]

    assert display_names(KEY, files) == {
        "d1": "Report_deduplicated.xlsx",
        "d2": "report_deduplicated (2).xlsx",
    }


def test_a_name_never_contains_the_session_key_or_a_task_number() -> None:
    for stored in (
        f"a_deduplicated_{KEY}-task-1.xlsx",
        f"a_deduplicated_{KEY}-task-1_no_empty_rows_{KEY}-task-2.xlsx",
    ):
        shown = download_name(stored, KEY)
        assert KEY not in shown and "task" not in shown


def test_two_files_with_the_same_name_are_numbered_in_the_order_delivered() -> None:
    files = [
        _delivered("d1", f"report_deduplicated_{KEY}-task-1.xlsx"),
        _delivered("d2", f"report_deduplicated_{KEY}-task-1.xlsx"),
        _delivered("d3", f"report_no_empty_rows_{KEY}-task-1.xlsx"),
        _delivered("d4", f"report_deduplicated_{KEY}-task-1.xlsx"),
    ]

    names = display_names(KEY, files)

    assert names == {
        "d1": "report_deduplicated.xlsx",
        "d2": "report_deduplicated (2).xlsx",
        "d3": "report_cleaned.xlsx",
        "d4": "report_deduplicated (3).xlsx",
    }
    assert len(set(n.casefold() for n in names.values())) == len(names)
    # The same files give the same names, whenever they are asked.
    assert display_names(KEY, files) == names


def _client(settings: Settings, replies: list[str]) -> tuple[TestClient, PipelineFactory]:
    factory = PipelineFactory(QueuedProvider(replies))
    return TestClient(create_app(settings, factory)), factory


def _turn(client: TestClient, session_id: str, message: str) -> dict[str, Any]:
    turn_id = start_turn(client, session_id, message).json()["turn_id"]
    return wait_finished(client, session_id, turn_id)


def test_the_download_header_and_the_lists_use_the_friendly_name(settings: Settings) -> None:
    client, factory = _client(settings, single("remove_duplicate_rows"))
    session_id = new_session(client)
    assert upload(client, session_id, xlsx_bytes()).status_code == 201

    turn = _turn(client, session_id, "remove duplicate rows")

    (delivered,) = turn["delivered_files"]
    on_disk = Path(factory.runs[0].outputs[0])
    assert on_disk.name != "report_deduplicated.xlsx" and "-task-" in on_disk.name
    assert delivered["name"] == "report_deduplicated.xlsx"
    response = client.get(delivered["download_url"])
    assert response.headers["Content-Disposition"] == (
        "attachment; filename*=UTF-8''report_deduplicated.xlsx"
    )
    assert response.content == on_disk.read_bytes()  # the very file, only the name differs
    listed = client.get(f"/api/v1/sessions/{session_id}/files").json()["files"]
    assert [f["name"] for f in listed if f["kind"] == "delivered"] == ["report_deduplicated.xlsx"]
    state = client.get(f"/api/v1/sessions/{session_id}").json()
    assert state["turns"][0]["delivered_files"][0]["name"] == "report_deduplicated.xlsx"
    assert (
        client.get(f"/api/v1/sessions/{session_id}/turns/{turn['turn_id']}").json()[
            "delivered_files"
        ][0]["name"]
        == "report_deduplicated.xlsx"
    )


def test_two_downloads_in_one_session_do_not_collide(settings: Settings) -> None:
    client, factory = _client(
        settings, single("remove_duplicate_rows") + single("remove_duplicate_rows")
    )
    session_id = new_session(client)
    assert upload(client, session_id, xlsx_bytes()).status_code == 201

    first = _turn(client, session_id, "remove duplicate rows")["delivered_files"][0]
    second = _turn(client, session_id, "remove duplicate rows again")["delivered_files"][0]

    assert (first["name"], second["name"]) == (
        "report_deduplicated.xlsx",
        "report_deduplicated (2).xlsx",
    )
    header = client.get(second["download_url"]).headers["Content-Disposition"]
    assert (
        header == f"attachment; filename*=UTF-8''{quote('report_deduplicated (2).xlsx', safe='')}"
    )
    # Two different files on disk, each downloadable under its own name.
    assert factory.runs[0].outputs != factory.runs[1].outputs
    assert (
        client.get(first["download_url"])
        .headers["Content-Disposition"]
        .endswith("report_deduplicated.xlsx")
    )
    names = [f["name"] for f in client.get(f"/api/v1/sessions/{session_id}/files").json()["files"]]
    assert names.count("report_deduplicated.xlsx") == 1


def test_a_chain_is_downloaded_as_deduplicated_then_cleaned(settings: Settings) -> None:
    client, _ = _client(settings, chain(*DEDUPE_THEN_EMPTY))
    session_id = new_session(client)
    assert upload(client, session_id, xlsx_bytes()).status_code == 201

    turn = _turn(client, session_id, "remove duplicate rows then remove empty rows")

    (delivered,) = turn["delivered_files"]
    assert delivered["name"] == "report_deduplicated_cleaned.xlsx"
    assert "task" not in client.get(delivered["download_url"]).headers["Content-Disposition"]

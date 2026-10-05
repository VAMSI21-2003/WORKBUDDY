"""Uploads: names, the size cap, the type and structure checks, the multi-sheet refusal.

Design Sections 1.2, 6.2 and 6.3, "Decisions (2026-09-30, part 2)" item 3,
"Decisions (2026-10-01)" item 6, and the owner's ``.xls`` decision
("Decisions (2026-10-04)").
"""

from __future__ import annotations

import hashlib
import io
import stat
import tempfile
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.errors import MESSAGES, XLS_MESSAGE, ErrorCode, file_too_large_message
from backend.main import create_app
from backend.settings import Settings
from backend.uploads import MAX_NAME_CHARS, check_workbook, sanitize_name
from tests.backend.helpers import (
    new_session,
    rezip,
    sheet_and_chartsheet,
    two_sheets,
    upload,
    xlsx_bytes,
    zip_bytes,
)


def _client(settings: Settings) -> TestClient:
    return TestClient(create_app(settings, lambda provider: None))  # type: ignore[arg-type,return-value]


@pytest.fixture
def client(settings: Settings) -> TestClient:
    return _client(settings)


@pytest.fixture
def temp_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point Python's temp directory (where multipart parts spool) at an empty folder."""
    folder = tmp_path / "tmp"
    folder.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(folder))
    return folder


def _files_under(root: Path) -> list[Path]:
    return [p for p in root.rglob("*")] if root.exists() else []


# -- Name sanitization (design Section 6.3) ---------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("report.xlsx", "report.xlsx"),
        ("../../x.xlsx", "x.xlsx"),
        ("..\\..\\x.xlsx", "x.xlsx"),
        ("a\\b.xlsx", "b.xlsx"),
        ("C:\\Users\\me\\report.xlsx", "report.xlsx"),
        ("re\x00port.xlsx", "report.xlsx"),
        ("evil\u202excod.xlsx", "evilxcod.xlsx"),
        ("x\u2066y\u2069.xlsx", "xy.xlsx"),
        ("zero\u200bwidth.xlsx", "zerowidth.xlsx"),
        ("Bericht über Q1.xlsx", "Bericht _ber Q1.xlsx"),
        ("ｒｅｐｏｒｔ.xlsx", "report.xlsx"),
        ("a<b>c|d?e*f:g\"h.xlsx", "a_b_c_d_e_f_g_h.xlsx"),
        ("...hidden.xlsx", "hidden.xlsx"),
        (" spaced .xlsx ", "spaced .xlsx"),
        ("report.xlsx.", "report.xlsx"),
        ("CON.xlsx", "_CON.xlsx"),
        ("nul.xlsx", "_nul.xlsx"),
        ("com1.report.xlsx", "_com1.report.xlsx"),
        ("lpt9.xlsx", "_lpt9.xlsx"),
        ("console.xlsx", "console.xlsx"),
        ("", None),
        ("...", None),
        ("../..", None),
        ("/", None),
        ("\x00\x01", None),
    ],
)
def test_sanitize_name(raw: str, expected: str | None) -> None:
    assert sanitize_name(raw) == expected


def test_a_long_name_is_capped_and_keeps_its_extension() -> None:
    name = sanitize_name("a" * 300 + ".xlsx")

    assert name is not None
    assert len(name) == MAX_NAME_CHARS
    assert name.endswith(".xlsx")


def test_every_sanitized_name_uses_only_safe_characters() -> None:
    hostile = ["../" * 50 + "x.xlsx", "\u202e" * 10 + ".xlsx", "😀😀.xlsx", "\t\n.xlsx", "ab/" * 99]
    for raw in hostile:
        name = sanitize_name(raw)
        if name is not None:
            assert all(c.isascii() and (c.isalnum() or c in "._ -") for c in name)
            assert not name.startswith(".")
            assert len(name) <= MAX_NAME_CHARS


# -- Accepted uploads -------------------------------------------------------------------


def test_an_upload_is_stored_once_read_only_and_becomes_active(
    client: TestClient, settings: Settings
) -> None:
    data = xlsx_bytes()
    session_id = new_session(client)

    response = upload(client, session_id, data, name="../../Quarterly Report.xlsx")

    assert response.status_code == 201
    body = response.json()
    assert body["file_id"].startswith("f_")
    assert body["name"] == "Quarterly Report.xlsx"
    assert body["size"] == len(data)
    assert body["sha256"] == hashlib.sha256(data).hexdigest()
    assert body["kind"] == "upload"
    stored = [p for p in (settings.uploads_dir).rglob("*") if p.is_file()]
    assert len(stored) == 1
    assert stored[0].name == "Quarterly Report.xlsx"
    assert stored[0].parent.name == body["file_id"]
    assert stored[0].read_bytes() == data
    assert stat.S_IMODE(stored[0].stat().st_mode) == 0o444
    state = client.get(f"/api/v1/sessions/{session_id}").json()
    assert state["active_file_id"] == body["file_id"]
    assert [f["file_id"] for f in state["files"]] == [body["file_id"]]


def test_two_uploads_with_the_same_name_dont_collide(
    client: TestClient, settings: Settings
) -> None:
    session_id = new_session(client)
    first = upload(client, session_id, xlsx_bytes()).json()
    second = upload(client, session_id, xlsx_bytes({"a": [1, 2]})).json()

    assert first["file_id"] != second["file_id"]
    stored = sorted(p for p in settings.uploads_dir.rglob("report.xlsx"))
    assert len(stored) == 2
    state = client.get(f"/api/v1/sessions/{session_id}").json()
    assert state["active_file_id"] == second["file_id"]


def test_an_upload_with_an_uppercase_extension_is_accepted(client: TestClient) -> None:
    response = upload(client, new_session(client), xlsx_bytes(), name="REPORT.XLSX")

    assert response.status_code == 201


def test_an_upload_to_an_unknown_session_is_404(client: TestClient) -> None:
    response = upload(client, "s_nope", xlsx_bytes())

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "session_not_found"


@pytest.mark.parametrize(
    "files",
    [
        [("file", ("a.xlsx", b"x")), ("file", ("b.xlsx", b"y"))],
        [("file", ("a.xlsx", b"x")), ("other", ("b.xlsx", b"y"))],
    ],
)
def test_more_than_one_file_part_is_refused(client: TestClient, files: list) -> None:  # type: ignore[type-arg]
    session_id = new_session(client)

    response = client.post(f"/api/v1/sessions/{session_id}/files", files=files)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"


def test_a_request_without_a_file_part_is_refused(client: TestClient) -> None:
    response = client.post(f"/api/v1/sessions/{new_session(client)}/files", data={"x": "1"})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"


# -- Size: the cap, at the boundary, and on the raw stream -----------------------------------


def test_an_upload_of_exactly_the_cap_is_accepted_and_one_byte_more_is_413(tmp_path: Path) -> None:
    data = xlsx_bytes()
    at_cap = _client(Settings(data_dir=tmp_path / "a", max_upload_bytes=len(data)))
    under_cap = _client(Settings(data_dir=tmp_path / "b", max_upload_bytes=len(data) - 1))

    assert upload(at_cap, new_session(at_cap), data).status_code == 201
    refused = upload(under_cap, new_session(under_cap), data)
    assert refused.status_code == 413
    assert refused.json() == {
        "error": {
            "code": "file_too_large",
            "message": file_too_large_message(len(data) - 1),
        }
    }
    assert _files_under(tmp_path / "b" / "uploads") == []


def test_the_413_message_names_the_configured_cap(client: TestClient, settings: Settings) -> None:
    big = b"PK\x03\x04" + b"\0" * (settings.max_upload_bytes + 1)

    response = upload(client, new_session(client), big)

    assert response.status_code == 413
    assert response.json()["error"]["message"] == "That file is larger than 2 MB."


def _multipart(payload_size: int) -> tuple[str, Iterator[bytes]]:
    boundary = "testboundary"
    head = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
        f"filename=\"big.xlsx\"\r\nContent-Type: application/octet-stream\r\n\r\n"
    ).encode()
    tail = f"\r\n--{boundary}--\r\n".encode()

    def chunks() -> Iterator[bytes]:
        yield head + b"PK\x03\x04"
        sent = 4
        while sent < payload_size:
            step = min(256 * 1024, payload_size - sent)
            yield b"\0" * step
            sent += step
        yield tail

    return f"multipart/form-data; boundary={boundary}", chunks()


def test_a_body_over_the_cap_without_content_length_is_cut_off(
    tmp_path: Path, temp_dir: Path
) -> None:
    settings = Settings(data_dir=tmp_path / "data", max_upload_bytes=1024 * 1024)
    client = _client(settings)
    content_type, body = _multipart(settings.max_body_bytes + 512 * 1024)

    response = client.post(
        f"/api/v1/sessions/{new_session(client)}/files",
        content=body,
        headers={"Content-Type": content_type},
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "file_too_large"
    assert _files_under(settings.uploads_dir) == []
    assert list(temp_dir.iterdir()) == []


def test_a_body_over_the_cap_with_a_false_content_length_is_cut_off(
    tmp_path: Path, temp_dir: Path
) -> None:
    settings = Settings(data_dir=tmp_path / "data", max_upload_bytes=1024 * 1024)
    client = _client(settings)
    content_type, body = _multipart(settings.max_body_bytes + 512 * 1024)

    response = client.post(
        f"/api/v1/sessions/{new_session(client)}/files",
        content=b"".join(body),
        headers={"Content-Type": content_type, "Content-Length": "1000"},
    )

    assert response.status_code == 413
    assert _files_under(settings.uploads_dir) == []
    assert list(temp_dir.iterdir()) == []


def test_a_declared_content_length_over_the_cap_is_refused_unread(
    client: TestClient, settings: Settings
) -> None:
    response = client.post(
        f"/api/v1/sessions/{new_session(client)}/files",
        content=b"x",
        headers={
            "Content-Type": "multipart/form-data; boundary=b",
            "Content-Length": str(settings.max_body_bytes + 1),
        },
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "file_too_large"


def test_an_oversized_body_on_another_route_is_request_too_large(
    client: TestClient, settings: Settings
) -> None:
    response = client.post(
        f"/api/v1/sessions/{new_session(client)}/turns",
        content=b"{" + b" " * (settings.max_body_bytes + 1) + b"}",
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 413
    assert response.json()["error"] == {
        "code": "request_too_large",
        "message": MESSAGES[ErrorCode.REQUEST_TOO_LARGE],
    }


# -- Type and structure ----------------------------------------------------------------


@pytest.mark.parametrize("name", ["report.xls", "REPORT.XLS", "../x.xls"])
def test_an_xls_upload_is_refused_with_its_fixed_message(client: TestClient, name: str) -> None:
    ole2 = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 504

    response = upload(client, new_session(client), ole2, name=name)

    assert response.status_code == 415
    assert response.json() == {"error": {"code": "unsupported_file_type", "message": XLS_MESSAGE}}


@pytest.mark.parametrize("name", ["report.csv", "report.xlsm", "report", "report.xlsx.exe", "..."])
def test_an_unlisted_extension_is_415(client: TestClient, name: str) -> None:
    response = upload(client, new_session(client), xlsx_bytes(), name=name)

    assert response.status_code == 415
    assert response.json()["error"] == {
        "code": "unsupported_file_type",
        "message": MESSAGES[ErrorCode.UNSUPPORTED_FILE_TYPE],
    }


def test_every_accepted_extension_passes_the_type_check(
    client: TestClient, settings: Settings
) -> None:
    for extension in settings.accepted_extensions:
        response = upload(client, new_session(client), xlsx_bytes(), name="r" + extension)
        assert response.status_code == 201


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"name,amount\nAlice,10\n",
        b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 504,
        b"PK\x03\x04 not really a zip",
        zip_bytes({"hello.txt": b"hi"}),
        zip_bytes({"[Content_Types].xml": b"<Types/>"}),
        zip_bytes({"xl/worksheets/sheet1.xml": b"<worksheet/>"}),
        zip_bytes({"[Content_Types].xml": b"<Types/>", "xl/chartsheets/sheet1.xml": b"<c/>"}),
    ],
    ids=["empty", "csv", "ole2", "fake-zip", "no-content-types", "no-worksheet",
         "worksheet-without-content-types", "chartsheet-only"],
)
def test_a_file_that_isnt_a_one_sheet_xlsx_is_422_invalid_file(
    client: TestClient, settings: Settings, data: bytes
) -> None:
    response = upload(client, new_session(client), data)

    assert response.status_code == 422
    assert response.json()["error"] == {
        "code": "invalid_file",
        "message": MESSAGES[ErrorCode.INVALID_FILE],
    }
    assert _files_under(settings.uploads_dir) == []


def test_too_many_zip_entries_is_invalid(tmp_path: Path) -> None:
    data = rezip(xlsx_bytes(), [(f"xl/media/{i}.bin", b"") for i in range(5)])
    path = tmp_path / "many.xlsx"
    path.write_bytes(data)
    entries = len(zipfile.ZipFile(path).infolist())

    check_workbook(path, Settings(data_dir=tmp_path, max_zip_entries=entries))
    with pytest.raises(Exception) as refused:
        check_workbook(path, Settings(data_dir=tmp_path, max_zip_entries=entries - 1))
    assert getattr(refused.value, "code", None) is ErrorCode.INVALID_FILE


def test_a_zip_bomb_is_refused_from_the_directory_alone(
    client: TestClient, settings: Settings
) -> None:
    # 210 MB of zeros declared, about 200 KB on disk; refused without decompressing.
    buffer = io.BytesIO()

    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", b"<Types/>")
        archive.writestr("xl/worksheets/sheet1.xml", b"<worksheet/>")
        with archive.open("xl/media/bomb.bin", "w", force_zip64=True) as part:
            chunk = b"\0" * (1024 * 1024)
            for _ in range(210):
                part.write(chunk)
    data = buffer.getvalue()
    assert len(data) < settings.max_upload_bytes

    response = upload(client, new_session(client), data)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_file"


# -- The multi-sheet refusal ("Decisions (2026-10-01)", item 6) ----------------------------


@pytest.mark.parametrize(
    "data",
    [
        two_sheets(),
        two_sheets(hidden="hidden"),
        two_sheets(hidden="veryHidden"),
        sheet_and_chartsheet(),
        rezip(xlsx_bytes(), [("XL/Worksheets/Sheet2.XML", b"<worksheet/>")]),
        rezip(xlsx_bytes(), [("xl/worksheets/sheet1.xml", b"<worksheet/>")]),
        rezip(xlsx_bytes(), [("xl/dialogsheets/sheet1.xml", b"<d/>")]),
        rezip(xlsx_bytes(), [("xl/macrosheets/sheet1.xml", b"<m/>")]),
    ],
    ids=["two", "hidden", "very-hidden", "chartsheet", "case", "repeated-name", "dialog", "macro"],
)
def test_a_workbook_with_more_than_one_sheet_is_refused(
    client: TestClient, settings: Settings, temp_dir: Path, data: bytes
) -> None:
    session_id = new_session(client)

    response = upload(client, session_id, data)

    assert response.status_code == 422
    assert response.json() == {
        "error": {"code": "multiple_sheets", "message": MESSAGES[ErrorCode.MULTIPLE_SHEETS]}
    }
    assert _files_under(settings.uploads_dir) == []
    assert list(temp_dir.iterdir()) == []
    state = client.get(f"/api/v1/sessions/{session_id}").json()
    assert state["files"] == [] and state["active_file_id"] is None


def test_a_one_sheet_workbook_is_accepted(client: TestClient) -> None:
    assert upload(client, new_session(client), xlsx_bytes()).status_code == 201


def test_an_upload_that_finishes_while_a_turn_started_is_409_and_kept_nowhere(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review L4: the busy check at registration, not only at the start."""
    from backend import routes
    from tests.backend.factories import BlockingFactory

    factory = BlockingFactory()
    client = TestClient(create_app(settings, factory))
    session_id = new_session(client)
    app = client.app
    real_store = routes.store_upload

    def store_then_a_turn_starts(*args, **kwargs):  # type: ignore[no-untyped-def]
        stored = real_store(*args, **kwargs)
        record = app.state.registry.get(session_id)  # type: ignore[attr-defined]
        app.state.runner.start(record, "remove duplicate rows", None, None)  # type: ignore[attr-defined]
        return stored

    monkeypatch.setattr(routes, "store_upload", store_then_a_turn_starts)
    response = upload(client, session_id, xlsx_bytes())

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "session_busy"
    state = client.get(f"/api/v1/sessions/{session_id}").json()
    assert state["files"] == [] and state["active_file_id"] is None
    assert [p for p in settings.uploads_dir.rglob("*") if p.is_file()] == []
    factory.release.set()

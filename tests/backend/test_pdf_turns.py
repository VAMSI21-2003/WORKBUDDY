"""PDFs through the API: upload, extract a table, then clean the extracted spreadsheet.

Every test runs the real app and the real pipeline (real skills, verifiers, sandboxed PDF worker
and speakers); only the LLM is faked. Every number in a reply is checked against an independent
read, with openpyxl, of the file the turn delivered, and the uploaded PDF's hash is checked after
each turn (rule 2).
"""

from __future__ import annotations

import hashlib
import io
import sys
from pathlib import Path
from typing import Any

import openpyxl
import pytest
from fastapi.testclient import TestClient

from backend.main import create_app
from backend.settings import Settings
from buddy.outcome import TABLE_TEXT_SENTENCE, VALUES_ONLY_SENTENCE
from buddy.voice import _COMPLETION_CLAIM
from tests import pdf_fixtures as fixtures
from tests.backend.factories import SPEAKERS, PipelineFactory, QueuedProvider, single
from tests.backend.helpers import upload, xlsx_bytes
from tests.backend.test_conversation_repair import brain, think
from tests.backend.test_follow_up_turns import Chat
from tests.file_sentences import worked_on_original, worked_on_original_skipping_copy

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="the sandbox is Linux-only (it fails closed elsewhere)",
)

FRUIT_PDF = "fruit.pdf"


@pytest.fixture
def make_chat(settings: Settings):  # type: ignore[no-untyped-def]
    def make(replies: list[str], speaker: str = "friend") -> Chat:
        factory = PipelineFactory(QueuedProvider(replies), speaker=speaker)
        client = TestClient(create_app(settings, factory))
        chat = Chat(client, factory, speaker)
        chat.settings = settings  # type: ignore[attr-defined]
        return chat

    return make


def pdf_bytes(tmp_path: Path, **kwargs: Any) -> bytes:
    return fixtures.write_table_pdf(tmp_path / "make.pdf", **kwargs).read_bytes()


def cells(data: bytes) -> list[list[object]]:
    book = openpyxl.load_workbook(io.BytesIO(data))
    return [[c.value for c in row] for row in book.worksheets[0].iter_rows()]


def sheet_facts(data: bytes) -> tuple[int, int]:
    grid = cells(data)
    return len(grid), max(len(row) for row in grid)


def upload_hashes(chat: Chat) -> dict[str, str]:
    root = chat.settings.uploads_dir  # type: ignore[attr-defined]
    return {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob("*") if p.is_file()
    }


# -- Upload ------------------------------------------------------------------------------------


def test_a_pdf_is_accepted_like_a_workbook(make_chat, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    chat = make_chat([])
    stored = chat.upload(pdf_bytes(tmp_path), FRUIT_PDF)
    assert stored["name"] == FRUIT_PDF
    session = chat.client.get(f"/api/v1/sessions/{chat.session_id}").json()
    assert session["limits"]["accepted_extensions"] == [".xlsx", ".pdf"]
    assert session["limits"]["max_upload_bytes"] == 2 * 1024 * 1024


def test_the_pdf_and_xlsx_name_are_checked_against_their_content(  # type: ignore[no-untyped-def]
    make_chat, tmp_path: Path
) -> None:
    chat = make_chat([])
    pdf = pdf_bytes(tmp_path)
    workbook = xlsx_bytes()
    for data, name in ((workbook, "x.pdf"), (pdf, "x.xlsx"), (b"", "x.pdf"), (b"%PDF", "x.pdf")):
        response = upload(chat.client, chat.session_id, data, name)
        assert response.status_code == 422, (name, response.text)
        assert response.json()["error"]["code"] == "invalid_file"
    assert list(chat.settings.uploads_dir.rglob("*.pdf")) == []  # type: ignore[attr-defined]
    assert list(chat.settings.uploads_dir.rglob("*.xlsx")) == []  # type: ignore[attr-defined]


def test_a_pdf_with_a_header_after_junk_is_refused(make_chat, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    chat = make_chat([])
    response = upload(chat.client, chat.session_id, b"\n" + pdf_bytes(tmp_path), "x.pdf")
    assert response.status_code == 422


def test_a_pdf_over_the_size_cap_is_refused(make_chat, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    chat = make_chat([])
    big = pdf_bytes(tmp_path) + b"\n%" + b"x" * (2 * 1024 * 1024)
    response = upload(chat.client, chat.session_id, big, "big.pdf")
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "file_too_large"


def test_twenty_pages_are_accepted_and_twenty_one_are_not(make_chat, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    chat = make_chat([])
    ok = fixtures.write_table_pdf(tmp_path / "20.pdf", text_pages_after=19).read_bytes()
    assert upload(chat.client, chat.session_id, ok, "twenty.pdf").status_code == 201
    too_many = fixtures.write_table_pdf(tmp_path / "21.pdf", text_pages_after=20).read_bytes()
    response = upload(chat.client, chat.session_id, too_many, "twenty-one.pdf")
    assert response.status_code == 422
    body = response.json()["error"]
    assert body["code"] == "invalid_file"
    assert body["message"] == "That PDF has more than 20 pages, which is the most it can have."
    names = {p.name for p in chat.settings.uploads_dir.rglob("*") if p.is_file()}  # type: ignore[attr-defined]
    assert "twenty-one.pdf" not in names


@pytest.mark.parametrize(
    ("make", "status"),
    [
        (lambda: fixtures.hostile_many_pages(30), 422),
        (fixtures.hostile_lying_page_count, 422),
        (lambda: b"%PDF-1.4\n" + b"\x00\xff" * 4000, 422),
        (fixtures.hostile_deep_nesting, 201),
        (fixtures.hostile_deep_page_tree, 201),
        (lambda: fixtures.build_pdf(fixtures._minimal_pages(), xref="huge"), 201),
        (lambda: fixtures.build_pdf(fixtures._minimal_pages(), xref="loop"), 201),
    ],
    ids=["30-pages", "lying-count", "garbage", "nested", "deep-tree", "huge-xref", "xref-loop"],
)
def test_hostile_pdfs_are_refused_at_upload_or_by_the_turn_and_nothing_is_handed_back(  # type: ignore[no-untyped-def]
    make_chat, make, status: int
) -> None:
    chat = make_chat(single("extract_pdf_table"))
    response = upload(chat.client, chat.session_id, make(), "bad.pdf")
    assert response.status_code == status, response.text
    stored = [p for p in chat.settings.uploads_dir.rglob("*") if p.is_file()]  # type: ignore[attr-defined]
    if status != 201:
        assert response.json()["error"]["code"] == "invalid_file"
        assert stored == []  # a refused upload leaves nothing behind
        return
    # Readable and short enough to be kept: the turn on it refuses, and hands back nothing.
    before = upload_hashes(chat)
    turn = chat.turn("extract the table")
    assert turn["outcome"] == "not_delivered" and turn["delivered_files"] == []
    assert "no file was handed back" in turn["reply"]
    assert _COMPLETION_CLAIM.search(turn["reply"]) is None, turn["reply"]
    assert upload_hashes(chat) == before


def test_an_encrypted_pdf_is_accepted_and_the_turn_says_it_is_encrypted(  # type: ignore[no-untyped-def]
    make_chat, tmp_path: Path
) -> None:
    chat = make_chat(single("extract_pdf_table"))
    chat.upload(pdf_bytes(tmp_path, password=("secret", "owner")), "locked.pdf")
    before = upload_hashes(chat)
    turn = chat.turn("extract the table")
    assert turn["outcome"] == "not_delivered" and turn["delivered_files"] == []
    assert "encrypted" in turn["reply"]
    assert "no file was handed back" in turn["reply"]
    assert upload_hashes(chat) == before


# -- Extract, then clean the extracted spreadsheet -----------------------------------------------


@pytest.mark.parametrize("speaker", SPEAKERS)
def test_extract_then_remove_the_duplicates_from_the_extracted_spreadsheet(  # type: ignore[no-untyped-def]
    make_chat, tmp_path: Path, speaker: str
) -> None:
    chat = make_chat(single("extract_pdf_table") + single("remove_duplicate_rows"), speaker)
    chat.upload(pdf_bytes(tmp_path), FRUIT_PDF)
    before = upload_hashes(chat)

    first = chat.turn("extract the table from this pdf")
    assert first["outcome"] == "delivered", first["reply"]
    (table,) = first["delivered_files"]
    assert table["name"] == "fruit_table.xlsx"
    assert cells(chat.download(table)) == fixtures.FRUIT
    rows, columns = sheet_facts(chat.download(table))
    # Counted the way the cleaning replies count: the first row, then the rows below it.
    assert f"{columns} columns, a first row and {rows - 1} rows below it" in first["reply"]
    assert VALUES_ONLY_SENTENCE in first["reply"] and TABLE_TEXT_SENTENCE in first["reply"]
    assert worked_on_original(FRUIT_PDF) in first["reply"]
    assert "cleaned copy" not in first["reply"]
    assert "pdf.extract_table" not in first["reply"]
    assert chat.state()["next_request_file"]["name"] == "fruit_table.xlsx"
    assert upload_hashes(chat) == before

    second = chat.turn("now remove the duplicate rows")
    assert second["outcome"] == "delivered", second["reply"]
    (cleaned,) = second["delivered_files"]
    grid = cells(chat.download(cleaned))
    assert grid == [row for i, row in enumerate(fixtures.FRUIT) if i != 3]  # the second Apple row
    assert "emoved 1 exact duplicate row" in second["reply"]
    assert "The copy has 3 rows" in second["reply"]  # 4 below the first row, 1 removed: adds up
    assert "the latest copy I handed back" in second["reply"]
    assert "`fruit_table.xlsx`" in second["reply"]
    assert upload_hashes(chat) == before


def test_the_plan_the_person_approves_names_the_pdf_and_no_capability_id(  # type: ignore[no-untyped-def]
    make_chat, tmp_path: Path
) -> None:
    chat = make_chat(single("extract_pdf_table"))
    chat.upload(pdf_bytes(tmp_path), FRUIT_PDF)
    from tests.backend.helpers import start_turn, wait_finished

    plans: list[dict[str, Any]] = []
    started = start_turn(chat.client, chat.session_id, "extract the table")
    wait_finished(chat.client, chat.session_id, started.json()["turn_id"], plans=plans)
    (plan,) = plans
    (step,) = plan["steps"]
    assert step == {
        "capability": "pdf.extract_table",
        "prompt": "Extract the table from fruit.pdf.",
    }


def test_declining_the_plan_extracts_nothing(make_chat, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    chat = make_chat(single("extract_pdf_table"))
    chat.upload(pdf_bytes(tmp_path), FRUIT_PDF)
    turn = chat.turn("extract the table", decision="reject")
    assert turn["outcome"] == "not_delivered" and turn["delivered_files"] == []
    assert _COMPLETION_CLAIM.search(turn["reply"]) is None, turn["reply"]


@pytest.mark.parametrize(
    ("kwargs", "words"),
    [
        ({"spans": [((0, 1), (1, 1))]}, "merged or spanning"),
        ({"password": ("x", "y")}, "encrypted"),
    ],
    ids=["merged", "encrypted"],
)
def test_refusals_hand_back_nothing_and_say_why(  # type: ignore[no-untyped-def]
    make_chat, tmp_path: Path, kwargs: dict[str, Any], words: str
) -> None:
    chat = make_chat(single("extract_pdf_table"))
    chat.upload(pdf_bytes(tmp_path, **kwargs), FRUIT_PDF)
    before = upload_hashes(chat)
    turn = chat.turn("extract the table")
    assert turn["outcome"] == "not_delivered" and turn["delivered_files"] == []
    assert words in turn["reply"]
    assert "no file was handed back" in turn["reply"]
    assert "your original file is unchanged" in turn["reply"]
    assert _COMPLETION_CLAIM.search(turn["reply"]) is None, turn["reply"]
    assert upload_hashes(chat) == before


# -- File settling: a PDF is a file of the session like a workbook -------------------------------


def test_a_pdf_named_with_a_slip_is_matched(make_chat, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    chat = make_chat([brain("extract_pdf_table", phrase="extract the table"), think()])
    chat.upload(pdf_bytes(tmp_path), FRUIT_PDF)
    chat.upload(xlsx_bytes(), "other.xlsx")  # the active upload
    turn = chat.turn("extract the table from frut.pdf")
    assert turn["outcome"] == "delivered", turn["reply"]
    assert worked_on_original(FRUIT_PDF) in turn["reply"]


def test_an_unknown_pdf_name_asks_which_file_and_the_answer_resumes_the_request(  # type: ignore[no-untyped-def]
    make_chat, tmp_path: Path
) -> None:
    chat = make_chat(
        [
            brain("extract_pdf_table", phrase="extract the table"),
            think(),
            brain("extract_pdf_table", phrase="extract the table", answers_pending=True),
            think(),
        ]
    )
    chat.upload(pdf_bytes(tmp_path), FRUIT_PDF)
    chat.upload(xlsx_bytes(), "other.xlsx")

    asked = chat.turn("extract the table from sales.pdf")
    assert asked["outcome"] == "not_delivered" and asked["delivered_files"] == []

    resumed = chat.turn(FRUIT_PDF)
    assert resumed["outcome"] == "delivered", resumed["reply"]
    (table,) = resumed["delivered_files"]
    assert cells(chat.download(table)) == fixtures.FRUIT
    assert worked_on_original(FRUIT_PDF) in resumed["reply"]


def test_a_follow_up_naming_the_extracted_file_works_on_it(make_chat, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    chat = make_chat(
        [
            brain("extract_pdf_table", phrase="extract the table"),
            think(),
            brain("remove_duplicate_rows"),
            think(),
        ]
    )
    chat.upload(pdf_bytes(tmp_path), FRUIT_PDF)
    chat.upload(xlsx_bytes(), "other.xlsx")  # now the active upload
    first = chat.turn("extract the table from fruit.pdf")
    (table,) = first["delivered_files"]
    second = chat.turn(f"remove the duplicate rows from {table['name']}")
    assert second["outcome"] == "delivered", second["reply"]
    assert f"`{table['name']}`" in second["reply"]
    (cleaned,) = second["delivered_files"]
    assert len(cells(chat.download(cleaned))) == 4


def test_start_over_goes_back_to_the_pdf_not_the_extracted_table(make_chat, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    chat = make_chat(single("extract_pdf_table") + single("extract_pdf_table", "original"))
    chat.upload(pdf_bytes(tmp_path), FRUIT_PDF)
    chat.turn("extract the table")
    again = chat.turn("start over from my original file and extract the table")
    assert again["outcome"] == "delivered", again["reply"]
    assert worked_on_original_skipping_copy(FRUIT_PDF) in again["reply"]


def test_asking_again_for_the_table_reads_the_pdf_not_the_spreadsheet_made_from_it(  # type: ignore[no-untyped-def]
    make_chat, tmp_path: Path
) -> None:
    """The copy handed back from a PDF is a spreadsheet no PDF skill can read."""
    chat = make_chat(single("extract_pdf_table") + single("extract_pdf_table"))
    chat.upload(pdf_bytes(tmp_path), FRUIT_PDF)
    first = chat.turn("extract the table")
    second = chat.turn("extract the table from fruit.pdf once more")

    assert second["outcome"] == "delivered", second["reply"]
    assert worked_on_original_skipping_copy(FRUIT_PDF) in second["reply"]
    (table,) = second["delivered_files"]
    assert cells(chat.download(table)) == fixtures.FRUIT
    assert table["file_id"] != first["delivered_files"][0]["file_id"]
    assert table["name"] == "fruit_table (2).xlsx"

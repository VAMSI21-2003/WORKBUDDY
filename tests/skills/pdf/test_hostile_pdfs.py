"""Hostile PDFs: every one ends in a refusal, never a crash of the server and never a hang.

Each file is written byte by byte by ``tests/pdf_fixtures.py`` (a page tree 20,000 levels deep, a
stream that inflates to 1.5 GiB, 25,000 pages, a cross-reference table that points nowhere, and so
on) and is read the way the product reads it: by the skill, through the sandboxed worker, in each
of the worker's three modes (the upload's page count, the extraction, the Inspector's second
reading). Beside each refusal the test checks the server is as it was: the next ordinary PDF is
read, and nothing was left running or written.
"""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from skills.base import PermissionTier, SkillStatus, SkillTask
from skills.pdf import PdfExtractTableSkill, sandbox
from skills.pdf.extract_table import REFUSALS
from tests import pdf_fixtures as fixtures

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="the sandbox is Linux-only (it fails closed elsewhere)",
)

# A refusal has to come well inside the deadline even on a slow machine; the bomb is the slowest.
PATIENCE = sandbox.TIMEOUT_SECONDS + 10


def _bomb() -> bytes:
    # About 1.5 MB of file that inflates to more than the worker's address space.
    return fixtures.hostile_compression_bomb(sandbox.ADDRESS_SPACE_BYTES + 512 * 1024 * 1024)


@pytest.fixture(scope="module")
def bomb(tmp_path_factory: pytest.TempPathFactory) -> bytes:
    path = tmp_path_factory.mktemp("bomb") / "bomb.pdf"
    path.write_bytes(_bomb())
    assert path.stat().st_size < 2 * 1024 * 1024, "the bomb has to fit the upload cap to be fair"
    return path.read_bytes()


CASES: dict[str, Callable[[], bytes]] = {
    "deeply nested array": fixtures.hostile_deep_nesting,
    "page tree 20000 levels deep": fixtures.hostile_deep_page_tree,
    "page tree that contains itself": fixtures.hostile_page_tree_cycle,
    "25000 pages": fixtures.hostile_many_pages,
    "page count that lies": fixtures.hostile_lying_page_count,
    "wrong xref offsets": lambda: fixtures.build_pdf(fixtures._minimal_pages(), xref="wrong"),
    "no xref at all": lambda: fixtures.build_pdf(fixtures._minimal_pages(), xref="none"),
    "xref with 99999999999 entries": lambda: fixtures.build_pdf(
        fixtures._minimal_pages(), xref="huge"
    ),
    "xref whose /Prev is itself": lambda: fixtures.build_pdf(
        fixtures._minimal_pages(), xref="loop"
    ),
    "header and garbage": lambda: b"%PDF-1.4\n" + b"\x00\xff" * 4000,
    "truncated after the header": lambda: b"%PDF-1.7\n1 0 obj\n<<",
    "only the header": lambda: b"%PDF-",
    "binary noise": lambda: bytes(range(256)) * 200,
}


def _task(pdf: Path, out: Path) -> SkillTask:
    return SkillTask(
        task_id="t-0",
        session_id="s",
        workspace_id="w",
        description="extract",
        input_references=[str(pdf)],
        permission=PermissionTier.WRITE,
        output_dir=str(out),
    )


def _assert_server_is_fine(tmp_path: Path) -> None:
    good = fixtures.write_table_pdf(tmp_path / "good.pdf")
    result = PdfExtractTableSkill().execute(_task(good, tmp_path / "good-out"))
    assert result.status is SkillStatus.SUCCESS, result.errors


def _nothing_left_behind(tmp_path: Path, out: Path) -> None:
    assert not out.exists() or list(out.iterdir()) == []
    # No stray pdfwork-* folders from the sandbox either.
    leftovers = [p for p in Path("/tmp").glob("pdfwork-*")] if Path("/tmp").exists() else []
    assert leftovers == [] or all(not p.exists() or not any(p.iterdir()) for p in leftovers)


@pytest.mark.parametrize("name", list(CASES))
def test_the_skill_refuses_a_hostile_pdf_inside_the_deadline(name: str, tmp_path: Path) -> None:
    pdf = tmp_path / "hostile.pdf"
    pdf.write_bytes(CASES[name]())
    started = time.monotonic()
    result = PdfExtractTableSkill().execute(_task(pdf, tmp_path / "out"))
    assert time.monotonic() - started < PATIENCE

    assert result.status is SkillStatus.FAILURE
    assert result.outputs == []
    assert len(result.errors) == 1 and result.errors[0] in REFUSALS.values()
    _nothing_left_behind(tmp_path, tmp_path / "out")
    _assert_server_is_fine(tmp_path)


@pytest.mark.parametrize("name", list(CASES))
@pytest.mark.parametrize("mode", ["probe", "extract", "verify"])
def test_every_worker_mode_ends_in_an_answer_or_a_refusal(
    name: str, mode: str, tmp_path: Path
) -> None:
    started = time.monotonic()
    outcome = sandbox.run(mode, CASES[name]())
    assert time.monotonic() - started < PATIENCE
    assert outcome.ok or outcome.reason in sandbox.REASONS
    if outcome.ok:
        # An answer is only ever small, plain data.
        assert isinstance(outcome.data, dict)
    _assert_server_is_fine(tmp_path)


def test_the_compression_bomb_is_refused_not_inflated(bomb: bytes, tmp_path: Path) -> None:
    pdf = tmp_path / "bomb.pdf"
    pdf.write_bytes(bomb)
    started = time.monotonic()
    result = PdfExtractTableSkill().execute(_task(pdf, tmp_path / "out"))
    assert time.monotonic() - started < PATIENCE
    assert result.status is SkillStatus.FAILURE and result.outputs == []
    assert result.errors[0] in REFUSALS.values()
    _assert_server_is_fine(tmp_path)


@pytest.mark.parametrize("mode", ["probe", "extract", "verify"])
def test_the_compression_bomb_is_refused_in_every_mode_and_never_reaches_the_limit_unchecked(
    bomb: bytes, mode: str
) -> None:
    outcome = sandbox.run(mode, bomb)
    # "probe" never decodes the page, so it may simply answer; the others must not succeed
    # with a table, and none may report more memory than the limit allows.
    if mode != "probe":
        assert not outcome.ok
    if outcome.vm_peak_kb is not None:
        assert outcome.vm_peak_kb * 1024 <= sandbox.ADDRESS_SPACE_BYTES


def test_a_small_address_space_stops_a_smaller_bomb_at_once(tmp_path: Path) -> None:
    pdf = fixtures.hostile_compression_bomb(300 * 1024 * 1024)
    started = time.monotonic()
    outcome = sandbox.run("extract", pdf, address_space=256 * 1024 * 1024)
    assert time.monotonic() - started < PATIENCE
    assert not outcome.ok and outcome.reason in {"too_large", "crashed", "unreadable", "no_text"}


def test_the_real_worker_is_killed_at_a_deadline_it_cannot_meet(tmp_path: Path) -> None:
    pdf = fixtures.write_table_pdf(tmp_path / "a.pdf").read_bytes()
    outcome = sandbox.run("extract", pdf, timeout_s=0.01)
    assert (outcome.ok, outcome.reason) == (False, sandbox.TIMEOUT)


def test_more_than_twenty_real_pages_are_counted_not_parsed() -> None:
    outcome = sandbox.run("probe", fixtures.hostile_many_pages(30))
    assert outcome.ok and outcome.data["pages"] == 30
    refused = sandbox.run("extract", fixtures.hostile_many_pages(30))
    assert (refused.ok, refused.reason) == (False, "too_many_pages")
    refused = sandbox.run("verify", fixtures.hostile_many_pages(30))
    assert (refused.ok, refused.reason) == (False, "too_many_pages")


def test_active_content_in_a_pdf_is_never_followed(tmp_path: Path) -> None:
    """JavaScript that submits a form, a /Launch, a URI link and an attachment: all ignored.

    A listener on localhost would hear a connection if anything followed the JavaScript or the
    link; the worker has no socket to make one with, and the attachment is never written.
    """
    import socket

    listener = socket.socket()
    listener.bind(("127.0.0.1", 9))  # the port the file's JavaScript and link name
    listener.listen(1)
    listener.settimeout(0.3)
    try:
        before = set(os.listdir(tmp_path))
        pdf = fixtures.hand_table_pdf(fixtures.FRUIT, active_content=True)
        extracted = sandbox.run("extract", pdf)
        verified = sandbox.run("verify", pdf)
        assert extracted.ok and verified.ok
        assert extracted.data["rows"] == verified.data["rows"] == fixtures.FRUIT
        with pytest.raises(socket.timeout):
            listener.accept()
        assert set(os.listdir(tmp_path)) == before
    except PermissionError:
        pytest.skip("port 9 can't be bound here")
    finally:
        listener.close()

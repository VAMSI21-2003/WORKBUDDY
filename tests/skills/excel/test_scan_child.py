"""The check of what a workbook needs runs in a child process under a memory cap and a time limit.

Review round 4 found that lists of caps by part name or element kind are bypassed (R4-1, R4-2),
so ``check_workbook_limits`` runs the readers the skills use on the file in a child
(``workbook._scan_in_child``) and refuses what needs more than ``SCAN_MEMORY_BYTES`` or
``SCAN_SECONDS``, or whose check crashed. These tests use real workbooks with the stdlib XML
parsers, as in the production image (which has no lxml), so a hostile part costs what it costs
there.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path

import pandas as pd
import pytest

from skills.excel import workbook
from skills.excel.workbook import (
    CHECK_BUSY,
    CHECK_STOPPED,
    SCAN_MEMORY_BYTES,
    SCAN_SECONDS,
    TOO_COSTLY,
    WorkbookRefused,
    check_workbook_limits,
)
from tests.skills.excel.test_hostile_workbooks import (
    _MAIN,
    _REL,
    _book,
    _ordinary,
    _replaced,
    _sheet_of,
)

pytestmark = pytest.mark.real_scan_child


@pytest.fixture(autouse=True)
def production_parsers(monkeypatch: pytest.MonkeyPatch) -> None:
    """No lxml, as in the image, and no verdict left over from another test."""
    monkeypatch.setenv("OPENPYXL_LXML", "False")
    workbook._scanned.clear()


def _refusal(path: Path) -> str:
    with pytest.raises(WorkbookRefused) as refused:
        check_workbook_limits(str(path))
    return str(refused.value)


def _workbook_part(extra: str) -> str:
    return (
        f'<?xml version="1.0"?><workbook xmlns="{_MAIN}" xmlns:r="{_REL}">'
        f'<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets>{extra}</workbook>'
    )


# -- what the child refuses: the files that got past every list of caps ----------------------


def test_the_limits_are_the_ones_the_owner_set_to_start_with() -> None:
    assert SCAN_MEMORY_BYTES == 512 * 1024 * 1024
    assert SCAN_SECONDS == 60


def test_a_workbook_part_under_another_name_is_refused_by_what_it_needs(
    tmp_path: Path,
) -> None:
    """R4-1 (P1): 1.4 million defined names, found by openpyxl through the content types."""
    names = '<definedName name="n">S1!$A$1</definedName>' * 1_400_000
    path = _book(tmp_path / "names.xlsx")
    with zipfile.ZipFile(path) as source:
        kept = {i.filename: source.read(i.filename) for i in source.infolist()}
    types = kept["[Content_Types].xml"].decode().replace("/xl/workbook.xml", "/xl/book.xml")
    rels = kept["_rels/.rels"].decode().replace("/xl/workbook.xml", "/xl/book.xml")
    kept.update({"[Content_Types].xml": types, "_rels/.rels": rels})
    kept["xl/book.xml"] = _workbook_part(f"<definedNames>{names}</definedNames>").encode()
    del kept["xl/workbook.xml"]
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as target:
        for name, data in kept.items():
            target.writestr(name, data)
    assert path.stat().st_size < 300_000

    assert _refusal(path) == TOO_COSTLY


def test_a_relationships_part_of_a_million_entries_is_refused_by_what_it_needs(
    tmp_path: Path,
) -> None:
    """R4-2 (P4): a part no list of caps named, parsed whole by openpyxl on every load."""
    rels = '<Relationship Id="x" Type="x" Target="x"/>' * 1_300_000
    path = _book(tmp_path / "rels.xlsx")
    with zipfile.ZipFile(path) as source:
        old = source.read("xl/_rels/workbook.xml.rels").decode()
    path = _replaced(path, rels=old.replace("</Relationships>", rels + "</Relationships>"))
    assert path.stat().st_size < 300_000

    assert _refusal(path) == TOO_COSTLY


def test_a_styles_part_just_under_the_old_cap_is_refused_by_what_it_needs(
    tmp_path: Path,
) -> None:
    """R4-2 (P7b): 1.6 million cell formats in 7.6 MiB, under the cap round 3 set."""
    styles = (
        f'<styleSheet xmlns="{_MAIN}"><fonts count="1"><font/></fonts><fills count="1"><fill/>'
        '</fills><borders count="1"><border/></borders><cellStyleXfs count="1"><xf/>'
        '</cellStyleXfs><cellXfs count="1600000">' + "<xf/>" * 1_600_000 + "</cellXfs></styleSheet>"
    )
    path = _replaced(_book(tmp_path / "styles.xlsx"), styles=styles)
    assert zipfile.ZipFile(path).getinfo("xl/styles.xml").file_size < 8 * 1024 * 1024

    assert _refusal(path) == TOO_COSTLY


def test_a_content_types_part_over_sixteen_mib_is_refused_at_once_as_before(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R4-2 (P3): main refused this part by its declared size before openpyxl saw it; so does
    this, without starting a child."""
    monkeypatch.setattr(
        workbook, "_scan_in_child", lambda _: pytest.fail("a child was started for it")
    )
    path = _book(tmp_path / "types.xlsx")
    with zipfile.ZipFile(path) as source:
        types = source.read("[Content_Types].xml").decode()
    extra = '<Override PartName="/a" ContentType="b"/>' * 450_000
    big = types.replace("</Types>", extra + "</Types>")
    assert len(big) > 16 * 1024 * 1024
    path = _replaced(path, **{"[Content_Types].xml": big})
    started = time.monotonic()

    assert "could not read the workbook" in _refusal(path)
    assert time.monotonic() - started < 5


def test_an_ordinary_workbook_passes_the_child(tmp_path: Path) -> None:
    check_workbook_limits(str(_ordinary(tmp_path / "ordinary.xlsx")))


# -- the child reads the file the way the skills do -------------------------------------------


def test_the_child_builds_the_tables_the_analysis_builds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The analysis holds a default read and a ``dtype=object`` read at once: so does the child."""
    calls: list[dict[str, object]] = []
    real = pd.read_excel

    def recording(path: object, **kwargs: object) -> pd.DataFrame:
        calls.append(kwargs)
        return real(path, **kwargs)

    listed: list[str] = []
    real_names = workbook.sheet_names
    monkeypatch.setattr(
        workbook, "sheet_names", lambda path: listed.append(path) or real_names(path)
    )
    monkeypatch.setattr(pd, "read_excel", recording)
    monkeypatch.setattr(workbook, "_in_scan_child", False)
    path = _ordinary(tmp_path / "ordinary.xlsx")

    assert workbook._scan_in_this_process(str(path)) == "PASS"
    assert {} in calls and {"dtype": object} in calls
    assert listed, "the sheet listing the planner reads was not made"


def test_running_out_of_memory_in_any_reader_is_a_refusal_not_a_file_that_cannot_be_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every catch-all that reads "error" as "can't be read" lets a file through; a MemoryError
    must come out of each of them."""
    import openpyxl

    path = _ordinary(tmp_path / "ordinary.xlsx")

    def starved(*args: object, **kwargs: object) -> None:
        raise MemoryError

    monkeypatch.setattr(openpyxl, "load_workbook", starved)
    monkeypatch.setattr(workbook, "_in_scan_child", True)
    for call in (
        lambda: workbook._reader_verdict(str(path)),
        lambda: workbook.sheet_names(str(path)),
        lambda: workbook._sheet_problem(str(path), "Sheet1"),
        lambda: workbook.formula_cell_counts(str(path)),
    ):
        with pytest.raises(MemoryError):
            call()
    costly = workbook._CHILD_REFUSALS.index(TOO_COSTLY)
    assert workbook._scan_in_this_process(str(path)) == f"REFUSE:{costly}"


# -- the child's limits -----------------------------------------------------------------------


def test_the_parents_limits_are_the_ones_the_child_checks_against(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(workbook, "MAX_ROWS", 3)
    path = _book(tmp_path / "rows.xlsx", _sheet_of("<row><c><v>1</v></c></row>" * 4))

    assert _refusal(path) == workbook.TOO_MANY_ROWS  # the child counted 4 rows against 3


def test_a_check_that_runs_past_its_time_is_a_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(workbook, "SCAN_SECONDS", 0.05)

    assert _refusal(_ordinary(tmp_path / "slow.xlsx")) == TOO_COSTLY


def test_a_check_whose_child_cannot_get_its_memory_is_a_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(workbook, "SCAN_MEMORY_BYTES", 120 * 1024 * 1024)  # below an empty import

    assert _refusal(_ordinary(tmp_path / "ok.xlsx")) in (TOO_COSTLY, CHECK_STOPPED)


@pytest.mark.parametrize(
    "child",
    [
        "import os; os.abort()",  # crashes
        "import sys; sys.exit(3)",  # ends with a status
        "print('PASS and some more')",  # an answer the parent doesn't know
        "print('REFUSE:99')",  # an index it doesn't have
        "print('REFUSE:SECRET-FROM-THE-FILE')",  # text is never taken from the child
        "",  # no answer
        "import sys; print('PASS'); sys.exit(3)",  # an answer, then a bad end
    ],
    ids=["abort", "exit", "unknown-answer", "bad-index", "text", "silence", "pass-then-fail"],
)
def test_a_crash_or_an_answer_the_parent_does_not_know_is_a_refusal_never_a_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, child: str
) -> None:
    monkeypatch.setattr(workbook, "_CHILD_CODE", child)

    message = _refusal(_ordinary(tmp_path / "ok.xlsx"))

    assert message == CHECK_STOPPED
    assert "SECRET" not in message


def test_a_child_that_cannot_be_started_is_refused_and_not_kept_as_the_files_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _ordinary(tmp_path / "ok.xlsx")

    def cannot_start(*args: object, **kwargs: object) -> None:
        raise OSError("cannot fork")

    with monkeypatch.context() as broken:
        broken.setattr(subprocess, "run", cannot_start)
        assert _refusal(path) == CHECK_STOPPED
    check_workbook_limits(str(path))  # the file itself was never judged


def test_the_child_sets_its_own_limits_and_oom_score_before_it_reads_anything(
    tmp_path: Path,
) -> None:
    if not Path("/proc/self/oom_score_adj").exists():
        pytest.skip("no /proc")
    cap = 700 * 1024 * 1024
    done = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            workbook._CHILD_PREAMBLE
            + "print(resource.getrlimit(resource.RLIMIT_AS)[0]);"
            + "print(open('/proc/self/oom_score_adj').read().strip())",
            "unused",
            str(cap),
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    assert done.stdout.split() == [str(cap), "1000"]


def test_where_there_is_no_resource_module_the_child_still_runs_and_the_time_limit_holds() -> None:
    """Windows has none: the memory cap is the production container's concern, and a developer's
    machine must not refuse every file because its child could not set one."""
    done = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            "import sys; sys.modules['resource'] = None\n"
            + workbook._CHILD_PREAMBLE
            + "print('ran')",
            "unused",
            str(SCAN_MEMORY_BYTES),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert done.returncode == 0 and done.stdout.strip() == "ran", done.stderr


# -- one check at a time ----------------------------------------------------------------------


def test_a_second_check_waits_briefly_then_says_the_server_is_busy_and_keeps_no_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _ordinary(tmp_path / "ok.xlsx")
    monkeypatch.setattr(workbook, "SCAN_WAIT_SECONDS", 0.1)
    assert workbook._scan_slot.acquire(timeout=1)
    try:
        started = time.monotonic()
        assert _refusal(path) == CHECK_BUSY
        assert 0.05 < time.monotonic() - started < 5
    finally:
        workbook._scan_slot.release()

    check_workbook_limits(str(path))  # busy was not a verdict on the file


def test_only_one_check_runs_at_a_time_and_a_file_is_checked_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    running = peak = calls = 0
    lock = threading.Lock()

    def slow_scan(_path: str) -> None:
        nonlocal running, peak, calls
        with lock:
            running += 1
            calls += 1
            peak = max(peak, running)
        time.sleep(0.2)
        with lock:
            running -= 1

    monkeypatch.setattr(workbook, "_scan", slow_scan)
    monkeypatch.setattr(workbook, "SCAN_WAIT_SECONDS", 30)
    same = _ordinary(tmp_path / "same.xlsx")
    other = _ordinary(tmp_path / "other.xlsx", rows=60)
    threads = [
        threading.Thread(target=check_workbook_limits, args=(str(path),))
        for path in (same, same, same, other)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert peak == 1
    assert calls == 2  # one for each file: the waiting threads used the first one's verdict

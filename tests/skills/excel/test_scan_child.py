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
    CHECK_NOT_RUN,
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
    monkeypatch.setattr(workbook, "_xml_reader_mode", lambda: "False")
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
    assert SCAN_SECONDS == 30
    assert workbook.MAX_CELLS == 250_000


def test_a_check_waits_for_the_one_running_check_as_long_as_it_can_take_plus_a_margin() -> None:
    """R5-2: an ordinary overlap of two users must not be "busy"."""
    assert workbook.SCAN_WAIT_SECONDS >= SCAN_SECONDS + 10


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
        assert _refusal(path) == CHECK_NOT_RUN
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


# -- review round 5 ----------------------------------------------------------------------------


def _formula_sheet(rows: int, refs: int, *, masters: bool) -> bytes:
    """Column A holds shared formulas of ``refs`` references; column B an id, so rows differ.

    ``masters=False``: one master in A2 and a 40-byte dependent on every other row (S1's shape,
    which costs time on every pass that parses formulas). ``masters=True``: every row is its own
    master, a tokenized copy of which openpyxl keeps for the whole sheet (S4's shape, memory).
    """
    formula = "+".join(f"B{i}" for i in range(1, refs + 1))
    head = (
        '<row r="1"><c r="A1" t="inlineStr"><is><t>total</t></is></c>'
        '<c r="B1" t="inlineStr"><is><t>id</t></is></c></row>'
    )
    body = [head]
    for row in range(2, rows + 2):
        if masters:
            cell = f'<f t="shared" ref="A{row}:A{row}" si="{row}">{formula}</f>'
        elif row == 2:
            cell = f'<f t="shared" ref="A2:A{rows + 1}" si="0">{formula}</f>'
        else:
            cell = '<f t="shared" si="0"/>'
        body.append(
            f'<row r="{row}"><c r="A{row}">{cell}<v>1</v></c><c r="B{row}"><v>{row}</v></c></row>'
        )
    return _sheet_of("".join(body))


def test_shared_formula_masters_that_the_inspectors_formula_count_would_keep_are_refused(
    tmp_path: Path,
) -> None:
    """R5-1 (S4): 2,000 masters of 4,000 references took 1.8 GiB of the server in the formula
    count, a load the child's other reads skip (they don't parse formulas)."""
    path = _book(tmp_path / "masters.xlsx", _formula_sheet(2_000, 4_000, masters=True))
    assert path.stat().st_size < 1_000_000

    assert _refusal(path) == TOO_COSTLY


def test_a_shared_formula_over_many_rows_that_takes_minutes_to_count_is_refused_in_the_time_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R5-1 (S1): one formula of a length Excel allows over 50,000 rows passed the check in 8 s
    and took the formula count 286 s. The time limit is lowered so the test is quick; the walk
    and the reads alone take under it."""
    monkeypatch.setattr(workbook, "SCAN_SECONDS", 15)
    path = _book(tmp_path / "formula.xlsx", _formula_sheet(50_000, 1_500, masters=False))

    assert _refusal(path) == TOO_COSTLY


def test_a_short_shared_formula_over_many_rows_is_an_ordinary_workbook(tmp_path: Path) -> None:
    check_workbook_limits(
        str(_book(tmp_path / "short.xlsx", _formula_sheet(20_000, 2, masters=False)))
    )


def test_the_child_counts_the_formulas_the_inspector_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    called: list[str] = []
    real = workbook.formula_cell_counts
    monkeypatch.setattr(
        workbook, "formula_cell_counts", lambda path: called.append(path) or real(path)
    )
    path = _ordinary(tmp_path / "ordinary.xlsx")

    assert workbook._scan_in_this_process(str(path)) == "PASS"
    assert called == [str(path)]
    workbook._in_scan_child = False


def test_running_out_of_memory_in_the_childs_table_reads_refuses_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R5-3: pandas is where a big table runs out; the read's catch-all must not take it for a
    file that can't be read."""

    def starved(*args: object, **kwargs: object) -> None:
        raise MemoryError

    monkeypatch.setattr(pd, "read_excel", starved)
    path = _ordinary(tmp_path / "ordinary.xlsx")
    costly = workbook._CHILD_REFUSALS.index(TOO_COSTLY)

    assert workbook._scan_in_this_process(str(path)) == f"REFUSE:{costly}"
    workbook._in_scan_child = False


def test_a_memory_error_in_the_formula_count_refuses_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def starved(path: str) -> None:
        raise MemoryError

    monkeypatch.setattr(workbook, "formula_cell_counts", starved)
    costly = workbook._CHILD_REFUSALS.index(TOO_COSTLY)

    assert (
        workbook._scan_in_this_process(str(_ordinary(tmp_path / "o.xlsx"))) == f"REFUSE:{costly}"
    )
    workbook._in_scan_child = False


# -- what a verdict keeps (R5-4) ---------------------------------------------------------------


def _count_children(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    runs: list[int] = []
    real = subprocess.run

    def counting(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        runs.append(1)
        return real(*args, **kwargs)  # type: ignore[call-overload, no-any-return]

    monkeypatch.setattr(subprocess, "run", counting)
    return runs


def test_a_pass_and_a_limit_the_file_passes_are_kept_so_the_file_is_checked_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runs = _count_children(monkeypatch)
    ordinary = _ordinary(tmp_path / "ordinary.xlsx")
    rows = _book(tmp_path / "rows.xlsx", _sheet_of("<row><c><v>1</v></c></row>" * 60))
    for _ in range(2):
        check_workbook_limits(str(ordinary))
    monkeypatch.setattr(workbook, "MAX_ROWS", 55)  # the ordinary file has 51 rows

    for _ in range(2):
        assert _refusal(rows) == workbook.TOO_MANY_ROWS

    assert len(runs) == 2  # one child for each file


@pytest.mark.parametrize(
    ("child", "expected"),
    [
        ("import os; os.abort()", CHECK_STOPPED),  # a crash
        (f"print('REFUSE:{workbook._CHILD_REFUSALS.index(TOO_COSTLY)}')", TOO_COSTLY),  # memory
    ],
    ids=["crash", "memory-error-answer"],
)
def test_a_crash_or_a_memory_error_is_not_kept_as_the_files_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, child: str, expected: str
) -> None:
    path = _ordinary(tmp_path / "ok.xlsx")
    with monkeypatch.context() as broken:
        broken.setattr(workbook, "_CHILD_CODE", child)
        assert _refusal(path) == expected
        assert _refusal(path) == expected
    check_workbook_limits(str(path))  # an idle server checks it again, and it passes


def test_a_timeout_is_not_kept_as_the_files_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _ordinary(tmp_path / "ok.xlsx")
    with monkeypatch.context() as slow:
        slow.setattr(workbook, "SCAN_SECONDS", 0.05)
        assert _refusal(path) == TOO_COSTLY
    check_workbook_limits(str(path))


# -- what the child is given (R5-5) ------------------------------------------------------------


def test_the_child_gets_the_path_the_locale_and_the_thread_variables_and_nothing_else(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "gsk_NOT-A-REAL-KEY")
    monkeypatch.setenv("WORKBUDDY_OPERATOR_TOKEN", "NOT-A-REAL-TOKEN")
    monkeypatch.setenv("LC_ALL", "C.UTF-8")
    monkeypatch.setenv("LANG", "C.UTF-8")
    monkeypatch.setenv("OPENPYXL_LXML", "True")  # the server's own setting is not passed on
    seen: list[dict[str, str]] = []
    real = subprocess.run

    def recording(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        seen.append(dict(kwargs["env"]))  # type: ignore[arg-type, call-overload]
        return real(*args, **kwargs)  # type: ignore[call-overload, no-any-return]

    monkeypatch.setattr(subprocess, "run", recording)
    check_workbook_limits(str(_ordinary(tmp_path / "ok.xlsx")))

    (env,) = seen
    allowed = {
        "PATH", "LANG", "LANGUAGE", "SYSTEMROOT", "OPENPYXL_LXML",
        "OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS",
    }  # fmt: skip
    assert all(name in allowed or name.startswith("LC_") for name in env), sorted(env)
    assert env["LC_ALL"] == "C.UTF-8" and "PATH" in env
    assert all(env[name] == "1" for name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS"))
    assert "NOT-A-REAL" not in " ".join(env.values())


def test_a_child_that_cannot_be_started_says_the_check_could_not_be_run() -> None:
    assert "I couldn't run the check of the workbook" in CHECK_NOT_RUN
    assert CHECK_NOT_RUN != CHECK_STOPPED


@pytest.mark.parametrize("lxml_in_use", [True, False])
def test_the_child_parses_with_the_same_xml_parsers_as_the_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, lxml_in_use: bool
) -> None:
    """What the child measures is only what the server's reads will cost if both use the same
    parsers: lxml is far lighter than the standard library's. The setting is derived from what the
    server is using, not copied from its environment."""
    import openpyxl.xml

    monkeypatch.undo()  # the autouse fixture's stand-in for the mode is not what is tested
    monkeypatch.setattr(openpyxl.xml, "LXML", lxml_in_use)
    seen: list[str] = []
    real = subprocess.run

    def recording(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        seen.append(kwargs["env"]["OPENPYXL_LXML"])  # type: ignore[index, call-overload]
        return real(*args, **kwargs)  # type: ignore[call-overload, no-any-return]

    monkeypatch.setattr(subprocess, "run", recording)
    workbook._scanned.clear()
    check_workbook_limits(str(_ordinary(tmp_path / "ok.xlsx")))

    assert seen == ["True" if lxml_in_use else "False"]

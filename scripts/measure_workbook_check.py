"""Measure what the workbook check costs, on the files that got past every list of caps.

The check (``skills.excel.workbook.check_workbook_limits``) runs the readers the skills use on an
upload in a child process, under ``SCAN_MEMORY_BYTES`` and ``SCAN_SECONDS``. Those two numbers
(512 MiB and 30 s) were chosen, not measured: this script is how they get measured, in the
production image, on the production host. Until it has been run there they are unmeasured.

Run it on the VM, inside the image, under the same memory limit as the app
(``deploy/docker-compose.yml`` gives it ``mem_limit: 1g``). The image holds only ``backend``,
``buddy``, ``core`` and ``skills``, so
bind-mount this repository's ``scripts`` folder::

    docker build -t workbuddy .
    docker run --rm --memory 1g --memory-swap 1g \\
        -v "$PWD/scripts:/app/scripts:ro" --entrypoint python workbuddy \\
        scripts/measure_workbook_check.py

``--memory-swap 1g`` matters: with ``--memory`` alone Docker lets the container use as much swap
again, and the figures would look better than the app's would. Options: ``--only P1,M9`` runs
only the files whose names start with those prefixes; ``--list`` prints the names.

Each file is checked in a fresh process, as a turn's first check would be. For each it prints:

- ``verdict``: ``PASS`` or the fixed sentence the user would be told;
- ``check s``: wall-clock seconds the check took (limit: ``SCAN_SECONDS``);
- ``child MiB``: the peak resident memory of the check's child process (cap:
  ``SCAN_MEMORY_BYTES`` of address space, which is always more than resident memory);
- ``server MiB``: the peak resident memory of this process, which stands for the server, after
  the check and, for a file that passed, after it also read the file the way the analysis does
  (a default read and a ``dtype=object`` read held at once). The server's own memory without
  any workbook is on the ``baseline`` line.

The server and one child must fit in the container together: the worst case is the app's usual
resident memory (``docker stats`` on the running app) plus the largest ``child MiB`` among the
files that should pass, and plus the largest ``child MiB`` among the files that are refused too,
because a refused file's child runs until it hits its cap. The files in ``SHOULD_PASS``
(ordinary ones, and the at-cap ones) must say ``PASS`` and finish well inside the time limit; the
others must be refused. At the end it prints the container's memory limit and how many times the
kernel killed a process in it (``oom_kill``): the check's child is meant to be the one, if any.

The bomb files are the ones review round 4 found; they are built by the review harness under
``scripts/review_harness/fix-security-pass/`` and written to a temporary folder.
"""

from __future__ import annotations

import argparse
import json
import resource
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
HARNESS = HERE / "review_harness" / "fix-security-pass"

# Files that should pass, with what they are; every other file should be refused. The check's
# limits are 250,000 cells and 30 s: ``M9c`` is the ordinary file at the cell cap; ``M9`` (a million
# cells) is refused by the cell count, and the formula files S1 to S5 by the time or memory they
# take to read (round 5). M5 passes because openpyxl is told to ignore external links.
SHOULD_PASS = {
    "P0_ordinary_control": "50 rows",
    "M0_ordinary_control": "50 rows",
    "M9c_dense_250k_cells": "249,990 numeric cells, at the cell cap",
    "S0_shared_formula_short": "20,000 rows of a short shared formula, as a real sheet has",
    "P11_proto_cap_ascii": "text at the weighted cap, ASCII",
    "P11b_proto_cap_emoji": "text at the weighted cap, emoji",
    "P9_huge_unused_string": "one 60-million-character string no cell uses",
    "M5_ext_link_2_3m_cells": "an external link caching 2.3 million cells, never read",
    "M6_short_names_10000": "10,000 rows of short names",
    "M6_names_2000x1000": "2,000 rows of 1,000-character names",
}


def _root() -> Path:
    """The folder holding ``skills``: the repository, or ``/app`` in the image."""
    for candidate in (HERE.parent, Path.cwd(), Path("/app")):
        if (candidate / "skills" / "excel" / "workbook.py").exists():
            return candidate
    raise SystemExit("run this from the repository or the image's /app (no skills/ folder found)")


def _cases() -> dict[str, object]:
    sys.path.insert(0, str(HARNESS))
    import probe_m_round3 as m
    import probe_p_round4 as p
    import probe_s_round5 as r5

    cases = {name: make for name, make in p.CASES.items()}
    for name, make in m.CASES.items():
        cases[name] = make
    cases["M9c_dense_250k_cells"] = lambda path: m.m9_dense_at_cap(path, rows=24_999, cols=10)
    for name, spec in r5.CASES.items():
        masters = bool(spec.get("masters"))
        arguments = {key: value for key, value in spec.items() if key != "masters"}
        build = r5.many_masters_sheet if masters else r5.shared_formula_sheet
        cases[name] = lambda path, build=build, arguments=arguments: m.package(
            path, build(**arguments)
        )
    return cases


def _mib(kib: int) -> float:
    return kib / 1024


def _cgroup(name: str) -> str | None:
    for base in ("/sys/fs/cgroup", "/sys/fs/cgroup/memory"):
        path = Path(base) / name
        if path.exists():
            return path.read_text().strip()
    return None


def _oom_kills() -> int | None:
    events = _cgroup("memory.events")
    if events is None:
        return None
    for line in events.splitlines():
        if line.startswith("oom_kill "):
            return int(line.split()[1])
    return None


def _peak_kib() -> int:
    """This process's peak resident memory in KiB.

    ``VmHWM`` and not ``ru_maxrss``: the kernel starts a new process's ``ru_maxrss`` at the
    parent's size when it was forked, which would put the runner's own peak in every figure.
    """
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmHWM:"):
                return int(line.split()[1])
    except OSError:
        pass
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss


def _measure_one(path: str) -> None:
    """Child mode: check one file in this fresh process and print one JSON line."""
    sys.path.insert(0, str(_root()))
    from skills.excel import workbook

    baseline = _peak_kib()
    started = time.monotonic()
    try:
        workbook.check_workbook_limits(path)
        verdict = "PASS"
    except workbook.WorkbookRefused as refused:
        verdict = str(refused)
    seconds = time.monotonic() - started
    after_check = _peak_kib()
    child = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    read = None
    if verdict == "PASS":
        import pandas as pd

        try:
            frames = (pd.read_excel(path), pd.read_excel(path, dtype=object))
            del frames
        except Exception as exc:  # noqa: BLE001 — reported, not raised: this is a measurement
            read = f"read failed: {type(exc).__name__}"
        else:
            read = _peak_kib()
    print(
        json.dumps(
            {
                "baseline": baseline,
                "after_check": after_check,
                "child": child,
                "seconds": seconds,
                "verdict": verdict,
                "read": read,
            }
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--only", default="", help="names, or name prefixes like P1,M9")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--one", help="(internal) check this file in this process")
    args = parser.parse_args()
    if args.one:
        _measure_one(args.one)
        return

    cases = _cases()
    if args.list:
        print("\n".join(cases))
        return
    wanted = [item for item in args.only.split(",") if item]
    root = _root()
    sys.path.insert(0, str(root))
    from skills.excel import workbook

    print(
        "memory limit of this container: "
        f"{_cgroup('memory.max') or _cgroup('memory.limit_in_bytes')}"
    )
    print(
        f"check limits: {workbook.SCAN_MEMORY_BYTES // 2**20} MiB address space, "
        f"{workbook.SCAN_SECONDS} s, {workbook.SCAN_WAIT_SECONDS} s wait for the one running check"
    )
    from openpyxl.xml import LXML

    print(
        "openpyxl is using lxml here: the image has none, so figures differ "
        "(set OPENPYXL_LXML=False)."
        if LXML
        else "openpyxl is using the standard-library XML parsers, as in the image."
    )
    kills_before = _oom_kills()
    print(
        f"{'file':32s} {'KB':>7s} {'check s':>8s} {'child MiB':>10s} {'server MiB':>11s}  verdict"
    )
    ok = True
    with tempfile.TemporaryDirectory() as folder:
        for name, make in cases.items():
            if wanted and not any(name.startswith(prefix) for prefix in wanted):
                continue
            path = Path(folder) / f"{name}.xlsx"
            make(path)  # type: ignore[operator]
            started = time.monotonic()
            done = subprocess.run(
                [sys.executable, str(Path(__file__).resolve()), "--one", str(path)],
                capture_output=True,
                text=True,
                check=False,
                timeout=workbook.SCAN_SECONDS + 600,
            )
            try:
                result = json.loads(done.stdout.strip().splitlines()[-1])
            except (ValueError, IndexError):
                print(
                    f"{name:32s} measuring failed (status {done.returncode}): {done.stderr[-300:]}"
                )
                ok = False
                path.unlink(missing_ok=True)
                continue
            server = result["read"] if isinstance(result["read"], int) else result["after_check"]
            verdict = result["verdict"]
            expected_pass = name in SHOULD_PASS
            if (verdict == "PASS") != expected_pass:
                ok = False
                verdict = f"UNEXPECTED {verdict}"
            note = result["read"] if isinstance(result["read"], str) else ""
            print(
                f"{name:32s} {path.stat().st_size / 1024:7.0f} {result['seconds']:8.1f} "
                f"{_mib(result['child']):10.0f} {_mib(server):11.0f}  {verdict[:70]} {note}"
                f"  (wall {time.monotonic() - started:.0f} s)",
                flush=True,
            )
            path.unlink(missing_ok=True)
    baseline = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            "import sys; sys.path.insert(0, sys.argv[1]); from skills.excel import workbook;"
            "print([l.split()[1] for l in open('/proc/self/status') if l.startswith('VmHWM')][0])",
            str(root),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if baseline.stdout.strip().isdigit():
        print(
            "baseline: a process that has imported the skills uses "
            f"{_mib(int(baseline.stdout)):.0f} MiB"
        )
    kills_after = _oom_kills()
    if kills_before is not None and kills_after is not None:
        print(
            "processes killed by the kernel for memory during this run: "
            f"{kills_after - kills_before}"
        )
    print("every file behaved as expected" if ok else "SOME FILES DID NOT BEHAVE AS EXPECTED")


if __name__ == "__main__":
    main()

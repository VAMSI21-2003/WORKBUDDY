"""Fail on any ruff error beyond the committed baseline of the known ones.

    python scripts/check_ruff_baseline.py            # CI runs this: exit 1 if there's a new error
    python scripts/check_ruff_baseline.py --update   # rewrite ruff-baseline.txt (see below)

``CLAUDE.md``: "There are known lint errors already in the tree. Don't add new ones." The baseline
(``ruff-baseline.txt``, next to ``pyproject.toml``) is those known errors, counted per file and rule
(``buddy/voice.py E501 3``), not by line, so editing a file doesn't move them. An error is new when
its file has more errors of that rule than the baseline allows. Fewer than the baseline is fine, and
the report says so: run ``--update`` in the change that fixed them, so the fix can't come back.

The paths are the ones ``CLAUDE.md`` names for the lint command (``buddy tests``); ruff is the one
in the active environment (``requirements-dev.lock`` pins it), because another ruff version can
report different errors.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / "ruff-baseline.txt"
PATHS = ("buddy", "tests")
HEADER = """\
# The known ruff errors (ruff check buddy tests), counted per file and rule: file, rule, count.
# CI fails on any error beyond these. Regenerate with:
#     python scripts/check_ruff_baseline.py --update
# Do that only to record errors that were fixed; never to accept new ones.
"""

Counts = Counter[tuple[str, str]]


def run_ruff() -> list[dict[str, object]]:
    done = subprocess.run(
        [sys.executable, "-m", "ruff", "check", *PATHS, "--output-format=json", "--no-cache"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    # Exit 1 means "found errors"; anything else is ruff itself failing.
    if done.returncode not in (0, 1):
        print(done.stderr or done.stdout)
        raise SystemExit(f"ruff failed to run (exit {done.returncode})")
    return list(json.loads(done.stdout or "[]"))


def relative(finding: dict[str, object]) -> str:
    return Path(str(finding["filename"])).resolve().relative_to(ROOT).as_posix()


def count(findings: list[dict[str, object]]) -> Counts:
    counts: Counts = Counter()
    for finding in findings:
        counts[(relative(finding), str(finding["code"]))] += 1
    return counts


def read_baseline(path: Path = BASELINE) -> Counts:
    counts: Counts = Counter()
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) != 3 or not parts[2].isdigit():
            raise SystemExit(f"{path.name}:{number}: expected 'file rule count', got {line!r}")
        counts[(parts[0], parts[1])] = int(parts[2])
    return counts


def write_baseline(counts: Counts, path: Path = BASELINE) -> None:
    lines = [f"{file} {rule} {n}" for (file, rule), n in sorted(counts.items())]
    path.write_text(HEADER + "\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def compare(baseline: Counts, current: Counts) -> tuple[list[str], list[str]]:
    """(new errors, improvements), each as a line of text."""
    new = [
        f"{file}: {n - baseline.get((file, rule), 0)} new {rule} "
        f"(now {n}, the baseline allows {baseline.get((file, rule), 0)})"
        for (file, rule), n in sorted(current.items())
        if n > baseline.get((file, rule), 0)
    ]
    better = [
        f"{file}: {rule} is down from {n} to {current.get((file, rule), 0)}"
        for (file, rule), n in sorted(baseline.items())
        if current.get((file, rule), 0) < n
    ]
    return new, better


def main(argv: list[str]) -> int:
    if argv not in ([], ["--update"]):
        print(__doc__)
        return 2
    findings = run_ruff()
    current = count(findings)
    if argv:
        write_baseline(current)
        print(f"wrote {BASELINE.name}: {sum(current.values())} known errors")
        return 0
    new, better = compare(read_baseline(), current)
    for line in better:
        print(f"improved: {line}")
    if better:
        print("Run python scripts/check_ruff_baseline.py --update to keep the improvement.")
    if new:
        print("New ruff errors beyond the baseline:")
        wanted = {line.split(":")[0] for line in new}
        for finding in findings:
            if relative(finding) in wanted:
                location = finding["location"]
                assert isinstance(location, dict)
                print(
                    f"  {relative(finding)}:{location['row']}: "
                    f"{finding['code']} {finding['message']}"
                )
        for line in new:
            print(f"NEW: {line}")
        return 1
    print(f"ruff: no errors beyond the baseline ({sum(current.values())} known).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

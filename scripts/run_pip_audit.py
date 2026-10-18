"""Fail on a known vulnerability in either lock file, except the ones listed with a reason.

    python scripts/run_pip_audit.py            # CI runs this: exit 1 on a vulnerability

pip-audit reads each lock file (hashes and all, nothing is installed) and asks the PyPI and OSV
advisory databases about every pinned version. ``pip-audit-ignores.toml`` lists the advisories
accepted for now. An entry without a reason or a review date, or whose review date has passed,
fails the run, so an ignore can't outlive the reason for it.
"""

from __future__ import annotations

import subprocess
import sys
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCKS = ("requirements-runtime.lock", "requirements-dev.lock")
IGNORES = ROOT / "pip-audit-ignores.toml"


@dataclass(frozen=True)
class Ignore:
    """One accepted advisory: its id and aliases, why, and the day it must be looked at again."""

    id: str
    aliases: tuple[str, ...]
    reason: str
    review_by: date


def read_ignores(path: Path = IGNORES, today: date | None = None) -> list[Ignore]:
    """The ignores in ``path``; ``SystemExit`` with the reason if one is incomplete or overdue."""
    today = today or date.today()
    entries = tomllib.loads(path.read_text(encoding="utf-8")).get("ignore", [])
    ignores: list[Ignore] = []
    for number, entry in enumerate(entries, start=1):
        name = f"{path.name} entry {number} ({entry.get('id', 'no id')})"
        if not str(entry.get("id", "")).strip():
            raise SystemExit(f"{name}: an ignore needs an id")
        if not str(entry.get("reason", "")).strip():
            raise SystemExit(f"{name}: an ignore needs a reason")
        review_by = entry.get("review_by")
        if not isinstance(review_by, date):
            raise SystemExit(f"{name}: an ignore needs a review_by date (YYYY-MM-DD)")
        if review_by < today:
            raise SystemExit(
                f"{name}: the review date {review_by} has passed. Read the advisory again, then "
                "fix the dependency and delete the entry, or write a new reason and a later date."
            )
        ignores.append(
            Ignore(
                id=str(entry["id"]).strip(),
                aliases=tuple(str(alias).strip() for alias in entry.get("aliases", [])),
                reason=str(entry["reason"]).strip(),
                review_by=review_by,
            )
        )
    return ignores


def command(lock: str, ignores: Sequence[Ignore]) -> list[str]:
    """The pip-audit command for one lock file."""
    args = [
        sys.executable,
        "-m",
        "pip_audit",
        "--requirement",
        lock,
        "--require-hashes",
        "--disable-pip",
        "--progress-spinner",
        "off",
    ]
    for ignore in ignores:
        for name in (ignore.id, *ignore.aliases):
            args += ["--ignore-vuln", name]
    return args


def main() -> int:
    ignores = read_ignores()
    for ignore in ignores:
        print(f"accepted until {ignore.review_by}: {ignore.id}: {ignore.reason}")
    failed = False
    for lock in LOCKS:
        print(f"== pip-audit {lock}", flush=True)
        done = subprocess.run(command(lock, ignores), cwd=ROOT, check=False)
        failed = failed or done.returncode != 0
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

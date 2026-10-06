# ruff: noqa: E501 — reviewer tooling, not product code
"""Round 2: one-line mutations of the M1 fix in ``backend/retention.remove_tree``.

    python scripts/review_harness/feat-backend-retention/r2_mutations.py > scripts/review_harness/feat-backend-retention/out/r2_mutations.txt

Each mutation runs in a fresh ``git archive HEAD`` copy (the checkout is never
touched) with ``pytest -q tests/backend/test_retention.py``. A mutation is
"killed" when at least one test fails.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
TARGET = "backend/retention.py"
GUARD = "        if stat.S_ISREG(mode) and not mode & stat.S_IWRITE:\n"
CHMOD = "            os.chmod(path, stat.S_IWRITE | stat.S_IREAD)\n"

MUTATIONS = {
    "no_chmod (round-1 code)": (CHMOD, "            pass\n"),
    "chmod_symlinks_too": (GUARD, "        if not mode & stat.S_IWRITE:\n"),
    "chmod_every_regular_file (equivalent)": (GUARD, "        if stat.S_ISREG(mode):\n"),
    "chmod_read_only_bit_kept": (CHMOD, "            os.chmod(path, stat.S_IREAD)\n"),
    "chmod_only_when_writable (inverted)": (GUARD, "        if stat.S_ISREG(mode) and mode & stat.S_IWRITE:\n"),
}


def run(name: str, old: str, new: str) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        archive = subprocess.run(["git", "-C", str(REPO), "archive", "HEAD"], capture_output=True, check=True).stdout
        subprocess.run(["tar", "-x", "-C", tmp], input=archive, check=True)
        target = Path(tmp) / TARGET
        text = target.read_text()
        assert text.count(old) == 1, name
        target.write_text(text.replace(old, new))
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/backend/test_retention.py"],
            cwd=tmp, capture_output=True, text=True, timeout=900,
        )
        failed = [ln for ln in proc.stdout.splitlines() if ln.startswith("FAILED")]
        verdict = "KILLED" if proc.returncode != 0 else "SURVIVED"
        return f"{verdict:8} {name}\n" + "".join(f"    {ln}\n" for ln in failed)


def main() -> None:
    for name, (old, new) in MUTATIONS.items():
        print(run(name, old, new), end="", flush=True)


if __name__ == "__main__":
    main()

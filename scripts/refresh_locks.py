"""Refresh the dependency lock files, on purpose.

    python scripts/refresh_locks.py       # both locks, everything re-resolved to the newest allowed
    python scripts/refresh_locks.py dev   # requirements-dev.lock only; the runtime lock (what the
                                          # image holds) stays exactly as it is

Nothing else ever changes a lock: building the image, running CI and installing for development all
read them and never resolve. After a refresh, read the diff, run the full suite, build the image,
and commit the locks in a change of their own (a change to the runtime lock changes what ships).

Needs ``uv`` (``pip install uv``). The locks are for Python 3.11, the image's version, and
``--universal``: one file that holds the hashes for every platform (Linux and Windows, x86-64 and
arm64), with the platform-only packages marked, so the same lock serves the server, CI and a
developer's machine.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ("requirements-runtime.txt", "requirements-runtime.lock")
DEV = ("requirements.txt", "requirements-dev.lock")

HEADER = """\
# {lock}: every package pinned to one exact version with its hashes, for Python 3.11.
# Generated from {source}; don't edit by hand. Refresh it on purpose with:
#     python scripts/refresh_locks.py{arg}
# {note}
"""
NOTES = {
    "requirements-runtime.lock": (
        "The Dockerfile installs from this file only (pip install --require-hashes --no-deps)."
    ),
    "requirements-dev.lock": (
        "CI and development install from this file. Every package it shares with\n"
        "# requirements-runtime.lock has the same version as there."
    ),
}


def uv_command() -> list[str]:
    found = shutil.which("uv")
    if found:
        return [found]
    return [sys.executable, "-m", "uv"]


def render(source: str, lock: str, body: str) -> str:
    """The lock's text: its header, then the resolved packages."""
    arg = "" if lock == RUNTIME[1] else " dev"
    return HEADER.format(lock=lock, source=source, arg=arg, note=NOTES[lock]) + "\n" + body


def compile_lock(source: str, lock: str, *, constraint: str | None) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        body = Path(tmp) / "body.txt"
        command = [
            *uv_command(), "pip", "compile", source,
            "--python-version", "3.11", "--universal", "--generate-hashes",
            "--no-header", "--no-annotate", "--upgrade", "-o", str(body),
        ]
        if constraint:
            command += ["-c", constraint]
        subprocess.run(command, cwd=ROOT, check=True)
        text = render(source, lock, body.read_text(encoding="utf-8"))
    (ROOT / lock).write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {lock}")


def main(argv: list[str]) -> int:
    only_dev = argv == ["dev"]
    if argv and not only_dev:
        print(__doc__)
        return 2
    if not only_dev:
        compile_lock(*RUNTIME, constraint=None)
    # The dev lock may not move a package the image ships: the runtime lock is a constraint on it.
    compile_lock(*DEV, constraint=RUNTIME[1])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

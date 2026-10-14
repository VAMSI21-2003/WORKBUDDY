"""Sandbox — runs the PDF worker in its own process and returns what it answered, or why not.

A PDF is untrusted input read by large parsers (pdfminer.six, PDFium). Neither is ever imported
into the server. This module starts ``_worker/main.py`` as a separate, isolated Python process
(``-I``: no user site, no environment, no current directory on the path), hands it the PDF's bytes
on stdin, and reads its one JSON answer back. The process:

- has a wall-clock deadline: past it, the whole process group is killed (``timeout``);
- limits itself, before it opens the file, to a fixed address space, CPU time and output size
  (``_worker/lockdown.py``), so a decompression bomb ends in a refusal, not in the server's memory;
- can't open a socket or start a program (a seccomp filter, same file);
- runs in an empty temporary folder with an empty environment, and never receives a path: it has
  the file's bytes and nothing else to read from.

The worker's answer is treated as untrusted too: it is parsed strictly, and a status or reason
outside the closed sets here becomes ``bad_output``. It never carries text for a person; the
caller maps each reason to a fixed sentence. Anything that goes wrong in the process itself (it
crashes, runs past the deadline, is killed, prints garbage, or the sandbox can't be set up on
this machine) is a refusal, never an exception and never a hang. Where the sandbox can't be set
up (anywhere but Linux), nothing is parsed.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

WORKER = Path(__file__).resolve().parent / "_worker" / "main.py"

# The most a PDF may have (the upload check and the worker both use it).
MAX_PAGES = 20
# The most bytes of a PDF handed to a worker (uploads are capped well below this).
MAX_PDF_BYTES = 8 * 1024 * 1024

# Defaults for one worker run. Chosen from ``docs/reviews/feat-pdf-extract-review.md``,
# "Parser process: peak memory".
TIMEOUT_SECONDS = 20.0
ADDRESS_SPACE_BYTES = 1024 * 1024 * 1024
CPU_SECONDS = 30
OUTPUT_BYTES = 8 * 1024 * 1024

# At most this many workers run at once, in the whole server process, so the memory they can use
# at once is bounded: this many address-space limits, not one per concurrent turn. One, because a
# dense PDF measured 706 MiB resident and the app's container is capped (deploy/docker-compose.yml,
# ``mem_limit``): two such workers and the server would not fit. A run waits for a free place for
# up to ``WAIT_SECONDS`` and is then refused as ``busy``.
MAX_CONCURRENT_WORKERS = 1
WAIT_SECONDS = 15.0
_places = threading.BoundedSemaphore(MAX_CONCURRENT_WORKERS)

# Reasons the worker may give (``_worker/main.py``), and the ones this module adds.
WORKER_REASONS = frozenset(
    {
        "encrypted",
        "no_text",
        "no_table",
        "many_tables",
        "not_plain",
        "one_column",
        "text_overflows",
        "columns_not_ruled",
        "too_many_pages",
        "too_large",
        "unreadable",
        "sandbox_unavailable",
    }
)
TIMEOUT = "timeout"
CRASHED = "crashed"
BAD_OUTPUT = "bad_output"
BUSY = "busy"
REASONS = WORKER_REASONS | {TIMEOUT, CRASHED, BAD_OUTPUT, BUSY}


@dataclass(frozen=True)
class WorkerOutcome:
    """What a worker run came to.

    Attributes:
        ok: The worker answered ``ok``; ``data`` is its answer.
        reason: When not ``ok``, one of ``REASONS``; ``""`` otherwise.
        data: The worker's answer, a plain dict (unvalidated beyond being JSON).
        peak_kb: The worker's own peak resident memory in KiB, if it reported one.
        vm_peak_kb: Its peak virtual memory in KiB (what the address-space limit counts).
    """

    ok: bool
    reason: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    peak_kb: int | None = None
    vm_peak_kb: int | None = None


def _refused(reason: str) -> WorkerOutcome:
    return WorkerOutcome(ok=False, reason=reason)


def run(
    mode: str,
    pdf: bytes,
    *,
    timeout_s: float = TIMEOUT_SECONDS,
    address_space: int = ADDRESS_SPACE_BYTES,
    cpu_seconds: int = CPU_SECONDS,
    output_bytes: int = OUTPUT_BYTES,
    command: list[str] | None = None,
) -> WorkerOutcome:
    """Run the worker in ``mode`` on ``pdf`` and return what it came to. Never raises.

    ``command`` replaces the worker's command line (the tests use it to start a process that
    hangs, or eats memory, to show the limits hold); production code never passes it.
    """
    if not sys.platform.startswith("linux") or not sys.executable:
        return _refused("sandbox_unavailable")
    if not _places.acquire(timeout=WAIT_SECONDS):
        logger.warning("PDF worker (%s) found every place taken for %.0f s", mode, WAIT_SECONDS)
        return _refused(BUSY)
    try:
        return _run_in_place(
            mode, pdf, command, timeout_s, address_space, cpu_seconds, output_bytes
        )
    finally:
        _places.release()


def _run_in_place(
    mode: str,
    pdf: bytes,
    command: list[str] | None,
    timeout_s: float,
    address_space: int,
    cpu_seconds: int,
    output_bytes: int,
) -> WorkerOutcome:
    argv = command or [
        sys.executable,
        "-I",
        str(WORKER),
        mode,
        str(address_space),
        str(cpu_seconds),
        str(output_bytes),
    ]
    try:
        with (
            tempfile.TemporaryDirectory(prefix="pdfwork-") as folder,
            tempfile.TemporaryFile() as out,
        ):
            process = subprocess.Popen(  # noqa: S603 - fixed argv, no shell, no user input
                argv,
                stdin=subprocess.PIPE,
                stdout=out,
                stderr=subprocess.DEVNULL,
                cwd=folder,
                env={},
                close_fds=True,
                start_new_session=True,
            )
            try:
                process.communicate(input=pdf, timeout=timeout_s)
            except subprocess.TimeoutExpired:
                _kill(process)
                logger.warning(
                    "PDF worker (%s) passed its %.0f s deadline and was killed", mode, timeout_s
                )
                return _refused(TIMEOUT)
            except BaseException:
                _kill(process)
                raise
            if process.returncode != 0:
                logger.warning("PDF worker (%s) exited with status %s", mode, process.returncode)
                return _refused(CRASHED)
            out.seek(0)
            raw = out.read(output_bytes + 1)
    except OSError:
        logger.exception("PDF worker (%s) couldn't be started", mode)
        return _refused("sandbox_unavailable")
    return _parse(raw, output_bytes)


def _kill(process: subprocess.Popen) -> None:
    """Kill a worker that is still running (its whole process group), then reap it.

    Only called before the process has been reaped, so its pid can't be someone else's yet.
    """
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        process.kill()
    except OSError:
        pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        logger.error("PDF worker %s didn't exit after SIGKILL", process.pid)
    if process.stdin is not None:
        try:
            process.stdin.close()
        except OSError:
            pass


def _parse(raw: bytes, output_bytes: int) -> WorkerOutcome:
    """The worker's output as an outcome; anything unexpected is ``bad_output``."""
    if len(raw) > output_bytes:
        return _refused(BAD_OUTPUT)
    try:
        answer = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return _refused(BAD_OUTPUT)
    if not isinstance(answer, dict):
        return _refused(BAD_OUTPUT)
    peak_kb, vm_peak_kb = (_count(answer.get(key)) for key in ("peak_kb", "vm_peak_kb"))
    status = answer.get("status")
    if status == "ok" and isinstance(answer.get("data"), dict):
        return WorkerOutcome(ok=True, data=answer["data"], peak_kb=peak_kb, vm_peak_kb=vm_peak_kb)
    reason = answer.get("reason")
    if status == "refused" and reason in WORKER_REASONS:
        return WorkerOutcome(ok=False, reason=reason, peak_kb=peak_kb, vm_peak_kb=vm_peak_kb)
    return _refused(BAD_OUTPUT)


def _count(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None

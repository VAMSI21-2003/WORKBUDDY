"""The sandbox that every PDF is read in: a deadline, memory and output limits, no network.

These tests start real processes. Where they need a process that misbehaves (hangs, eats memory,
prints garbage) they hand ``sandbox.run`` their own command line; the limits they check are the
worker's own (``skills/pdf/_worker/lockdown.py``), applied to it by that command exactly as
``main.py`` applies them.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

from skills.pdf import sandbox

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="the sandbox is Linux-only (it fails closed elsewhere)",
)

WORKER_DIR = str(sandbox.WORKER.parent)
MIB = 1024 * 1024


def _python(code: str, *args: str) -> list[str]:
    return [sys.executable, "-I", "-c", code, *args]


def _limited(body: str, *, address_space: int = 256 * MIB, output: int = 4 * MIB) -> str:
    """A script that puts the worker's own limits on itself, then runs ``body``."""
    return (
        "import sys\n"
        f"sys.path.append({WORKER_DIR!r})\n"
        "import lockdown\n"
        f"lockdown.apply(lockdown.Limits({address_space}, 20, {output}))\n" + body
    )


# -- The worker's lockdown ------------------------------------------------------------------


def test_the_worker_cannot_open_a_socket_start_a_program_or_a_process() -> None:
    outcome = sandbox.run("selftest", b"")
    assert outcome.ok, outcome.reason
    required = {k: v for k, v in outcome.data.items() if k != "filesystem_confined"}
    assert required == {
        "python_socket_refused": True,
        "libc_socket_refused": True,
        "exec_refused": True,
        "io_uring_refused": True,  # io_uring can open sockets by itself
        "fork_refused": True,
        "threads_work": True,  # a thread is still a thread
    }


def _landlock_here() -> bool:
    return bool(sandbox.run("selftest", b"").data.get("filesystem_confined"))


def test_the_worker_can_read_nothing_but_the_python_installation_and_write_nothing() -> None:
    if not _landlock_here():
        pytest.skip("Landlock is not available on this kernel")
    outcome = sandbox.run("selftest_fs", b"")
    assert outcome.ok, outcome.reason
    assert outcome.data == {
        "write_outside_refused": True,
        "create_in_tmp_refused": True,
        "read_etc_refused": True,
        "mkdir_refused": True,
        "read_cwd_listing_refused": True,
        "filesystem_confined": True,
    }


def test_another_persons_upload_cannot_be_read_or_changed_from_inside_the_worker(
    tmp_path: Path,
) -> None:
    if not _landlock_here():
        pytest.skip("Landlock is not available on this kernel")
    upload = tmp_path / "other-persons-upload.xlsx"
    upload.write_bytes(b"secret contents")
    upload.chmod(0o444)
    code = _limited(
        "import json, os\n"
        "result = {}\n"
        f"path = {str(upload)!r}\n"
        "lockdown.confine_filesystem()\n"
        "for name, action in (('read', lambda: open(path, 'rb').read()),\n"
        "                     ('write', lambda: open(path, 'ab').write(b'x')),\n"
        "                     ('truncate', lambda: open(path, 'wb')),\n"
        "                     ('unlink', lambda: os.unlink(path))):\n"
        "    try:\n"
        "        action(); result[name] = 'allowed'\n"
        "    except OSError:\n"
        "        result[name] = 'refused'\n"
        "print(json.dumps({'status': 'ok', 'data': result}))\n"
    )
    outcome = sandbox.run("x", b"", command=_python(code))
    assert outcome.ok and outcome.data == {
        "read": "refused",
        "write": "refused",
        "truncate": "refused",
        "unlink": "refused",
    }
    assert upload.read_bytes() == b"secret contents"


@pytest.mark.skipif(
    __import__("platform").machine().lower() not in ("x86_64", "amd64"),
    reason="the x32 numbering exists on x86-64 only",
)
def test_a_call_in_the_x32_numbering_kills_the_worker() -> None:
    outcome = sandbox.run("selftest_x32", b"")
    assert (outcome.ok, outcome.reason) == (False, sandbox.CRASHED)


def test_the_real_modes_still_read_a_pdf_with_the_filesystem_confined() -> None:
    from tests import pdf_fixtures

    pdf = pdf_fixtures.hand_table_pdf(pdf_fixtures.FRUIT)
    for mode in ("probe", "extract", "verify"):
        assert sandbox.run(mode, pdf).ok, mode


def test_a_socket_to_a_listening_port_is_refused_from_inside_the_sandbox() -> None:
    """A real listener on localhost is never reached: connecting fails before it could."""
    import socket

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    listener.settimeout(0.5)
    try:
        code = _limited(
            "import socket, json\n"
            "try:\n"
            f"    socket.create_connection(('127.0.0.1', {port}), timeout=2)\n"
            "    answer = 'connected'\n"
            "except PermissionError:\n"
            "    answer = 'refused'\n"
            "print(json.dumps({'status': 'ok', 'data': {'answer': answer}}))\n"
        )
        outcome = sandbox.run("x", b"", command=_python(code))
        assert outcome.ok and outcome.data == {"answer": "refused"}
        with pytest.raises(socket.timeout):
            listener.accept()  # nothing ever connected
    finally:
        listener.close()


def test_the_worker_has_no_environment_and_no_path_to_the_server(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "placeholder-not-a-key")
    monkeypatch.setenv("WORKBUDDY_SECRET", "x")
    monkeypatch.chdir(tmp_path)
    code = (
        "import os, json\n"
        "print(json.dumps({'status': 'ok', 'data': {'env': dict(os.environ), 'cwd': os.getcwd(),"
        " 'stdin': len(__import__('sys').stdin.buffer.read())}}))\n"
    )
    outcome = sandbox.run("x", b"12345", command=_python(code))
    assert outcome.ok
    # Python itself sets LC_CTYPE when the locale is "C"; nothing of the server's is there.
    assert set(outcome.data["env"]) <= {"LC_CTYPE"}
    assert outcome.data["stdin"] == 5
    assert Path(outcome.data["cwd"]) != tmp_path
    assert not Path(outcome.data["cwd"]).exists()  # an empty temporary folder, removed after


# -- Deadline ------------------------------------------------------------------------------


def test_a_worker_that_hangs_is_killed_at_the_deadline(tmp_path: Path) -> None:
    pid_file = tmp_path / "pid"
    code = (
        "import os, sys, time\n"
        f"open({str(pid_file)!r}, 'w').write(str(os.getpid()))\n"
        "time.sleep(120)\n"
    )
    started = time.monotonic()
    outcome = sandbox.run("x", b"", command=_python(code), timeout_s=1.0)
    elapsed = time.monotonic() - started
    assert (outcome.ok, outcome.reason) == (False, sandbox.TIMEOUT)
    assert elapsed < 10
    pid = int(pid_file.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)  # gone, and reaped


def test_a_process_the_worker_started_dies_with_it(tmp_path: Path) -> None:
    child_file = tmp_path / "child"
    code = (
        "import subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
        f"open({str(child_file)!r}, 'w').write(str(child.pid))\n"
        "time.sleep(120)\n"
    )
    outcome = sandbox.run("x", b"", command=_python(code), timeout_s=1.5)
    assert outcome.reason == sandbox.TIMEOUT
    child = int(child_file.read_text())
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        os.kill(child, 9)
        pytest.fail("the worker's child outlived the deadline")


# -- Memory and output limits ---------------------------------------------------------------


def test_a_worker_that_wants_more_memory_than_its_limit_is_refused() -> None:
    code = _limited("block = bytearray(600 * 1024 * 1024)\nprint('{}')\n", address_space=256 * MIB)
    outcome = sandbox.run("x", b"", command=_python(code))
    assert (outcome.ok, outcome.reason) == (False, sandbox.CRASHED)


def test_the_server_is_unharmed_by_a_worker_that_ran_out_of_memory() -> None:
    code = _limited("block = bytearray(600 * 1024 * 1024)\n", address_space=256 * MIB)
    sandbox.run("x", b"", command=_python(code))
    assert sandbox.run("selftest", b"").ok  # the next worker runs as before


def test_a_worker_that_writes_more_than_its_output_limit_is_stopped() -> None:
    code = _limited(
        "import sys\nfor _ in range(64):\n    sys.stdout.write('x' * (1024 * 1024))\n"
        "    sys.stdout.flush()\n",
        output=1 * MIB,
    )
    outcome = sandbox.run("x", b"", command=_python(code), output_bytes=1 * MIB)
    assert (outcome.ok, outcome.reason) == (False, sandbox.CRASHED)


def test_output_over_the_limit_without_the_worker_stopping_itself_is_bad_output() -> None:
    code = "import sys; sys.stdout.write('x' * (3 * 1024 * 1024))"
    outcome = sandbox.run("x", b"", command=_python(code), output_bytes=1 * MIB)
    assert (outcome.ok, outcome.reason) == (False, sandbox.BAD_OUTPUT)


# -- What a worker may say -----------------------------------------------------------------


@pytest.mark.parametrize(
    "printed",
    [
        "",
        "not json",
        "[]",
        "null",
        '{"status": "ok"}',
        '{"status": "ok", "data": []}',
        '{"status": "refused"}',
        '{"status": "refused", "reason": "a sentence the server would show"}',
        '{"status": "refused", "reason": "timeout"}',  # the sandbox's own codes aren't the worker's
        '{"status": "maybe", "data": {}}',
        "\xff\xfe",
    ],
)
def test_an_answer_outside_the_closed_set_is_bad_output(printed: str) -> None:
    code = f"import sys; sys.stdout.buffer.write({printed.encode('latin-1')!r})"
    outcome = sandbox.run("x", b"", command=_python(code))
    assert (outcome.ok, outcome.reason) == (False, sandbox.BAD_OUTPUT)


def test_a_valid_refusal_and_a_valid_answer_pass_through() -> None:
    refused = sandbox.run(
        "x",
        b"",
        command=_python('print(\'{"status": "refused", "reason": "no_table"}\')'),
    )
    assert (refused.ok, refused.reason) == (False, "no_table")
    answered = sandbox.run(
        "x",
        b"",
        command=_python(
            'print(\'{"status": "ok", "data": {"a": 1}, "peak_kb": 5, "vm_peak_kb": true}\')'
        ),
    )
    assert answered.ok and answered.data == {"a": 1}
    assert answered.peak_kb == 5 and answered.vm_peak_kb is None  # a bool is not a size


def test_a_nonzero_exit_is_a_crash_whatever_was_printed() -> None:
    code = 'import sys; print(\'{"status": "ok", "data": {}}\'); sys.exit(3)'
    outcome = sandbox.run("x", b"", command=_python(code))
    assert (outcome.ok, outcome.reason) == (False, sandbox.CRASHED)


def test_a_worker_killed_by_a_signal_is_a_crash() -> None:
    outcome = sandbox.run("x", b"", command=_python("import os, signal; os.kill(os.getpid(), 9)"))
    assert (outcome.ok, outcome.reason) == (False, sandbox.CRASHED)


# -- Where the sandbox can't be built ------------------------------------------------------


def test_off_linux_nothing_is_started(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("a process was started")

    monkeypatch.setattr(sandbox.sys, "platform", "win32")
    monkeypatch.setattr(sandbox.subprocess, "Popen", fail)
    outcome = sandbox.run("probe", b"%PDF-1.4")
    assert (outcome.ok, outcome.reason) == (False, "sandbox_unavailable")


def test_a_failure_to_start_is_a_refusal_not_an_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise OSError("no processes left")

    monkeypatch.setattr(sandbox.subprocess, "Popen", fail)
    outcome = sandbox.run("probe", b"%PDF-1.4")
    assert (outcome.ok, outcome.reason) == (False, "sandbox_unavailable")


def test_the_worker_refuses_to_parse_when_it_cannot_lock_itself_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the filter can't be installed the worker parses nothing (it fails closed)."""
    sys.path.append(WORKER_DIR)
    try:
        import lockdown  # type: ignore[import-not-found]
    finally:
        sys.path.remove(WORKER_DIR)
    monkeypatch.setattr(lockdown.platform, "machine", lambda: "riscv64")
    with pytest.raises(lockdown.LockdownUnavailable):
        lockdown._install_seccomp()


# -- The number of workers at once ------------------------------------------------------------


def test_when_every_place_is_taken_a_run_waits_then_is_refused_as_busy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import threading

    held = threading.BoundedSemaphore(1)
    held.acquire()
    monkeypatch.setattr(sandbox, "_places", held)
    monkeypatch.setattr(sandbox, "WAIT_SECONDS", 0.2)
    started = time.monotonic()
    outcome = sandbox.run("selftest", b"")
    assert (outcome.ok, outcome.reason) == (False, sandbox.BUSY)
    assert 0.15 < time.monotonic() - started < 5
    held.release()
    assert sandbox.run("selftest", b"").ok  # and runs again once a place is free


def test_no_more_workers_run_at_once_than_the_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    import threading

    monkeypatch.setattr(sandbox, "_places", threading.BoundedSemaphore(2))
    code = (
        "import json, time\n"
        "start = time.time(); time.sleep(0.5); end = time.time()\n"
        "print(json.dumps({'status': 'ok', 'data': {'start': start, 'end': end}}))\n"
    )
    spans: list[tuple[float, float]] = []

    def one() -> None:
        outcome = sandbox.run("x", b"", command=_python(code))
        assert outcome.ok, outcome.reason
        spans.append((outcome.data["start"], outcome.data["end"]))

    threads = [threading.Thread(target=one) for _ in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    assert len(spans) == 5
    # At any moment, how many of the five were running?
    instants = sorted(t for span in spans for t in span)
    most = max(sum(a <= t < b for a, b in spans) for t in instants)
    assert most <= 2, most


def test_the_release_check_passes_where_the_sandbox_holds() -> None:
    import subprocess

    done = subprocess.run(
        [sys.executable, "-m", "skills.pdf"], capture_output=True, text=True, timeout=60
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert "The PDF sandbox holds." in done.stdout


def test_the_server_never_imports_a_pdf_parser() -> None:
    """The app, the pipeline and the skill load without pdfplumber, pdfminer, pypdfium2 or pypdf."""
    import subprocess

    code = (
        "import sys\n"
        "import backend.main, buddy.engine_factory, skills.pdf, backend.uploads\n"
        "from buddy.engine_factory import build_engine\n"
        "loaded = sorted(m for m in sys.modules if m.split('.')[0] in\n"
        "                ('pdfplumber', 'pdfminer', 'pypdfium2', 'pypdf'))\n"
        "print(loaded)\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=120, check=True
    )
    assert done.stdout.strip() == "[]", done.stdout

# ruff: noqa: E501
"""Round 4: can the locked-down worker (seccomp + Landlock, as main.py applies them) still signal the
server without kill/tgkill? An fd's owner (`fcntl F_SETOWN`) gets SIGIO, or the signal named by
`F_SETSIG`, when the fd becomes ready; the kernel checks only that the uids match. Landlock's signal
scope (ABI 6) would cover it; `lockdown.confine_filesystem` doesn't set a scope.

A throwaway "server" process runs the worker through the real `sandbox.run`, then works for 4 s.

    python r4_sandbox.py            # every try; prints the server's exit status (-9 SIGKILL, -29 SIGIO, -15 SIGTERM)
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import r2_sandbox as r2  # noqa: E402

from skills.pdf import sandbox  # noqa: E402

TRIES = {
    "fsetown_sigio_default": (
        'import fcntl\n'
        'rd, wr = os.pipe()\n'
        'fcntl.fcntl(rd, fcntl.F_SETOWN, os.getppid())\n'
        'fcntl.fcntl(rd, fcntl.F_SETFL, fcntl.fcntl(rd, fcntl.F_GETFL) | os.O_ASYNC)\n'
        'os.write(wr, b"x")\n'
        'r["v"] = "wrote to an O_ASYNC pipe owned by the server"'
    ),
    "fsetown_setsig_sigkill": (
        'import fcntl\n'
        'rd, wr = os.pipe()\n'
        'fcntl.fcntl(rd, fcntl.F_SETOWN, os.getppid())\n'
        'fcntl.fcntl(rd, fcntl.F_SETSIG, 9)\n'
        'fcntl.fcntl(rd, fcntl.F_SETFL, fcntl.fcntl(rd, fcntl.F_GETFL) | os.O_ASYNC)\n'
        'os.write(wr, b"x")\n'
        'r["v"] = "wrote; F_SETSIG SIGKILL"'
    ),
    "fsetown_setsig_sigterm": (
        'import fcntl\n'
        'rd, wr = os.pipe()\n'
        'fcntl.fcntl(rd, fcntl.F_SETOWN, os.getppid())\n'
        'fcntl.fcntl(rd, fcntl.F_SETSIG, 15)\n'
        'fcntl.fcntl(rd, fcntl.F_SETFL, fcntl.fcntl(rd, fcntl.F_GETFL) | os.O_ASYNC)\n'
        'os.write(wr, b"x")\n'
        'r["v"] = "wrote; F_SETSIG SIGTERM"'
    ),
    "fsetown_group_minus_ppid": (
        'import fcntl\n'
        'rd, wr = os.pipe()\n'
        'fcntl.fcntl(rd, fcntl.F_SETOWN, -os.getpgid(os.getppid()))\n'
        'fcntl.fcntl(rd, fcntl.F_SETSIG, 9)\n'
        'fcntl.fcntl(rd, fcntl.F_SETFL, fcntl.fcntl(rd, fcntl.F_GETFL) | os.O_ASYNC)\n'
        'os.write(wr, b"x")\n'
        'r["v"] = "wrote; owner = the server\'s process group"'
    ),
    "control_kill_parent": 'r["v"] = sc(62, os.getppid(), 9)',
}

SERVER = r'''
import sys, time
sys.path.insert(0, %(root)r)
from skills.pdf import sandbox
code = %(code)r
out = sandbox.run("x", b"", command=[sys.executable, "-I", "-c", code])
print("worker:", out.ok, out.reason, out.data, flush=True)
end = time.time() + 4
x = 0
while time.time() < end:
    x += 1
print("server still alive after 4 s of work", flush=True)
'''


def main() -> None:
    fmt = {"worker": str(r2.ROOT / "skills" / "pdf" / "_worker"), "as_": sandbox.ADDRESS_SPACE_BYTES,
           "cpu": sandbox.CPU_SECONDS, "out": sandbox.OUTPUT_BYTES, "landlock": "--no-landlock" not in sys.argv}
    for name, body in TRIES.items():
        if len(sys.argv) > 1 and name not in sys.argv[1:] and not sys.argv[1].startswith("--"):
            continue
        code = (r2.PRELUDE + body + r2.EPILOGUE) % fmt
        p = subprocess.run([sys.executable, "-c", SERVER % {"root": str(r2.ROOT), "code": code}],
                           capture_output=True, text=True, timeout=60, start_new_session=True)  # own group: the group try must not reach this shell
        print(json.dumps({"try": name, "server_exit": p.returncode, "server_stdout": p.stdout.strip()[-300:]}))


if __name__ == "__main__":
    main()

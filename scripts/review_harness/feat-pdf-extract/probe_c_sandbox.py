# ruff: noqa: E501
"""Probe C: what code running inside the worker's lockdown can still do.

Stands in for a parser that has been taken over by a hostile PDF (the case the sandbox exists for):
runs the real `skills/pdf/_worker/lockdown.apply` with the sandbox's real limits, then tries things
the design says are impossible or that it doesn't mention. Started with the sandbox's own `command`
hook, so it runs exactly as the worker does (empty env, -I, temp cwd, stdin bytes, seccomp).

    python probe_c_sandbox.py            # prints one JSON object
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))

from skills.pdf import sandbox  # noqa: E402

CANARY_DIR = Path(tempfile.mkdtemp(prefix="pdfprobe-canary-"))
SECRET = CANARY_DIR / "other_users_upload.xlsx"
SECRET.write_text("OTHER-USER-DATA")
SECRET.chmod(0o444)

CHILD = r'''
import ctypes, errno, json, os, sys
sys.path.insert(0, "%(worker)s")
import lockdown
lockdown.apply(lockdown.Limits(%(as_)d, %(cpu)d, %(out)d))
libc = ctypes.CDLL(None, use_errno=True)
libc.syscall.restype = ctypes.c_long
r = {}
def sc(nr, *a):
    v = libc.syscall(ctypes.c_long(nr), *[ctypes.c_long(x) for x in a]); return v, ctypes.get_errno()
r["libc_socket"] = sc(41, 2, 1, 0)
r["x32_socket(0x40000000|41)"] = sc(0x40000000 | 41, 2, 1, 0)
r["io_uring_setup(425)"] = sc(425, 8, 0)
try:
    r["read_other_file"] = open("%(secret)s").read()
except Exception as e:
    r["read_other_file"] = repr(e)
try:
    r["list_root"] = sorted(os.listdir("/"))[:5]
except Exception as e:
    r["list_root"] = repr(e)
try:
    p = "%(dir)s/written_by_worker.txt"
    open(p, "w").write("x"); r["write_outside_cwd"] = os.path.exists(p)
except Exception as e:
    r["write_outside_cwd"] = repr(e)
try:
    os.chmod("%(secret)s", 0o644); r["chmod_other_file"] = True
except Exception as e:
    r["chmod_other_file"] = repr(e)
try:
    pid = os.fork()
    if pid == 0:
        os._exit(0)
    os.waitpid(pid, 0); r["fork"] = "allowed"
except Exception as e:
    r["fork"] = repr(e)
sys.stdout.write(json.dumps({"status": "ok", "data": r}))
'''


def main() -> None:
    code = CHILD % {
        "worker": str(ROOT / "skills" / "pdf" / "_worker"),
        "as_": sandbox.ADDRESS_SPACE_BYTES, "cpu": sandbox.CPU_SECONDS, "out": sandbox.OUTPUT_BYTES,
        "secret": SECRET, "dir": CANARY_DIR,
    }
    outcome = sandbox.run("x", b"", command=[sys.executable, "-I", "-c", code])
    print(json.dumps({"ok": outcome.ok, "reason": outcome.reason, "data": outcome.data,
                      "secret_mode_after": oct(SECRET.stat().st_mode & 0o777)}, indent=1))


if __name__ == "__main__":
    main()

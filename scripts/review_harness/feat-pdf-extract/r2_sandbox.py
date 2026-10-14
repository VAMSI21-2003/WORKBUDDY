# ruff: noqa: E501
"""Round 2: what code inside the worker can still do once the whole round-1 lockdown is on.

Mirrors `_worker/main.py`: `lockdown.apply`, the parser imports, `lockdown.confine_filesystem`,
then tries each thing below in its own process (started with the sandbox's own `command` hook:
empty env, -I, temp cwd, close_fds, new session). The parent plays the server: it holds a
listening TCP socket and a connected one, and a 0444 file standing for another person's upload.

    python r2_sandbox.py [--no-landlock]   # one JSON line per try; --no-landlock: as on a kernel without it
"""
from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))

from skills.pdf import sandbox  # noqa: E402

CANARY_DIR = Path(tempfile.mkdtemp(prefix="pdfprobe-r2-"))
SECRET = CANARY_DIR / "other_users_upload.xlsx"
SECRET.write_text("OTHER-USER-DATA")
SECRET.chmod(0o444)

PRELUDE = r'''
import ctypes, errno, json, os, sys
sys.path.insert(0, "%(worker)s")
import lockdown
lockdown.apply(lockdown.Limits(%(as_)d, %(cpu)d, %(out)d))
import pypdfium2, pypdfium2.raw, pdfplumber, pdfminer.pdfpage
conf = lockdown.confine_filesystem() if %(landlock)s else lockdown.Confinement(False, "skipped by probe")
libc = ctypes.CDLL(None, use_errno=True)
libc.syscall.restype = ctypes.c_long
def sc(nr, *a):
    v = libc.syscall(ctypes.c_long(nr), *[ctypes.c_long(x) for x in a]); return [v, errno.errorcode.get(ctypes.get_errno(), ctypes.get_errno())]
r = {"confined": conf.confined}
'''
EPILOGUE = '\nsys.stdout.write(json.dumps({"status": "ok", "data": r}))\n'

TRIES = {
    "read_other_upload": 'try:\n    r["v"] = open("%(secret)s").read()\nexcept Exception as e:\n    r["v"] = repr(e)',
    "chmod_other_upload": 'try:\n    os.chmod("%(secret)s", 0o666); r["v"] = "chmod allowed"\nexcept Exception as e:\n    r["v"] = repr(e)',
    "read_parent_environ": 'try:\n    r["v"] = len(open("/proc/%%d/environ" %% os.getppid(), "rb").read())\nexcept Exception as e:\n    r["v"] = repr(e)',
    "signal_parent(kill 0)": 'r["v"] = sc(62, os.getppid(), 0)',
    "pidfd_open_parent": 'r["v"] = sc(434, os.getppid(), 0)',
    "steal_parent_socket(pidfd_getfd)+send": (
        'pfd = sc(434, os.getppid(), 0)[0]\n'
        'r["pidfd"] = pfd\n'
        'got = sc(438, pfd, %(conn_fd)d, 0) if pfd >= 0 else None\n'
        'r["getfd"] = got\n'
        'if got and got[0] >= 0:\n'
        '    r["send"] = sc(44, got[0], ctypes.cast(ctypes.c_char_p(b"EXFIL-FROM-WORKER"), ctypes.c_void_p).value, 17, 0, 0, 0)\n'
        'r["v"] = "see fields"'
    ),
    "steal_listening_socket+accept": (
        'pfd = sc(434, os.getppid(), 0)[0]\n'
        'got = sc(438, pfd, %(listen_fd)d, 0) if pfd >= 0 else None\n'
        'r["getfd"] = got\n'
        'r["v"] = "a listening socket the worker could accept on" if got and got[0] >= 0 else "refused"'
    ),
    "io_uring_setup": 'r["v"] = sc(425, 8, ctypes.addressof(ctypes.create_string_buffer(120)))',
    "raw_clone_process": 'r["v"] = sc(56, 17, 0, 0, 0, 0)',
    "unshare_user_ns": 'r["v"] = sc(272, 0x10000000)',
    "memfd_create": 'r["v"] = sc(319, ctypes.cast(ctypes.c_char_p(b"x"), ctypes.c_void_p).value, 0)',
    "write_tmp": 'try:\n    open("/tmp/r2-worker-wrote", "w").write("x"); r["v"] = "wrote"\nexcept Exception as e:\n    r["v"] = repr(e)',
}


def main() -> None:
    lst = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    lst.bind(("127.0.0.1", 0))
    lst.listen(4)
    cli = socket.create_connection(lst.getsockname())
    srv, _ = lst.accept()
    os.set_inheritable(cli.fileno(), False)
    fmt = {"worker": str(ROOT / "skills" / "pdf" / "_worker"), "as_": sandbox.ADDRESS_SPACE_BYTES,
           "cpu": sandbox.CPU_SECONDS, "out": sandbox.OUTPUT_BYTES, "secret": SECRET,
           "conn_fd": cli.fileno(), "listen_fd": lst.fileno(),
           "landlock": "--no-landlock" not in sys.argv}
    for name, body in TRIES.items():
        code = (PRELUDE + body + EPILOGUE) % fmt
        out = sandbox.run("x", b"", command=[sys.executable, "-I", "-c", code])
        rec = {"try": name, "ok": out.ok, "reason": out.reason, "data": out.data}
        if name.startswith("steal_parent_socket"):
            srv.settimeout(0.5)
            try:
                rec["server_peer_received"] = srv.recv(100).decode()
            except Exception as e:  # noqa: BLE001
                rec["server_peer_received"] = repr(e)
        print(json.dumps(rec))
    print(json.dumps({"other_upload_mode_after": oct(SECRET.stat().st_mode & 0o777),
                      "other_upload_bytes": SECRET.read_text()}))


if __name__ == "__main__":
    main()

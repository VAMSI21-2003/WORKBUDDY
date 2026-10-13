# ruff: noqa: E501
"""Round 3: what the worker can still do to the server process after the R2-M3 fix.

Reuses r2_sandbox.py's prelude (the full lockdown + Landlock, as main.py applies it). The worker's
parent plays the server. Calls that act on another process by pid without ptrace rights or a signal:
prlimit64, setpriority, sched_setaffinity, ioprio_set; plus kill on the process group and on -1.
`--kill-server`: a throwaway "server" process runs the worker, the worker sets the server's
RLIMIT_CPU with prlimit64, and we watch whether the server dies.

    python r3_sandbox.py [--kill-server]
"""
from __future__ import annotations

import json
import os
import resource
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import r2_sandbox as r2  # noqa: E402

from skills.pdf import sandbox  # noqa: E402  (r2 put ROOT on the path)

TRIES = {
    "setpriority_parent": 'r["v"] = sc(141, 0, os.getppid(), 19)',
    "sched_setaffinity_parent": (
        'mask = (ctypes.c_uint64 * 1)(1)\n'
        'r["v"] = sc(203, os.getppid(), 8, ctypes.addressof(mask))'
    ),
    "ioprio_set_parent": 'r["v"] = sc(251, 1, os.getppid(), (3 << 13))',
    "kill_group_0": 'r["v"] = sc(62, 0, 0)',
    "kill_minus_1": 'r["v"] = sc(62, -1, 0)',
    "tgkill_self_parenttid": 'r["v"] = sc(234, os.getpid(), os.getppid(), 0)',
    # struct rlimit64 {soft, hard}: NOFILE soft 5 on the server (it can no longer open a file or accept)
    "prlimit64_parent_nofile": (
        'buf = (ctypes.c_uint64 * 2)(5, 5)\n'
        'r["v"] = sc(302, os.getppid(), 7, ctypes.addressof(buf), 0)'
    ),
}  # prlimit last: afterwards this process can hardly open anything

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
           "cpu": sandbox.CPU_SECONDS, "out": sandbox.OUTPUT_BYTES, "landlock": True}
    if "--kill-server" in sys.argv:
        body = 'buf = (ctypes.c_uint64 * 2)(1, 1)\nr["v"] = sc(302, os.getppid(), 0, ctypes.addressof(buf), 0)'
        code = (r2.PRELUDE + body + r2.EPILOGUE) % fmt
        p = subprocess.run([sys.executable, "-c", SERVER % {"root": str(r2.ROOT), "code": code}],
                           capture_output=True, text=True, timeout=60)
        print(json.dumps({"server_exit": p.returncode, "server_stdout": p.stdout.strip(),
                          "note": "-24 = killed by SIGXCPU"}))
        return
    before = resource.getrlimit(resource.RLIMIT_NOFILE)
    for name, body in TRIES.items():
        code = (r2.PRELUDE + body + r2.EPILOGUE) % fmt
        out = sandbox.run("x", b"", command=[sys.executable, "-I", "-c", code])
        rec = {"try": name, "ok": out.ok, "reason": out.reason, "data": out.data}
        if name == "prlimit64_parent_nofile":
            rec["server_nofile_after"] = resource.getrlimit(resource.RLIMIT_NOFILE)
            rec["server_nofile_before"] = before
        print(json.dumps(rec))
    print(json.dumps({"server_nice_after": os.getpriority(os.PRIO_PROCESS, 0),
                      "server_affinity_after": sorted(os.sched_getaffinity(0))}))


if __name__ == "__main__":
    main()

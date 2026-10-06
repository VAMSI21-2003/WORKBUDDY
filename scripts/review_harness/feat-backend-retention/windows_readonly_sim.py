# ruff: noqa: E501 — reviewer tooling, not product code
"""Finding M1: the retention sweep on Windows, simulated on Linux.

    python scripts/review_harness/feat-backend-retention/windows_readonly_sim.py > out/windows_readonly_sim.json

On Windows, ``os.chmod(path, 0o444)`` sets a file's read-only attribute, and
``os.unlink`` (``DeleteFileW``) refuses a read-only file with
``PermissionError`` (WinError 5) until the attribute is cleared. The API
stores every upload (``backend/uploads.py``) and every delivered file
(``backend/delivery.py``) with ``0o444``; ``backend/retention.remove_tree``
makes folders writable but calls ``os.unlink`` on files as they are.

This script runs the real app and pipeline (fake LLM), delivers a file, lets
the session go idle past the TTL, and sweeps three times with
``backend.retention.os.unlink`` replaced by one that applies the Windows rule
(a file whose mode lacks the owner-write bit can't be unlinked). Everything
else is real. ``--fixed`` also applies the one-line fix (clear the read-only
bit before unlinking a regular file) to show it then succeeds.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import pr4_probe as p  # noqa: E402

import backend.retention as retention  # noqa: E402

real_unlink = os.unlink


def windows_unlink(path: os.PathLike[str] | str, *a: object, **k: object) -> None:
    st = os.lstat(path)
    if stat.S_ISREG(st.st_mode) and not st.st_mode & stat.S_IWRITE:
        raise PermissionError(13, "[WinError 5] Access is denied (simulated read-only attribute)", str(path))
    real_unlink(path)


def main() -> None:
    fixed = "--fixed" in sys.argv
    retention.os = type(sys)("os_shim")
    retention.os.__dict__.update(os.__dict__)
    retention.os.unlink = windows_unlink
    if fixed:
        original_remove_tree = retention.remove_tree

        def remove_tree(path: Path) -> None:
            st = os.lstat(path)
            if stat.S_ISREG(st.st_mode):
                os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
            original_remove_tree(path)

        retention.remove_tree = remove_tree
    out: dict[str, object] = {"fixed": fixed}
    rig = p.Rig("template", p.h.FakeLLM(p.h.single("remove_duplicate_rows")))
    try:
        rig.login_bearer()
        _, sid = p.new_session(rig, "template")
        _, _, original = p.upload(rig, sid, p.h.xlsx())
        t = p.turn(rig, sid, "remove duplicate rows", original)
        out["outcome"] = t["outcome"]
        out["retention_hours_advertised"] = rig.req("GET", f"/api/v1/sessions/{sid}").json()["retention_hours"]
        sweeps = []
        for _ in range(3):
            rig.wall.advance(25 * p.HOUR)
            res = rig.sweep()
            sweeps.append({"removed": len(res.removed), "failed": len(res.failed), "folders": rig.folders(sid)})
        out["sweeps_25h_apart"] = sweeps
        out["upload_still_on_disk"] = original.exists()
        out["session_after_75h_idle_http"] = rig.req("GET", f"/api/v1/sessions/{sid}").status_code
        out["download_after_75h_idle_http"] = rig.req("GET", f"/api/v1/sessions/{sid}/files/d_{t['turn_id']}_1/download").status_code
    finally:
        rig.close()
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()

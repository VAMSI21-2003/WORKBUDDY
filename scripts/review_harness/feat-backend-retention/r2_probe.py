# ruff: noqa: E501 — reviewer tooling, not product code
"""Round 2 probe: the M1 fix (``remove_tree`` clears the read-only bit) through the real app and pipeline.

    python scripts/review_harness/feat-backend-retention/r2_probe.py [--tree DIR] [--only NAME] > out/r2_probe_<tree>.jsonl

Reuses ``pr4_probe.py`` (real ``create_app``, real ``build_engine``, real skills
and verifiers, fake LLM; injected clocks). ``--tree`` (consumed by ``pr3``)
selects the checkout, e.g. a worktree of round 1 (``4352d1c``). "Windows rule"
means ``backend.retention``'s ``os.unlink`` refuses a regular file without
the owner-write bit, as ``DeleteFileW`` refuses a read-only file.

Cases (w1, w2 and pf per speaker):
- w1_windows_mixed_sweep: Windows rule. A delivered session, a delivered chain,
  a plan waiting across the sweep, and a session used at +23 h. Sweep at +25 h,
  then approve the waiting plan and run a turn on the kept session.
- w2_windows_restart_sweep: Windows rule. Delivered in one process, swept from
  disk by a second process 25 h later (the ``claim_unloaded`` path).
- pf_windows_partial_failure: Windows rule plus one upload whose unlink fails
  for another reason (on Windows: a file still open). What is left, its mode,
  and what a returning user gets (finding M2's path).
- hl_hard_link: no Windows rule. A read-only file outside the data root is
  hard-linked into a session's outputs; after the sweep, the outside file's mode.
"""

from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import pr4_probe as p  # noqa: E402  (imports pr3, which consumes --tree)

import backend.retention as retention  # noqa: E402
from backend.sessions import session_key  # noqa: E402

real_unlink = os.unlink
EXTRA_REFUSE: set[str] = set()


def windows_unlink(path: Any, *a: Any, **k: Any) -> None:
    st = os.lstat(path)
    if str(path) in EXTRA_REFUSE:
        raise PermissionError(32, "[WinError 32] The process cannot access the file (simulated: still open)", str(path))
    if stat.S_ISREG(st.st_mode) and not st.st_mode & stat.S_IWRITE:
        raise PermissionError(13, "[WinError 5] Access is denied (simulated read-only attribute)", str(path))
    real_unlink(path, *a, **k)


class windows_rule:
    def __enter__(self) -> None:
        shim = type(sys)("os_shim")
        shim.__dict__.update(os.__dict__)
        shim.unlink = windows_unlink
        self.saved = retention.os
        retention.os = shim

    def __exit__(self, *exc: object) -> None:
        retention.os = self.saved
        EXTRA_REFUSE.clear()


def mode(path: Path) -> str | None:
    try:
        return oct(os.lstat(path).st_mode & 0o777)
    except FileNotFoundError:
        return None


def check_reply(t: dict[str, Any]) -> dict[str, Any]:
    """The reply's claimed counts next to the independent read of the delivered file."""
    return {k: t.get(k) for k in ("outcome", "reply", "detail_steps", "delivered", "original_unchanged", "original_sha_before", "original_sha_after", "TIMEOUT", "start_http")}


def w1_windows_mixed_sweep(speaker: str) -> dict[str, Any]:
    script = (p.h.single("remove_duplicate_rows")
              + p.h.chain((p.DEDUPE, "remove duplicate rows"), (p.EMPTY, "then remove empty rows"))
              + p.h.single("remove_duplicate_rows") + p.h.single("remove_duplicate_rows") + p.h.single("remove_empty_rows"))
    rig = p.Rig(speaker, p.h.FakeLLM(script))
    out: dict[str, Any] = {}
    try:
        rig.login_bearer()
        _, a = p.new_session(rig, speaker)
        _, _, oa = p.upload(rig, a, p.h.xlsx())
        ta = p.turn(rig, a, "remove duplicate rows", oa)
        _, b = p.new_session(rig, speaker)
        _, _, ob = p.upload(rig, b, p.h.xlsx())
        tb = p.turn(rig, b, "remove duplicate rows, then remove empty rows", ob)
        _, d = p.new_session(rig, speaker)
        _, _, od = p.upload(rig, d, p.h.xlsx())
        td = p.turn(rig, d, "remove duplicate rows", od)
        d_out = sorted((rig.settings.outputs_dir / session_key(d)).rglob("*.xlsx"))
        out["a"] = check_reply(ta)
        out["b"] = check_reply(tb)
        out["d_first"] = check_reply(td)
        _, c = p.new_session(rig, speaker)
        _, _, oc = p.upload(rig, c, p.h.xlsx())
        info: dict[str, Any] = {}

        def during_plan(rig: p.Rig, sid: str, tid: str, body: dict[str, Any]) -> None:
            rig.wall.advance(23 * p.HOUR)
            rig.req("GET", f"/api/v1/sessions/{d}/files")  # d used at +23 h
            rig.wall.advance(2 * p.HOUR)
            with windows_rule():
                res = rig.sweep()
            info["sweep"] = {"removed": len(res.removed), "failed": len(res.failed),
                             "removed_keys_are_a_and_b": sorted(res.removed) == sorted([session_key(a), session_key(b)])}
            info["folders"] = {"a": rig.folders(a), "b": rig.folders(b), "c_waiting": rig.folders(c), "d_used_at_23h": rig.folders(d)}
            info["d_modes_after_sweep"] = {"upload": mode(od), "delivered": [mode(x) for x in d_out]}
            info["c_upload_mode_after_sweep"] = mode(oc)
            info["http_after_sweep"] = {
                "a_session": rig.req("GET", f"/api/v1/sessions/{a}").status_code,
                "a_download": rig.req("GET", f"/api/v1/sessions/{a}/files/d_{ta['turn_id']}_1/download").status_code,
                "b_session": rig.req("GET", f"/api/v1/sessions/{b}").status_code,
                "d_session": rig.req("GET", f"/api/v1/sessions/{d}").status_code,
                "d_download": rig.req("GET", f"/api/v1/sessions/{d}/files/d_{td['turn_id']}_1/download").status_code,
            }

        tc = p.turn(rig, c, "remove duplicate rows", oc, on_plan=during_plan)
        out.update(info)
        out["c_after_waiting"] = check_reply(tc)
        td2 = p.turn(rig, d, "remove empty rows", od)
        out["d_second_after_sweep"] = check_reply(td2)
        return out
    finally:
        rig.close()


def w2_windows_restart_sweep(speaker: str) -> dict[str, Any]:
    tmp = tempfile.TemporaryDirectory()
    data = Path(tmp.name) / "data"
    wall = p.Clock(1_800_000_000.0)
    try:
        one = p.Rig(speaker, p.h.FakeLLM(p.h.single("remove_duplicate_rows")), data=data, wall=wall)
        one.login_bearer()
        _, sid = p.new_session(one, speaker)
        _, _, orig = p.upload(one, sid, p.h.xlsx())
        t = p.turn(one, sid, "remove duplicate rows", orig)
        wall.advance(25 * p.HOUR)
        two = p.Rig(speaker, p.h.FakeLLM([]), data=data, wall=wall)
        two.login_bearer()
        with windows_rule():
            res = two.sweep()
        return {"first": check_reply(t), "removed": len(res.removed), "failed": len(res.failed), "folders": two.folders(sid),
                "session_http": two.req("GET", f"/api/v1/sessions/{sid}").status_code}
    finally:
        tmp.cleanup()


def pf_windows_partial_failure(speaker: str) -> dict[str, Any]:
    rig = p.Rig(speaker, p.h.FakeLLM(p.h.single("remove_duplicate_rows") * 2))
    try:
        rig.login_bearer()
        _, sid = p.new_session(rig, speaker)
        _, _, o1 = p.upload(rig, sid, p.h.xlsx(), "first.xlsx")
        _, _, o2 = p.upload(rig, sid, p.h.xlsx(), "second.xlsx")
        sha2 = p.h.sha(o2)
        t1 = p.turn(rig, sid, "remove duplicate rows", o1)
        rig.wall.advance(25 * p.HOUR)
        with windows_rule():
            EXTRA_REFUSE.add(str(o2))
            res = rig.sweep()
        left = {str(x.relative_to(rig.settings.data_dir)): mode(x) for x in sorted(rig.settings.data_dir.rglob("*")) if x.is_file()}
        files = rig.req("GET", f"/api/v1/sessions/{sid}/files")
        t2 = p.turn(rig, sid, "remove duplicate rows", o2 if o2.exists() else None)
        return {"first": check_reply(t1), "removed": len(res.removed), "failed": len(res.failed), "files_left_with_modes": left,
                "second_upload_sha_unchanged": o2.exists() and p.h.sha(o2) == sha2,
                "session_http_after": rig.req("GET", f"/api/v1/sessions/{sid}").status_code,
                "files_listed": files.json() if files.status_code == 200 else files.status_code, "returning_turn": check_reply(t2)}
    finally:
        rig.close()


def hl_hard_link(speaker: str) -> dict[str, Any]:
    rig = p.Rig(speaker, p.h.FakeLLM(p.h.single("remove_duplicate_rows")))
    outside_dir = tempfile.TemporaryDirectory()
    try:
        outside = Path(outside_dir.name) / "elsewhere.xlsx"
        outside.write_bytes(p.h.xlsx())
        outside.chmod(0o444)
        rig.login_bearer()
        _, sid = p.new_session(rig, speaker)
        _, _, orig = p.upload(rig, sid, p.h.xlsx())
        t = p.turn(rig, sid, "remove duplicate rows", orig)
        link = rig.settings.outputs_dir / session_key(sid) / t["turn_id"] / "hardlink.xlsx"
        os.link(outside, link)
        before = mode(outside)
        rig.wall.advance(25 * p.HOUR)
        res = rig.sweep()
        return {"outcome": t["outcome"], "removed": len(res.removed), "outside_mode_before": before, "outside_mode_after": mode(outside), "outside_still_there": outside.exists()}
    finally:
        rig.close()
        outside_dir.cleanup()


CASES = {f.__name__: f for f in (w1_windows_mixed_sweep, w2_windows_restart_sweep, pf_windows_partial_failure, hl_hard_link)}
PER_SPEAKER = {"w1_windows_mixed_sweep", "w2_windows_restart_sweep", "pf_windows_partial_failure"}


def main() -> None:
    only = sys.argv[sys.argv.index("--only") + 1] if "--only" in sys.argv else None
    os.environ.pop("WORKBUDDY_DEMO_PASSWORD", None)
    for name, fn in CASES.items():
        if only and only not in name:
            continue
        for sp in (p.pr3.SPEAKERS if name in PER_SPEAKER else ("template",)):
            try:
                res = fn(sp)
            except Exception as exc:  # noqa: BLE001
                res = {"PROBE_ERROR": f"{type(exc).__name__}: {exc}", "tb": traceback.format_exc()[-1500:]}
            print(json.dumps({"case": name, "speaker": sp, **res}, default=str), flush=True)


if __name__ == "__main__":
    main()

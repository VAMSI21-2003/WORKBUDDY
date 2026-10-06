# ruff: noqa: E501 — reviewer tooling, not product code
"""PR-4 review probe: access, limits, retention and quotas through the real app and pipeline.

Usage (from the repo root)::

    python scripts/review_harness/feat-backend-retention/pr4_probe.py [--only NAME] [--speaker S] > out.jsonl

Every case runs ``backend.main.create_app`` with production-like settings
(a placeholder password, an https public origin, the real buckets) and the
real pipeline (``build_engine``: real skills, Hands, Inspector, verifiers,
Voice) with only the LLM faked (``../feat-backend-api-core/harness.py``).
Both clocks are injected: each poll advances the monotonic clock by 2 s (the
frontend's cadence); retention advances the wall clock. One JSON line per
case and speaker: HTTP statuses, the reply, outcome, an independent openpyxl
read of each delivered file, the original's SHA-256 before and after, the
bucket levels, and whatever the case checks.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "feat-backend-api-confirmation"))
import pr3  # noqa: E402  (puts this tree and ../feat-backend-api-core on sys.path)
from fastapi.testclient import TestClient  # noqa: E402
from pr3 import h  # noqa: E402

from backend.main import create_app  # noqa: E402
from backend.sessions import session_key  # noqa: E402
from backend.settings import Settings  # noqa: E402

PASSWORD = "placeholder-demo-password"  # an obvious placeholder, never a real secret
ORIGIN = "https://demo.example"
COOKIE = "__Host-wb_access"
DEDUPE = "spreadsheet.deduplicate"
EMPTY = "spreadsheet.remove_empty_rows"
HOUR = 3600.0


class Clock:
    def __init__(self, now: float) -> None:
        self.now = now
        self._lock = threading.Lock()

    def __call__(self) -> float:
        with self._lock:
            return self.now

    def advance(self, s: float) -> None:
        with self._lock:
            self.now += s


class Rig:
    def __init__(self, speaker: str, llm: h.FakeLLM, data: Path | None = None, mono: Clock | None = None, wall: Clock | None = None, **overrides: Any) -> None:
        self.tmp = tempfile.TemporaryDirectory() if data is None else None
        self.data = data if data is not None else Path(self.tmp.name) / "data"
        values: dict[str, Any] = {"data_dir": self.data, "env": "production", "demo_password": PASSWORD, "public_origin": ORIGIN}
        values.update(overrides)
        self.settings = Settings(**values)
        self.factory = pr3.Factory(llm, speaker)
        self.mono = mono or Clock(10_000.0)
        self.wall = wall or Clock(1_800_000_000.0)
        self.app = create_app(self.settings, self.factory, clock=self.mono, wall_clock=self.wall)
        self.client = TestClient(self.app, raise_server_exceptions=False)
        self.headers: dict[str, str] = {}
        self.http: list[int] = []

    # credentials
    def login_cookie(self) -> int:
        r = self.client.post("/api/v1/access", data={"password": PASSWORD}, headers={"Origin": ORIGIN})
        if r.status_code == 204:
            token = r.headers["set-cookie"].split(";", 1)[0].split("=", 1)[1]
            self.headers = {"Cookie": f"{COOKIE}={token}", "Origin": ORIGIN}
        return r.status_code

    def login_bearer(self) -> None:
        self.headers = {"Authorization": f"Bearer {PASSWORD}"}

    def req(self, method: str, url: str, **kw: Any) -> Any:
        headers = {**self.headers, **kw.pop("headers", {})}
        r = self.client.request(method, url, headers=headers, **kw)
        self.http.append(r.status_code)
        return r

    def tokens(self, ip: str = "testclient") -> dict[str, float]:
        lim = self.app.state.limiter
        return {b: round(lim.tokens(ip, b), 2) for b in ("general", "poll", "turns", "uploads", "access")}

    def sweep(self) -> Any:
        return self.app.state.maintenance.run_once()

    def folders(self, sid: str) -> dict[str, bool]:
        key = session_key(sid)
        s = self.settings
        return {"uploads": (s.uploads_dir / key).exists(), "outputs": (s.outputs_dir / key).exists(), "sessions": (s.sessions_dir / key).exists()}

    def close(self) -> None:
        if self.tmp is not None:
            self.tmp.cleanup()


def new_session(rig: Rig, speaker: str) -> tuple[int, str | None]:
    mode = "focus" if speaker == "focus" else "friend"
    r = rig.req("POST", "/api/v1/sessions", json={"mode": mode})
    return r.status_code, (r.json().get("session_id") if r.status_code == 201 else None)


def upload(rig: Rig, sid: str, data: bytes, name: str = "report.xlsx") -> tuple[int, str | None, Path | None]:
    r = rig.req("POST", f"/api/v1/sessions/{sid}/files", files={"file": (name, data, "application/octet-stream")})
    if r.status_code != 201:
        return r.status_code, None, None
    fid = r.json()["file_id"]
    path = next(p for p in rig.settings.uploads_dir.rglob("*") if p.is_file() and p.parent.name == fid)
    return 201, fid, path


def drive(rig: Rig, sid: str, tid: str, decision: str | None = "approve", timeout_s: float = 60.0, on_plan: Any = None) -> dict[str, Any]:
    end = time.monotonic() + timeout_s
    statuses: list[str] = []
    polls = 0
    rate_limited = 0
    answered: set[str] = set()
    while True:
        rig.mono.advance(2.0)
        r = rig.req("GET", f"/api/v1/sessions/{sid}/turns/{tid}")
        polls += 1
        if r.status_code == 429:
            rate_limited += 1
        body = r.json()
        status = body.get("status", f"HTTP {r.status_code}")
        if not statuses or statuses[-1] != status:
            statuses.append(status)
        if status == "finished" or r.status_code in (401, 404):
            return {"statuses": statuses, "polls": polls, "poll_429s": rate_limited, "body": body}
        if status == "awaiting_confirmation":
            cid = body["pending_confirmation"]["confirmation_id"]
            if cid not in answered:
                answered.add(cid)
                if on_plan is not None:
                    on_plan(rig, sid, tid, body)
                if decision is not None:
                    a = rig.req("POST", f"/api/v1/sessions/{sid}/turns/{tid}/confirmation", json={"confirmation_id": cid, "decision": decision})
                    statuses.append(f"answer:{a.status_code}")
        if time.monotonic() > end:
            return {"statuses": statuses, "polls": polls, "poll_429s": rate_limited, "body": body, "TIMEOUT": True}
        time.sleep(0.01)


def delivered(rig: Rig, body: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for d in body.get("delivered_files") or []:
        r = rig.req("GET", d["download_url"])
        e: dict[str, Any] = {"name": d["name"], "http": r.status_code}
        if r.status_code == 200:
            e["sha_matches"] = h.sha(r.content) == d["sha256"]
            e["independent"] = h.describe(h.read_rows(r.content))
        out.append(e)
    return out


def turn(rig: Rig, sid: str, message: str, original: Path | None, decision: str | None = "approve", **kw: Any) -> dict[str, Any]:
    before = h.sha(original) if original else None
    r = rig.req("POST", f"/api/v1/sessions/{sid}/turns", json={"message": message})
    if r.status_code != 202:
        return {"start_http": r.status_code, "start_body": r.json()}
    tid = r.json()["turn_id"]
    d = drive(rig, sid, tid, decision, **kw)
    body = d["body"]
    after = h.sha(original) if original and original.exists() else ("<gone>" if original else None)
    return {
        "turn_id": tid,
        "statuses": d["statuses"],
        "polls": d["polls"],
        "poll_429s": d["poll_429s"],
        "outcome": body.get("outcome"),
        "reply_source": body.get("reply_source"),
        "reply": body.get("reply"),
        "detail_steps": [f"{s['capability']}[{s['status']}]" for s in ((body.get("detail") or {}).get("steps") or [])],
        "delivered": delivered(rig, body),
        "original_unchanged": before == after,
        "original_sha_before": before,
        "original_sha_after": after,
        "TIMEOUT": d.get("TIMEOUT", False),
    }


# -- Cases ---------------------------------------------------------------------------------


def c01_cookie_flow(speaker: str) -> dict[str, Any]:
    """The browser's flow: exchange, session, upload, dedupe approved, download, all by cookie."""
    rig = Rig(speaker, h.FakeLLM(h.single("remove_duplicate_rows")))
    try:
        login = rig.login_cookie()
        code, sid = new_session(rig, speaker)
        _, _, original = upload(rig, sid, h.xlsx())
        t = turn(rig, sid, "remove duplicate rows", original)
        state = rig.req("GET", f"/api/v1/sessions/{sid}").json()
        return {"login": login, "session": code, **t, "retention_hours": state.get("retention_hours"), "any_429": 429 in rig.http, "tokens": rig.tokens()}
    finally:
        rig.close()


def c02_bearer_chain(speaker: str) -> dict[str, Any]:
    """A script's flow (Bearer, no Origin): a two-step chain, approved."""
    rig = Rig(speaker, h.FakeLLM(h.chain((DEDUPE, "remove duplicate rows"), (EMPTY, "then remove empty rows"))))
    try:
        rig.login_bearer()
        _, sid = new_session(rig, speaker)
        _, _, original = upload(rig, sid, h.xlsx())
        t = turn(rig, sid, "remove duplicate rows, then remove empty rows", original)
        return {**t, "any_429": 429 in rig.http, "tokens": rig.tokens()}
    finally:
        rig.close()


def c03_cookie_reject(speaker: str) -> dict[str, Any]:
    rig = Rig(speaker, h.FakeLLM(h.single("remove_duplicate_rows")))
    try:
        rig.login_cookie()
        _, sid = new_session(rig, speaker)
        _, _, original = upload(rig, sid, h.xlsx())
        t = turn(rig, sid, "remove duplicate rows", original, decision="reject")
        written = sorted(p.name for p in (rig.settings.outputs_dir / session_key(sid)).rglob("*.xlsx"))
        return {**t, "written_anywhere": written}
    finally:
        rig.close()


def c04_cookie_confirm_without_origin(speaker: str) -> dict[str, Any]:
    """A cookie-authenticated answer with no Origin is 403: the plan stays, then times out."""
    rig = Rig(speaker, h.FakeLLM(h.single("remove_duplicate_rows")), confirmation_timeout_s=1.0)
    try:
        rig.login_cookie()
        _, sid = new_session(rig, speaker)
        _, _, original = upload(rig, sid, h.xlsx())
        seen: dict[str, Any] = {}

        def foreign(rig: Rig, sid: str, tid: str, body: dict[str, Any]) -> None:
            cid = body["pending_confirmation"]["confirmation_id"]
            for label, origin in (("none", None), ("foreign", "https://evil.example"), ("null", "null"), ("trailing_slash", ORIGIN + "/")):
                headers = {"Cookie": rig.headers["Cookie"]}
                if origin is not None:
                    headers["Origin"] = origin
                r = rig.client.post(f"/api/v1/sessions/{sid}/turns/{tid}/confirmation", json={"confirmation_id": cid, "decision": "approve"}, headers=headers)
                seen[label] = (r.status_code, r.json().get("error", {}).get("code"))

        t = turn(rig, sid, "remove duplicate rows", original, decision=None, on_plan=foreign)
        return {**t, "approve_attempts": seen}
    finally:
        rig.close()


def c05_retention_after_delivery(speaker: str) -> dict[str, Any]:
    """A delivered session idle past the TTL is gone (API and disk); one used at 23 h stays."""
    rig = Rig(speaker, h.FakeLLM(h.single("remove_duplicate_rows") + h.single("remove_duplicate_rows")))
    try:
        rig.login_bearer()
        _, old = new_session(rig, speaker)
        _, _, o1 = upload(rig, old, h.xlsx())
        t1 = turn(rig, old, "remove duplicate rows", o1)
        _, kept = new_session(rig, speaker)
        _, _, o2 = upload(rig, kept, h.xlsx())
        t2 = turn(rig, kept, "remove duplicate rows", o2)
        rig.wall.advance(23 * HOUR)
        rig.req("GET", f"/api/v1/sessions/{kept}/files")
        rig.wall.advance(1 * HOUR + 1)
        swept = rig.sweep()
        url_old = t1["delivered"][0]["name"] if t1["delivered"] else None
        return {
            "t1_outcome": t1["outcome"], "t2_outcome": t2["outcome"], "t1_reply": t1["reply"],
            "removed": len(swept.removed), "failed": len(swept.failed),
            "old_session_http": rig.req("GET", f"/api/v1/sessions/{old}").status_code,
            "old_folders": rig.folders(old),
            "kept_session_http": rig.req("GET", f"/api/v1/sessions/{kept}").status_code,
            "kept_folders": rig.folders(kept),
            "kept_download": rig.req("GET", f"/api/v1/sessions/{kept}/files/d_{t2['turn_id']}_1/download").status_code if t2["delivered"] else None,
            "old_delivered_name": url_old,
        }
    finally:
        rig.close()


def c06_retention_never_takes_a_waiting_plan(speaker: str) -> dict[str, Any]:
    """The wall clock jumps 25 h while a plan waits; the sweep runs; then the user approves."""
    rig = Rig(speaker, h.FakeLLM(h.single("remove_duplicate_rows")))
    try:
        rig.login_cookie()
        _, sid = new_session(rig, speaker)
        _, _, original = upload(rig, sid, h.xlsx())
        info: dict[str, Any] = {}

        def jump(rig: Rig, sid: str, tid: str, body: dict[str, Any]) -> None:
            rig.wall.advance(25 * HOUR)
            res = rig.sweep()
            info["removed_while_waiting"] = len(res.removed)
            info["folders_while_waiting"] = rig.folders(sid)

        t = turn(rig, sid, "remove duplicate rows", original, on_plan=jump)
        rig.wall.advance(25 * HOUR)
        res = rig.sweep()
        return {**t, **info, "removed_after_idle": len(res.removed), "folders_after": rig.folders(sid)}
    finally:
        rig.close()


def c07_retention_across_restarts(speaker: str) -> dict[str, Any]:
    """Used at +23 h in a second process, swept at +25 h in a third (kept) and +48 h (removed)."""
    tmp = tempfile.TemporaryDirectory()
    data = Path(tmp.name) / "data"
    wall = Clock(1_800_000_000.0)
    try:
        a = Rig(speaker, h.FakeLLM(h.single("remove_duplicate_rows")), data=data, wall=wall)
        a.login_bearer()
        _, sid = new_session(a, speaker)
        _, _, original = upload(a, sid, h.xlsx())
        t = turn(a, sid, "remove duplicate rows", original)
        wall.advance(23 * HOUR)
        b = Rig(speaker, h.FakeLLM([]), data=data, wall=wall)
        b.login_bearer()
        b_get = b.req("GET", f"/api/v1/sessions/{sid}").status_code
        wall.advance(2 * HOUR)
        c = Rig(speaker, h.FakeLLM([]), data=data, wall=wall)
        c.login_bearer()
        r25 = c.sweep()
        folders_25 = c.folders(sid)
        wall.advance(23 * HOUR)
        d = Rig(speaker, h.FakeLLM([]), data=data, wall=wall)
        d.login_bearer()
        r48 = d.sweep()
        return {"outcome": t["outcome"], "b_get": b_get, "removed_at_25h": len(r25.removed), "folders_25h": folders_25,
                "removed_at_48h": len(r48.removed), "folders_48h": d.folders(sid),
                "session_after_48h": d.req("GET", f"/api/v1/sessions/{sid}").status_code}
    finally:
        tmp.cleanup()


def c08_partial_sweep_then_turn(speaker: str) -> dict[str, Any]:
    """A sweep that deletes the uploads and then fails on the outputs; the user comes back and runs a turn."""
    import backend.retention as retention

    rig = Rig(speaker, h.FakeLLM(h.single("remove_duplicate_rows") + h.single("remove_duplicate_rows")))
    try:
        rig.login_bearer()
        _, sid = new_session(rig, speaker)
        _, fid, original = upload(rig, sid, h.xlsx())
        t1 = turn(rig, sid, "remove duplicate rows", original)
        rig.wall.advance(25 * HOUR)
        real = retention.remove_tree

        def failing(path: Path) -> None:
            if rig.settings.outputs_dir in Path(path).parents or Path(path) == rig.settings.outputs_dir / session_key(sid):
                raise PermissionError("simulated")
            real(path)

        retention.remove_tree = failing
        try:
            res = rig.sweep()
        finally:
            retention.remove_tree = real
        folders = rig.folders(sid)
        state = rig.req("GET", f"/api/v1/sessions/{sid}")
        files = rig.req("GET", f"/api/v1/sessions/{sid}/files")
        old_dl = rig.req("GET", f"/api/v1/sessions/{sid}/files/d_{t1['turn_id']}_1/download").status_code if t1["delivered"] else None
        t2 = turn(rig, sid, "remove duplicate rows", None)
        rig.wall.advance(1 * HOUR)
        res2 = rig.sweep()
        return {
            "t1_outcome": t1["outcome"], "failed": len(res.failed), "removed": len(res.removed), "folders_after_failed_sweep": folders,
            "session_http_after": state.status_code,
            "files_listed_after": [(f.get("name"), f.get("kind", f.get("downloadable"))) for f in (files.json().get("files") or [])] if files.status_code == 200 else files.status_code,
            "old_download_http": old_dl,
            "t2": {k: t2.get(k) for k in ("start_http", "start_body", "outcome", "reply_source", "reply", "detail_steps", "delivered")},
            "next_sweep_1h_later_removed": len(res2.removed),
        }
    finally:
        rig.close()


def c09_quotas(speaker: str) -> dict[str, Any]:
    rig = Rig(speaker, h.FakeLLM(h.single("remove_duplicate_rows") * 3), max_turns_per_session=1, max_uploads_per_session=1)
    try:
        rig.login_cookie()
        _, sid = new_session(rig, speaker)
        _, _, original = upload(rig, sid, h.xlsx())
        second_upload = rig.req("POST", f"/api/v1/sessions/{sid}/files", files={"file": ("b.xlsx", h.xlsx(), "application/octet-stream")})
        t1 = turn(rig, sid, "remove duplicate rows", original)
        t2 = turn(rig, sid, "remove duplicate rows", original)
        return {"second_upload": (second_upload.status_code, second_upload.json()), "t1_outcome": t1["outcome"], "t1_reply": t1["reply"], "t2": t2, "turns_in_state": len(rig.req("GET", f"/api/v1/sessions/{sid}").json()["turns"])}
    finally:
        rig.close()


def c10_storage_cap_and_a_turn(speaker: str) -> dict[str, Any]:
    """A cap just above one upload: the upload fits, a new session is 503, and a turn still writes."""
    rig = Rig(speaker, h.FakeLLM(h.single("remove_duplicate_rows")), max_total_bytes=len(h.xlsx()) + 2000)
    try:
        rig.login_bearer()
        _, sid = new_session(rig, speaker)
        up, _, original = upload(rig, sid, h.xlsx())
        rig.mono.advance(3)
        s2 = rig.req("POST", "/api/v1/sessions", json={})
        t = turn(rig, sid, "remove duplicate rows", original)
        from backend.retention import directory_bytes

        return {"upload": up, "second_session": (s2.status_code, s2.json().get("error")), "outcome": t["outcome"], "reply": t["reply"], "delivered": t["delivered"],
                "bytes_used": directory_bytes(rig.settings.data_dir), "cap": rig.settings.max_total_bytes}
    finally:
        rig.close()


def c11_guessing_and_retry_after(speaker: str) -> dict[str, Any]:
    rig = Rig(speaker, h.FakeLLM([]))
    try:
        codes = []
        for _ in range(12):
            r = rig.client.post("/api/v1/access", data={"password": "wrong"}, headers={"Origin": ORIGIN})
            codes.append((r.status_code, r.headers.get("retry-after")))
        before = rig.tokens()
        right_while_limited = rig.client.post("/api/v1/access", data={"password": PASSWORD}, headers={"Origin": ORIGIN}).status_code
        wait = int(codes[-1][1] or 0)
        rig.mono.advance(wait)
        right_after = rig.client.post("/api/v1/access", data={"password": PASSWORD}, headers={"Origin": ORIGIN}).status_code
        spoof = [rig.client.get("/api/v1/sessions/x", headers={"X-Forwarded-For": f"10.0.0.{i}"}).status_code for i in range(40)]
        return {"wrong_passwords": codes, "tokens_after_12": before, "right_password_while_limited": right_while_limited, "retry_after": wait, "right_after_wait": right_after,
                "spoofed_xff_unauth_gets": spoof.count(401), "spoofed_xff_429": spoof.count(429)}
    finally:
        rig.close()


def c12_hostile_credentials(speaker: str) -> dict[str, Any]:
    rig = Rig(speaker, h.FakeLLM([]))
    try:
        rig.login_cookie()
        token = rig.headers["Cookie"].split("=", 1)[1]
        import hashlib

        cases = {
            "cookie_is_password": {"Cookie": f"{COOKIE}={PASSWORD}"},
            "cookie_is_token_hash": {"Cookie": f"{COOKIE}={hashlib.sha256(token.encode()).hexdigest()}"},
            "cookie_other_name": {"Cookie": f"wb_access={token}"},
            "cookie_twice_bad_first": {"Cookie": f"{COOKIE}=bad; {COOKIE}={token}"},
            "cookie_twice_good_first": {"Cookie": f"{COOKIE}={token}; {COOKIE}=bad"},
            "bearer_trailing_space": {"Authorization": f"Bearer {PASSWORD} "},
            "bearer_two_spaces": {"Authorization": f"Bearer  {PASSWORD}"},
            "basic": {"Authorization": f"Basic {PASSWORD}"},
            "x_demo_password": {"X-Demo-Password": PASSWORD},
            "query_password": None,
        }
        out = {}
        for name, headers in cases.items():
            if headers is None:
                r = rig.client.post(f"/api/v1/sessions?password={PASSWORD}", json={})
            else:
                r = rig.client.post("/api/v1/sessions", json={}, headers={**headers, "Origin": ORIGIN})
            out[name] = r.status_code
            if PASSWORD in r.text or token in r.text:
                out[name + "_LEAK"] = True
        # paths around the routes, no credential: does any handler run?
        before = len(rig.app.state.registry.live_keys())
        paths = {}
        for method, path in (("POST", "/api/v1/sessions/"), ("POST", "//api/v1/sessions"), ("POST", "/api/v1//sessions"), ("POST", "/API/v1/sessions"), ("POST", "/api/v1/sessions?x=1"),
                             ("POST", "/api/v1/%73essions"), ("HEAD", "/api/v1/sessions/abc"), ("OPTIONS", "/api/v1/sessions"), ("GET", "/docs"), ("GET", "/openapi.json")):
            r = rig.client.request(method, path, json={} if method == "POST" else None, follow_redirects=False)
            paths[f"{method} {path}"] = r.status_code
        after = len(rig.app.state.registry.live_keys())
        return {"credentials": out, "unauthenticated_paths": paths, "sessions_created_without_credential": after - before}
    finally:
        rig.close()


def c13_gate_fails_closed(speaker: str) -> dict[str, Any]:
    rig = Rig(speaker, h.FakeLLM([]))
    try:
        rig.login_bearer()

        def boom(*a: Any, **k: Any) -> Any:
            raise RuntimeError("limiter broke gsk_FAKEFAKE")

        rig.app.state.limiter.charge = boom
        r = rig.req("POST", "/api/v1/sessions", json={})
        return {"http": r.status_code, "body": r.json(), "sessions_created": len(rig.app.state.registry.live_keys()), "secret_in_body": "gsk_" in r.text}
    finally:
        rig.close()


def c14_maintenance_interval_overflow(speaker: str) -> dict[str, Any]:
    """WORKBUDDY_SWEEP_INTERVAL_S above threading.TIMEOUT_MAX: does the loop keep sweeping?"""
    out: dict[str, Any] = {"timeout_max": threading.TIMEOUT_MAX}
    for interval in (1e10, 1e6):
        try:
            s = Settings.from_env({"WORKBUDDY_ENV": "dev", "WORKBUDDY_DATA_DIR": tempfile.mkdtemp(), "WORKBUDDY_SWEEP_INTERVAL_S": str(interval), "WORKBUDDY_SESSION_TTL_HOURS": "1"})
        except Exception as exc:  # noqa: BLE001
            out[str(interval)] = f"refused: {type(exc).__name__}"
            continue
        app = create_app(s, pr3.Factory(h.FakeLLM([]), speaker))
        loop = app.state.maintenance
        errors: list[str] = []
        old_hook = threading.excepthook
        threading.excepthook = lambda a: errors.append(a.exc_type.__name__)
        try:
            loop.start()
            time.sleep(0.5)
            alive = loop._thread.is_alive() if loop._thread else None
            loop._stop.set()
        finally:
            threading.excepthook = old_hook
        out[str(interval)] = {"accepted": True, "thread_alive_after_first_pass": alive, "thread_errors": errors, "retention_hours_advertised": s.retention_hours, "sweep_every_hours": round(interval / 3600, 1)}
    return out


def c15_unknown_and_wrong_routes_cost(speaker: str) -> dict[str, Any]:
    """With a valid cookie: what OPTIONS/HEAD and unknown paths cost, and whether a poll of a stranger's turn costs general."""
    rig = Rig(speaker, h.FakeLLM([]))
    try:
        rig.login_cookie()
        start = rig.tokens()
        rig.req("GET", "/api/v1/sessions/aaaa/turns/t0001")
        after_poll = rig.tokens()
        rig.req("HEAD", "/api/v1/healthz")
        rig.req("GET", "/healthz")
        after_health = rig.tokens()
        return {"start": start, "after_unknown_poll": after_poll, "after_health": after_health}
    finally:
        rig.close()


CASES = {f.__name__: f for f in (c01_cookie_flow, c02_bearer_chain, c03_cookie_reject, c04_cookie_confirm_without_origin, c05_retention_after_delivery,
                                 c06_retention_never_takes_a_waiting_plan, c07_retention_across_restarts, c08_partial_sweep_then_turn, c09_quotas,
                                 c10_storage_cap_and_a_turn, c11_guessing_and_retry_after, c12_hostile_credentials, c13_gate_fails_closed,
                                 c14_maintenance_interval_overflow, c15_unknown_and_wrong_routes_cost)}
PER_SPEAKER = {"c01_cookie_flow", "c02_bearer_chain", "c03_cookie_reject", "c04_cookie_confirm_without_origin", "c05_retention_after_delivery",
               "c06_retention_never_takes_a_waiting_plan", "c07_retention_across_restarts", "c08_partial_sweep_then_turn", "c09_quotas", "c10_storage_cap_and_a_turn"}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--only")
    p.add_argument("--speaker", choices=pr3.SPEAKERS)
    a = p.parse_args()
    os.environ.pop("WORKBUDDY_DEMO_PASSWORD", None)
    for name, fn in CASES.items():
        if a.only and a.only not in name:
            continue
        speakers = ((a.speaker,) if a.speaker else pr3.SPEAKERS) if name in PER_SPEAKER else ("template",)
        for sp in speakers:
            try:
                res = fn(sp)
            except Exception as exc:  # noqa: BLE001
                import traceback

                res = {"PROBE_ERROR": f"{type(exc).__name__}: {exc}", "tb": traceback.format_exc()[-1500:]}
            print(json.dumps({"case": name, "speaker": sp, **res}, default=str), flush=True)


if __name__ == "__main__":
    main()

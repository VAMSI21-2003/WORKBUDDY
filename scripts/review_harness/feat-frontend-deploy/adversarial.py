# ruff: noqa: E501 — reviewer tooling, not product code
"""FE-2 adversarial cases over HTTP (``TestClient``), real app and pipeline, fake LLM.

    python scripts/review_harness/feat-frontend-deploy/adversarial.py [--tree DIR] [--speaker S] > out/adversarial_head.jsonl

Reuses ``../feat-frontend-demo/fe1.py`` (``PageApp``, ``drive_page``, the HTML tree) and, through
it, the core harness's fake LLM and fixture. One JSON line per case and speaker.

- a01 hostile ``v`` values on a waiting turn's poll (negative, huge, text, float, empty, repeated).
- a02 a restart under a waiting plan (same data root, new process): the poll with the held version,
  the reload, and the reply against rule 1 (file read with openpyxl; original's SHA-256).
- a03 the same restart, but the page's next request is the lost-answer lookup
  (``?unconfirmed=``, no ``v``): what version the answer carries against what the page holds.
- a04 a restart under a finished turn, polled with a held version (hostile: the page never polls a
  finished turn).
- a05 static files: HEAD, Range, a stale ETag, upper-case and padded versions, two ``v``s, encoded
  traversal, a directory; every response's Cache-Control, Referrer-Policy and nosniff.
- a06 headers on every other kind of response the page meets: the gate, a 401, a 404, a 413, a
  422, a download, a 409 on the plan card, a poll; Referrer-Policy and no-store on each.
- a07 a restart under a waiting plan in password mode: what the poll gets (no HX-Refresh can
  reach a page whose access token died with the process).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "feat-frontend-demo"))
import fe1  # noqa: E402  (consumes --tree)
from fastapi.testclient import TestClient  # noqa: E402
from fe1 import h  # noqa: E402

from backend.main import create_app  # noqa: E402
from backend.settings import Settings  # noqa: E402

try:
    from backend.web.templating import static_url, static_version  # head only
except ImportError:  # base
    static_url = static_version = None  # type: ignore[assignment]


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def restarted(app: fe1.PageApp, speaker: str, **settings: Any) -> TestClient:
    """A new app (a new process's memory) on the same data root, with the old cookies."""
    values: dict[str, Any] = {"data_dir": app.settings.data_dir, "rate_limits": fe1.RELAXED}
    values.update(settings)
    new = create_app(Settings(**values), fe1.pr3.Factory(h.FakeLLM([]), speaker))
    client = TestClient(new, base_url="https://testserver", raise_server_exceptions=False)
    for cookie in app.client.cookies.jar:
        client.cookies.jar.set_cookie(cookie)
    client.headers["Origin"] = app.settings.public_origin
    return client


def plan_waiting(app: fe1.PageApp, sid: str) -> tuple[int, str]:
    """Start a dedupe turn and poll until its plan card is up: the held version and the confirmation id."""
    r = app.start(sid, "remove duplicate rows", "focus")
    assert r.status_code == 200, r.text[:200]
    v = 0
    for _ in range(300):
        p = app.poll(sid, "t0001", v)
        c = fe1.container_of(p.text, "t0001")
        if c is not None:
            v = int(c.attrs.get("data-version") or 0)
            form = c.find("form")
            if form is not None and c.find("section", cls="plan") is not None:
                cid = [i.attrs.get("value") for i in c.find_all("input") if i.attrs.get("name") == "confirmation_id"][0]
                return v, cid or ""
        time.sleep(0.05)
    raise AssertionError("no plan")


def headers_of(r: Any) -> dict[str, Any]:
    return {
        "status": r.status_code,
        "cache": r.headers.get("cache-control"),
        "referrer": r.headers.get("referrer-policy"),
        "nosniff": r.headers.get("x-content-type-options"),
        "hx_refresh": r.headers.get("hx-refresh"),
        "body_len": len(r.content),
    }


def a01(speaker: str) -> dict[str, Any]:
    app = fe1.PageApp(h.FakeLLM(h.single("remove_duplicate_rows")), speaker)
    try:
        sid = app.new_session()
        app.upload(sid, h.xlsx())
        held, _ = plan_waiting(app, sid)
        out = {"held": held}
        for raw in ["-1", "0", str(held), str(held + 1), str(10**18), "abc", "1.5", "", "99999999999999999999999999"]:
            r = app.client.get(f"/ui/s/{sid}/turns/t0001/poll", params={"v": raw})
            kind = "reload" if r.headers.get("hx-refresh") else ("poll-only" if 'class="poll"' in r.text and "data-turn-id" not in r.text else ("turn" if "data-turn-id" in r.text else "other"))
            out[f"v={raw}"] = {"status": r.status_code, "kind": kind}
        r = app.client.get(f"/ui/s/{sid}/turns/t0001/poll?v={held}&v={held + 5}")
        out["v twice"] = {"status": r.status_code, "hx_refresh": r.headers.get("hx-refresh")}
        out["api_status"] = app.api_turn(sid, "t0001")["status"]
        return out
    finally:
        app.close()


def a02(speaker: str) -> dict[str, Any]:
    app = fe1.PageApp(h.FakeLLM(h.single("remove_duplicate_rows")), speaker)
    try:
        sid = app.new_session()
        _, original = app.upload(sid, h.xlsx())
        before = sha(original)
        held, _ = plan_waiting(app, sid)
        client = restarted(app, speaker)
        r = client.get(f"/ui/s/{sid}/turns/t0001/poll", params={"v": held})
        page = client.get("/")
        c = fe1.container_of(page.text, "t0001")
        reply = fe1.reply_slot(c) if c is not None else None
        api = client.get(f"/api/v1/sessions/{sid}/turns/t0001").json()
        return {
            "held": held,
            "poll_after_restart": headers_of(r),
            "reloaded_status": c.attrs.get("data-status") if c is not None else None,
            "reloaded_version": c.attrs.get("data-version") if c is not None else None,
            "reloaded_has_poll": bool(c is not None and c.find(cls="poll")),
            "reloaded_has_plan": bool(c is not None and c.find("section", cls="plan")),
            "reply": reply,
            "reply_equals_api": reply == api.get("reply"),
            "api_outcome": api.get("outcome"),
            "delivered": [d["name"] for d in api.get("delivered_files") or []],
            "links_in_turn": fe1.links(c) if c is not None else None,
            "claim_words": h.claim_words(reply or ""),
            "original_sha_before": before,
            "original_sha_after": sha(original),
            "outputs_on_disk": sorted(p.name for p in app.settings.outputs_dir.rglob("*") if p.is_file()),
        }
    finally:
        app.close()


def a03(speaker: str) -> dict[str, Any]:
    app = fe1.PageApp(h.FakeLLM(h.single("remove_duplicate_rows")), speaker)
    try:
        sid = app.new_session()
        _, original = app.upload(sid, h.xlsx())
        before = sha(original)
        held, cid = plan_waiting(app, sid)
        client = restarted(app, speaker)
        r = client.get(f"/ui/s/{sid}/turns/t0001/poll", params={"unconfirmed": cid})
        c = fe1.container_of(r.text, "t0001")
        answer = client.post(f"/ui/s/{sid}/turns/t0001/confirmation", data={"confirmation_id": cid, "decision": "approve"})
        return {
            "held": held,
            "lookup": headers_of(r),
            "lookup_version": c.attrs.get("data-version") if c is not None else None,
            "swap_guard_would_drop": c is not None and held > int(c.attrs.get("data-version") or 0),
            "lookup_status": c.attrs.get("data-status") if c is not None else None,
            "stale_click_after_restart": {"status": answer.status_code, "body": answer.text[:200]},
            "original_unchanged": before == sha(original),
        }
    finally:
        app.close()


def a04(speaker: str) -> dict[str, Any]:
    app = fe1.PageApp(h.FakeLLM(h.single("remove_duplicate_rows")), speaker)
    try:
        sid = app.new_session()
        _, original = app.upload(sid, h.xlsx())
        before = sha(original)
        out = fe1.drive_page(app, sid, "remove duplicate rows", "focus", "approve")
        page = app.client.get("/")
        c = fe1.container_of(page.text, "t0001")
        held = int(c.attrs.get("data-version") or 0)
        client = restarted(app, speaker)
        r = client.get(f"/ui/s/{sid}/turns/t0001/poll", params={"v": held})
        download = [href for href, _ in fe1.links(c)]
        d = client.get(download[0]) if download else None
        return {
            "held": held,
            "reply": out.get("page_reply"),
            "poll_after_restart": headers_of(r),
            "download_after_restart": d.status_code if d is not None else None,
            "original_unchanged": before == sha(original),
        }
    finally:
        app.close()


def a05(speaker: str) -> dict[str, Any]:
    app = fe1.PageApp(h.FakeLLM([]), speaker)
    try:
        c = app.client
        out: dict[str, Any] = {}
        if static_version is None:
            names = {"app.js": None}
        else:
            names = {"app.js": static_version("app.js")}
        v = names["app.js"] or "x"
        cases = {
            "current": ("GET", f"/static/app.js?v={v}", {}),
            "HEAD current": ("HEAD", f"/static/app.js?v={v}", {}),
            "range current": ("GET", f"/static/app.js?v={v}", {"Range": "bytes=0-9"}),
            "stale etag current": ("GET", f"/static/app.js?v={v}", {"If-None-Match": '"stale"'}),
            "upper-case v": ("GET", f"/static/app.js?v={v.upper()}", {}),
            "padded v": ("GET", f"/static/app.js?v={v}%20", {}),
            "two v": ("GET", f"/static/app.js?v={v}&v={v}", {}),
            "v plus other param": ("GET", f"/static/app.js?v={v}&x=1", {}),
            "no v": ("GET", "/static/app.js", {}),
            "encoded traversal": ("GET", "/static/%2e%2e/routes.py", {}),
            "double-encoded traversal": ("GET", "/static/%252e%252e/routes.py", {}),
            "directory": ("GET", "/static/", {}),
            "missing": ("GET", f"/static/nope.js?v={v}", {}),
            "POST to static": ("POST", f"/static/app.js?v={v}", {}),
        }
        for name, (method, url, hdrs) in cases.items():
            r = c.request(method, url, headers=hdrs)
            out[name] = headers_of(r)
        if static_url is not None:
            page = c.get("/").text
            out["page_links"] = [u for u in ("styles.css", "htmx.min.js", "app.js") if static_url(u) in page]
        return out
    finally:
        app.close()


def a06(speaker: str) -> dict[str, Any]:
    app = fe1.PageApp(h.FakeLLM(h.single("remove_duplicate_rows") + h.single("remove_duplicate_rows")), speaker, password="pw for probe")
    try:
        c = app.client
        c.headers["Origin"] = app.settings.public_origin  # the browser sends it on every POST
        out: dict[str, Any] = {}
        out["gate GET /"] = headers_of(c.get("/"))
        out["401 api"] = headers_of(c.get("/api/v1/sessions/s_x"))
        out["401 ui"] = headers_of(c.post("/ui/sessions"))
        out["gate wrong password"] = headers_of(c.post("/api/v1/access", data={"password": "nope"}))
        gate = c.post("/api/v1/access", data={"password": "pw for probe"})
        out["gate POST"] = headers_of(gate)
        sid = app.new_session()
        out["404 session"] = headers_of(c.get("/ui/s/s_AAAAAAAAAAAAAAAAAAAAAA/turns/t0001/poll"))
        out["413 upload"] = headers_of(c.post(f"/ui/s/{sid}/files", files={"file": ("big.xlsx", b"x" * (3 * 1024 * 1024), "application/octet-stream")}))
        out["422/415 upload"] = headers_of(c.post(f"/ui/s/{sid}/files", files={"file": ("x.csv", b"a,b\n", "text/csv")}))
        _, original = app.upload(sid, h.xlsx())
        held, cid = plan_waiting(app, sid)
        out["poll"] = headers_of(app.poll(sid, "t0001", held))
        out["answer"] = headers_of(app.answer(sid, "t0001", cid, "approve"))
        out["409 second answer"] = headers_of(app.answer(sid, "t0001", cid, "reject"))
        for _ in range(200):
            t = app.api_turn(sid, "t0001")
            if t["status"] == "finished":
                break
            time.sleep(0.05)
        files = t.get("delivered_files") or []
        if files:
            out["download"] = headers_of(c.get(files[0]["download_url"]))
        return out
    finally:
        app.close()


def a07(speaker: str) -> dict[str, Any]:
    app = fe1.PageApp(h.FakeLLM(h.single("remove_duplicate_rows")), speaker, password="pw for probe")
    try:
        app.client.headers["Origin"] = app.settings.public_origin
        g = app.client.post("/api/v1/access", data={"password": "pw for probe"})
        assert g.status_code in (200, 204), g.status_code
        sid = app.new_session()
        app.upload(sid, h.xlsx())
        held, _ = plan_waiting(app, sid)
        client = restarted(app, speaker, env="", demo_password="pw for probe")
        r = client.get(f"/ui/s/{sid}/turns/t0001/poll", params={"v": held})
        return {"held": held, "poll_after_restart": headers_of(r), "body": r.text[:200]}
    finally:
        app.close()


CASES = {"a01": a01, "a02": a02, "a03": a03, "a04": a04, "a05": a05, "a06": a06, "a07": a07}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--speaker", choices=fe1.SPEAKERS)
    p.add_argument("--only")
    a = p.parse_args()
    for speaker in (a.speaker,) if a.speaker else fe1.SPEAKERS:
        for name, fn in CASES.items():
            if a.only and a.only not in name:
                continue
            try:
                row = {"case": name, "speaker": speaker, **fn(speaker)}
            except Exception as exc:  # noqa: BLE001
                row = {"case": name, "speaker": speaker, "PROBE_ERROR": repr(exc)}
            print(json.dumps(row, default=str), flush=True)


if __name__ == "__main__":
    _ = tempfile
    main()

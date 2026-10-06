# ruff: noqa: E501 — reviewer tooling, not product code
"""FE-1 adversarial probe over HTTP: hostile input, odd paths, races, a restart, a model that fails.

    python scripts/review_harness/feat-frontend-demo/adversarial.py [--speaker S] [--only NAME] > out/adversarial_head.jsonl

Every case runs the real app and pipeline (only the LLM faked) through the page's
routes, and prints one JSON line: what was sent, the HTTP status, what the page
showed (reply slot, links, notices, any raw markup that was not escaped), the
JSON API's view of the same turn, and the original's SHA-256 before and after.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fe1  # noqa: E402
from fe1 import h  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from backend.main import create_app  # noqa: E402
from backend.settings import Settings  # noqa: E402

DEDUPE = "spreadsheet.deduplicate"
EMPTY = "spreadsheet.remove_empty_rows"
HOSTILE = '<img src=x onerror=alert(1)>"\'{{7*7}}{% raw %}<script>alert(2)</script>'
STATIC_TAGS = {'<script src="/static/htmx.min.js" defer>', '<script src="/static/app.js" defer>'}


def raw_markup(html: str) -> list[str]:
    """Hostile markup that reached the HTML unescaped."""
    found = re.findall(r"<img[^>]*onerror[^>]*>|<script(?![^>]*src=\"/static/)[^>]*>|<b>x</b>|\b49\b", html)
    return sorted(set(found))


def basic(app: fe1.PageApp, speaker: str, message: str, fixture: bytes | None, filename: str = "report.xlsx", decision: str | None = "approve") -> dict[str, Any]:
    sid = app.new_session()
    original = None
    up = None
    if fixture is not None:
        r, original = app.upload(sid, fixture, filename)
        up = {"http": r.status_code, "raw_markup": raw_markup(r.text), "text": fe1.visible_text(fe1.parse(r.text))[:300]}
    before = h.sha(original) if original else None
    mode = "focus" if speaker == "focus" else "friend"
    out = fe1.drive_page(app, sid, message, mode, decision)
    tid = out.get("turn_id")
    api = app.api_turn(sid, tid) if tid else {}
    full = app.client.get("/")
    return {
        "upload": up,
        "statuses": out.get("statuses"),
        "plans": out.get("plans"),
        "page_reply": out.get("page_reply"),
        "api_reply": api.get("reply"),
        "page_equals_api": out.get("page_reply") == api.get("reply"),
        "api_outcome": api.get("outcome"),
        "links": out.get("page_turn_links"),
        "downloads": fe1.check_downloads(app, out.get("page_turn_links") or [], api),
        "full_page_raw_markup": raw_markup(full.text),
        "full_page_files_text": fe1.visible_text(fe1.parse(full.text).find("section", id="files"))[:400],
        "original_unchanged": before == (h.sha(original) if original else None),
        "sid": sid,
        "tid": tid,
    }


# -- cases ------------------------------------------------------------------------------


def c01_hostile_file_name(speaker: str) -> dict[str, Any]:
    app = fe1.PageApp(h.FakeLLM(h.single("remove_duplicate_rows")), speaker)
    try:
        r = basic(app, speaker, "remove duplicate rows", h.xlsx(), filename=HOSTILE + ".xlsx")
        r.pop("sid"), r.pop("tid")
        return r
    finally:
        app.close()


def c02_hostile_message(speaker: str) -> dict[str, Any]:
    app = fe1.PageApp(h.FakeLLM(h.single("remove_duplicate_rows")), speaker)
    try:
        r = basic(app, speaker, "remove duplicate rows " + HOSTILE, h.xlsx())
        r.pop("sid"), r.pop("tid")
        return r
    finally:
        app.close()


def c03_hostile_headers_in_file(speaker: str) -> dict[str, Any]:
    """Column names with markup; ask to analyze (the reply may quote the columns)."""
    rows = {"<b>x</b>": ["a", "a", "b"], HOSTILE: [1, 1, 2]}
    app = fe1.PageApp(h.FakeLLM(h.single("analyze_spreadsheet") ), speaker)
    try:
        r = basic(app, speaker, "describe this spreadsheet", h.xlsx(rows))
        r.pop("sid"), r.pop("tid")
        return r
    finally:
        app.close()


def c04_odd_paths_and_params(speaker: str) -> dict[str, Any]:
    app = fe1.PageApp(h.FakeLLM(h.single("remove_duplicate_rows")), speaker)
    try:
        sid = app.new_session()
        app.upload(sid, h.xlsx())
        app.start(sid, "remove duplicate rows", "friend")
        out = {}
        probes = {
            "poll_v_text": f"/ui/s/{sid}/turns/t0001/poll?v=abc",
            "poll_v_negative": f"/ui/s/{sid}/turns/t0001/poll?v=-1",
            "poll_v_huge": f"/ui/s/{sid}/turns/t0001/poll?v=99999999999999999999999",
            "poll_unconfirmed_markup": f"/ui/s/{sid}/turns/t0001/poll?unconfirmed=%3Cscript%3E",
            "poll_turn_traversal": f"/ui/s/{sid}/turns/..%2F..%2Fx/poll",
            "poll_turn_crlf": f"/ui/s/{sid}/turns/t0001%0d%0aX-Evil:%201/poll",
            "poll_unknown_session": "/ui/s/s_AAAAAAAAAAAAAAAAAAAAAA/turns/t0001/poll",
            "poll_bad_session": "/ui/s/..%2F..%2Fetc/turns/t0001/poll",
            "transcript_after_text": f"/ui/s/{sid}/transcript?after=abc",
            "transcript_after_negative": f"/ui/s/{sid}/transcript?after=-5",
            "transcript_after_huge": f"/ui/s/{sid}/transcript?after=999999",
            "turn_unknown": f"/ui/s/{sid}/turns/t9999/poll",
        }
        for k, url in probes.items():
            r = app.client.get(url)
            out[k] = {
                "http": r.status_code,
                "content_type": r.headers.get("content-type"),
                "body": r.text[:160],
                "headers_of_note": {n: v for n, v in r.headers.items() if n.lower().startswith(("hx-", "x-evil"))},
                "raw_markup": raw_markup(r.text),
            }
        for k, data in {
            "turn_mode_bogus": {"message": "hi", "mode": "bogus"},
            "turn_empty": {"message": "", "mode": "friend"},
            "turn_too_long": {"message": "x" * 100_000, "mode": "friend"},
        }.items():
            r = app.client.post(f"/ui/s/{sid}/turns", data=data)
            out[k] = {"http": r.status_code, "body": r.text[:200]}
        for k, data in {
            "answer_bad_decision": {"confirmation_id": "c_x", "decision": "maybe"},
            "answer_missing": {},
        }.items():
            r = app.client.post(f"/ui/s/{sid}/turns/t0001/confirmation", data=data)
            out[k] = {"http": r.status_code, "body": r.text[:200]}
        return out
    finally:
        app.close()


def c05_pointer_cookie(speaker: str) -> dict[str, Any]:
    app = fe1.PageApp(h.FakeLLM(h.single("remove_duplicate_rows")), speaker)
    try:
        a = app.new_session()
        out: dict[str, Any] = {}
        for label, value in {
            "junk": "x",
            "traversal": "../../../../etc/passwd",
            "unknown_shape_ok": "s_AAAAAAAAAAAAAAAAAAAAAA",
            "markup": "<script>",
        }.items():
            c = TestClient(app.app, base_url="https://testserver")
            c.cookies.set("__Host-wb_session", value)
            r = c.get("/")
            out[label] = {
                "http": r.status_code,
                "notice": fe1.visible_text(fe1.parse(r.text).find("div", id="page-notices") or fe1.parse("")),
                "clears_pointer": "__Host-wb_session=" in (r.headers.get("set-cookie") or ""),
                "raw_markup": raw_markup(r.text),
            }
        # A pointer to another (existing) session: the page renders it, as the API would for that id.
        c = TestClient(app.app, base_url="https://testserver")
        c.cookies.set("__Host-wb_session", a)
        r = c.get("/")
        out["other_existing_session"] = {"http": r.status_code, "renders_it": f'data-session-id="{a}"' in r.text}
        return out
    finally:
        app.close()


def c06_cross_session_fragment(speaker: str) -> dict[str, Any]:
    app = fe1.PageApp(h.FakeLLM(h.single("remove_duplicate_rows")), speaker)
    try:
        a = app.new_session()
        app.upload(a, h.xlsx())
        fe1.drive_page(app, a, "remove duplicate rows", "friend", "approve")
        b = app.new_session()
        r = app.poll(b, "t0001", 0)
        r2 = app.client.get(f"/ui/s/{b}/transcript")
        return {
            "b_poll_a_turn_http": r.status_code,
            "b_poll_body": r.text[:120],
            "b_transcript_mentions_a": a in r2.text,
            "b_transcript_has_turn": "data-turn-id" in r2.text,
        }
    finally:
        app.close()


def _to_plan(app: fe1.PageApp, sid: str, message: str = "remove duplicate rows") -> tuple[str, str, int]:
    r = app.start(sid, message, "friend")
    tid = re.search(r'data-turn-id="(t\d+)"', r.text).group(1)
    held = 0
    for _ in range(3000):
        pr = app.poll(sid, tid, held)
        c = fe1.container_of(pr.text, tid)
        if c is not None:
            held = int(c.attrs.get("data-version"))
            inp = c.find("input", name="confirmation_id")
            if inp is not None:
                return tid, inp.attrs["value"], held
        time.sleep(0.01)
    raise AssertionError("no plan")


def c07_answer_races(speaker: str) -> dict[str, Any]:
    app = fe1.PageApp(h.FakeLLM(h.single("remove_duplicate_rows")), speaker)
    try:
        sid = app.new_session()
        _, original = app.upload(sid, h.xlsx())
        before = h.sha(original)
        tid, cid, held = _to_plan(app, sid)
        lost_pending = app.poll(sid, tid, held, unconfirmed=cid)
        lost_c = fe1.container_of(lost_pending.text, tid)
        stale = app.answer(sid, tid, "c_notthisone_xxxxxxxxxx", "approve")
        first = app.answer(sid, tid, cid, "approve")
        second = app.answer(sid, tid, cid, "reject")  # a second tab, same card
        after_lost = app.poll(sid, tid, held, unconfirmed=cid)
        ac = fe1.container_of(after_lost.text, tid)
        # finish
        res = fe1.drive_page.__wrapped__ if hasattr(fe1.drive_page, "__wrapped__") else None  # noqa: F841
        end = time.monotonic() + 30
        c = ac
        v = int(ac.attrs.get("data-version"))
        while c.attrs.get("data-status") != "finished" and time.monotonic() < end:
            pr = app.poll(sid, tid, v)
            cc = fe1.container_of(pr.text, tid)
            if cc is not None:
                c, v = cc, int(cc.attrs.get("data-version"))
            time.sleep(0.02)
        api = app.api_turn(sid, tid)
        return {
            "unconfirmed_while_pending": {
                "http": lost_pending.status_code,
                "card_text": fe1.visible_text(lost_c.find("section", cls="plan")) if lost_c is not None else None,
            },
            "stale_answer": {"http": stale.status_code, "body": stale.text[:160]},
            "first_answer": {"http": first.status_code, "status": (fe1.container_of(first.text, tid) or fe1.Node("x", {})).attrs.get("data-status")},
            "second_answer": {"http": second.status_code, "body": second.text[:160]},
            "unconfirmed_after_answer": {
                "http": after_lost.status_code,
                "status": ac.attrs.get("data-status") if ac is not None else None,
                "has_card": ac.find("section", cls="plan") is not None if ac is not None else None,
                "text": fe1.visible_text(ac)[:200] if ac is not None else None,
            },
            "final_page_reply": fe1.reply_slot(c),
            "api_reply": api.get("reply"),
            "api_confirmation": (api.get("detail") or {}).get("confirmation"),
            "original_unchanged": before == h.sha(original),
        }
    finally:
        app.close()


def c08_busy_session(speaker: str) -> dict[str, Any]:
    app = fe1.PageApp(h.FakeLLM(h.single("remove_duplicate_rows") + h.single("remove_empty_rows")), speaker)
    try:
        sid = app.new_session()
        app.upload(sid, h.xlsx())
        tid, cid, held = _to_plan(app, sid)
        up, _ = app.upload(sid, h.xlsx(), "second.xlsx")
        st = app.start(sid, "remove empty rows", "friend")
        full = app.client.get("/")
        tree = fe1.parse(full.text)
        files = tree.find("section", id="files")
        composer = tree.find("form", id="composer")
        return {
            "upload_while_waiting": {"http": up.status_code, "body": up.text[:160]},
            "turn_while_waiting": {"http": st.status_code, "body": st.text[:160]},
            "full_page_upload_disabled": "disabled" in (files.find("input", id="file").attrs or {}),
            "full_page_send_disabled": "disabled" in (composer.find("textarea").attrs or {}),
            "full_page_title": (tree.find("title").text() if tree.find("title") else None),
        }
    finally:
        app.close()


def c09_pipeline_fails_halfway(speaker: str) -> dict[str, Any]:
    """The model raises at the planner of a chain (after Brain and Think); then a second turn raises in Brain."""
    replies = [h.understand("multi_step_request", 0.9), h.think(), h.raising(RuntimeError("boom gsk_SECRETSECRET /tmp/path <b>x</b>"))]
    app = fe1.PageApp(h.FakeLLM(replies), speaker)
    try:
        r = basic(app, speaker, "remove duplicate rows, then remove empty rows", h.xlsx())
        r["secret_or_exception_in_page"] = any(s in json.dumps(r) for s in ("gsk_", "boom", "RuntimeError", "/tmp/path"))
        r.pop("sid"), r.pop("tid")
        return r
    finally:
        app.close()


def c10_misread_intent(speaker: str) -> dict[str, Any]:
    """User asks for empty rows; the model says duplicates. The card shows what will run."""
    app = fe1.PageApp(h.FakeLLM(h.single("remove_duplicate_rows")), speaker)
    try:
        r = basic(app, speaker, "remove the empty rows", h.xlsx(), decision="approve")
        r.pop("sid"), r.pop("tid")
        return r
    finally:
        app.close()


def c11_active_file_after_delivery(speaker: str) -> dict[str, Any]:
    """After a delivery, which file does the page say is active, and what does the next turn work on?"""
    app = fe1.PageApp(h.FakeLLM(h.single("remove_duplicate_rows") + h.single("remove_empty_rows")), speaker)
    try:
        sid = app.new_session()
        app.upload(sid, h.xlsx())
        t1 = fe1.drive_page(app, sid, "remove duplicate rows", "friend", "approve")
        page = app.client.get("/")
        files_text = fe1.visible_text(fe1.parse(page.text).find("section", id="files"))
        state = app.client.get(f"/api/v1/sessions/{sid}").json()
        t2 = fe1.drive_page(app, sid, "remove empty rows", "friend", "approve")
        return {
            "files_panel_after_t1": files_text,
            "api_active_file_id": state.get("active_file_id"),
            "api_files": [(f["file_id"], f["name"], f.get("kind")) for f in state.get("files", [])],
            "t2_plan_steps": [p["steps"] for p in t2.get("plans", [])],
            "t2_reply": t2.get("page_reply"),
            "t1_reply": t1.get("page_reply"),
        }
    finally:
        app.close()


def c12_restart_version_regression(speaker: str) -> dict[str, Any]:
    """Dev (no password): a page holding a turn at version >= 1, the server restarts, the page polls."""
    llm = h.FakeLLM(h.single("remove_duplicate_rows"))
    app = fe1.PageApp(llm, speaker)
    try:
        sid = app.new_session()
        app.upload(sid, h.xlsx())
        tid, cid, held = _to_plan(app, sid)
        # A second server life on the same data root (the first one's memory is gone).
        app2 = create_app(app.settings, fe1.pr3.Factory(h.FakeLLM([]), speaker))
        c2 = TestClient(app2, base_url="https://testserver")
        pr = c2.get(f"/ui/s/{sid}/turns/{tid}/poll", params={"v": str(held)})
        cont = fe1.container_of(pr.text, tid)
        return {
            "page_holds_version": held,
            "poll_http": pr.status_code,
            "response_status": cont.attrs.get("data-status") if cont is not None else None,
            "response_version": int(cont.attrs.get("data-version")) if cont is not None else None,
            "swap_guard_would_drop": cont is not None and held > int(cont.attrs.get("data-version")),
            "reply": fe1.reply_slot(cont) if cont is not None else None,
        }
    finally:
        app.close()


def c13_production_cookie_and_origin(speaker: str) -> dict[str, Any]:
    """Production-like access: a password and an explicit origin. The page's routes keep the API's gate."""
    app = fe1.PageApp(h.FakeLLM(h.single("remove_duplicate_rows")), speaker, password="pw", public_origin="https://testserver")
    try:
        c = app.client
        out: dict[str, Any] = {}
        g = c.get("/")
        out["gate_without_cookie"] = {"http": g.status_code, "is_gate": 'id="gate-form"' in g.text, "creates_session": 'hx-post="/ui/sessions"' in g.text}
        out["ui_post_without_cookie"] = c.post("/ui/sessions", headers={"Origin": "https://testserver"}).status_code
        bad = c.post("/api/v1/access", data={"password": "nope"}, headers={"Origin": "https://testserver"})
        out["wrong_password"] = {"http": bad.status_code, "body": bad.text[:120]}
        ok = c.post("/api/v1/access", data={"password": "pw"}, headers={"Origin": "https://testserver"})
        out["right_password"] = {"http": ok.status_code, "set_cookie_names": re.findall(r"(__Host-[\w]+)=", ok.headers.get("set-cookie", ""))}
        out["ui_post_no_origin"] = c.post("/ui/sessions").status_code
        out["ui_post_foreign_origin"] = c.post("/ui/sessions", headers={"Origin": "https://evil.example"}).status_code
        out["ui_post_localhost_alias"] = c.post("/ui/sessions", headers={"Origin": "http://localhost:8000"}).status_code
        made = c.post("/ui/sessions", headers={"Origin": "https://testserver"})
        out["ui_post_right_origin"] = made.status_code
        out["workspace_get"] = 'id="workspace"' in c.get("/").text
        page = c.get("/")
        out["page_headers"] = {k: page.headers.get(k) for k in ("content-security-policy", "cache-control", "x-content-type-options", "referrer-policy", "x-frame-options")}
        out["password_in_page"] = "pw\"" in page.text or ">pw<" in page.text
        sid = re.search(r'data-session-id="([^"]+)"', made.text).group(1)
        out["poll_get_foreign_origin"] = c.get(f"/ui/s/{sid}/transcript", headers={"Origin": "https://evil.example"}).status_code
        return out
    finally:
        app.close()


def c14_session_quota_message(speaker: str) -> dict[str, Any]:
    app = fe1.PageApp(h.FakeLLM(h.single("unclear") * 3), speaker, max_turns_per_session=1)
    try:
        sid = app.new_session()
        fe1.drive_page(app, sid, "hi", "friend", None)
        r = app.start(sid, "hi again", "friend")
        page = app.client.get("/")
        return {
            "http": r.status_code,
            "message": r.text[:200],
            "page_has_new_session_control": bool(re.search(r"(?i)new session", fe1.visible_text(fe1.parse(page.text)))),
        }
    finally:
        app.close()


def c15_timeout_card(speaker: str) -> dict[str, Any]:
    """Nobody answers; the server's confirmation timeout runs out."""
    app = fe1.PageApp(h.FakeLLM(h.single("remove_duplicate_rows")), speaker, confirmation_timeout_s=0.6)
    try:
        r = basic(app, speaker, "remove duplicate rows", h.xlsx(), decision=None)
        r.pop("sid"), r.pop("tid")
        return r
    finally:
        app.close()


def c16_gone_session_then_create_fails(speaker: str) -> dict[str, Any]:
    """The pointer names a swept session, and the page's own POST /ui/sessions is then refused (the general bucket is spent)."""
    limits = dict(fe1.RELAXED)
    limits["general"] = fe1.settings_module.BucketSpec(burst=1, per_hour=1)
    app = fe1.PageApp(h.FakeLLM([]), speaker, rate_limits=limits)
    try:
        c = app.client
        c.post("/ui/sessions")  # spends the one token
        c.cookies.set("__Host-wb_session", "s_AAAAAAAAAAAAAAAAAAAAAA")
        page = c.get("/")
        notice = fe1.visible_text(fe1.parse(page.text).find("div", id="page-notices"))
        made = c.post("/ui/sessions")
        return {"page_notice": notice, "create_http": made.status_code, "create_body": made.text[:200]}
    finally:
        app.close()


CASES = {k: v for k, v in globals().items() if re.match(r"c\d\d_", k)}


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
                result = fn(speaker)
            except Exception as exc:  # noqa: BLE001
                result = {"PROBE_ERROR": repr(exc)}
            print(json.dumps({"case": name, "speaker": speaker, **result}, default=str), flush=True)


if __name__ == "__main__":
    main()

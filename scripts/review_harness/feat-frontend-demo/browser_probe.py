# ruff: noqa: E501 — reviewer tooling, not product code
"""FE-1 browser probe: the page in Chromium against the real app (real pipeline, fake LLM).

    python scripts/review_harness/feat-frontend-demo/browser_probe.py [--only NAME] [--speaker S] > out/browser_probe.jsonl

Needs ``playwright`` and the Chromium in ``/opt/pw-browsers``. The app runs under uvicorn on a free
loopback port, with the production rate limits, and a demo password unless the case says dev.
Cases:

- b01_restart_while_plan_waits: dev (no password, so a restart doesn't end the access cookie).
  The page shows a plan; the server restarts on the same data root and port; does the page ever
  show the turn as the server now has it (finished, failed)?
- b02_hostile_name_and_message: markup in the file name and the message; dialogs, CSP reports,
  elements made from it.
- b03_two_tabs_answer_the_same_plan: tab A approves, tab B (same cookies) then rejects its stale card.
- b04_double_click_approve: two fast clicks on Approve.
- b05_long_name_at_360: a 100-character file name; delivered link at 360 px; horizontal overflow.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fe1  # noqa: E402
from fe1 import h  # noqa: E402

import uvicorn  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

from backend.main import create_app  # noqa: E402
from backend.settings import Settings  # noqa: E402

PASSWORD = "probe password"
HOSTILE = '<img src=x onerror=alert(1)>{{7*7}}<script>alert(2)</script>'


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class Server:
    def __init__(self, llm: Any, speaker: str, data_dir: Path, port: int, *, password: str | None = PASSWORD, **settings: Any) -> None:
        values: dict[str, Any] = {"data_dir": data_dir, "port": port}
        if password is not None:
            values.update(env="", demo_password=password)
        values.update(settings)
        self.settings = Settings(**values)
        self.factory = fe1.pr3.Factory(llm, speaker)
        self.app = create_app(self.settings, self.factory)
        self.server = uvicorn.Server(uvicorn.Config(self.app, host="127.0.0.1", port=port, log_level="warning"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        end = time.monotonic() + 15
        while not self.server.started and time.monotonic() < end:
            time.sleep(0.02)
        assert self.server.started
        self.base = f"http://127.0.0.1:{port}"

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(10)

    def api_turn(self, sid: str, tid: str = "t0001") -> dict[str, Any]:
        import httpx

        headers = {"Authorization": f"Bearer {PASSWORD}"} if self.settings.demo_password else {}
        return httpx.get(f"{self.base}/api/v1/sessions/{sid}/turns/{tid}", headers=headers, timeout=10).json()


def watch(page: Any) -> dict[str, list[Any]]:
    w: dict[str, list[Any]] = {"dialogs": [], "console_errors": [], "requests": [], "page_errors": []}
    page.on("dialog", lambda d: (w["dialogs"].append(d.message), d.dismiss()))
    page.on("console", lambda m: w["console_errors"].append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: w["page_errors"].append(str(e)))
    page.on("response", lambda r: w["requests"].append((r.status, r.url.split("?")[0].rsplit("/", 2)[-2:])))
    return w


def sign_in(page: Any, base: str, password: str | None) -> None:
    page.goto(base + "/")
    if password is not None:
        page.fill("#password", password)
        page.click("#gate-form button[type=submit]")
    page.wait_for_selector("#workspace[data-session-id]")


def upload(page: Any, data: bytes, name: str = "report.xlsx") -> None:
    page.set_input_files("#file", files=[{"name": name, "mimeType": "application/octet-stream", "buffer": data}])
    page.click("#upload-form button[type=submit]")
    page.wait_for_selector("#files .file-list li:not(.empty)")


def send(page: Any, message: str) -> None:
    page.fill("#message", message)
    page.click("#composer button[type=submit]")


def wait_plan(page: Any) -> None:
    page.wait_for_selector(".turn .plan .plan-actions button[value=reject]")


def wait_finished(page: Any, timeout_ms: int = 30_000) -> None:
    page.wait_for_selector('.turn[data-status="finished"] [data-slot="reply"]', timeout=timeout_ms)


def originals(settings: Settings) -> dict[str, str]:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in settings.uploads_dir.rglob("*") if p.is_file()}


def state_of(page: Any) -> dict[str, Any]:
    return page.evaluate(
        """() => {
        const t = document.querySelector('.turn:last-of-type') || document.querySelector('.turn');
        const reply = t && t.querySelector('[data-slot="reply"]');
        return {
          status: t && t.dataset.status, version: t && t.dataset.version,
          reply: reply ? reply.textContent : null,
          card: !!(t && t.querySelector('.plan')),
          links: Array.from(document.querySelectorAll('.turn a[download]')).map(a => a.textContent.trim()),
          notices: Array.from(document.querySelectorAll('#page-notices .notice')).map(n => n.textContent),
          error_slots: Array.from(document.querySelectorAll('.error-slot')).map(n => n.textContent).filter(Boolean),
          progress: !!(t && t.querySelector('.progress')),
          polls: document.querySelectorAll('.poll').length,
          send_disabled: !!document.querySelector('#composer button[type=submit]')?.disabled,
          title: document.title,
          overflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
        };
      }"""
    )


# -- cases --------------------------------------------------------------------------


def b01_restart_while_plan_waits(pw: Any, speaker: str) -> dict[str, Any]:
    tmp = tempfile.TemporaryDirectory()
    port = free_port()
    data = Path(tmp.name) / "data"
    a = Server(h.FakeLLM(h.single("remove_duplicate_rows")), speaker, data, port, password=None)
    browser = pw.chromium.launch()
    try:
        page = browser.new_context(viewport={"width": 1000, "height": 800}).new_page()
        w = watch(page)
        sign_in(page, a.base, None)
        sid = page.get_attribute("#workspace", "data-session-id")
        upload(page, h.xlsx())
        before = originals(a.settings)
        send(page, "remove duplicate rows")
        wait_plan(page)
        held = state_of(page)
        a.stop()
        b = Server(h.FakeLLM([]), speaker, data, port, password=None)
        page.wait_for_timeout(20_000)
        after_20s = state_of(page)
        api = b.api_turn(sid)
        # The user clicks Approve on the card that is still there.
        clicked = None
        if page.locator(".turn .plan button[value=approve]").count():
            page.click(".turn .plan button[value=approve]")
            page.wait_for_timeout(5_000)
            clicked = state_of(page)
        polls = [r for r in w["requests"] if r[1][-1] == "poll"]
        b.stop()
        return {
            "held_before_restart": held,
            "page_after_20s": after_20s,
            "after_clicking_approve": clicked,
            "server_turn": {"status": api.get("status"), "outcome": api.get("outcome"), "reply": api.get("reply")},
            "polls_answered_200": sum(1 for r in polls if r[0] == 200),
            "page_shows_server_state": after_20s["status"] == api.get("status"),
            "original_unchanged": before == originals(b.settings),
        }
    finally:
        browser.close()
        tmp.cleanup()


def b02_hostile_name_and_message(pw: Any, speaker: str) -> dict[str, Any]:
    tmp = tempfile.TemporaryDirectory()
    s = Server(h.FakeLLM(h.single("remove_duplicate_rows")), speaker, Path(tmp.name) / "d", free_port())
    browser = pw.chromium.launch()
    try:
        page = browser.new_context().new_page()
        w = watch(page)
        sign_in(page, s.base, PASSWORD)
        sid = page.get_attribute("#workspace", "data-session-id")
        upload(page, h.xlsx(), HOSTILE + ".xlsx")
        before = originals(s.settings)
        send(page, "remove duplicate rows " + HOSTILE)
        wait_plan(page)
        page.click(".turn .plan button[value=approve]")
        wait_finished(page)
        st = state_of(page)
        made = page.evaluate("() => document.querySelectorAll('img, body script:not([src])').length")
        api = s.api_turn(sid)
        return {
            "dialogs": w["dialogs"],
            "elements_from_input": made,
            "csp_or_js_errors": [e for e in w["console_errors"] + w["page_errors"] if "status of" not in e],
            "message_shown": page.text_content('.turn [data-slot="message"]'),
            "files_text": page.text_content("#files"),
            "reply_equals_api": st["reply"] == api.get("reply"),
            "links": st["links"],
            "original_unchanged": before == originals(s.settings),
        }
    finally:
        browser.close()
        s.stop()
        tmp.cleanup()


def b03_two_tabs_answer_the_same_plan(pw: Any, speaker: str) -> dict[str, Any]:
    tmp = tempfile.TemporaryDirectory()
    s = Server(h.FakeLLM(h.chain(("spreadsheet.deduplicate", "remove duplicate rows"), ("spreadsheet.remove_empty_rows", "then remove empty rows"))), speaker, Path(tmp.name) / "d", free_port())
    browser = pw.chromium.launch()
    try:
        ctx = browser.new_context()
        a = ctx.new_page()
        wa = watch(a)
        sign_in(a, s.base, PASSWORD)
        sid = a.get_attribute("#workspace", "data-session-id")
        upload(a, h.xlsx())
        before = originals(s.settings)
        send(a, "remove duplicate rows, then remove empty rows")
        wait_plan(a)
        b = ctx.new_page()
        wb = watch(b)
        b.goto(s.base + "/")
        wait_plan(b)
        # Hold tab B's polls (as a slow network would) so its card is still up when A has answered.
        held: list[Any] = []
        b.route("**/poll*", lambda route: held.append(route))
        a.click(".turn .plan button[value=approve]")
        wait_finished(a)
        b.click(".turn .plan button[value=reject]")
        b.unroute("**/poll*")
        for route in held:
            try:
                route.continue_()
            except Exception:  # noqa: BLE001
                pass
        b.wait_for_timeout(500)
        b_right_after = state_of(b)
        try:
            wait_finished(b, 15_000)
        except Exception:  # noqa: BLE001
            pass
        api = s.api_turn(sid)
        sb = state_of(b)
        sa = state_of(a)
        return {
            "b_right_after_reject": b_right_after,
            "b_final": sb,
            "a_final_reply_equals_api": sa["reply"] == api.get("reply"),
            "b_final_reply_equals_api": sb["reply"] == api.get("reply"),
            "api_confirmation": (api.get("detail") or {}).get("confirmation"),
            "api_outcome": api.get("outcome"),
            "b_responses_4xx": [r for r in wb["requests"] if r[0] >= 400],
            "a_responses_4xx": [r for r in wa["requests"] if r[0] >= 400],
            "original_unchanged": before == originals(s.settings),
        }
    finally:
        browser.close()
        s.stop()
        tmp.cleanup()


def b06_stale_card_409_refetch(pw: Any, speaker: str) -> dict[str, Any]:
    """Tab B answers a plan tab A already answered. Is a refetch sent after B's 409 (design 1.7)?

    Variant ``in_flight``: B's poll is in flight when B clicks (held by a route, as on a slow
    network). Variant ``idle``: B clicks just after one of its polls came back, so none is in flight.
    For each, the poll requests B sends in the 1.5 s after its 409, and B's state 8 s later.
    """
    out: dict[str, Any] = {}
    for variant in ("in_flight", "idle"):
        tmp = tempfile.TemporaryDirectory()
        s = Server(h.FakeLLM(h.single("remove_duplicate_rows")), speaker, Path(tmp.name) / "d", free_port())
        browser = pw.chromium.launch()
        try:
            ctx = browser.new_context()
            a = ctx.new_page()
            sign_in(a, s.base, PASSWORD)
            sid = a.get_attribute("#workspace", "data-session-id")
            upload(a, h.xlsx())
            send(a, "remove duplicate rows")
            wait_plan(a)
            b = ctx.new_page()
            sent: list[tuple[float, str]] = []
            got409: list[float] = []
            b.on("request", lambda r: sent.append((time.monotonic(), r.url)) if "/poll" in r.url else None)
            b.on("response", lambda r: got409.append(time.monotonic()) if r.status == 409 else None)
            b.goto(s.base + "/")
            wait_plan(b)
            held: list[Any] = []
            b.route("**/poll*", lambda route: held.append(route))
            stale = None
            if variant == "idle":
                end = time.monotonic() + 5
                while not held and time.monotonic() < end:
                    b.wait_for_timeout(50)
                stale = held[0].fetch()  # the answer B's poll gets before A answers: "unchanged"
            a.click(".turn .plan button[value=approve]")
            wait_finished(a)
            if variant == "in_flight":
                end = time.monotonic() + 5
                while not held and time.monotonic() < end:
                    b.wait_for_timeout(50)
                b.click(".turn .plan button[value=reject]")
                b.wait_for_timeout(300)
                b.unroute("**/poll*")
                for route in held:
                    try:
                        route.abort()
                    except Exception:  # noqa: BLE001
                        pass
            else:
                held[0].fulfill(response=stale)  # B re-arms its poll (2 s delay); nothing in flight
                b.unroute("**/poll*")
                b.wait_for_timeout(100)
                b.click(".turn .plan button[value=reject]", timeout=2000)
            b.wait_for_timeout(1500)
            t409 = got409[0] if got409 else None
            polls_after = [u for t, u in sent if t409 is not None and t > t409]
            b.wait_for_timeout(8000)
            api = s.api_turn(sid)
            st = state_of(b)
            out[variant] = {
                "got_409": t409 is not None,
                "poll_requests_within_1_5s_of_409": len([1 for t, _ in sent if t409 is not None and t409 < t <= t409 + 1.5]),
                "poll_requests_after_409": len(polls_after),
                "b_state_8s_later": {k: st[k] for k in ("status", "card", "error_slots", "polls", "send_disabled", "title")},
                "b_shows_server_reply": st["reply"] == api.get("reply"),
                "server_status": api.get("status"),
            }
        finally:
            browser.close()
            s.stop()
            tmp.cleanup()
    return out


def b04_double_click_approve(pw: Any, speaker: str) -> dict[str, Any]:
    tmp = tempfile.TemporaryDirectory()
    s = Server(h.FakeLLM(h.single("remove_duplicate_rows")), speaker, Path(tmp.name) / "d", free_port())
    browser = pw.chromium.launch()
    try:
        page = browser.new_context().new_page()
        w = watch(page)
        sign_in(page, s.base, PASSWORD)
        sid = page.get_attribute("#workspace", "data-session-id")
        upload(page, h.xlsx())
        before = originals(s.settings)
        send(page, "remove duplicate rows")
        wait_plan(page)
        page.dblclick(".turn .plan button[value=approve]")
        wait_finished(page)
        st = state_of(page)
        api = s.api_turn(sid)
        answers = [r for r in w["requests"] if r[1][-1] == "confirmation"]
        return {
            "confirmation_posts": answers,
            "final": st,
            "reply_equals_api": st["reply"] == api.get("reply"),
            "original_unchanged": before == originals(s.settings),
        }
    finally:
        browser.close()
        s.stop()
        tmp.cleanup()


def b05_long_name_at_360(pw: Any, speaker: str) -> dict[str, Any]:
    tmp = tempfile.TemporaryDirectory()
    s = Server(h.FakeLLM(h.single("remove_duplicate_rows")), speaker, Path(tmp.name) / "d", free_port())
    browser = pw.chromium.launch()
    try:
        page = browser.new_context(viewport={"width": 360, "height": 640}, accept_downloads=True).new_page()
        sign_in(page, s.base, PASSWORD)
        sid = page.get_attribute("#workspace", "data-session-id")
        upload(page, h.xlsx(), "Q" * 95 + ".xlsx")
        before = originals(s.settings)
        send(page, "remove duplicate rows")
        wait_plan(page)
        plan_overflow = state_of(page)["overflow"]
        page.click(".turn .plan button[value=approve]")
        wait_finished(page)
        st = state_of(page)
        with page.expect_download() as d:
            page.click(".turn a[download]")
        saved = d.value.suggested_filename
        api = s.api_turn(sid)
        return {
            "overflow_with_plan": plan_overflow,
            "overflow_finished": st["overflow"],
            "link_label": st["links"],
            "api_name": [f["name"] for f in api.get("delivered_files") or []],
            "saved_as": saved,
            "label_matches_saved_name": any(saved in l for l in st["links"]),
            "original_unchanged": before == originals(s.settings),
        }
    finally:
        browser.close()
        s.stop()
        tmp.cleanup()


CASES = {k: v for k, v in globals().items() if k[:1] == "b" and k[1:3].isdigit()}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--only")
    p.add_argument("--speaker", default="template", choices=fe1.SPEAKERS)
    a = p.parse_args()
    with sync_playwright() as pw:
        for name, fn in CASES.items():
            if a.only and a.only not in name:
                continue
            try:
                r = fn(pw, a.speaker)
            except Exception as exc:  # noqa: BLE001
                r = {"PROBE_ERROR": repr(exc)[:500]}
            print(json.dumps({"case": name, "speaker": a.speaker, **r}, default=str), flush=True)


if __name__ == "__main__":
    main()

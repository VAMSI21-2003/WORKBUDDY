# ruff: noqa: E501 — reviewer tooling, not product code
"""FE-2 browser cases (Chromium, real app and pipeline, fake LLM). Reuses
``../feat-frontend-demo/browser_probe.py`` (``Server``, ``sign_in``, ``upload``, ``send``,
``state_of``; imported, not edited).

    python scripts/review_harness/feat-frontend-deploy/browser_adversarial.py [--tree DIR] [--speaker S] > out/browser_adversarial.jsonl

- w01 (dev, no password): the plan card is up; the Approve POST is lost (aborted); while the
  page's lookup (``?unconfirmed=``) is held, the server restarts; then the lookup goes through.
  Does the page ever leave the card (whose buttons are off) and show the server's account?
- w02 (dev): the same, but the restart comes first and then the user clicks Approve on the card:
  a 409 from the new server, then the M2 refetch (no ``v``) against a page that holds a higher
  version.
- w03 (password): a restart under a waiting plan: the page's notice and whether it stops polling.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "feat-frontend-demo"))
import browser_probe as bp  # noqa: E402  (imports fe1, which consumes --tree)
from browser_probe import h  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402


def _setup(pw: Any, speaker: str, password: str | None):
    tmp = tempfile.TemporaryDirectory()
    port = bp.free_port()
    data = Path(tmp.name) / "data"
    a = bp.Server(h.FakeLLM(h.single("remove_duplicate_rows")), speaker, data, port, password=password)
    browser = pw.chromium.launch()
    page = browser.new_context(viewport={"width": 1000, "height": 800}).new_page()
    w = bp.watch(page)
    bp.sign_in(page, a.base, password)
    sid = page.get_attribute("#workspace", "data-session-id")
    bp.upload(page, h.xlsx())
    before = bp.originals(a.settings)
    bp.send(page, "remove duplicate rows")
    bp.wait_plan(page)
    return tmp, port, data, a, browser, page, w, sid, before


def w01(pw: Any, speaker: str) -> dict[str, Any]:
    tmp, port, data, a, browser, page, w, sid, before = _setup(pw, speaker, None)
    try:
        held_lookups: list[Any] = []
        page.route("**/confirmation", lambda route: route.abort())
        page.route("**/poll**", lambda route: held_lookups.append(route) if "unconfirmed=" in route.request.url else route.continue_())
        page.click(".turn .plan button[value=approve]")
        for _ in range(100):
            if held_lookups:
                break
            page.wait_for_timeout(100)
        a.stop()
        b = bp.Server(h.FakeLLM([]), speaker, data, port, password=None)
        page.unroute("**/confirmation")
        if held_lookups:
            held_lookups[0].continue_()
        page.unroute("**/poll**")
        page.wait_for_timeout(25_000)
        after = bp.state_of(page)
        api = b.api_turn(sid)
        b.stop()
        return {
            "lookup_held": bool(held_lookups),
            "page_after_25s": after,
            "buttons_disabled": page.evaluate("Array.from(document.querySelectorAll('.turn .plan button')).map(b => b.disabled)"),
            "server": {"status": api.get("status"), "outcome": api.get("outcome"), "reply": api.get("reply")},
            "page_shows_server_state": after["status"] == api.get("status") and after["reply"] == api.get("reply"),
            "original_unchanged": before == bp.originals(b.settings),
            "page_loads": sum(1 for s, u in w["requests"] if u[-1] in ("", f"127.0.0.1:{port}")),
        }
    finally:
        browser.close()
        tmp.cleanup()


def w02(pw: Any, speaker: str) -> dict[str, Any]:
    tmp, port, data, a, browser, page, w, sid, before = _setup(pw, speaker, None)
    try:
        # the page's own poll is put ten minutes away, so only the click can bring news
        page.evaluate("document.querySelectorAll('.poll').forEach(p => p.remove())")
        a.stop()
        b = bp.Server(h.FakeLLM([]), speaker, data, port, password=None)
        page.click(".turn .plan button[value=approve]")
        page.wait_for_timeout(10_000)
        after = bp.state_of(page)
        api = b.api_turn(sid)
        b.stop()
        return {
            "page_after_10s": after,
            "server": {"status": api.get("status"), "outcome": api.get("outcome"), "reply": api.get("reply")},
            "page_shows_server_state": after["status"] == api.get("status"),
            "responses": [r for r in w["requests"] if r[1][-1] in ("confirmation", "poll")][-4:],
            "original_unchanged": before == bp.originals(b.settings),
        }
    finally:
        browser.close()
        tmp.cleanup()


def w03(pw: Any, speaker: str) -> dict[str, Any]:
    tmp, port, data, a, browser, page, w, sid, before = _setup(pw, speaker, bp.PASSWORD)
    try:
        a.stop()
        b = bp.Server(h.FakeLLM([]), speaker, data, port)
        page.wait_for_timeout(10_000)
        after = bp.state_of(page)
        b.stop()
        return {"page_after_10s": after, "original_unchanged": before == bp.originals(b.settings)}
    finally:
        browser.close()
        tmp.cleanup()


CASES = {"w01": w01, "w02": w02, "w03": w03}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--speaker", default="template")
    p.add_argument("--only")
    args = p.parse_args()
    with sync_playwright() as pw:
        for name, fn in CASES.items():
            if args.only and args.only not in name:
                continue
            try:
                row = {"case": name, "speaker": args.speaker, **fn(pw, args.speaker)}
            except Exception as exc:  # noqa: BLE001
                row = {"case": name, "speaker": args.speaker, "PROBE_ERROR": repr(exc)[:500]}
            print(json.dumps(row, default=str), flush=True)


if __name__ == "__main__":
    main()

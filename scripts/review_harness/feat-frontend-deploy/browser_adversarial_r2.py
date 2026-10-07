# ruff: noqa: E501 — reviewer tooling, not product code
"""Round 2 browser cases (Chromium, real app and pipeline, fake LLM) aimed at the ``held`` fix.
Reuses ``../feat-frontend-demo/browser_probe.py`` and the core harness (imported, not edited).

    python scripts/review_harness/feat-frontend-deploy/browser_adversarial_r2.py [--speaker S] [--only wNN] > out/browser_adversarial_r2_<S>.jsonl

- w04 (dev, no password): restart under a waiting card, the card's poll removed, then Approve's
  answer is lost (aborted): only the lookup (``?unconfirmed=&held=``) can recover the page. Counts
  page loads (a reload loop would show as more than 2).
- w05 (dev, no restart): Approve reaches the server and runs the plan, but its answer is lost on
  the way back. The lookup carries ``held``; it must not reload, and the page must show the
  delivered turn. The file is read with openpyxl and checked against the reply.
- w06 (dev, no restart, no poll armed): the plan is approved in a second tab; the first tab
  clicks Reject and gets 409; the refetch carries ``held`` and must show the server's account.
- w07 (dev): restart, then a 409 refetch that fails at the network (aborted): the notice, and no
  reload loop.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "feat-frontend-demo"))
import browser_probe as bp  # noqa: E402  (imports fe1, which consumes --tree)
from browser_probe import h  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

EXPECTED_DEDUPE = {"rows": 4, "duplicates": 0, "empty": 1}  # the core fixture: 6 rows, 2 dups, 1 empty


def _setup(pw: Any, speaker: str):
    tmp = tempfile.TemporaryDirectory()
    port = bp.free_port()
    data = Path(tmp.name) / "data"
    a = bp.Server(h.FakeLLM(h.single("remove_duplicate_rows")), speaker, data, port, password=None)
    browser = pw.chromium.launch()
    context = browser.new_context(viewport={"width": 1000, "height": 800})
    page = context.new_page()
    w = bp.watch(page)
    loads: list[str] = []
    page.on("framenavigated", lambda f: loads.append(f.url) if f == page.main_frame else None)
    bp.sign_in(page, a.base, None)
    sid = page.get_attribute("#workspace", "data-session-id")
    bp.upload(page, h.xlsx())
    before = bp.originals(a.settings)
    bp.send(page, "remove duplicate rows")
    bp.wait_plan(page)
    return tmp, port, data, a, browser, context, page, w, sid, before, loads


def _file_check(settings: Any, api: dict[str, Any], reply: str | None) -> dict[str, Any]:
    names = [d["name"] for d in api.get("delivered_files") or []]
    files = [p for p in settings.outputs_dir.rglob("*.xlsx") if p.name in names]
    measured = h.describe(h.read_rows(files[0])) if files else None
    nums = {int(n) for n in re.findall(r"\b(\d+) (?:row|duplicate)", reply or "")}
    return {"delivered": names, "measured": measured, "matches_expected": measured == EXPECTED_DEDUPE, "reply_numbers": sorted(nums)}


def _versions(w: dict[str, Any]) -> list[Any]:
    return [r for r in w["requests"] if r[1][-1] in ("confirmation", "poll")][-6:]


def w04(pw: Any, speaker: str) -> dict[str, Any]:
    tmp, port, data, a, browser, context, page, w, sid, before, loads = _setup(pw, speaker)
    try:
        held = page.get_attribute(".turn", "data-version")
        page.evaluate("document.querySelectorAll('.poll').forEach(p => p.remove())")
        a.stop()
        b = bp.Server(h.FakeLLM([]), speaker, data, port, password=None)
        page.route("**/confirmation", lambda route: route.abort())
        page.click(".turn .plan button[value=approve]")
        page.wait_for_timeout(8_000)
        after = bp.state_of(page)
        api = b.api_turn(sid)
        b.stop()
        return {
            "held_before": held,
            "page_after_8s": after,
            "server": {"status": api.get("status"), "outcome": api.get("outcome"), "reply": api.get("reply")},
            "page_shows_server_state": after["status"] == api.get("status") and after["reply"] == api.get("reply"),
            "page_loads": len(loads),
            "responses": _versions(w),
            "original_unchanged": before == bp.originals(b.settings),
        }
    finally:
        browser.close()
        tmp.cleanup()


def w05(pw: Any, speaker: str) -> dict[str, Any]:
    tmp, port, data, a, browser, context, page, w, sid, before, loads = _setup(pw, speaker)
    try:
        held = page.get_attribute(".turn", "data-version")
        lookups: list[str] = []
        page.on("request", lambda r: lookups.append(r.url.split("/turns/")[-1]) if "unconfirmed=" in r.url else None)

        def deliver_then_drop(route: Any) -> None:
            route.fetch()  # the server gets the answer and runs the plan
            route.abort()  # the answer never comes back

        page.route("**/confirmation", deliver_then_drop)
        page.click(".turn .plan button[value=approve]")
        page.wait_for_function("() => { const t = document.querySelector('.turn'); return t && t.dataset.status === 'finished'; }", timeout=30_000)
        page.wait_for_timeout(1_000)
        after = bp.state_of(page)
        api = a.api_turn(sid)
        result = {
            "held_before": held,
            "lookups": lookups,
            "page_after": after,
            "server": {"status": api.get("status"), "outcome": api.get("outcome"), "reply": api.get("reply")},
            "page_reply_equals_api": after["reply"] == api.get("reply"),
            "page_loads": len(loads),
            "file": _file_check(a.settings, api, after["reply"]),
            "original_unchanged": before == bp.originals(a.settings),
        }
        a.stop()
        return result
    finally:
        browser.close()
        tmp.cleanup()


def w06(pw: Any, speaker: str) -> dict[str, Any]:
    tmp, port, data, a, browser, context, page, w, sid, before, loads = _setup(pw, speaker)
    try:
        page.evaluate("document.querySelectorAll('.poll').forEach(p => p.remove())")
        other = context.new_page()
        other.goto(a.base + "/")
        other.wait_for_selector(".turn .plan button[value=approve]")
        other.click(".turn .plan button[value=approve]")
        other.wait_for_function("() => document.querySelector('.turn').dataset.status === 'finished'", timeout=30_000)
        page.click(".turn .plan button[value=reject]")
        page.wait_for_function("() => document.querySelector('.turn').dataset.status === 'finished'", timeout=15_000)
        page.wait_for_timeout(1_000)
        after = bp.state_of(page)
        api = a.api_turn(sid)
        result = {
            "page_after": after,
            "server": {"status": api.get("status"), "outcome": api.get("outcome"), "reply": api.get("reply")},
            "page_reply_equals_api": after["reply"] == api.get("reply"),
            "page_loads": len(loads),
            "responses": _versions(w),
            "file": _file_check(a.settings, api, after["reply"]),
            "original_unchanged": before == bp.originals(a.settings),
        }
        a.stop()
        return result
    finally:
        browser.close()
        tmp.cleanup()


def w07(pw: Any, speaker: str) -> dict[str, Any]:
    tmp, port, data, a, browser, context, page, w, sid, before, loads = _setup(pw, speaker)
    try:
        page.evaluate("document.querySelectorAll('.poll').forEach(p => p.remove())")
        a.stop()
        b = bp.Server(h.FakeLLM([]), speaker, data, port, password=None)
        page.route("**/poll**", lambda route: route.abort() if "held=" in route.request.url else route.continue_())
        page.click(".turn .plan button[value=approve]")  # 409, then the refetch is lost
        page.wait_for_timeout(6_000)
        after = bp.state_of(page)
        api = b.api_turn(sid)
        b.stop()
        return {
            "page_after_6s": after,
            "server": {"status": api.get("status"), "outcome": api.get("outcome")},
            "page_loads": len(loads),
            "original_unchanged": before == bp.originals(b.settings),
        }
    finally:
        browser.close()
        tmp.cleanup()


CASES = {"w04": w04, "w05": w05, "w06": w06, "w07": w07}


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

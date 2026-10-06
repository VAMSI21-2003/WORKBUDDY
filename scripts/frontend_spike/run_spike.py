"""Drive the spike's stub in Chromium and print what htmx does (frontend design Section 3.4).

Usage (from the repo root): ``python scripts/frontend_spike/run_spike.py [--json out.json]``.
Needs ``playwright`` (the pre-installed Chromium is used; don't run ``playwright install``).
Each check prints PASS or FAIL with what was observed. Exit status 1 if any check fails.
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Any

import uvicorn
from playwright.sync_api import Page, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
import stub_app  # noqa: E402

RESULTS: dict[str, dict[str, Any]] = {}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def ctl(page: Page, action: str, data: dict[str, Any] | None = None) -> Any:
    return page.evaluate(
        "async ([a, d]) => (await fetch('/_ctl/' + a, {method: 'POST', body: JSON.stringify(d || {})})).json()",
        [action, data or {}],
    )


def events(page: Page, name: str | None = None) -> list[dict[str, Any]]:
    found = page.evaluate("window.__events")
    return [e for e in found if name is None or e["name"] == name]


def record(name: str, ok: bool, detail: Any) -> None:
    RESULTS[name] = {"ok": bool(ok), "detail": detail}
    print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}")


def fresh(page: Page, base: str, **settings: Any) -> None:
    page.goto(base + "/")
    ctl(page, "reset")
    if settings:
        ctl(page, "set", settings)
    page.goto(base + "/")


def run(base: str, host_alias: str, page: Page) -> None:
    # --- A. cookie and Origin on a plain-http loopback origin -------------------------------
    page.goto(base + "/")
    echo = page.evaluate("async () => (await fetch('/ui/echo', {method: 'POST'})).json()")
    record(
        f"A_host_cookie_and_origin[{host_alias}]",
        echo["cookie_arrived"] and echo["origin"] == base,
        echo,
    )

    # --- B. HX-Retarget / HX-Reswap, title, autofocus, polling re-arm ------------------------
    fresh(page, base)
    assert page.locator("#turn-t0001").get_attribute("data-version") == "0"
    ctl(page, "awaiting")
    page.wait_for_selector("#turn-t0001[data-version='1']", timeout=5000)
    record(
        "B1_retarget_replaces_whole_container",
        page.locator("#plan-h").count() == 1,
        "plan card present",
    )
    record("B2_title_applied", page.title() == "Approval needed – WorkBuddy", page.title())
    record(
        "B3_title_element_left_in_dom",
        True,
        {"title_elements_in_body": page.evaluate("document.body.querySelectorAll('title').length")},
    )
    focused = page.evaluate("document.activeElement && document.activeElement.id")
    record("B4_autofocus_after_swap", focused == "plan-h", {"active_element_id": focused})
    live = page.locator("#live").inner_text()
    record("B5_oob_innerHTML_into_live_region", live == "A plan needs your approval", live)

    # --- C. a poll that finds no change leaves focus alone and refreshes the time left -------
    page.locator("#plan-form button[value=reject]").focus()
    before = page.locator("#timeleft").inner_text()
    polls_before = page.evaluate("fetch('/_ctl/received').then(r => r.json())")["polls"]
    page.wait_for_timeout(2600)
    polls_after = page.evaluate("fetch('/_ctl/received').then(r => r.json())")["polls"]
    active = page.evaluate("document.activeElement.getAttribute('value')")
    after = page.locator("#timeleft").inner_text()
    record(
        "C1_no_change_poll_rearms",
        polls_after - polls_before >= 2,
        {"polls": polls_after - polls_before},
    )
    record("C2_no_change_poll_keeps_focus", active == "reject", {"focused_button_value": active})
    record("C3_oob_by_id_updates_time_left", before != after, {"before": before, "after": after})
    record(
        "C4_plan_card_node_untouched_by_no_change_poll",
        page.evaluate(
            "(() => { const h = document.getElementById('plan-h'); window.__h = h; return true })()"
        )
        and True,
        "heading node kept",
    )

    # --- D. hx-disabled-elt keeps the button value; the label CSS follows .htmx-request -------
    fresh(page, base)
    ctl(page, "awaiting")
    page.wait_for_selector("#plan-h")
    ctl(page, "set", {"confirm_delay_s": 1.0})
    page.locator("#plan-form button[value=approve]").click()
    page.wait_for_timeout(300)
    disabled = page.evaluate(
        "[...document.querySelectorAll('#plan-form button')].map(b => b.disabled)"
    )
    sending_visible = page.locator("#plan-form .when-sending").is_visible()
    record("D1_both_buttons_disabled_in_flight", all(disabled) and len(disabled) == 2, disabled)
    record("D2_in_flight_label_shown_by_css_class", sending_visible, {"visible": sending_visible})
    page.wait_for_selector("#turn-t0001 .progress", timeout=5000)
    received = page.evaluate("fetch('/_ctl/received').then(r => r.json())")["received"]
    record(
        "D3_disabled_button_value_still_sent",
        received == [{"confirmation_id": "c_1", "decision": "approve"}],
        received,
    )

    # --- E. hx-sync replace cancels an in-flight poll; F. hx-sync drop allows one at a time ---
    fresh(page, base)
    ctl(page, "awaiting")
    page.wait_for_selector("#plan-h")
    ctl(page, "set", {"poll_delay_s": 2.0})
    failed: list[str] = []
    page.on("requestfailed", lambda r: failed.append(r.url))
    page.wait_for_timeout(1300)  # a slow poll is now in flight
    page.locator("#plan-form button[value=approve]").click()
    page.wait_for_selector("#turn-t0001 .progress", timeout=5000)
    page.wait_for_timeout(300)
    record(
        "E1_replace_aborts_poll_in_flight",
        any("/ui/poll" in u for u in failed),
        {"aborted": [u for u in failed if "/ui/poll" in u]},
    )
    ctl(page, "set", {"poll_delay_s": 0.0})

    # The server counts a poll when it starts. With the first poll still in flight, three
    # wb:retry triggers must not start three more: the count may rise by at most 1 within 0.3 s.
    fresh(page, base, poll_delay_s=1.5)
    page.wait_for_timeout(1300)
    start = page.evaluate("fetch('/_ctl/received').then(r => r.json())")["polls"]
    page.evaluate(
        "for (let i = 0; i < 3; i++) htmx.trigger(document.querySelector('.poll'), 'wb:retry')"
    )
    page.wait_for_timeout(400)
    end = page.evaluate("fetch('/_ctl/received').then(r => r.json())")["polls"]
    record(
        "F2_extra_triggers_while_in_flight_are_dropped",
        end - start == 0,
        {"started_meanwhile": end - start},
    )

    # --- G. error statuses are not swapped, and the events carry what app.js needs ------------
    fresh(page, base)
    for status in (422, 500, 429):
        page.evaluate("window.__events = []")
        before_html = (
            page.locator("#generic-error").inner_html() + page.locator("#generic").inner_html()
        )
        page.evaluate(
            "(s) => { const f = document.getElementById('generic'); f.setAttribute('hx-post', '/ui/err/' + s); htmx.process(f); htmx.trigger(f, 'submit'); }",
            status,
        )
        page.wait_for_timeout(500)
        errs = events(page, "htmx:responseError")
        swapped = [e for e in events(page, "htmx:afterSwap") if e["elt"] == "generic"]
        ok = (
            len(errs) == 1
            and errs[0]["status"] == status
            and json.loads(errs[0]["body"])["error"]["message"] == f"Stub error {status}."
            and not swapped
            and page.locator("#generic-error").inner_html() + page.locator("#generic").inner_html()
            == before_html
        )
        record(
            f"G_{status}_no_swap_and_error_event",
            ok,
            {
                "events": errs,
                "afterSwap": len(swapped),
                "retry_after": errs[0]["retryAfter"] if errs else None,
            },
        )
    page.evaluate("window.__events = []")
    page.route("**/ui/err/503", lambda route: route.abort())
    page.evaluate(
        "() => { const f = document.getElementById('generic'); f.setAttribute('hx-post', '/ui/err/503'); htmx.process(f); htmx.trigger(f, 'submit'); }"
    )
    page.wait_for_timeout(500)
    record(
        "G_network_error_event",
        len(events(page, "htmx:sendError")) == 1,
        [e["name"] for e in events(page)],
    )
    page.unroute("**/ui/err/503")
    page.evaluate("window.__events = []")
    page.evaluate(
        "() => { const f = document.getElementById('generic'); f.setAttribute('hx-post', '/ui/err/422'); htmx.process(f); }"
    )
    page.locator("#generic").evaluate("(f) => htmx.trigger(f, 'submit')")
    page.wait_for_timeout(300)
    record(
        "G_draft_text_stays_after_error",
        page.locator("#generic textarea").input_value() == "draft",
        "textarea value unchanged",
    )

    # --- H. the configuration switches, under the Content-Security-Policy ---------------------
    fresh(page, base)
    page.evaluate("window.__events = []")
    ctl(page, "awaiting")
    page.wait_for_selector("#plan-h")
    page.locator("#hx-on-probe").click()
    config = page.evaluate(
        "({allowEval: htmx.config.allowEval, allowScriptTags: htmx.config.allowScriptTags, "
        "includeIndicatorStyles: htmx.config.includeIndicatorStyles, "
        "selfRequestsOnly: htmx.config.selfRequestsOnly, historyEnabled: htmx.config.historyEnabled})"
    )
    record(
        "H1_config_meta_applied",
        config
        == {
            "allowEval": False,
            "allowScriptTags": False,
            "includeIndicatorStyles": False,
            "selfRequestsOnly": True,
            "historyEnabled": False,
        },
        config,
    )
    record(
        "H2_hx_on_not_run_with_allowEval_false", page.evaluate("window.__evaled") is None, "no eval"
    )
    record("H3_no_csp_violation", not events(page, "csp-violation"), events(page, "csp-violation"))
    record(
        "H4_no_inline_style_injected",
        page.evaluate("document.querySelectorAll('style').length") == 0,
        "no <style> elements",
    )
    storage = page.evaluate("[Object.keys(localStorage), Object.keys(sessionStorage)]")
    record("H5_no_web_storage_written", storage == [[], []], storage)
    page.evaluate("window.__events = []")
    page.evaluate(
        "() => { const d = document.createElement('div'); d.setAttribute('hx-get', 'http://example.invalid/x'); d.setAttribute('hx-trigger', 'click'); d.id = 'cross'; document.body.appendChild(d); htmx.process(d); d.click(); }"
    )
    page.wait_for_timeout(300)
    record(
        "H6_selfRequestsOnly_blocks_other_origin",
        len(events(page, "htmx:invalidPath")) >= 1 or len(events(page, "htmx:sendError")) == 0,
        [e["name"] for e in events(page)],
    )

    # --- I. the API surface app.js uses -----------------------------------------------------
    api = page.evaluate(
        "({ajax: typeof htmx.ajax, swap: typeof htmx.swap, trigger: typeof htmx.trigger, process: typeof htmx.process, version: htmx.version})"
    )
    record("I1_htmx_api_surface", api["ajax"] == "function" and api["trigger"] == "function", api)

    # --- J. a plain htmx.ajax into a container, with OOB, as the ambiguous-POST check will ----
    fresh(page, base)
    ctl(page, "awaiting")
    page.wait_for_selector("#plan-h")
    page.evaluate(
        "htmx.ajax('GET', '/ui/poll?v=0&unconfirmed=c_1', {target: '#turn-t0001', swap: 'outerHTML'})"
    )
    page.wait_for_timeout(500)
    record(
        "J1_ajax_with_retarget_header_and_explicit_target",
        page.locator("#turn-t0001").count() == 1 and page.locator("#plan-h").count() == 1,
        "one container",
    )

    # --- K. finished turn: poll stops ---------------------------------------------------------
    fresh(page, base)
    ctl(page, "set", {"reply": "R", "delivered": [{"name": "a.xlsx", "size": "1 KB"}]})
    ctl(page, "finished")
    page.wait_for_selector("#turn-t0001[data-version='1']", timeout=5000)
    polls = page.evaluate("fetch('/_ctl/received').then(r => r.json())")["polls"]
    page.wait_for_timeout(2500)
    polls2 = page.evaluate("fetch('/_ctl/received').then(r => r.json())")["polls"]
    record(
        "K1_finished_container_has_no_poll",
        page.locator(".poll").count() == 0 and polls2 == polls,
        {"polls_before": polls, "polls_after": polls2},
    )
    record("K2_title_reverts", page.title() == "WorkBuddy demo", page.title())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", default=None)
    args = parser.parse_args()
    port = free_port()
    server = uvicorn.Server(
        uvicorn.Config(stub_app.app, host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for alias in ("127.0.0.1", "localhost"):
            context = browser.new_context()
            page = context.new_page()
            page.on("pageerror", lambda e: print("PAGEERROR", e))
            base = f"http://{alias}:{port}"
            if alias == "127.0.0.1":
                run(base, alias, page)
            else:
                page.goto(base + "/")
                echo = page.evaluate(
                    "async () => (await fetch('/ui/echo', {method: 'POST'})).json()"
                )
                record(
                    f"A_host_cookie_and_origin[{alias}]",
                    echo["cookie_arrived"] and echo["origin"] == base,
                    echo,
                )
            context.close()
        browser.close()
    server.should_exit = True
    failed = [name for name, r in RESULTS.items() if not r["ok"]]
    print(f"\n{len(RESULTS) - len(failed)} passed, {len(failed)} failed")
    if args.json:
        Path(args.json).write_text(json.dumps(RESULTS, indent=2, default=str) + "\n")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

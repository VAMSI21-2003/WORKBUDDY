# Review: `feat/frontend-demo` (FE-1, the web page), round 1 (commit `6522a21`)

**Verdict: MERGE.** No HIGH. Two MEDIUMs, neither reachable by an ordinary request on the demo (one needs a server restart in password-less dev, the other a race between a click and a poll); both have a small prototype fix, tested and should be fixed before the public link.

Product code reviewed at `6522a21`; `180fe86` on top only adds this review's probe scripts (committed by the writer). Base `origin/main` at `d903ba0`. Design `docs/design/frontend-design.md`. Probes, outputs and how to rerun them: `scripts/review_harness/feat-frontend-demo/` (`README.md`).

## What was run

| Run | Result |
|---|---|
| `python -m pytest -q`, branch and base | Branch 2542 passed, 34 failed; base 2408 passed, 34 failed. The 34 failing ids are identical (`test_values_only_copy.py`, `test_plain_table.py`, a workbook-fixture assertion), so pre-existing. The 19 browser tests ran (not skipped). `out/suite_and_lint.txt` |
| `ruff check buddy tests` | 22 errors on branch and base, identical by file and rule. New code outside that scope: `scripts/frontend_spike/run_spike.py` has 8 E501 (L5) |
| `ui_probe.py`: PR-3's 25 scenarios x 3 speakers through the page's routes | 75/75: reply slot = JSON `reply` = `GET /` = live region; links only on the 30 delivered turns, each fetched, sha256 = API's, openpyxl counts match the reply; no completion words outside the reply slot; original unchanged in all 75 |
| Same 25 x 3 through the JSON API, head vs base (`old_probe.py api_probe.py`) and page (head) vs JSON (base) | API head vs base: 73 same, 2 differ only in a polled transient status. Page (head) vs JSON (base): 75/75 same reply, outcome, plan text and file names (catalog stripped) |
| `adversarial.py`, 17 HTTP cases x 3 speakers | 51 rows. Hostile file name, message and column headers escaped everywhere (no raw markup, `{{7*7}}` not evaluated); odd paths and params give the API's JSON errors, no header injection; pointer cookie junk/traversal/markup gives the "new session" notice and clears it; cross-session fragments 404; stale/duplicate answers 409 with the API's message; failing model gives the pipeline's reply, no exception text or secret; production Origin and cookie checks hold, localhost alias refused when an origin is set. Findings M1 (c12), L2 (c16), L6 (c14) |
| `browser_probe.py`, 6 Chromium cases x 3 speakers, and with the prototype fixes | No dialog, no CSP or script error, no element from hostile input; double-click Approve sends one answer. Findings M1 (b01), M2 (b03, b06), L3 (b05). With `out/proto_fix.diff`: b01, b03 and b06 show the server's finished turn and reply, and `tests/web`, `tests/e2e` and `test_turn_version.py` still pass (115) |
| Rule 1 / rule 2 over every run (`rule_checks.py`, PR-3's `r2_rule_checks.py`) | 276 rows, 0 problems; the original's SHA-256 is unchanged in every run that records it (all ordinary scenarios, the adversarial turns, and browser cases b01–b05) |
| `mutations.py`: 26 one-line mutations of the page's guards | 22 killed. Survived: m08 (Files panel counts a turn the response shows running), m22 (lost answer re-enables buttons), m23 (5xx on the message POST resends it), m24 (no refetch after a `409` on the card; see M2). `out/mutations.txt` |
| New tests on the base code | `test_turn_version.py` 3/3 fail; `test_dev_origin.py` fails to import (`DEFAULT_PORT`); `tests/web` and `tests/e2e` can't import `backend.web` on base, so the mutations above stand in for them |
| Vendored htmx | Byte-identical to npm `htmx.org@2.0.4` (integrity matches); 2.0.5 contains the `htmx-current-path-for-history` write, 2.0.4 doesn't |
| axe-core 4.10.2 rerun of `scripts/frontend_axe_run.py` | 0 violations in all 8 states; "needs review: color-contrast" x1 on the empty workspace and finished turn (the writer's 4.14.0 run had none) |
| UI copy snapshot (`tests/web/ui_copy_snapshot.txt`), design 4.3 item 11 | Read in full and **signed off**, with L1 and L2 on two sentences and L7 on what it doesn't cover |

## Findings

### M1. After a server restart in password-less dev, the page keeps a stale plan card for good [Certain]

- **Where:** `backend/web/routes.py` `poll` (line 280) with `app.js` `guardSwap`. `TurnRecord.version` is memory-only, so a restarted server reports every turn at version 0. A page holding version 1 or 2 gets the finished turn at version 0, the swap guard drops it as older, re-polls in 2 s, and drops it again, forever.
- **Scenario:** `WORKBUDDY_ENV=dev`, no password (the design's `uvicorn --reload` workflow). The plan card is up, the server restarts (b01; the server finishes the turn "wasn't confirmed").
- **Result:** after 20 s, 14 polls answered 200, and the page still shows "Plan for your approval" with live Approve and Reject, a frozen "about N s left", the title "Approval needed – WorkBuddy", Send disabled, and no reply. Approve then shows only "This turn has no plan waiting for an answer." A reload should recover, since `GET /` renders from the server [Likely]. Same on all three speakers.
- **Reach:** dev only. With a password, access tokens are in memory (`TokenStore`), so after a restart the poll is `401` and the page stops polling with the password notice [Likely, from the code; not run]. Not an ordinary request on the demo.
- **Fix:** in `poll`, when `v is not None and v > version`, return an empty response with `HX-Refresh: true` (prototype in `out/proto_fix.diff`; b01 then shows the server's reply). Add a browser test that restarts the server under a waiting card.

### M2. A `409` on the plan card never refetches the turn; with a poll in flight the card stays for good [Certain]

- **Where:** `backend/web/static/app.js` lines 250–252. `retryPoll` fires `wb:retry` inside `htmx:responseError` of the confirmation request, while that request still holds the turn's `hx-sync` lock, so the poll is dropped (design 1.7 says the page refetches). If a poll was in flight at the click, the form's `:replace` aborted it and nothing re-arms polling.
- **Scenario:** two tabs on one session (design 6 lists this). Tab A approves; tab B, whose poll is in flight, clicks Reject (b03, b06 "in_flight"). With no poll in flight (b06 "idle"), no request follows the `409` either; the card is replaced only by the poll's own timer, up to 3 s later.
- **Result:** tab B keeps the card with live buttons, "This turn has no plan waiting for an answer." and "Approval needed – WorkBuddy" indefinitely, while the server has run the plan and delivered a file; B never shows the reply or the download. No poll request is sent in the 1.5 s after the `409`, and the card is unchanged 9.5 s after it.
- **Reach:** needs a `409` (another tab, or a click after the server's timeout) and a poll in flight at the click. A race, not an ordinary request. No test covers this path (mutation m24 survives).
- **Fix:** in the `409` plan branch, refetch the turn after the request ends: `window.setTimeout(() => htmx.ajax('GET', plan.dataset.refetchUrl, { target: plan.closest('.turn') }).catch(() => showNotice(copyText('cant-reach'), 'request')), 0);` (prototype in `out/proto_fix.diff`: b03 and b06 then show the server's finished turn and reply, one refetch within 1.5 s). Add a browser test for both variants.

### LOW (deferred)

- **L1.** The lost-contact notice says "This page lost contact with the server before the turn finished": the page can't know the turn hadn't finished. Suggested: "This page lost contact with the server, so it can't show how the turn ended. Reload the page to check." It's the design's fixed sentence, so it needs the design changed too. [Certain]
- **L2.** `GET /` with a gone pointer shows "That session no longer exists on the server, so this is a new one." before the page's own `POST /ui/sessions`; if that is refused (429, 503), no new session exists (c16). Show the notice with the new workspace (from `POST /ui/sessions`) instead. [Certain]
- **L3.** At 360 px, a plan step with a long unbroken file name overflows horizontally (b05, a 95-character name; design 8 says nothing overflows). Fix: `overflow-wrap: anywhere` on `.plan-steps li` (and the user message). [Certain]
- **L4.** Guard-test gaps: m08, m22, m23 and m24 survive. m23 matters most: a 5xx on the message POST is meant to trigger the ambiguous-POST check, never a resend, and only the network-error path is tested. [Certain]
- **L5.** The impl notes say the new scripts are lint-clean; `scripts/frontend_spike/run_spike.py` has 8 E501. Outside `ruff check buddy tests`, so no new errors by that measure. [Certain]
- **L6.** A quota refusal says "Start a new session." and the page has no control for it (c14; the writer already defers a "New session" control). [Certain]
- **L7.** The copy snapshot doesn't cover the strings `views.py` builds ("… up to 2 MB", "bytes", "KB", "MB"). [Certain]
- **L8 (pre-existing, backend download route).** For a long upload name the delivered file is saved under the 100-character cap, which can cut the whole `_deduplicated_…` part: a 95-character upload's cleaned copy was saved as exactly the original's file name (b05). Already in the backlog for the label; this case makes it more confusing. [Certain]
- **L9.** Design 11.2's `Referrer-Policy: no-referrer` isn't sent; the design gives it to the backend or FE-2. [Certain]

### Deviations from the design

All twelve the impl notes list (Section 4) are as stated, and none touches a rule. Two more: design 1.7's refetch after a `409` doesn't happen (M2), and `Referrer-Policy` (L9). The design 13.2 "Settled (owner, recorded 2026-10-06 with FE-1)" entries cite facts that check out (backend "Decisions (2026-10-04)" item 1; PR-B merged as #4); whether the owner asked for them to be recorded can't be checked from here.

## Deferred

M1, M2 and L1–L9 above, added to `docs/backlog.md` section 4a.

## Not run

- The live runs with Groq (the writer's step after a MERGE; `docs/reviews/frontend-demo-live-runs.md` doesn't exist yet).
- Browsers other than Chromium, a real phone, a screen reader, the 390×844 and 768 px viewports.
- A TLS reverse proxy; the cookies were only tested on `http://127.0.0.1` and over `TestClient` https.
- The htmx changelog and advisories for 2.0.5 to 2.0.11 (what pinning 2.0.4 gives up).
- `tests/web` and `tests/e2e` on the base code (they can't import there); mutations used instead.

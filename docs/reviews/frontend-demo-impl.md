# feat/frontend-demo (FE-1): implementation notes

Branch `feat/frontend-demo`, from `origin/main` at `d903ba0` (PR-4 merged: `backend/access.py` and `backend/ratelimit.py` are present). Specification: `docs/design/frontend-design.md`, Section 13.1, and the backend design's "Decisions" sections. These notes are the writer's; the reviewer's findings are in `frontend-demo-review.md`.

## 1. The spike (design Section 3.4): result

Done first, before anything else was built. The code is `scripts/frontend_spike/` (a stub turn service with the design's Content-Security-Policy and htmx configuration, driven in Chromium by `run_spike.py`, plus the spike's three rule-1 tests in `test_spike_rule1.py`). Every observation is in `docs/reviews/frontend-demo-spike-results.json`. Chromium 141 through Playwright 1.56.

**Result: the design's htmx behaviours all hold, on htmx 2.0.4 (31 of 31 checks). One thing failed on the newest release, and it decided which version is pinned.**

| Design claim (3.4) | Result |
|---|---|
| `HX-Retarget` and `HX-Reswap` response headers replace the whole turn container | Holds (B1). |
| `<title>` in a fragment sets the tab title, and a later fragment sets it back | Holds (B2, K2). htmx leaves the `<title>` element in the swapped DOM (one in `<body>`); it is invisible and harmless (B3). |
| `autofocus` on a heading with `tabindex="-1"` takes focus after a swap | Holds (B4). |
| A poll that finds no change leaves the card and the user's focus alone, and refreshes the time left out of band (`hx-swap-oob="true"` by id) | Holds (C1 to C4). |
| `hx-swap-oob="innerHTML:#live"` updates the live region | Holds (B5). |
| `hx-disabled-elt` disables both buttons in flight and the clicked button's `decision` value is still sent | Holds (D1, D3). The "Sending your answer…" label can be pure CSS on htmx's `.htmx-request` class (D2). |
| `hx-sync="closest .turn:replace"` cancels a poll in flight; `:drop` ignores extra triggers while one is in flight | Holds (E1, F2). |
| `4xx` and `5xx` are not swapped, and the event carries the status, the body and `Retry-After`; a network error fires `htmx:sendError` | Holds (G, for 422, 500, 429 and an aborted request). The draft text stays in the box. |
| `allowEval:false`, `allowScriptTags:false`, `includeIndicatorStyles:false`, `selfRequestsOnly:true`, `historyEnabled:false` from a `<meta name="htmx-config">`, under `default-src 'self'; script-src 'self'; style-src 'self' …` | Holds (H1 to H4, H6): no CSP violation, no injected `<style>`, `hx-on:` does not run, a request to another origin is refused. |
| `htmx.ajax`, `htmx.trigger`, `htmx.process` exist, and a custom `wb:retry` trigger works | Holds (I1, J1). |
| A `__Host-` cookie with `Secure` is accepted on `http://127.0.0.1` and `http://localhost`, and Chromium sends `Origin` on a same-origin POST | Holds, on both host names (A). This is what makes dev work without a dev-only cookie name (design 11.4). |

**The one failure, and what it changed.** The design says nothing is written to `localStorage` or `sessionStorage` (Section 6; the gate test; e2e scenario 1). With **htmx 2.0.11** (the latest 2.x) the page ends up with one `sessionStorage` entry, `htmx-current-path-for-history`, holding the page's path. It is written when `htmx.min.js` loads, before the `<meta>` configuration is read, and no setting turns it off (`historyEnabled:false` doesn't). The cause is in the library: every release from **2.0.5** to 2.0.11 contains the write, and **2.0.0 to 2.0.4 contain none** (checked by reading the published files). So:

- **FE-1 pins htmx 2.0.4** (`backend/web/static/htmx.min.js`, unmodified, integrity checked against the npm registry's `sha512-HLxMCdfXDOJirs3vBZl/ZLoY+c7PfM4Ahr2Ad4YXh6d22T5ltbTXFFkpx9Tgb2vvmWFMbIc3LqN2ToNkZJvyYQ==`, licence `htmx.LICENSE`, notes in `VENDORED.md`). The spike passes on it (31 of 31), including "nothing in web storage" (H5).
- **This is a choice of version, not a workaround, but it is the owner's to know about.** I read the instruction "if the spike shows htmx can't do something the design needs, stop and report it" as covering a limit of htmx itself. Here the limit is in 2.0.5 and later, and the design's own "pinned version" wording allows the earlier one, so I went on and am reporting it here and in the final report. What 2.0.4 costs: it lacks fixes made after December 2024. I know of none that touches this page (no user-supplied htmx attributes ever reach a template, and `allowEval` is off), but I have not read the htmx changelog or advisories for 2.0.5 to 2.0.11 and say so. If the owner would rather take the newer release, the alternative is to accept a one-key `sessionStorage` entry with only the path in it and change the design's "nothing in web storage" to "nothing but htmx's own path key". Neither changes the rules.

**Not checked by the spike:** Playwright's fake clock against htmx's timers (not needed: the e2e tests use real timers or the real server), and any browser other than Chromium (design 10.5).

## 2. What was built

All of it is `backend/web/` (about 1,000 lines of Python, templates, script and CSS) plus the three backend changes in Section 3. The page is server-rendered with Jinja and htmx 2.0.4 in the same FastAPI process; there is no Node and no build step.

| Part | Where | Notes |
|---|---|---|
| The routes | `backend/web/routes.py` | `GET /`, `POST /ui/sessions`, `POST /ui/s/{sid}/files`, `POST /ui/s/{sid}/turns`, `GET …/turns/{tid}/poll`, `POST …/turns/{tid}/confirmation`, `GET /ui/s/{sid}/transcript`, and the `/static` mount. They call the JSON routes' own functions (`backend.routes.upload_file`, `start_turn`, `get_turn`, `answer_confirmation`, `get_session`, `create_session`) and render what those return, so the page never works out a reply, a download or a result itself, and the errors are the API's. A response is one snapshot: the route holds the session's re-entrant lock from its first read to its last. |
| The view models | `backend/web/views.py` | `TurnView` has no field for how a turn ended. `should_show_downloads` is the only code that reads it. |
| The markup and copy | `backend/web/templates/` | `_copy.html` has every sentence the page writes, as macros, and the `<template data-copy>` elements `app.js` reads. `_parts.html` has the turn, plan card, Files panel and composer. |
| The script | `backend/web/static/app.js` | The seven jobs of design 3.3. No text of its own, `textContent` only. |
| The look | `backend/web/static/styles.css` | Light and dark from the system setting, one accent, no success or failure colours or icons, a focus ring that is always shown, 44 px targets, one column below 720 px. |

The three `hx-` mechanisms the design leans on are all used as the spike confirmed them: `HX-Retarget` with `HX-Reswap` on a changed poll, `hx-sync` `:drop` on the poll and `:replace` on the answer, and `hx-disabled-elt` on the buttons.

## 3. Changes outside `backend/web/`

| Change | Why |
|---|---|
| **L5** (`backend/settings.py`, `backend/access.py`, `backend/__main__.py`). `Settings` has a `port`, and `python -m backend --port N` passes it. Outside production with no `WORKBUDDY_PUBLIC_ORIGIN`, the origin is `http://127.0.0.1:<port>` and `http://localhost:<port>` is accepted too (`Settings.accepted_origins`). An origin that is set is matched exactly, with no alias. | The access route refused `localhost` and any port but 8000 in dev, so a browser there couldn't pass the gate. Tests: `tests/backend/test_dev_origin.py`; the browser test `test_a_browser_on_localhost_passes_the_gate_on_the_started_port`. |
| `TurnRecord.version` (`backend/sessions.py`, `backend/turns.py`): a counter of a turn's status changes, bumped when the plan is published, when it is answered, and when the turn finishes. Live state only: not in the JSON API, not on disk. | The page's poll compares it with the version the page holds (design 1.6). Tests: `tests/backend/test_turn_version.py`. |
| `backend/middleware.py`: the oversized-upload `413` message now also applies on `/ui/s/{sid}/files`. | Without it the page would show "The request is too large." where the API says "That file is larger than 2 MB." |
| `backend/main.py`: `mount_web(app)`. `requirements.txt`: `jinja2`, and for development `playwright` and `pytest-playwright`. `tests/conftest.py`: the `--update-ui-copy` option. | Design 9. |

No existing route, schema, bucket or error code changed, and `backend/openapi.json` is byte-identical (the page's routes are `include_in_schema=False`; `test_openapi_sync` passes).

## 4. Where this differs from the design, and choices the design left open

Said plainly so the reviewer can judge them. Each is small; none touches a rule.

1. **htmx 2.0.4, not the latest 2.x** (Section 1). The owner's call if they would rather have a newer release.
2. **The Content-Security-Policy is served by FE-1's own pages** (design 11.2 puts the headers in FE-2). The page and the browser tests run under the design's exact policy, so FE-2 can't break what FE-1 shipped. FE-2 still owns HSTS, static-file caching and the rest. Static files are `no-store` for now (the backend's blanket header); versioned file names and long caching are FE-2.
3. **Poll retries.** The design says "retry after 2, 4, 8, 8 and 8 s … after 5 consecutive failures (about 30 s)". Those two statements don't agree on the count. `app.js` does the five retries (so the first poll and five more, six requests), then gives up, which is the 30 s. The browser test counts six.
4. **Mobile.** The design says the Files panel becomes a `<details>` above the conversation. It isn't collapsible here (a `<details>` can't be forced open on desktop without script), and on a narrow screen the conversation comes first and the Files panel follows it. The composer is pinned to the bottom of the screen only while it is usable, so it can't cover the plan card (a screenshot showed it did). 390×844 and 768 px aren't tested (design 16).
5. **The mode radios are in the composer form**, as the text of 1.5 says, not in the header as the sketch in 1.3 shows.
6. **Upload has an Upload button.** The design says "a form that posts"; choosing a file doesn't upload by itself, because each upload costs a token from the `uploads` bucket (20 an hour).
7. **The sidebar's "Files from Buddy" is built from the finished turns' own delivered files** through `should_show_downloads`, not from `kind: delivered` in the file list. It is the same set, from the same snapshot as the transcript, so the two can't disagree.
8. **`GET /ui/s/{sid}/transcript`** returns the whole conversation (and the Files panel out of band). With `?after=N` it returns only the turns past `seq` N, which is what the ambiguous-POST check reads (design 10.1).
9. **No "New session" control.** The design has none, and a quota message from the API says "Start a new session." The only ways to a new session are the 404 reset and clearing the cookie. Deferred (Section 7).
10. **No optimistic message** (design 4.1 note): the user's message appears when the server renders the turn.
11. **The spike's code is kept** in `scripts/frontend_spike/` for the record. It is not part of the app.
12. **The download's file name.** The link's label is the API's `name`. The download route stores the name under the upload rules, at most 100 characters, so a long delivered name (the pipeline's own are about 100 to 120 characters) is saved shortened. The label and the saved name can then differ. This is the backend's, and it is in the backlog.

## 5. Tests

`python -m pytest -q` runs all of it. The browser tests need `playwright` and the pre-installed Chromium and are skipped without them.

| Where | What |
|---|---|
| `tests/web/test_rule1.py` | Design 4.3 items 1 to 10 and the text-only bans: only `should_show_downloads` reads how a turn ended (and `TurnView` has no field for it); the four outcomes render the same tree, attributes included, once the download area is removed; the reply slot is exactly the reply, with no element made from it, for a markup-like sentinel under each outcome; no link unless delivered, even with files listed; nothing result-shaped before the turn is finished; the M3 guard for an empty, null or missing `steps`; session scoping; a fallback reply unchanged; the page's reply equals the JSON reply for a delivered, a rejected and a no-plan turn; a finished turn missing a field is a `500`; no `|safe`, `Markup(` or autoescape off; no `innerHTML` and the rest in `app.js`; the banned-word tripwire; no check marks, crosses or status colours. |
| `tests/web/test_copy_snapshot.py`, `ui_copy_snapshot.txt` | Item 11: every string the page writes. **The reviewer reads the snapshot** and signs it off in the review file. |
| `tests/web/test_gate_and_session.py`, `test_upload_and_turns.py`, `test_plan_card.py`, `test_errors_and_limits.py`, `test_static.py` | The cases of design 10.1: the gate, the session lifecycle, upload, the turn and poll (with an injected clock), the plan card, every error row the server decides, the bucket each route is charged to, the 300-second full turn at the page's own cadence with no `429` (backend "Decisions (2026-10-01)", item 3), the route declarations, and the pinned htmx. |
| `tests/e2e/test_browser.py` | The five browser tests of design 10.3, in Chromium against the real app (the real pipeline, a queued fake LLM, the production rate limits): the golden path at 360×640, reject, a pipeline failure, an approval timeout, and a lost connection then a reload. |
| `tests/e2e/test_browser_paths.py` | The script's other paths, which `TestClient` can't show (design 10.2): a `429` on a poll, the swap guard, the reset on a `404` (on a send and on a poll), a plan answer that never arrived and one whose reply was lost, a message POST that never arrived and one whose reply was lost, the gate's errors and its `429`, upload errors, Enter and Shift+Enter, and a browser on `localhost`. |

**The base suite on this machine** (Python 3.13, `origin/main` at `d903ba0`) is 2408 passed and **34 failed**, before this change. The 34 are in `tests/buddy/test_values_only_copy.py` and `tests/skills/excel/test_plain_table.py` (a workbook-fixture assertion in `tests/workbook_fixtures.py`); they fail the same way on the base and have nothing to do with this change. `ruff check buddy tests`: 22 errors on the base and 22 on the branch, identical by file and rule; the new code under `backend`, `tests/web` and `tests/e2e` has none. (`scripts/frontend_spike/run_spike.py` has 8 line-length errors; `scripts/` is outside `ruff check buddy tests`. Found by the round 1 review, L5.)

## 6. Accessibility, as design 5 asks the review to say

- **axe-core 4.14.0**, one run over the gate, the empty workspace, the plan card and a finished turn, in the light and the dark scheme: **no violations, nothing marked "needs review"** (`scripts/frontend_axe_run.py`, results in `docs/reviews/frontend-demo-axe-results.json`). Disabled controls are not audited by axe.
- **Not done** (design 16): axe in CI, the 390×844 and 768 px viewports, and a manual NVDA or VoiceOver pass. Only one viewport (360×640) is in the browser tests.

## 7. Deferred

For `docs/backlog.md`; the design's own list (Section 16) stands as well.

- A **"New session"** control (Section 4, item 9).
- **Versioned static file names and long caching** (FE-2).
- A collapsible Files panel on a phone, if it is wanted (Section 4, item 4).
- **The download name** (Section 4, item 12): show the name the download will save, or lift the backend's 100-character cap.
- The newer **htmx** release, if the owner accepts its one `sessionStorage` key (Section 1).
- Auto-focus on the composer when a turn finishes will raise the on-screen keyboard on a phone. The design asks for it, and it is untested on a real phone.

## 8. Not run

- A real phone, and any browser but Chromium (design 10.5).
- A reverse proxy and HTTPS: the browser tests run on `http://127.0.0.1`, where Chromium accepts the `Secure` cookies. Whether the pointer cookie behaves the same behind a TLS proxy on another host is untested here.
- Nothing else in the live runs: see `frontend-demo-live-runs.md` for what they did and didn't cover.

## 9. Review and live runs

- Round 1 of the independent review (`frontend-demo-review.md`, product code `6522a21`): **MERGE**, no HIGH, two MEDIUM that an ordinary request can't reach (M1: after a restart in password-less dev, a stale plan card stays; M2: a `409` on the plan card with a poll in flight leaves the card), nine LOW. The reviewer's prototype fixes for both are in `scripts/review_harness/feat-frontend-demo/out/proto_fix.diff`. They are deferred, as the loop says, and are in `docs/backlog.md`, Section 4a. They should be fixed before the public link.
- The reviewer ran as Opus: the strongest model the agent tool offers (Fable) was refused twice for lack of usage credits.
- Live runs: `frontend-demo-live-runs.md` (4 of 4, no 429, no false claim, originals unchanged).

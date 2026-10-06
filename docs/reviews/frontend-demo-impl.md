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

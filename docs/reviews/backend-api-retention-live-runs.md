# Live runs: `feat/backend-retention`

Run on head `dad8b1a` (product code identical to the reviewed `6959db2`: only review files were added after it), by `python scripts/live_api_runs.py --runs A,W,O,L,S --pause 5`, through the real HTTP server (`python -m backend`, uvicorn, one worker, bound to 127.0.0.1) with the real pipeline and the real Groq provider (proxy-attached key, never read). Server settings: `WORKBUDDY_ENV=production`, a placeholder demo password, `WORKBUDDY_PUBLIC_ORIGIN=https://workbuddy-live.example`, the design's real rate limits (runs A, W, O, L); run S uses a short `WORKBUDDY_SESSION_TTL_HOURS=0.004` (14.4 s) and `WORKBUDDY_SWEEP_INTERVAL_S=2`. Every request carries the access cookie and the page's `Origin`, as the demo page does (the cookie is `Secure`, so the driver attaches it by hand over loopback http).

**Result: all 5 runs completed. No Groq 429 (0 provider 429 lines in the server logs).** Two runs (A, S) used the real model; W, O and L need no model call.

## Turns with the real model

| | Run A: the full flow | Run S: a session removed by the sweep |
|---|---|---|
| Flow | exchange password for the cookie, upload, turn, approve the plan, download | upload, turn, approve, download, then idle until swept |
| Intent / `clarification_required` | `remove_duplicate_rows` / `false` | `remove_duplicate_rows` / `false` |
| Steps executed | `spreadsheet.analyze` (success, verified), `spreadsheet.deduplicate` (success, verified), `spreadsheet.analyze` re-check (success, verified) | the same three |
| Plan | one plan listing the write step; approved; `confirmation: approve / user` | the same |
| File delivered | yes, `manual_report_deduplicated_<id>-task-1.xlsx`; download SHA-256 equals the recorded one | yes; same checks |
| Independent check of the file (pandas, against the original) | 4 rows, 0 duplicates, 1 empty row; equals `drop_duplicates()` of the original (6 rows, 2 duplicates, 1 empty) | the same |
| Reply | "Done — removed 2 duplicate row(s). The copy has 4 row(s), 0 duplicate row(s) remaining. Your cleaned copy of manual_report.xlsx is ready. The copy has the values only; formatting, column widths and other sheet settings aren't carried over. I also noticed: name: 1 missing value(s) (25.0%); amount: 1 missing value(s) (25.0%). Want me to tackle any of these?" | the same text |
| False claim? | No: 2 removed, 4 rows, 0 duplicates remaining, and 1 missing value in each of 4 rows, all match my own read of the file | No |
| Original SHA-256 before / at upload / stored after | `498940f8…0afda` in all three | `498940f8…0afda` in all three |
| Cookie | `Set-Cookie: __Host-wb_access=<token>; Path=/; Secure; HttpOnly; SameSite=Strict`; Bearer for scripts also served the session (`200`) | `retention_hours` advertised: `0.004` |
| After idling | | the three folders existed before; **18.0 s** after the turn all three (uploads, outputs, sessions) were gone; `GET` of the session `404`, the delivered file's download `404`; a control session polled every 3 s was still served (`200`) |

## Runs without a model call

- **W, wrong password and no credential.** Six probes: a wrong password, a form with no password field, no credential, a wrong Bearer, an unknown cookie, and a wrong Bearer beside the valid cookie. Every one is the same `401`, with the same body (`unauthorized`, "The demo password is missing or wrong.") and `WWW-Authenticate: Bearer realm="workbuddy-demo"`; none sets a cookie. `every_one_is_the_same_401: true`.
- **O, a wrong Origin.** A valid cookie with a foreign Origin, with none, and with `null`, and the access route with the right password and a foreign Origin or none: five attempts, each `403 forbidden_origin`, no cookie set, and **0 sessions created**. A cookie `GET` without Origin is served (`404`, the session doesn't exist: authorized), and a Bearer `POST` without Origin gets `201` (the script path).
- **L, going over a rate limit.** After 38 quick requests (the bucket had already been used by runs A, W and O) the next was `429 rate_limited` with `Retry-After: 1`. A second request at once was `429` again. After waiting exactly 1 s the same request was served (`404`, no such session): recovered. `GET /healthz` was `200` while `general` was limited.

## Server logs

0 logging errors, 0 tracebacks, 0 lines containing a session id, 0 containing the demo password, 0 containing any of the 3 access tokens issued; 2 of the server's own `429` responses (run L), 0 provider `429`s.

## Not run

Python 3.13, Windows (the read-only-file fix is checked only by simulation), a real browser and a real reverse proxy, the older numbered runs (1 to 9, R, T, C: PR-4 doesn't touch the pipeline or the replies, and the reviewer's regression probes found the replies equal to the base's), and run 9's host measurement for the demo host (a deployment checklist item in part 2, not a PR-4 test).

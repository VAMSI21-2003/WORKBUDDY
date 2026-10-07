# `feat/frontend-deploy`: fixes for review round 1

Answers `docs/reviews/frontend-deploy-review.md`, round 1 (commit `0f0a09c`, verdict DON'T MERGE). Fixes are in commit `0c132fa`; the live-runs file in the commit after it. I haven't overruled or downgraded any finding. Each is closed here only as a claim for the next reviewer to confirm.

| Finding | What I did | Test |
|---|---|---|
| **M1** (blocking): `frontend-deploy-live-runs.md` cited but missing | Ran the reviewer's prescribed fix: `python scripts/live_frontend_runs.py --container workbuddy:fe2 --runs 1,2,3,4 --shots docs/reviews/frontend-deploy-screens`, against the image rebuilt from `0c132fa`, real Groq. 4 of 4 completed, no 429, no false claim I could find, original unchanged in all four. Wrote `docs/reviews/frontend-deploy-live-runs.md` (the `CLAUDE.md` format) and `…-live-runs.json`, with screenshots. Pointed the notes' package-list reference at `frontend-deploy-image-freeze.txt`. **The post-MERGE repeat on the final head, which the task also asks for, will replace this file.** | the file; the runs themselves are the evidence |
| **L1**: `deploy.md` Section 4 said a page open across a restart is reloaded | Reworded: the reload applies in a server with no password (dev); in production a restart ends every access token and the page shows the password notice | none (prose) |
| **L2**: "reported by the variable's name" | Reworded in Sections 1 and 2: the message names the setting, sometimes by the variable and sometimes by its internal field (`max_upload_bytes`, `public_origin`, …), never its value. I changed the doc and not the messages | none (prose) |
| **L3**: after a restart, the lost-answer lookup and the `409` refetch carry no version | New query parameter `held` on the poll route: the page sends the turn's `data-version` with both requests; `poll` treats `v` or `held` above the server's version as a restart and answers `HX-Refresh`. `v` still takes precedence; the no-change shortcut still needs `v`. The copy snapshot gained the two URL fragments (`&held=`, `?held=`), regenerated with `--update-ui-copy` for the reviewer to read | `test_a_refetch_that_names_the_version_the_page_holds_gets_the_reload_after_a_restart` (route); browser: `test_a_409_after_a_restart_with_no_poll_armed_…` and `test_a_lost_answer_after_a_restart_with_no_poll_armed_…` (the page's polls are removed after the restart, so only the refetches can recover it). Each fails when I take `held` out of the JS URL or the route (mutations run) |
| **L4**: two guards untested | (a) The middleware's `static and` condition: a direct ASGI test over six paths (a static path keeps its own `Cache-Control`; a static error, an API path, `/ui/…`, `/staticfile` and `/` get `no-store`). It fails with `static = True` and with `static = False` (mutations run). (b) The `setTimeout(…, 0)` in `refetchTurn`: **I did not write a test for it, and I couldn't show it's needed**: the comment no longer claims a fetch started inside the handler "would be dropped". It now says only that the fetch starts once the handler has returned. Left for the reviewer to judge whether to keep the timeout | `test_no_store_is_replaced_only_by_what_a_static_file_route_marks_itself` |
| **L5**: dev server caches a static file's version for the process | `static_version` now reads the file's `st_mtime_ns` and size, and caches the hash per state of the file, so an edited file gets a new `?v=` | `test_an_edited_static_file_gets_a_new_version_under_a_running_server` (fails with the old caching: mutation run) |
| **L6**: 20,000 rows | Corrected: 24,497 data rows, 1,223 exact duplicates, 3 empty (I generated the fixture again and counted with pandas) | none |
| **L7**: a root-owned `/data` ends in a traceback | `backend/__main__.py` catches `PermissionError` from `create_app` and prints "WorkBuddy can't start: this process can't write to its data folder … (Permission denied)", exit status 2. `deploy.md` Section 4 says so | `test_a_data_folder_this_user_cant_write_is_named_not_a_traceback` |
| **L8**: `llm_configured: true` doesn't mean the key works | `deploy.md` checklist: that value only says a key is set; send one real message after setting it and read the reply | none (prose) |
| **L9**: backlog 4a stale | Marked FE-1's M1, M2, L3, L4, L9 and this review's L1 to L9 as fixed on the branch ("not merged"), the static-caching item done, FE-1 merged, FE-2 under review | none |

Also in this round: two documents had cited the live-runs file for the package list (the impl notes), and `deploy.md` Section 6 now has the right row count.

## What the suite and lint show

The first full suite run after these fixes (lxml installed) had **2 failures, both mine**: the two `409` browser tests recognised the refetch by a poll URL with no query string, and the refetch now carries `?held=`. I fixed the helper (`settle_stale_card` in `tests/e2e/test_browser_races.py`: the refetch is the poll request that names `held` and no `v`) and checked the old `409` branch still fails the test (mutation run). With that, all 9 tests in the file pass; the full suite is run again before the next review. `ruff check buddy tests`: 22 errors, the same by file and rule as the base.

## For the reviewer to look at

- `poll` in `backend/web/routes.py` (the `held` parameter) and `refetchTurn` / `lostAnswer` in `app.js`.
- Whether the `setTimeout` in `refetchTurn` should stay (L4b).
- The new live-runs file: do the replies hold up against the files?

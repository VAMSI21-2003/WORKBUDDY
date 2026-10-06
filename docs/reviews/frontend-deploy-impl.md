# `feat/frontend-deploy`: implementation notes (FE-2, with the FE-1 review's fixes and a fixture fix)

Written by the implementer. Everything here is a claim for the reviewer to check, not a finding. Base: `origin/main` at `a49f46a` (FE-1 merged). Design: `docs/design/frontend-design.md` (Sections 11 and 13.1, updated by this branch). Review being answered in Part A: `docs/reviews/frontend-demo-review.md`.

The branch is three parts, one commit each (plus the review loop's own files): A the FE-1 review's fixes, B the test fixtures, C FE-2.

## Part A: fixes from `frontend-demo-review.md`

| Finding | Change | Test (fails on the old code) |
|---|---|---|
| M1 (a restart under a waiting plan card) | `backend/web/routes.py` `poll`: a poll whose `v` is higher than the server's version for that turn gets an empty `200` with `HX-Refresh: true`. The reviewer's prototype, with a comment. | `tests/web/test_upload_and_turns.py::test_a_poll_from_a_page_that_outlived_a_restart_is_an_empty_reload_instruction`; browser: `tests/e2e/test_browser_races.py::test_a_server_restart_under_a_waiting_plan_card_reloads_into_what_the_server_has` (a real restart of the server under the page, dev mode, one reload and no loop) |
| M2 (a `409` on the plan card never refetches) | `app.js`: the `409` branch calls `refetchTurn(plan)`: after the answer's own request has ended (`setTimeout 0`), `htmx.ajax('GET', plan.dataset.refetchUrl, {target: turn})`. It no longer goes through the turn's poll element. The reviewer's prototype. | browser: `test_a_409_on_the_plan_card_with_a_poll_in_flight_…` and `…_with_no_poll_in_flight_…` (two tabs; the second variant gives tab B a poll armed 600 s out, so only the refetch can update it) |
| L3 (a long unbroken file name at 360 px) | `styles.css`: `overflow-wrap: anywhere` on `.plan-steps li`, `.plan-note` and `.plan-warning` | browser: `test_a_long_unbroken_file_name_overflows_nothing_at_360_px` (plan card, reach of both buttons, the delivered link) |
| L9 (`Referrer-Policy`) | `backend/middleware.py` `SecurityHeadersMiddleware` sets `Referrer-Policy: no-referrer` on every response | `tests/backend/test_app_basics.py`, `tests/web/test_gate_and_session.py` |
| L4 m23 (a 5xx on the message POST) | no product change | browser: `test_a_5xx_on_the_message_post_the_server_never_got_…` and `…_did_get_…`: the POST is sent once and never again, the transcript is asked once (`?after=`), the draft is kept or the turn is found, the server holds 0 or 1 turn |
| L4 m22 (a lost answer re-enables the buttons) | no product change | browser: `test_after_a_lost_plan_answer_the_buttons_stay_off_until_the_server_says_what_state_it_is_in` (the page's lookup is held; the buttons must be off while it waits) |
| L4 m08 (Files panel counts a turn the response shows running) | no product change | `tests/web/test_upload_and_turns.py::test_the_files_panel_leaves_out_what_the_turn_being_rendered_as_running_has_delivered` |
| L4 m24 | covered by the two M2 tests | |

I checked each new test against the code it guards by mutating a copy of the tree and running only that test: removing the `v > version` branch, putting back the old `409` branch, removing the lines that disable the buttons, replacing `ambiguousPost` with `composer.requestSubmit()`, removing the `exclude_turn_id` test in `files_view`, removing the CSS rule, removing the header. Each mutation made its test fail (the reviewer's m22 and m23 mutations are among them).

`tests/e2e/conftest.py`: `live_app` can now pass `env="dev", demo_password=None`, and `LiveApp.restart(factory)` stops the server and starts a new app on the same port and data root (everything in memory is gone, what's on disk stays).

**Not done, on purpose (for the reviewer to judge):**
- The refetch after a lost answer (`lostAnswer`) and the new `409` refetch carry no `v`, so after a server restart they could still be dropped by the swap guard (version 0 against the version the page holds). That needs a restart during the very click, in password-less dev (with a password a restart ends every access token, and the poll's `401` stops the page). The next poll of the turn gets the `HX-Refresh`, if its poll element is still armed. I didn't add a parameter for it.
- L1, L2, L5, L6, L7, L8: not in the request. They stay in `docs/backlog.md` section 4a.

## Part B: the test fixtures

The failure, reproduced here with lxml 6.1.3 (Python 3.13, openpyxl 3.1.5): the same 34 tests (`tests/buddy/test_values_only_copy.py`, `tests/skills/excel/test_plain_table.py`). `lxml` is installed in this sandbox only because `python-docx` and `python-pptx` need it; the project's own requirements don't pull it in, and the Docker image has none.

Cause: `save_formula_values` matched openpyxl's serialization with a regex (`<v />`); with lxml openpyxl writes `<v></v>` (and puts namespace declarations elsewhere). One test (`test_an_empty_string_result_counts_as_no_saved_value`) did the same with a literal `str.replace` in its body and was caught only by its own `assert crafted != xml`.

Change (`tests/workbook_fixtures.py`):
- `_edit_first_sheet` parses the first sheet with `xml.etree`, lets the helper change elements, and writes it back (namespace registered as the default, so no `ns0:`). The helpers no longer depend on how the XML was written.
- `save_formula_values` uses it; it asserts the cell is a formula cell with no saved value, as before.
- `save_empty_string_result(source, target, cell)` is the old inline craft (`t="str"`, an explicit `<v></v>`). The test now calls it and asserts the exact text `<c r="C3" t="str"><f>B3*2</f><v></v></c>` is in the written part. `tests/skills/excel/test_plain_table.py` changed only in that test, and what it asserts about the counts is unchanged.

Evidence that the workbooks are the same either way (`scripts/check_fixture_parity.py`, which builds every fixture helper's output and compares the parsed XML trees of every part, ignoring the two timestamps in `docProps/core.xml`, plus openpyxl's reading of the cells):
- lxml in use against `OPENPYXL_LXML=False` (openpyxl's own switch for writing as it does without lxml): 16 files, 128 parts, 0 different.
- The new helpers against the old helpers' output with `OPENPYXL_LXML=False` (where the old ones worked): 16 files, 128 parts, 0 different.
Full suite (`python -m pytest -q`, this head before the review loop; Python 3.13.16, pandas 2.3.3, openpyxl 3.1.5, lxml 6.1.3 when installed):
- with lxml installed (`openpyxl.LXML` is `True`): **2588 passed, 0 failed**;
- with lxml uninstalled (`pip uninstall lxml`; `openpyxl.LXML` is `False`; reinstalled afterwards): **2588 passed, 0 failed**.
- For comparison, the FE-1 review's own run of `main` (with lxml) was 2408 passed, 34 failed. I didn't run `main` myself. This branch adds 12 tests (2 route tests, 3 static tests, 7 browser tests) to the 2576 the FE-1 review counted on its branch.
Summaries: `docs/reviews/frontend-deploy-lxml-parity/suite_*_summary.txt`.

The product, not only the fixtures, in both environments: the existing probes `scripts/review_harness/values_only_probe.py` (725 turns: 29 odd one-sheet workbooks x 5 requests x template, friend and focus) and `round2_probe.py` (400 turns), which use the real `build_engine`, skills, Hands, Inspector and Voice with only the LLM faked. **They can't build their own fixtures when lxml is installed** (the same cause: they match openpyxl's XML text, and stop on `assert new != xml`), and they are a reviewer's scripts, so they aren't edited. `scripts/probe_lxml_parity.py` runs their fixtures and their loop unchanged, but builds each fixture once in a subprocess where openpyxl writes as it does without lxml, and runs the product in-process with lxml installed and used (`openpyxl.LXML` is `True` there). Compared by (fixture, request, speaker, session id), on the reply, whether a file was handed back, the steps, the original's hash check, and the cells and raw XML scan of the original and of the delivered copy: **0 fields differ in 725 of 725 runs, and 0 in 400 of 400**; the original changed in 0 runs, and 0 runs errored. As a control, the wrapper with `OPENPYXL_LXML=False` also equals the unmodified probe's no-lxml output in both probes. 320 and 144 of those runs delivered a file. Outputs: `docs/reviews/frontend-deploy-lxml-parity/`.
Not run: pandas 3.0.6 (the reporter's version; this sandbox has 2.3.3, and the image resolved 2.3.3 too).

## Part C: FE-2

- `Dockerfile`: `python:3.11-slim`; user `app` (uid 10001, no login shell) that can write only `/data`; installs `requirements-runtime.txt`; copies `backend/ buddy/ core/ skills/`; `WORKBUDDY_ENV=production`, `WORKBUDDY_DATA_DIR=/data`, `PORT=8000`; `HEALTHCHECK` calls `/healthz`; `CMD python -m backend --port "$PORT"`. An optional BuildKit secret `pip_ca` lets pip trust an extra CA bundle for the install step (needed here: the sandbox's proxy re-signs TLS); without the secret nothing changes and the bundle isn't in the image.
- `.dockerignore`: ignore everything, then allow only what the Dockerfile copies (so no `.env`, `workspace/`, `scratch/`, `.git/`, or tests reach the build context).
- `requirements-runtime.txt` (new) holds the runtime packages; `requirements.txt` is now `-r requirements-runtime.txt` plus the development tooling, so `pip install -r requirements.txt` installs exactly what it did.
- Static caching: the pages link `/static/<name>?v=<12 hex digits of the file's SHA-256>` (`static_url` in `backend/web/templating.py`). `StaticAssets` (a `StaticFiles` subclass in `backend/web/routes.py`) answers `Cache-Control: public, max-age=31536000, immutable` when `v` is the file's current version and `no-cache` otherwise (200 and 304). `SecurityHeadersMiddleware` keeps its `no-store` on everything except a `/static/` response whose route set its own `Cache-Control`; a missing file's error stays `no-store`. Tests: `tests/web/test_static.py`.
- `Referrer-Policy`: Part A (L9). The CSP, `nosniff`, `no-store`, HSTS in production and the htmx configuration of design 11.2 were already in FE-1.
- `docs/deploy.md`: every environment variable (from `Settings.from_env`, the Groq and Anthropic keys, `PORT`), the 13.2 checklist with each item's status, restart and disk behaviour, and the measurements.
- `docs/design/frontend-design.md`: 11.1, 11.2 (static versions), 11.3 (Option A removed; no host chosen), 13.1 (FE-2's contents), and two other lines that named Option A.
- `scripts/measure_container.py`: the measurement harness (below), built on `scripts/live_api_runs.py`'s driver and fixtures.

**Deviations from the design, on purpose:**
1. The command is `python -m backend --port "$PORT"`, not `uvicorn backend.main:app …`: `backend.main` has no `app`.
2. The static version is in the query string, not the file name (no renaming, no build step); the immutable header applies only when the version in the URL is the file's current one.
3. A separate `requirements-runtime.txt` (the design said `pip install -r requirements.txt`, which would put Playwright, mypy, black and the rest in the image).

**Not done:** no host is chosen. Pinning or locking the runtime dependencies isn't done: the image resolves versions at build time (the build here got pandas 2.3.3, ydata-profiling 4.18.4, splink 4.0.17; the full list is in `docs/reviews/frontend-deploy-live-runs.md`). `huggingface.co` can't be reached from this sandbox, so the claim in design 11.3 about Docker Spaces needing a paid plan is the owner's, and unchecked.

## Measurements and runs

How they were done, and every result, are in `docs/deploy.md` (Section 6) and `docs/reviews/frontend-deploy-live-runs.md`. Docker works in this sandbox: the daemon was started by hand (`dockerd`), the base image comes from a mirror (`mirror.gcr.io/library/python:3.11-slim`, the same digest as Docker Hub's; Docker Hub itself answered `429` to the anonymous pull) and was tagged `python:3.11-slim` locally, and containers run with `--network host`.

## What to look at first

- `backend/web/routes.py` `poll` and `StaticAssets`; `backend/middleware.py`; `app.js` `refetchTurn`.
- The new browser tests: do they test what their names say (the `409` tests build tab B's state by routing its polls)?
- `tests/workbook_fixtures.py`: does anything a test reads from a fixture differ from before?
- `Dockerfile` and `.dockerignore`: what is in the image, who can write where.
- `docs/deploy.md`: every variable against `backend/settings.py`.

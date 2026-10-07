# Review: `feat/frontend-deploy` (round 1, commit 0f0a09c)

**Verdict: DON'T MERGE.** One blocking item, M1: the live runs that `CLAUDE.md` requires before a merge aren't in the branch, although `docs/deploy.md` and the implementation notes point to them. The fix is to commit them. The product code has no HIGH finding and no MEDIUM finding. The FE-1 review's M1 and M2 are fixed, and I confirmed both with my own runs.

Base `origin/main` a49f46a. Reviewer with no part in writing the change. Probes, outputs and how to rerun them: `scripts/review_harness/feat-frontend-deploy/` (`README.md`).

## What I ran

| Run | Result |
|---|---|
| `python -m pytest -q` (head, lxml 6.1.3 installed) | 2588 passed, 0 failed (427 s) |
| The 2 lxml-sensitive files, base vs head | base: 34 failed. head: 196 passed, and 196 passed with `OPENPYXL_LXML=False` |
| `ruff check buddy tests` | 22 errors on head and 22 on base, identical by file and rule. `backend/` and the new scripts are clean |
| Branch's new tests on base code | 9 fail (M1 route, M1 browser, both M2 browser tests, L3 browser, Referrer-Policy ×4), and `test_static.py` errors at import. The m08/m22/m23 guard tests pass on base, as expected for guards (`out/new_tests_on_base.txt`) |
| Mutations (17, throwaway copies) | 14 killed. 3 survived: d05 and d06 are equivalent mutations; d13 is L4 below (`out/mutations.txt`) |
| UI probe, 25 scenarios × template/friend/focus, real `build_engine`, fake LLM, head vs base | 75 vs 75 rows. Same outcome, reply (catalog lines masked), plan, links and downloads. The one difference is a polling-timing artifact. Rule checks: 150 rows, 0 problems. Original unchanged 75/75 (`out/ui_head_vs_base.txt`) |
| FE-1 browser probe, 3 speakers (head) and template (base) | b01 (restart under a plan): head shows the server's account after a reload, base is stuck on the card. b06 (409 with a poll in flight, and idle): head refetches, base stays stale. All other cases are the same. Original unchanged in every case |
| Adversarial HTTP probe, 7 cases × 3 speakers, head and base | Hostile `v` → reload or 422, never a 500. Restart under a plan → `HX-Refresh`, and the reload shows the failed turn's honest reply with no link. Static edge cases (HEAD, Range, stale ETag, case and padding of `v`, two `v`s, encoded traversal, directory, POST). Every non-static response is `no-store` + `no-referrer` + `nosniff`. Original unchanged in every run (`out/adversarial_*.jsonl`) |
| Browser adversarial (template) | w01, a restart during a lost answer: recovers through the poll's reload. w02 is L3. w03, a restart with a password: 401 notice, polling stops |
| Fixture parity (my own, strict XML trees and the product's reads) | New helpers (lxml, and `OPENPYXL_LXML=False`) vs old helpers: 5 of 5 identical. Old helpers with lxml: fail at C2, as reported |
| Image, built from head (`docker_probe.py`) | Runs as uid 10001. `/app` is read-only, `/data` is writable. No tests, docs, `.git`, `.env`, pytest, playwright or lxml in the image. No `/run/secrets`, and no CA bundle or secret in `docker history`. Bad settings exit 2 and never echo a value. Healthy after 6 s. `docker stop` takes 0.9 s. A production flow: password and placeholder key absent from the logs, and the original's SHA-256 is the same before and after |
| Live runs, real Groq, against my image (4 runs of design 10.4) | 4 of 4, no 429. Runs 1 and 2 delivered files that match the reply when I read them with openpyxl (4 rows / 0 duplicates / 1 empty; 3 / 0 / 0). Runs 3 and 4 delivered nothing, and their replies say so truthfully. No false claim. Original SHA-256 `d9096c8c…` before and after in all 4 (`out/live_runs_container.*`) |
| `docs/deploy.md` against `backend/settings.py` | Every variable and every default matches. Wording errors are L1 and L2 |
| Design deviations (11.1–11.3, 13.1) | Three: the CMD is `python -m backend`; the version goes in the query string, not the file name; `requirements-runtime.txt`. All three are stated in the design and the notes. `requirements.txt` still resolves the same package set as base. Option A was removed on the owner's report, which is marked as unchecked |

## Findings

**M1 (MEDIUM, blocking): the required live-runs file is missing, and the docs cite it.** [Certain]
- **Where:** `docs/reviews/frontend-deploy-live-runs.md` doesn't exist in any commit.
- **Who cites it:**
  - `docs/deploy.md` line 129 says it "has the four runs of design 10.4 against the image".
  - `docs/deploy.md` line 180 also points to it.
  - `docs/reviews/frontend-deploy-impl.md` lines 67 and 71 say it holds every result and the image's package list. The package list is actually in `frontend-deploy-image-freeze.txt`.
- **Why it blocks:** `CLAUDE.md` says live runs with Groq come before merging, with the results in this file. The owner choosing a host would follow a link to evidence that isn't there.
- **Fix:**
  1. Run `python scripts/live_frontend_runs.py --container <image> --runs 1,2,3,4 --shots <dir> --json <file>`. Pass `--shots`, because the default overwrites FE-1's `frontend-demo-screens/`.
  2. Commit the results to that file in the `CLAUDE.md` format: intent, `clarification_required`, steps, delivered, independent file check, reply, false claim, and the original's hash before and after.
  3. Point the notes' package list at `frontend-deploy-image-freeze.txt`.

  My own four runs (`out/live_runs_container.*`) show the runs pass. They are a reviewer's check, not the branch's record.

**L1 (LOW): `docs/deploy.md` Section 4 says a page open across a restart is reloaded by `HX-Refresh`.** [Certain]
- That only happens in dev without a password.
- In production a restart ends every access token, so the next poll gets `401` and the page shows "The demo password is missing or wrong. Enter the password again" (a07, w03).
- **Fix:** say that the reload applies to dev only, and that production users see the gate notice.

**L2 (LOW): `docs/deploy.md` Section 2 says a bad value is reported "with the variable's name".** [Certain]
- Range and format errors name the internal field instead, for example `max_upload_bytes must be a positive integer` or `public_origin must be an https:// origin in production` (k01).
- **Fix:** reword the doc, or put the variable names in those `SettingsError` messages.

**L3 (LOW, dev without a password only; disclosed by the implementer): stale answers after a restart.** [Certain]
- **What happens:** after a restart, the lost-answer lookup and the new `409` refetch carry no `v`. Their answer is version 0, and the swap guard drops it.
- **Recovery:** the page recovers only when its poll element is still armed (w01 recovered).
- **When it doesn't recover:** with no poll armed, the card stays, with "This turn has no plan waiting for an answer." (w02). The page always has a poll while a plan waits, so ordinary use doesn't reach this.
- **Fix:** add `v=<turn's data-version>` to the `lostAnswer` and `refetchTurn` URLs.

**L4 (LOW): two guards have no test that fails without them.** [Certain that the mutations survive; Possible that the timeout is needed]
- **The `setTimeout(…, 0)` in `refetchTurn` (`app.js`):** removing it leaves both `409` browser tests green (d13). The comment's reason, that "a fetch started now would be dropped", isn't shown by any test.
- **The `static and` condition in `SecurityHeadersMiddleware`:** no test checks it (d06). It's equivalent today, because no other route sets `Cache-Control`.
- **Fix:** a test for each, or soften the comment.

**L5 (LOW, dev only): an edited static file can be cached under its old version.** [Certain]
- `static_version` is cached for the life of the process (`@cache`).
- If a static file is edited while a dev server runs, the new content is served under the old `?v=` with `immutable`, so a browser can keep a stale script until the server restarts.
- The image's files are read-only, so production isn't affected.
- **Fix:** don't cache the version in dev, or answer `no-cache` there.

**L6 (LOW): `docs/deploy.md` Section 6 gives the 2 MB fixture as "20,000 rows".** [Certain]
- `sized_fixture` rescales the row count. The measurements show 23,272 + 1,225 = 24,497 data rows.
- **Fix:** correct the number.

**L7 (LOW): a root-owned bind mount on `/data` makes the container exit 1 with a Python traceback** (`PermissionError: '/data/uploads'`, k03). [Certain]
- The doc does warn about this.
- **Fix:** optionally, a one-line startup message that names `/data` and uid 10001.

**L8 (LOW, existing behaviour, a gap in the doc): a configured but unreachable or invalid LLM key isn't visible to an operator.** [Likely]
- In k04 the key was a placeholder and the container had no egress. `/healthz` still said `llm_configured: true`.
- A dedupe request then ended with "…No changes were made to any file this turn.", with no hint that the model failed.
- That is true, but `deploy.md` describes only the no-key case.
- **Fix:** a line in Section 2 or the checklist: verify one real turn after setting the key.

**L9 (LOW): `docs/backlog.md` 4a is stale.**
- It still lists M1, M2, L3, L4 and L9 as open, and says "FE-2 … is open" and "static files are `no-store` for now".
- **Fix:** mark them closed when this branch merges.

## Deferred (added to `docs/backlog.md` 4a)
L1–L9 above. M1 is not deferred: it blocks the merge.

## Not run
- The full suite with lxml uninstalled. I used `OPENPYXL_LXML=False` on the two affected files instead, because the environment is shared.
- The implementer's `probe_lxml_parity.py` (1,125 turns). My strict fixture parity and the UI probe cover the same question more narrowly.
- `scripts/measure_container.py`: memory limits, the 2 MB fixture, and concurrent chains. I checked the numbers in `deploy.md` Section 6 against `frontend-deploy-measurements.json` (all match except L6), but I didn't re-measure.
- The browser adversarial cases for friend and focus (template only). Their text paths are covered by the 3-speaker UI and browser probes.
- pandas 3.x, a real host, `WORKBUDDY_TRUSTED_PROXY_HOPS` behind a real proxy, and `huggingface.co` (blocked).

## Review round 2 (commit aa98844)

**Verdict: MERGE.** No HIGH, no MEDIUM. Round 1's M1 and L1–L9 are closed on my own evidence (L4b by the softened comment, as round 1 allowed). Two new LOW findings, deferred.

Scope: `0f0a09c..aa98844` (product: `backend/__main__.py`, `web/routes.py` `held`, `web/static/app.js`, `web/templating.py`; tests; docs; the live-runs file), round 1's open findings, and regression against base `a49f46a`. A different reviewer from round 1. The earlier cut-off attempt's `_r2` files were read, then every output rerun by me; nothing of theirs is relied on. Probes and outputs: `scripts/review_harness/feat-frontend-deploy/` (README, "Round 2").

| Run | Result |
|---|---|
| `python -m pytest -q` (head) | 2599 passed, 0 failed (526 s); 11 more than round 1, all new tests |
| `ruff check buddy tests` | 22 on head, 22 on base, identical by file and rule. `backend/` and the harness clean (`out/suite_and_lint_r2.txt`) |
| New tests on `0f0a09c` code | 8 fail: L7, L5, the route `held` test, the copy snapshot, both new restart browser tests, both 409 tests (their helper now expects `held=`). The middleware test passes there, as a guard should (`out/new_tests_on_0f0a09c.txt`) |
| Mutations of the fixes (14, throwaway copies) | 13 killed. r07 (drop `refetchTurn`'s `setTimeout`) survives, as disclosed; the comment no longer claims it's needed (`out/mutations_r2.txt`) |
| UI probe, 25 scenarios × 3 speakers, head vs base, both rerun | 75 vs 75: same outcome, reply (catalog lines masked), plan, links, files; only polling-timing differences in 2 status sequences. Rule checks 150 rows, 0 problems. Original unchanged 75/75 |
| FE-1 browser probe (b01–b06) × 3 speakers, head, against round 1's head output | Same behaviour; differences are random ids and opener lines only. Original unchanged everywhere |
| Round 1 HTTP adversarial (a01–a07) × 3, rerun on head | Same as round 1 except app.js's size. Original unchanged 21/21 |
| `held` HTTP probe (h01–h05) × 3 | Hostile values: never a 5xx (`abc`, `1.5`, `''`, `0x10`, `1e3` → 422); reload only above the server's version; `v` wins when both are sent (`v=1&held=8` → poll fragment). Restart + lookup / 409 refetch → `HX-Refresh`; reloaded page shows the failed turn's honest reply, no link. No restart + 409: the whole turn, file read 4 rows / 0 dup / 1 empty, matches the reply's 2 / 4 / 0. Others' session / unknown turn / no token: same 404/401 with or without `held` |
| Browser, w01–w08 × 3 speakers | w02 (round 1's L3) now recovers in all three. w04 (restart + lost answer, no poll): recovers, 2 page loads, no loop. w05 (answer lost on the way back): lookup sends `held=1`, no reload, delivered turn shown, file matches the reply. w06: 409 with no poll recovers. w07 (refetch lost too): "This page can't reach the server.", card stays; true. w08 (password, restart): 401 notice, link, password, same session, page shows the server's reply. Original unchanged in all 24 |
| L5 end to end (`r2_extra.py s01`) | Head: editing app.js under a running app changes the page's `?v=`; the old `?v=` is `no-cache`, the new one `immutable`. `0f0a09c`: same `?v=`, edited content served `immutable` |
| Image built from head (`workbuddy:r2rev`; its 4 changed files byte-equal to head) | Root-owned `/data`: exit 2, the one-line message, no traceback (round 1's image: exit 1, traceback). `:ro` mount and root-owned subfolders: L10 |
| Live runs, real Groq, against `workbuddy:r2rev` (design 10.4) | 4 of 4, no 429, password not in the log. Delivered files read with openpyxl: run 1 4 rows / 0 dup / 1 empty, run 2 3 / 0 / 0, matching every number in the replies; runs 3 and 4 deliver nothing and say so. No false claim. Original SHA-256 the same before and after in all 4 (`out/live_runs_container_r2*`) |
| Committed live-runs file (round 1 M1) | Present, in the `CLAUDE.md` format, consistent with its JSON, and its outcomes and replies match my own runs on the same code (opener lines aside) |
| Docs: L1, L2, L6, L8, L9 | L1's new production claim checked by w08. L2's wording matches the `SettingsError` messages. L6: regenerated, 24,497 rows / 1,223 duplicates / 3 empty. L8, L9 present |

### Findings

**L10 (LOW): `docs/deploy.md` Section 4 says a `/data` the app can't write always stops the server with the one-line message and status 2.** [Certain]
- True only when `/data` itself refuses writes (k03).
- A read-only mount (`-v …:/data:ro`, k06): exit 1 with `OSError: [Errno 30] Read-only file system: '/data/uploads'` and a traceback.
- A writable `/data` holding root-owned `uploads/`, `outputs/` or `sessions/` (an earlier container run as root, k08): the server starts and is healthy, then every new session answers `500 internal_error` (log: `PermissionError … '/data/sessions/[REDACTED]'`).
- Operator setup only; no reply and no user file is affected.
- **Fix:** narrow the sentence and add "`chown -R 10001:10001`, and don't mount it read-only". Optionally catch `OSError` in `backend/__main__.py` too, and check each subfolder with `os.access(…, os.W_OK)` at start.

**L11 (LOW): the design doesn't describe the poll's `held` parameter** (nor round 1's restart reload). [Certain] Design 1.7 and 10.1 list only `v` and `unconfirmed`. The route's docstring and `frontend-deploy-fixes1.md` describe it. **Fix:** one line in design 1.7.

### Deferred (added to `docs/backlog.md` 4a)
L10, L11.

### Not run
- The full suite with lxml uninstalled (not touched by this round's diff).
- `scripts/measure_container.py` (no change to the image's contents beyond the four files above).
- A real host, `WORKBUDDY_TRUSTED_PROXY_HOPS` behind a real proxy, browsers other than Chromium, pandas 3.x.

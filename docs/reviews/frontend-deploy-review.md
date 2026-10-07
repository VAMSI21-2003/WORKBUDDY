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

# feat/google-sign-in: live runs

Run after the round 2 verdict (MERGE at 75eb95b). Head at the time: `dbdd6cb` (it differs from the reviewed commit only in documents, two tests and `scripts/live_stack/`; `git diff 75eb95b dbdd6cb -- backend buddy core skills deploy` is empty). Raw output of every run: `docs/reviews/feat-google-sign-in-live-runs/`. Drivers: `scripts/live_stack/people_flow.py`, `legacy_check.py`, `stack_flow.py` (and the stand-in model, `fake_groq.py`).

## Read this first: what these runs are not

1. **The model was not the real Groq.** Through this sandbox's proxy every call to `api.groq.com` answers `401 Invalid API Key`, with or without an `Authorization` header (checked at the start, in the middle and after the reviews; the proxy's Groq credential is not accepted by Groq). It is a 401, not the 429 that `CLAUDE.md` says to stop on. So the app container talked to `fake_groq.py`, a small server on the host that answers only the three model stages (intent, reasoning, planning) from the user's message, in the JSON shapes the tests' canned replies use (`GROQ_BASE_URL` in the stack's `.env`). **Everything after those three answers is real**: the skills, Hands, the Inspector's checks, the code-rendered reply, the files, the sign-in, ownership and Caddy. The runs show what the sign-in and ownership change does on the compose stack. They say nothing about how the real model classifies a request, and `CLAUDE.md`'s requirement of live runs with Groq is **not met**; they need repeating with a working key.
2. **Google was a fake**: `tests/idp/server.py` on the host, behind Caddy as `https://idp.test` (`WORKBUDDY_OIDC_ISSUER`), signing real RS256 tokens. Real Google sign-in cannot be run from here.
3. It is the real `deploy/` stack (the image, Caddy, `release.sh`, `smoke.sh`, the data volume) on this machine, named `localhost` (Caddy's own certificate authority, trusted by the drivers). Not an Azure VM or a Let's Encrypt certificate. The app is told to trust the two CAs and to reach the model through the sandbox relay (a git-ignored `docker-compose.override.yml`, as in the earlier live runs).
4. For the people runs the per-address `general`, `poll`, `uploads` and `turns` buckets and the sign-in bucket were raised in the stack's `.env`, because every request here comes from one address and the cross-check makes about 80. The per-person buckets (`user_turns`, `user_uploads`) were left at their defaults.

## Run 1: the demo-password version, then a failed release, then the upgrade

- **1a.** `origin/main` (`4b7dec2`) released on an empty stack: `RELEASED 4b7dec29e75d`; its smoke signs in with the password and delivers. (`run1a-…`)
- **1b.** A real session on it: upload, "remove duplicate rows", approved, delivered (6 rows to 4), download hash equals the recorded one. This session is the "saved before sign-in" data for the upgrade. (`run1b-…`)
- **2. A failed release rolls back to the demo version.** The new settings added to `.env` (the Google client, the operator token; `WORKBUDDY_DEMO_PASSWORD` left in), then `release.sh` on a throwaway commit made on purpose to fail (`create_session` raises; never pushed): the new smoke authenticated with the operator token, failed at `POST /api/v1/sessions` (HTTP 500), **rolled back to `4b7dec29e75d`**, and the new `smoke.sh`, seeing the operator token refused, **used the demo password for that old release and passed** ("this is a release from before sign-in with Google"). The site ended up on the old image. (`run2-…`)
- **3. The upgrade.** `release.sh` on the head: build, backup (18 files), start, healthy, smoke with the operator token (upload, plan, delivered, hash check): `RELEASED dbdd6cb9e238 … (was 4b7dec29e75d)`. `releases.log` has all four events. (`run3-…`)
- **3b. What happened to the session from 1b.** Alice, Bob and the operator each asked for its session, its file list, its delivered file and its page: all `404`, byte for byte the answer for a session that doesn't exist. Its folder has an `owner.json` that says nobody; the upload and the delivered file on the volume have the SHA-256 they had before (`45f300dd…` and the recorded one). (`run3b-…`)
- **5. The idle sweep.** With `WORKBUDDY_SESSION_TTL_HOURS=0.01`, the first sweep logged `removed 7 session(s), 0 failed`; the volume then held no session folder, and the legacy upload and delivered file were gone. (`run5-…`) (It removed the sessions of run 4 too; they were idle as well.)

## Run 4: two people on the stack (`people_flow.py`)

Alice and Bob signed in through the account chooser of the fake Google, over HTTPS through Caddy, each in their own cookie jar. The page showed "Signed in as …" to each; an anonymous visitor saw "Continue with Google".

| | Alice | Bob |
|---|---|---|
| message, mode | "remove duplicate rows", focus | "remove duplicate rows, then remove empty rows", friend |
| intent / `clarification_required` | `remove_duplicate_rows` / false | `multi_step_request` / false |
| steps run | analyze, deduplicate, analyze (recheck): all `success`, `verified` | analyze, deduplicate, remove_empty_rows (reads step 1's output), analyze (recheck): all `success`, `verified` |
| plan | shown once, approved | shown once (both steps), approved |
| file delivered | 1 | 1 |
| independent check (pandas) | 6 rows to 4; equals `drop_duplicates()`; 0 duplicates left | 6 rows to 3; equals dedupe then `dropna(how="all")`; 0 duplicates left |
| download hash = recorded hash | yes | yes |
| reply | "Done — removed 2 duplicate row(s). The copy has 4 row(s), 0 duplicate row(s) remaining. … name: 1 missing value(s) (25.0%); amount: 1 missing value(s) (25.0%)." | "… Removed 2 duplicate row(s). Removed 1 fully-empty row(s). The copy has 3 row(s) … I couldn't tell for sure from your message what order you wanted, so the steps ran in this order: deduplicate, then remove empty rows. …" |
| false claim? | none (the file has the 4 rows the reply counts; 1 of 4 missing is 25%) | none (3 rows; the order sentence is true) |
| original SHA-256 before / after | `45f300dd1801ec0a…` local, as recorded, and the server's stored copy: identical before and after | identical |

**Refused the other's session**: for each of Bob (against Alice's session) and Alice (against Bob's), 13 requests (the session, its files, an upload, a turn, a turn read, a plan answer, the delivered file's download, an upload's download, and the page's five routes): all `404`, and byte for byte the answer for a session that doesn't exist (13/13 each). The operator token: 13/13 the same. No credential: 13/13 `401`. Alice reading her own session: `200`; her own download: `200` with the hash above.
**A plan answered by the wrong person**: Alice's second turn waited for approval; Bob's approve and reject both got `404 session_not_found`; Alice's turn was still `awaiting_confirmation` with her plan, and she then rejected it herself (`200`).
**Landing, sign-out, restart**: Alice in a new browser landed in a session of hers; Bob's page did not contain Alice's session. The page's sign-out request returned `204` with `HX-Redirect: /`, and the same browser then got `401`. After `compose restart app`, Alice with the same cookie read her session (`200`); Bob still got `404`.
**The app's log** held none of: the operator token, the client secret, a token (`eyJ…`), either email, a session id, an access-cookie value.

## Rule checks

- **Rule 1:** the two replies above, and the 1b reply, read against the delivered files: no false claim. The wording around the counts is code-rendered, so it does not depend on the stand-in model; the intent classification does, and was not tested with a real model.
- **Rule 2:** the original's SHA-256 was identical before and after, three ways (local, as the server recorded it, and the server's stored copy read from the volume), for both turns.

## Not run

- The real Groq (see above), real Google, Azure, a public name, Let's Encrypt.
- `deploy/check-proxy.sh` (it checks the proxy layout, which this change doesn't touch) and `install.sh`.
- A second real browser on this stack (the Chromium tests in `tests/e2e` ran in the suite, against the fake Google with the app and the provider on different sites).

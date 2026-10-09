# WorkBuddy: rules for every session

WorkBuddy ("Buddy") is an AI coworker that changes the user's files through skills and tells them what happened. This file is loaded into every Claude Code session. Follow it exactly.

## The two core rules

1. **Never claim an outcome that didn't happen.** A reply must never say or imply that something was done, removed, saved or fixed unless it was done and independently verified. A reply that's honest but flat is always better than a friendly false one.
2. **Never modify the user's original file.** Skills read the upload read-only and write a new file under `workspace/outputs/`. Every live run and end-to-end test checks the original is byte-identical afterwards.

Any change that could affect either rule counts as rule-touching (see below).

## Working method

- **Every change goes on its own branch** from `main`. Never commit to `main` directly.
- **Whoever wrote the code never reviews it.** A review is done by a reviewer with no memory of writing the code: a fresh reviewer agent started by the working session, or a separate session. The full procedure is in `docs/process/review-loop.md`. Follow it exactly.
- **Big or rule-touching changes get an independent review** written to `docs/reviews/<branch>-review.md`. Fixes for a review get their own `docs/reviews/<branch>-fixes*.md`. Items left open are listed under a "Deferred" heading in the review, and in `docs/backlog.md`.
- **One session runs the whole loop for such a change:** write, review, fix, review again, until a reviewer's verdict is MERGE. There is no fixed number of rounds: one is enough if the first reviewer finds nothing blocking, and a change that needs more gets more. Every round uses a new reviewer agent.
- **The writer can't overrule a reviewer.** A finding is closed by a fix that the next reviewer confirms. The writer never downgrades a finding, edits a review, or writes a verdict.
- **Changes that are neither big nor rule-touching** (scaffolding, CI, README, deployment config, visual polish) need the test suite and lint, not the review loop. If in doubt, treat the change as rule-touching.
- **A change to any on-disk format needs a migration, and the old fixture stays.** That covers what's saved under `/data`: session and turn records, the file registry, uploads and delivered files. Data an earlier version wrote must still open after the change, because a release replaces the server and keeps `/data`. The change includes the migration; the existing fixture under `tests/deploy/fixtures/` stays exactly as it is; a new one is added beside it with `python scripts/make_data_fixture.py <N>`. `tests/deploy/test_saved_data_survives.py` opens every fixture with the current server and fails if the format changed without a new fixture. Such a change is rule-touching.
- **Live runs with Groq before merging** (see below), with the results written to `docs/reviews/<branch>-live-runs.md`. Which live runs a change needs, and who does them, is in `docs/process/review-loop.md` ("Live runs"): a change that alters what the pipeline does or says gets a few runs in the session; any other change is released on the owner's server and checked there, which costs no session usage. Report, for each run: the intent, `clarification_required`, the steps executed, whether a file was delivered, an independent check of the file, the reply, whether it makes any false claim, and the original file's hash before and after. If you hit a 429, stop and report how many runs completed.
- **Only the user merges to `main`.** Don't merge unless the user explicitly asks in the current conversation.
- Report results faithfully. If tests fail or a step was skipped, say so.

## Secrets

- **Never print, log or commit any part of an API key**, not even a prefix or length check.
- Read keys from the environment: `GROQ_API_KEY` (preferred, free) or `ANTHROPIC_API_KEY`. On the user's Windows machine the Groq key is stored at user scope and is loaded into a command's environment without echoing it, for example in PowerShell:
  `$env:GROQ_API_KEY = [Environment]::GetEnvironmentVariable('GROQ_API_KEY','User')`.
  In cloud sessions, `GROQ_API_KEY` is a placeholder and the proxy attaches the real key for `api.groq.com`, so don't try to read the key.
- `.env` files are git-ignored. Tests use obvious placeholder keys only.

## Running things

- **Tests:** `python -m pytest -q`. The full suite takes about 150 seconds, so use a command timeout of at least 5 minutes. All LLM calls in tests are faked; no key is needed.
- **Lint:** `ruff check buddy tests` (line length 100). There are known lint errors already in the tree. Don't add new ones.
- **Live run:** `python scripts/run_dedupe_manually.py --mode focus --message "<request>"` with `GROQ_API_KEY` set. It runs the real pipeline (`build_engine` with `PersonalityVoice`) on a fresh fixture (`scratch/uploads/manual_report.xlsx`: 6 rows, 2 exact duplicates, 1 empty row), then prints every stage, the reply, an independent check of the delivered file, and the original's hash before and after. `scratch/` and `workspace/outputs/` are git-ignored.
- **Setup:** `pip install -r requirements.txt`. `.xls` reading needs `xlrd`, which isn't installed.

## Where things live

- **Pipeline:** `buddy/`. A turn runs Eyes → Brain (intent) → Think (reasoning) → Planner → Delegate → Hands (runs skills, chains steps) → Inspector (independent verification) → Voice (the reply). The real wiring is in `buddy/engine_factory.py`.
- **Engine and dispatcher:** `core/`.
- **Skills:** `skills/` (`skills/excel/`: `dedupe.py`, `remove_empty_rows.py`, `analyze.py`).
- **Architecture and product docs:** `docs/WORKBUDDY_ARCHITECTURE.md`, `docs/BUDDY_SKILLS.md`, `docs/BUDDY_PERSONALITY.md`.
- **Design docs:** `docs/design/`.
- **Reviews, fixes and live-run results:** `docs/reviews/`.
- **Open work, in priority order:** `docs/backlog.md`.

## Decided behaviour (don't undo without the user)

- **The Voice model never writes reply text. On a delivered, verified turn, the reply is rendered by code from execution and verifier data, with opener and closer lines chosen by code from a reviewed phrase catalog.**
- **Brain intents are a closed vocabulary.** Anything else becomes `unclear`.
- **Multi-step requests run as a chain,** in the order the user stated. The order is enforced from `source_phrase` positions, using an allowlist of forward connectors. When the order can't be confirmed, the reply says which order ran.
  - The chain stops on any failure, including failed verification, and then nothing is handed back.
  - Only the final file is handed back.
  - `spreadsheet.analyze` runs first, on the original, outside the chain, and is re-run on the final file.
  - **A chain that contains a step no skill can run doesn't start.** No file-changing step runs, no plan is shown, and the reply says which steps never ran. (Confirmed by the owner after the PR-3 review: `docs/reviews/backend-api-confirmation-review.md`, "For the owner".)
- **The planner drops any step whose `source_phrase` is empty,** because the user never asked for it.

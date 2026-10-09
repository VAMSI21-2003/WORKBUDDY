# The review loop

How a big or rule-touching change gets from a branch to a MERGE verdict inside one session. `CLAUDE.md` says when this applies. Decided by the owner on 2026-10-04; it replaces "reviews are done in a separate session".

## Why it works this way

- A review only counts if the reviewer has no memory of writing the code. A fresh reviewer agent has none, as long as the writer doesn't hand it one.
- The owner used to carry every prompt between sessions by hand. That was the slowest part of the work, and it added no independence.

## The loop

The working session is the **writer**. It does the following, in order.

1. **Write** the change on its branch, with tests. Run the full suite and lint. Write `docs/reviews/<branch>-impl.md`.
   - **Before every review round, bring the branch up to date:** `git fetch`, and if `origin/main` has moved, `git merge origin/main` (no rebase, no force-push), then run the suite again. A review of a stale branch doesn't cover what will be merged.
2. **Start a new reviewer agent**, on the same model as this session unless the owner's prompt names a stronger one (it does so only for security code and for a new verifier). Give it exactly this, and nothing else:
   - the text of "The reviewer's brief" below, word for word;
   - the branch name and the base it's compared with (normally `origin/main`);
   - the path of the design document, if the change has one;
   - the round number, and the path of the review file from earlier rounds, if any.

   Do **not** give it your reasoning, your summary of the change, a list of what you think is risky, or any hint about what to check or skip. It reads the code and the documents itself.
3. **Commit the reviewer's file as written.** Don't edit it, shorten it or reword its verdict. Commit its probe scripts too.
4. **If the verdict is DON'T MERGE:** fix what it lists, add tests that fail on the old code, write `docs/reviews/<branch>-fixes<N>.md`, run the suite and lint, and go back to step 2 with a **new** reviewer agent. A reviewer that specified or prototyped a fix never reviews that fix.
5. **If the verdict is MERGE:** do the live runs the "Live runs" section below says this change needs, on the final head, and write them to `docs/reviews/<branch>-live-runs.md`. If a live run shows a false claim or a changed original, that's a HIGH finding: fix it and go back to step 2.
6. **Check the branch can be merged.** `git fetch` again. If `origin/main` moved after the last reviewed commit, merge it in and run the suite. If that merge changed anything under `buddy/`, `core/`, `skills/` or `backend/`, go back to step 2 for one more round on the merged code. If it changed only documents or scripts, say so in the report.
7. **Open the pull request** if it isn't open yet, so the tests on GitHub run.
8. **Report and stop.** Say the verdict, how many rounds ran, the final commit, what's deferred, and anything that wasn't run. Don't merge. Don't wait for the tests on GitHub to finish, and don't start anything else: the owner's advisor checks them.

There's no fixed number of rounds. The loop ends when a reviewer says MERGE.

## Rules for the writer

- **You can't overrule a reviewer.** You may not downgrade a finding, decide it's out of scope, or skip it. If you think a finding is wrong, say why in the fixes file and let the next reviewer judge it.
- **Stop patching a mechanism that keeps failing.** If two rounds in a row find a HIGH in the same function, don't patch it a third time. Replace it with something simpler that can't make the false claim, even if replies get flatter, and say so in the fixes file. (`_no_skill_outcome` took five rounds of word-list patches; that's what this rule prevents.)
- **If you can't start a reviewer agent,** stop after step 1 and say so. The review is then done by a separate session using the same brief.
- **Keep the reviewer's files apart from other branches'.** Probe scripts go in `scripts/review_harness/<branch>/`, with the branch's slashes written as dashes (for `feat/backend-api-core`, `scripts/review_harness/feat-backend-api-core/`). Never add or change a file directly in `scripts/review_harness/` or in another branch's folder: two branches that both add `harness.py` there can't be merged. Import from another folder if you need its helpers.
- **If you're about to run out of room or usage,** commit and push what you have, and write the exact state (the round, the open findings, the next step) at the top of the latest fixes file, so another session can pick it up.
- **Keep the owner's decisions.** A reviewer can recommend changing something under "Decided behaviour" in `CLAUDE.md`. Neither of you changes it. List it in the report for the owner.

## Live runs: which ones, and who does them

Session usage is the project's scarcest resource, and live runs inside a session are its most expensive step. So:

- **A change that alters what the pipeline does or says** (anything under `buddy/`, `core/` or `skills/`, or reply text anywhere): the session does **at most three** live runs with the real Groq key, chosen to exercise the change, in the format `CLAUDE.md` gives. No more.
- **Any other change** (`backend/` plumbing, the page, `deploy/`, CI, documents): the session does **no** live runs. After the MERGE verdict the owner releases the branch's head on the server (`./deploy/release.sh <commit>` after `git fetch`), which runs a real turn with the real model on the real host, checks the page by hand, and merges. If the release fails, it rolls back by itself, and the owner brings the output to the advisor.
- **Never build stand-ins to get a live run through.** If the real model or another live service refuses (a `401`, a `403`, a network error), stop the live runs, report the exact error, and finish. A run against a fake service is a test, not a live run, and the tests already exist.

## The reviewer's brief

> You're an independent reviewer for WorkBuddy. You didn't write this change, and you have no memory of it. Read `CLAUDE.md` first. Treat every claim in the implementer's documents as unverified until you've checked it yourself.
>
> **Don't modify anything** under `buddy/`, `core/`, `skills/`, `backend/` or `tests/`. You write one review file and your own probe scripts, nothing else. Do mutations and prototype fixes in a throwaway copy.
>
> **Scope.** In round 1: the whole diff between the base and the branch head. In later rounds: the diff since the last reviewed commit, every finding the last round left open, and regression against the base.
>
> **Do all of this:**
> 1. Run `python -m pytest -q` (allow at least 5 minutes) and `ruff check buddy tests`. Compare the lint errors with the base's by file and rule. Report anything new.
> 2. Check the change against its design document, if there is one, and list every deviation.
> 3. Probe it through the real pipeline: the real `build_engine`, real skills and real verifiers, with only the LLM faked. Reuse the scripts under `scripts/review_harness/` by importing them; don't edit another branch's scripts. Run the same probes on a worktree of the base and compare. Cover all three speakers (template, friend, focus).
> 4. Try to break it. Write your own adversarial cases aimed at the new code: odd files, odd wording, a model that misreads the request, a stage that fails halfway, hostile input. For every case, record what happened, what was handed back, and the reply.
> 5. For every reply you produce, check rule 1: does it say or imply anything that didn't happen and wasn't independently verified? Check the numbers in it against your own read of the files.
> 6. For every run, check rule 2: the original's SHA-256 before and after.
> 7. Check that the new tests fail on the old code, and that guard tests aren't vacuous (mutate the code in a scratch copy and confirm a test fails).
> 8. In later rounds, confirm each earlier finding is fixed with your own evidence, not the implementer's.
>
> **Severity.**
> - HIGH: a reply claims something that didn't happen; a delivered file is wrong or lost data without the reply saying so; the original changed; text from a file or an exception reaches a reply; a secret is exposed; one user's data reaches another.
> - MEDIUM: misleading without being false, including a false "can't do" about work that was done.
> - LOW: wording, noise, tidiness.
>
> For every finding give the place in the code, the exact scenario, the reply or result, how reachable it is in ordinary use, and the exact fix. Mark each claim [Certain], [Likely] or [Possible].
>
> **Verdict.** MERGE if there's no HIGH, and no MEDIUM that an ordinary request can reach. Otherwise DON'T MERGE, with the exact fix needed. Don't hold a merge for LOW findings: list them under "Deferred" and add them to `docs/backlog.md`.
>
> **Write** your review to `docs/reviews/<branch>-review.md`. In round 1, create it. In later rounds, append a section headed "Review round N (commit <hash>)". Start with the verdict in one line. Include a "Not run" list of anything you couldn't do. Save your probe scripts in this branch's own folder, `scripts/review_harness/<branch with slashes as dashes>/`, with a short `README.md` inside that folder on how to run them, so the next round reuses them and doesn't rebuild them. Don't add or change files directly in `scripts/review_harness/`.
>
> **Keep the review file short.** The verdict, one table of what you ran and what happened, the findings, the deferred list and the not-run list. Put long outputs in files next to your probe scripts and point to them. Don't narrate your method beyond what someone needs to rerun it.

# Review of `feat/urgency-tone` (head `433d035`, base `origin/main` `d818409`), round 1

**Verdict: MERGE.** No HIGH and no MEDIUM found. Six LOW items are deferred (below, and in `docs/backlog.md`).

No design document; I checked it against `docs/BUDDY_PERSONALITY.md` section 9 (which the branch extends) and `CLAUDE.md`.

## What I ran

All probes: real `build_engine`, real skills and verifiers, fake LLM only. Scripts and README: `scripts/review_harness/feat-urgency-tone/` (raw output in its git-ignored `out/`).

| Run | Result |
|---|---|
| `python -m pytest -q` (head) | 3956 passed, 5 skipped, 0 failed (17 min 39 s) |
| `ruff check buddy tests`, by file and rule, head vs base | identical (14 file/rule groups on both); `ruff check backend scripts/measure_workbook_check.py` clean on head. Nothing new |
| p1: 22 scenarios x {calm, urgent, frustrated, `user_state` absent} x {template, friend, focus} = 264 turns, head and base | head with calm or absent == base, byte for byte (0 diffs). Urgent and frustrated == the calm *focus* reply (template: the calm template reply), 0 diffs. 0 errors, 0 voice-model calls, original SHA-256 unchanged in 264/264. Scenarios: dedupe, clean file, empty rows, chain, dedupe then report, partly done, chain that can't start, greeting with and without file, 3 questions, no file, unsupported, garbage and password files, hostile headers, two sheets, near-duplicates, Brain raises, Brain not JSON, Think raises |
| `check_numbers.py`: counts in delivered replies vs an independent openpyxl read | 96 (p1) + 36 (p4) delivered replies, 0 mismatches |
| p2: 24 odd `user_state` values, plus non-dict Brain replies, x 3 speakers | only `urgent`/`frustrated` in any casing or padding is brief (`"Urgent "`, `"URGENT"`, `"  Frustrated\n"`, `"uRgEnT"`); `"urgent."`, `"urgent!"`, fullwidth, zero-width, `"u r g e n t"`, `""`, null, 0, 1, a list, a dict, a 100,000-character string, an "ignore previous instructions" string and unknown words are all calm and send the same reply as before. None of the value reaches any reply. No crash. Brain replies that are a list, string, null or number fail as on base |
| p3: HTTP API, 5 plans x 4 states x 3 speakers (60 turns) | the card is the brief card exactly for urgent and frustrated, the calm card for calm and absent. Same steps, same order, same note facts, asked once; the plan then runs on approve and a reject runs nothing. Delivered file read independently (3 rows, 0 duplicates, 0 empty on the chain). Original unchanged in 60/60 |
| p4: 6 faults (dedupe verifier fails, analysis verifier fails, Hands raises, Planner raises, read raises, none) x single and chain x 3 states x 3 speakers = 108 turns | urgent and frustrated == calm focus in every case; original unchanged; no stage's exception text in any reply (the one planted verifier finding that reaches a reply does so identically on base: not this branch) |
| p5: three turns in one session (urgent, calm, frustrated, then a greeting); a question then an urgent answer | state is per turn: the calm turn gets the friend opener again, the session's mode is never changed. The urgent answer to a pending question runs the chain with the usual "I couldn't tell for sure ... what order" disclosure |
| p6: `HEAD /healthz` | 200, empty body, security headers, same bucket as `GET` (6 GET + 6 HEAD: the 11th is a 429), 429 on HEAD carries `retry-after`, other methods 405, not in `openapi.json` |
| Mutations (`mutations.py`, 29 one-place edits in a scratch copy) | 27 killed by the new tests (26 in the first run, plus the one bad edit of mine, redone and killed). 2 survived: `voice-brief-also-template` (equivalent: `PersonalityVoice` always has a mode) and `hands-first-step-only` (see R1-3). Killed include: brief never or always, brief mode "friend", urgent-only, frustrated-only, accept any word, substring match, no casefold, state read from message words, cross-check drops state, Voice uses `request.mode` on either path, Hands never or always brief, provider ignores brief, note drops "final file" or "order", step loses its number, dedupe step says "empty", brief note on one step, focus opener or closer with an apology, friend opener naming a feeling, `HEAD` unlimited or 204 |
| New tests on the old code | `test_app_basics`, `test_ratelimit` HEAD tests: 3 fail on base. `test_user_state`, `test_confirmation_provider`, `test_confirmation_api`: ImportError on base (new names), so for those the mutations above are the evidence they aren't vacuous |

Rule 1: every reply from the 264 + 108 + 60 turns above was compared with the calm focus reply, which already existed; the brief mode changes only the opener, the closer, the greeting and question wording, so every count, "Partly done", the values-only disclosure, the "I worked on ... your original upload" sentence, "no file was handed back" and "your original file is unchanged" are the calm reply's. No false or implied claim found. Rule 2: original SHA-256 unchanged in every run (264 + 264 + 108 + 60).

Deviations from `BUDDY_PERSONALITY.md` section 9 and the implementer's note: see R1-1, R1-2, R1-3. Nothing in the diff reads a user's words for the state (`grep` of the diff and mutation `parse-reads-message-words` killed).

## Findings

No HIGH, no MEDIUM. LOW findings are under Deferred.

## Deferred (LOW; also in `docs/backlog.md`)

- **R1-1 (behaviour/doc) [Certain]:** on a stop or a declined plan, the urgent reply leads with the analysis findings and says "nothing ran, no file came back" only at the end (`chain_stops`: "Before anything else, here's what I noticed: ... Want me to tackle any of these? I ran into a problem before I could change anything..."). `BUDDY_PERSONALITY.md` section 9 says "the outcome first" for every urgent reply. It is the calm focus text, so not new, and no claim is false. Fix: put the stop sentence first when nothing was delivered, or narrow the doc to delivered replies.
- **R1-2 (tests/docs) [Certain]:** the implementer's note says 14 scenarios and three questions; `test_user_state.py` has 13 and two. The brief `which_file` question is checked only by the apology lint (I printed all nine question texts: facts are the same in every style).
- **R1-3 (tests) [Certain]:** mutation "only step 1 marked brief" survives because the card reads `any(step.brief)`. The docs say every step. Assert every step or soften the claim.
- **R1-4 (logging) [Certain]:** `_parse_user_state` logs the unknown value with `%r`, unbounded. Follows `file_to_use`; the module comment says logged failures carry none of the reply's words. Log the type or cap the length.
- **R1-5 (API) [Certain]:** `HEAD /healthz` returns `content-length: 0` and no `content-type`; `GET` returns 33 and `application/json`. Status, bucket and security headers match.
- **R1-6 (docs) [Certain]:** the new `docker run` line in `scripts/measure_workbook_check.py` is unrun. I confirmed from the code that `probe_m_round3.py` (imported by the script) does `OUT.mkdir(exist_ok=True)` at import beside itself, as the docstring says, and that the image's `PYTHONDONTWRITEBYTECODE=1` means no other write there. Whether the container then runs under `--user` on the VM is for the owner's first run.

Noted, not a finding: Brain sees recent conversation, which includes earlier replies that quote column headers; a hostile header could make Brain read "urgent". The only effect is the brief wording (same facts). It is the existing channel into Brain's prompt.

## Not run

- Live runs with Groq (the writer's step 5; I used no key). So the real model's `user_state` judgment, and whether adding the field to the Brain prompt changes its other answers, is untested here.
- `docker run` of `scripts/measure_workbook_check.py` (no container runtime), and the backlog's owner measurements (P9 refused, 135 s turn): not re-measured.
- p3 (HTTP API) against the base worktree: the calm card is the unchanged constant text, shown in p3's calm records; the engine-level head-vs-base comparison covers the replies.
- Browser end-to-end tests (`tests/e2e`) beyond the 5 skipped in the suite.
- A real multi-file session for the `which_file` question through the engine (only its catalog text was checked).

Changes I left uncommitted: this file, `scripts/review_harness/feat-urgency-tone/`, and the six lines added to `docs/backlog.md`.

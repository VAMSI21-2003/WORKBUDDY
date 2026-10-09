# feat/reply-quality: implementation notes

Writer's notes for the independent review (`docs/process/review-loop.md`, step 1). Design: `docs/design/reply-quality.md`. Base: `origin/main` at `5ec6cac`. Rule-touching: yes (it changes what Buddy says on every turn that delivers nothing, and what it says about a requested report).

## What changed, by the owner's five parts

| Part | Where | Notes |
|---|---|---|
| 1. "Nothing ran" says why | `buddy/voice.py` (`TemplateSpeaker._no_change_reply`), `buddy/voice_catalog.py` (the fixed sentences), `buddy/planner.py` (`cleaning_refusal`, `BuddyState.file_refusal`), `buddy/outcome.py` (`not_done_sentences`) | `NOTHING_RAN_REPLY` is gone: every no-change turn has a reason. Closes backlog R2-1, N2, R2-L4. |
| 2. Greetings, Think's question | `buddy/actions.py` (`CONVERSATION`, `is_conversation`), `buddy/brain.py` (new intent), `buddy/think.py` (`clarification_reason`), `buddy/voice_catalog.py` (greeting, capability, question lines), `buddy/planner.py` (no analysis for these turns) | Backlog item 2. |
| 3. Report; exact duplicates | `buddy/actions.py` (`mentions_report`, `is_only_a_report_request`, `strip_report_request`), `buddy/planner.py` (analysis forced; report step → `spreadsheet.analyze`), `buddy/voice.py` (`_report_text`, `_report_unavailable_sentence`), `buddy/outcome.py` (`report_stated`), `buddy/inspector.py` + `skills/excel/analyze.py` (exact duplicates) | Backlog item 3 (L-3, L-4). |
| 4. Wording | `buddy/text.py` (`count_noun`), `Action.row_noun`, plurals in `inspector`, `outcome`, `hands`, skills; "with N duplicate rows remaining"; reply flow (see below) | Page copy snapshot (`tests/web/ui_copy_snapshot.txt`) unchanged: no string the page writes changed. |
| 5. Download names | `backend/names.py`, `backend/views.py`, `backend/routes.py` | The stored `DeliveredFile.name` is unchanged (still the name on disk), so there is **no on-disk format change and no migration**; the friendly name is computed when a response is built. No new data fixture. One existing test (`tests/deploy/test_saved_data_survives.py`) compares the name read back with the fixture's recorded one; it now compares the recorded name minus the internal ids, and the fixtures are untouched. |

## Template read-through (item 4): what changed in wording, and why

- `row(s)`, `cell(s)`, `step(s)`, `file(s)`, `value(s)`, `format(s)`, `thing(s)` → counted nouns (`count_noun`), also in the verifier findings and the skills' own summaries.
- "The copy has 4 row(s), 0 duplicate row(s) remaining." → "The copy has 4 rows, with 0 duplicate rows remaining."
- "Before anything else, here's what I noticed: …" (on a turn that changed nothing) → "Here's what I noticed when I looked it over: …": nothing is "before" anything there.
- "I ran into a problem partway through." → "I ran into a problem before I could change anything." when no file-changing step had succeeded before the chain stopped (a refused file, or a chain that never started): "partway through" was false there.
- "I can't do this part yet: “x”." → "I can't do that yet: “x”." when nothing at all was done.
- A requested report has no "Want me to tackle any of these?": the user asked for a report, and most findings aren't something Buddy can act on.
- Unchanged on purpose: the question "Want me to tackle any of these?" after "I also noticed: …" findings (deferred, see below).

## Rule 1: how each new sentence is produced

| Sentence | Comes from |
|---|---|
| greeting, capabilities, next step | catalog constants, chosen by a stable hash of the session id and turn index |
| clarification question | catalog constant for Think's closed reason code; Think's own text is never read |
| "I don't have a file…" | `BuddyState.uploaded_files` / `context.active_file` empty |
| "I can't work on this file: …" | the row-cleaning skills' own refusal text, computed by the Planner from the file on this turn |
| "I didn't read that as something I can do" + capabilities | an empty ledger (Brain listed nothing, the mention detector found nothing) |
| ledger sentences ("Your message mentions …") | the outcome renderer's existing, reviewed sentences |
| "Report on the copy: …" | the Inspector's independently re-derived findings of the analysis re-run on the final file |
| "N rows repeat an earlier row exactly" | `DataFrame.duplicated()` over the file, in the skill and again, independently, in the Inspector |

No model call is made for any reply (`voice_calls == 0` in every matrix run).

## Decisions I'd like the reviewer to check hardest

1. **A model-chosen label is the only new model influence on a reply.** Brain's new `conversation` intent and Think's `clarification_reason` are chosen by the model from a closed list; code writes every sentence. Both are the same pattern as Brain's existing intent vocabulary. Anything outside the list becomes `unclear` / `other`.
2. **The greeting path is reached only when no file-changing step ran** (and a delivered turn is rendered before it), so a greeting can never replace or hide a delivery. A misread action request whose wording gives it away gets the "your message mentions …" sentences; one that gives nothing away gets a greeting, which claims nothing.
3. **No analysis for conversation / pending-question turns** (a deviation from `proactive-analysis-design.md` 1.2, recorded there as an addendum).
4. **`mentions_report` is a deterministic regex.** It over-matches ("email me the report" → also a report block) and under-matches paraphrases ("a rundown"). It can only add a labelled, true report, or a sentence saying there isn't one.

## Tests

- `tests/buddy/test_reply_quality.py`: every new reply path through `build_engine`, in template, friend and focus, with the file read independently (counts, hashes). `tests/buddy/test_reply_quality_units.py`: detectors, Think/Brain parsing, planner choices, catalog lint, analyzer and verifier. `tests/backend/test_download_names.py`.
- Existing tests updated for the new wording (plurals, "with"), for `PersonalityVoice` now wording the greeting and the question for the mode (`_factual` in `test_voice.py`), and for the report step: the old "the report step has no skill, so the chain doesn't start" scenarios use a dashboard step instead, because a report now has a skill (the analysis). The decided behaviour (a chain with a step no skill can run doesn't start) is untouched and still tested with the dashboard/sort steps.
- Found while testing: `ClaudeThinker._parse_reply("[1]")` raised `AttributeError` (pre-existing, on `main`); it is now caught like the other malformed shapes.

## Results at the head handed to the reviewer (`0f1a5d1` and the commit that moves the probes)

- **Suite:** `python -m pytest -q`: **2985 passed, 6 skipped** (base `5ec6cac`: 2792 passed, 6 skipped). The first full run after the change had 623 failures, all from the new wording; each was read before its expectation was changed (see "Tests").
- **Lint:** `ruff check buddy tests`: no error beyond the baseline (`scripts/check_ruff_baseline.py`; the baseline is not updated).
- **Existing harnesses against `origin/main`** (worktree of `5ec6cac`; `scripts/review_harness/feat-reply-quality/writer/compare.py`, output in `writer/results/`):

| harness | turns | plural only | plural + exact-duplicate finding / flow | named new reply | other |
|---|---|---|---|---|---|
| `harness.py` | 532 | 434 | 84 | 14 (`r-unclear`: not-understood + capabilities) | 0 |
| `rereview3.py` | 1078 | 1008 | 63 | 0 | 7 (below) |
| `round5.py` | 222 | 72 (+54 identical) | 96 | 0 | 0 |
| `values_only_probe.py` (26 of 29 fixtures build here) | 650 | 175 (+50 identical) | 271 | 60 refused-file replies (`I can't work on this file`), 60 not-understood | 34 (below) |
| `round2_probe.py` (12 of 14 fixtures build here) | 320 | 76 (+20 identical) | 160 | 36 refusal + 24 not-understood | 4 (below) |

  In every run: 0 replies with `(s)`, "verifier" or a raw capability id; 0 voice-model calls; 0 changed originals; whether a file was handed back is the same as on the base for every turn. `round5.py`'s 6 errors are its intentional raising-Voice scenario, on the base too.
  - "other" in `rereview3`/`round2`/`values_only` is one pattern: `remove the empty rows` on a file that still has a repeated row. Base: "None of the issues I check for were found." (not true of the file); new: "I also noticed: N rows repeat an earlier row exactly. Want me to tackle any of these?", with the friend closer dropped because a question is asked. The count is `df.duplicated().sum()` of the delivered file.
  - The other "other" cases in `values_only` are the `text_equals` fixture, whose dedupe step fails its own check on the base too; the new reply adds the exact-duplicate finding before the same failure sentence.
- **`writer/writer_matrix.py`** (40 scenarios, 120 turns, template/friend/focus), base vs new: 0 errors, 0 voice-model calls, 0 changed originals, 0 forbidden text. The only difference in whether a file was handed back is `r2-dedupe-then-report` ("remove the duplicate rows and then give me a report" where the planner names the report step `spreadsheet.report`): the base delivered nothing (the chain doesn't start because no skill runs a report step), the new code delivers the deduplicated copy and the report (the report step is the analysis). That is intended (backlog item 3), but it moves the line the owner confirmed in `CLAUDE.md` ("a chain that contains a step no skill can run doesn't start") by giving one more step a skill; the rule itself is unchanged and still tested with dashboard and sort steps.
- **Fixtures:** none added. `tests/deploy/fixtures/data-v1` and `data-v2` are untouched; nothing under `/data` changed format.

(Live runs: see `docs/reviews/feat-reply-quality-live-runs.md`, written after a MERGE verdict.)

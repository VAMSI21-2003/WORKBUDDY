# feat/reply-quality: the report becomes Brain's action (owner's decision after round 3)

Round 3 (`docs/reviews/feat-reply-quality-review.md`, "Review round 3") was DON'T MERGE: H1 (HIGH), M1 and M2 (MEDIUM), all in the code that decides from words whether a request asks for a report, and what else a phrase asks for. My round-3 fixes (`docs/reviews/feat-reply-quality-fixes3.md`, commit `398b256`) replaced one word list with another mechanism that still read words. **Round 4 was stopped before it gave a verdict** (the owner's instruction; its first reviewer hit a rate limit, its second was stopped). Nothing from round 4 is a finding, and `398b256` is not a reviewed head.

The owner's decision: *deciding from word lists whether a request asks for a report has failed three review rounds; replace the mechanism instead of patching it.* This file records what was replaced and how. Round 5 is a new reviewer on this head.

## What was deleted

Every word list, pattern and phrase test that decided whether a message or a phrase asks for a report, a summary or an overview:

- `buddy/actions.py`: `_REPORT_NOMINAL`, `_REPORT_VERBAL`, `_REPORT_NOUN`, `_REPORT_ABOUT`, `_ABOUT_BUDDY`, `_A_PART_OF_THE_FILE`, `_REPORT_BY_NAME`, `_REPORT_BY_ACTION`, `_REPORT_REQUEST`, `_REPORT_NEGATED`, `_NOT_A_REFUSAL`, `_REPORT_BY_THE_PERSON`, `_REPORT_BY_ACTION_BY_THE_PERSON`, `_REPORT_ELSEWHERE`, `_REPORT_FILLER`, and the functions `mentions_report`, `strip_report_request`, `is_only_a_report_request`. The N2 guards ("myself", "I can", "don't ...", "no need to", "email me the report") went with them.
- `buddy/outcome.py`: `REPORT_ONLY`, `only_the_report_was_done`, the `report_stated` parameter of `build_outcome_report` and `_no_skill_outcome`, and the report stripping inside `_no_skill_outcome`. (`_OTHER_REQUEST_WORDS` was already gone in `398b256`.)
- `buddy/planner.py`: the `mentions_report` gate in `_resolve_analysis_target` and the `is_only_a_report_request` test that renamed a planned step.
- `buddy/voice.py`: `mentions_report` in the request builder, `_report_unavailable_sentence`, `_report_for_unchanged_file`.

`is_conversation` no longer reads the message for a report; a conversation is Brain's closed `conversation` intent with an empty ledger and no file-changing action mentioned (the mention detector for the two removal actions is the decided cross-check and is unchanged).

## What replaced them

1. **`report_on_file` is a row of the action table** (`buddy/actions.py`) and a member of Brain's closed vocabulary, as an intent and as a `requested_actions` action. Capability `spreadsheet.analyze` (the analysis, delivered as the labelled report); `changes_file=False` (so it is not a planner intent step: `INTENT_CAPABILITIES` holds file-changing actions only); **`mention=None`**: the mention detector skips it, so it can never add or confirm a report. Brain's prompt defines it and no longer names a report as the example of `unsupported`. Think's prompt says a report needs no clarification.
2. **Brain's reading decides.** `asks_for_report(understanding)` is true only when the ledger's Brain entries (or Brain's intent) have the action.
3. **The ledger reports it like every other action** (`buddy/outcome.py`, `_report_outcome`): `DONE` when the analysis of the right file passed (the re-run on the delivered copy when a file-changing step passed; the analysis of the original when nothing changed), `NOT_DONE` otherwise, with a reason read from the verdicts (`report_recheck_failed`, `report_analysis_failed`, `report_not_run`) and a fixed sentence for each. So "Done" now means the report was given as well, and a delivery whose report can't be given is "Partly done" with the reason. The findings are put in the reply by Voice, from the Inspector's independently derived findings, as before.
4. **When unsure, the honest side.** Brain did not read a report: nothing is labelled a report (an `unsupported` entry is stated as "I can't do that yet: “...”", as for any action Buddy lacks). Brain read one and the step ran: it is labelled. A `report_on_file` that can't be given is never "can't do": it is "I couldn't produce the report you asked for: <reason>".
5. **Planner.** A file is analysed when Brain's reading has the report even if analysed earlier; a file the cleaning skills refuse is still not analysed. A planned step under a capability no skill has, whose `source_phrase` equals (ignoring case and spacing) a phrase Brain gave to `report_on_file`, is planned as the analysis; the planner prompt names `spreadsheet.analyze` as the capability for a report. Anything else stays a step no skill can run and stops the chain (decided behaviour), with the reply saying so.
6. **A mention inside the phrase Brain gave to the report** ("a report on the duplicate rows") is not added to the ledger (`mentioned_actions(..., except_inside=...)`, text comparison). A mention elsewhere is still added.

## Tests

- `tests/buddy/test_reply_quality.py`: the wording tables of rounds 1 to 3 (about 1,000 generated and listed wordings, and the guard word lists) are gone with the code that read them. New tests go through `build_engine` in all three speakers: a report Brain read is delivered whatever the words are (nine wordings that broke earlier rounds: "analyze the email column", "give me a report but don't remove anything", "I built this file myself, can you analyze it", ...), alone and after a dedupe, with "Done" and no "can't do"; a report Brain did not read is never labelled (`unsupported` or nothing); the extra Brain separated from the report ("send it to my boss") is named exactly and the lead word is "Partly done"; a delivery whose re-check fails is "Partly done" with the reason; the mention detector never adds a report; a mention inside Brain's report phrase is not a removal request; a greeting is Brain's intent, never the words; a listed report beats a `conversation` label.
- `tests/buddy/test_actions.py`, `test_reply_quality_units.py`: the report is a table row with no mention pattern and no file change; `asks_for_report`; `is_conversation`; Brain parses and prompts the action; the planner's phrase-identity rule; `mentioned_actions(..., except_inside=...)`.
- Suite, lint and harnesses: see `docs/reviews/feat-reply-quality-impl.md`.

## What it costs (stated)

- **Recall is Brain's.** If the real Brain files a report request under `unsupported` (its old habit: the previous prompt named a report as the `unsupported` example), the reply says "I can't do that yet: “give me a report”" beside the plain findings ("Here's what I noticed ..."). True to Brain's label, flat, and a worse reply than the word lists gave. The prompt now says otherwise, and the recall of this action is a thing to measure (backlog RQ-R4-1). I did not run the real model (no live runs in this session, by the owner's instruction).
- Brain gives `report_on_file` a shorter phrase than the user's ("a report") and the message says "a report on the duplicate rows": the mention detector still adds "Your message mentions duplicate rows, but I didn't run the duplicate-row removal." beside the report (backlog RQ-R4-2). True; reads as if a removal was asked.
- A planner step that names the report under a capability no skill has and quotes different words from Brain's stops the chain (decided behaviour); the planner prompt makes `spreadsheet.analyze` the usual name.

## Other changes in this commit

- The reviewers' raw outputs are removed from the branch: all `*.json`, `*.log` and `*.done` under `scripts/review_harness/feat-reply-quality/out/` and the JSON under `writer/results/`; the unfinished round-4 outputs (`out/r4-*`) are removed with them; text summaries are cut to at most 199 lines. Raw outputs are not committed again; the scripts regenerate them (`README.md` in that folder). The scripts `r4_*.py` and `r3_*.py` still import functions that no longer exist (`mentions_report`, ...) and are historical: they will not run on this head.
- `docs/design/reply-quality.md` D1 and D4, `docs/design/proactive-analysis-design.md` (addendum), `docs/backlog.md` (RQ-R4-1, RQ-R4-2; RQ-L2, RQ-N1, RQ-N2, RQ-R3-1, RQ-R3-2, RQ-R3-5 no longer exist).
- `writer/writer_matrix.py` uses `report_on_file` where it scripts a report, and has three scenarios more (a report Brain filed under `unsupported`, a report plus an extra, a `conversation` label on a message that asks for a report).

## For the owner

`CLAUDE.md` ("Decided behaviour") does not yet say that the report is Brain's closed action and that no word list may decide it. It is your file; this decision is recorded in `docs/design/reply-quality.md` D4.

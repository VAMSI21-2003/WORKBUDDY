# feat/reply-quality: fixes for review round 3

Round 3 (`docs/reviews/feat-reply-quality-review.md`, "Review round 3 (commit 2ecaecd)", source `efaf27d`): **DON'T MERGE**: H1 (HIGH), M1 and M2 (MEDIUM). I did not edit the review or any finding, and I did not write a verdict. The round-3 reviewer prototyped a fix (`scripts/review_harness/feat-reply-quality/out/r3-prototype-fix-for-F1-F2-F3.diff`); a reviewer that prototyped a fix doesn't review it, so round 4 is a new agent.

## H1 and M1: the mechanism is replaced again, and nothing decides by word any more

Round 1, round 2 and round 3 each found a false sentence in `_no_skill_outcome` about the phrase Brain hands over as `unsupported` when it holds a report request. Each fix moved a word list, and the failure moved with it:

| Round | Mechanism | What it got wrong |
|---|---|---|
| 1 | an open list of filler words a report phrase may contain | a false "can't do" about a report just delivered |
| 2 | a closed list of verbs of other work (`_OTHER_REQUEST_WORDS`) | an extra with no listed verb was dropped, so the lead word said "Done" (H1); a listed word that only describes the report ("the email column", "what needs to be fixed", "don't remove anything") gave a false "can't do" (M1) |
| 3 (this) | **no word decides** | see below |

`docs/process/review-loop.md` says to stop patching a mechanism that keeps failing and to replace it with something simpler that cannot make the false claim, even if replies get flatter. I did that: `_OTHER_REQUEST_WORDS` and its early return are deleted.

**New rule (`buddy/outcome.py`, `_no_skill_outcome`):**

1. A phrase that is **only** a report request (`is_only_a_report_request`) says nothing: the report has its own sentence.
2. A phrase that holds a report request **and any word that is not a function word** gets the new `REPORT_ONLY` outcome: "I did nothing beyond the report for that part of your request." That is true whether the other words ask for something Buddy can't do or only describe the report, and true when the report itself could not be made. The outcome has no `source`, so the lead word is neither "Done" nor "Partly done": the reply cannot read as complete, and it never says "can't do" beside a report.
3. A phrase with no report request keeps "I can't do that yet: “<the user's words>”."

On a turn that changed nothing, the report comes first, then the one `REPORT_ONLY` sentence, and no list of what Buddy can do (the report was the answer).

**The noun list (`_REPORT_ABOUT`) is the only place a word can keep a reply silent**, and it holds nouns that cannot be a request: file, sheet, tab, rows, columns, duplicates, problems, errors, "everything", "missing/empty/blank values|cells|rows", "first/second sheet", and several of them joined by "and". A report-leading verb ("provide", "compile", "see", "have", "tell", "like") and function words ("i'd", "want", "need", "let") are added to the pattern, because "Can you provide a report?" and "I want to see a report" are two-word requests that would otherwise get the flat sentence. This also closes RQ-R3-1 for the report case ("report on the empty rows" no longer adds "Your message mentions empty rows ..."). A word missing from this list costs a flat sentence, never a false one.

**Cost, stated:**
- A descriptor not in the noun list ("report on the pasted rows", "what you cleaned") now produces "I did nothing beyond the report for that part of your request." beside a correct report. Flat, true.
- "give me a report and sort it by amount" no longer says "I can't do this part yet: “sort it by amount”." after the report: the same neutral sentence replaces it. True but less informative. The explicit "can't do" returns for a phrase with no report request ("sort by amount").
- **For the owner:** the option that needs neither a list nor a flat sentence is a closed `analyze` action in Brain's vocabulary, so the model, not a pattern, says which phrase is the report. It changes the ledger's action table and Brain's prompt, which `CLAUDE.md` makes yours to decide.

**Tests (`tests/buddy/test_reply_quality.py`):** the wordings of round 3's `r3_extras.py` (53 and the nine of round 2), `r3_listed.py` (42 that the detector reads as a report request), `r3_columns.py` (34 column names x 2 heads), `r3_nonreport.py` (13), `r3_cautious.py` (10), and rounds 1 and 2's 40 + 405:
- every report request gives no action or exactly one `REPORT_ONLY` action, with no source and no quoted phrase (never `no_skill`);
- every report request with another word, a listed verb or not, gives exactly one `REPORT_ONLY` action (never silent), including the 13 non-report requests the bare noun reads as a report ("update the summary", "finalize the report");
- 15 plain report wordings stay silent; five phrases with no report request still say "can't do" and quote the user's words;
- through `build_engine` in all three speakers: after a dedupe, seven extras produce a delivered copy, "Report on the copy: ...", the `REPORT_ONLY` sentence, and none of "Done", "Partly done", "can't do", "mentions", "didn't run"; alone, four phrases produce the report first, the sentence once after it, no capability list and no completion claim.

## M2: the guards no longer veto a genuine request

The `myself` / `I can` / `won't` / `don't ... verb` guards were applied to every wording, so a request made by name was vetoed by words about the person or about something else. Now:
- `_REPORT_BY_NAME` (a report noun or verb: "a summary", "analyze") is **not** vetoed by "myself", "I can review it" or "so I can check it". Only a first-person marker + the verb + "myself" in one clause is the person's own plan ("I'll summarize it myself").
- The weaker `_REPORT_BY_ACTION` ("check/look at/review/inspect the file", "what's wrong with ...") keeps its guards, narrowed to the verb with its object and "myself" ("check the file myself", "I'll check the file").
- "don't forget to", "don't hesitate to", "don't worry" and "never hesitate" are not refusals (`_NOT_A_REFUSAL`).

`r3_guards.py` (the reviewer's 44 messages, 30 genuine requests and 14 declines): 0 disagree (round 3 source: 14 of 30 requests vetoed). The 44 are in the tests, and four of the requests also go through `build_engine` with Brain's `unclear` and `conversation` readings: the report is delivered, no greeting.

## Also changed

- `_REPORT_BY_ACTION`'s "check ... for" clause holds empty/blank rows, blanks and missing values, and `_REPORT_ABOUT` holds several nouns joined by "and" ("the data problems and the empty rows"), so a descriptor of this kind is neither a "Your message mentions empty rows ..." sentence (RQ-R3-1) nor a flat sentence. Tests: `test_what_a_report_is_about_is_not_a_request_to_remove_it`.
- Six more declines are tested ("I won't check the file, just remove the empty rows", "never look at my data", "I'll summarize it myself", "I can analyze it myself", "let me check the data myself", "I won't look at the file again") so the `myself`, `I can`, `won't` guards are killed from both sides (RQ-R3-2).

## Known, not fixed

- Two of the reviewer's 44 "pure report" phrases are not read as a report request by the detector at all ("check how clean my data is", "what needs fixing in my file"); the base treats them the same way (a paraphrase the detector misses: the analysis runs, its findings are stated as "what I noticed", and Brain's `unsupported` phrase gets "I can't do that yet"). Chasing paraphrases with more patterns is the loop this round stops.
- "check my file for empty rows and remove them": the mention detector ignores "empty rows" inside the report request and "remove them" names nothing, so only Brain's own reading carries the removal. The same already held for duplicates after round 2.

## Mutations (writer's check that the tests test the new rule)

Nine one-place mutations of this head, each run against `tests/buddy/test_reply_quality.py`, `test_reply_quality_units.py`, `test_actions.py` and `test_outcome.py`: silence for any phrase with a report in it (round 3's H1), no `REPORT_ONLY` block, the person guard on every alternative (M2), no "don't forget/hesitate" exception, no `myself` veto, no veto for the weaker alternatives, the report not placed before the `REPORT_ONLY` sentence, no "provide/see" verbs and no multi-noun "about". **9 of 9 killed.**

## Results (head = this commit)

See `docs/reviews/feat-reply-quality-impl.md` ("Results at the head handed to the reviewer") for the suite count and the harness comparisons, re-run on this head.

## Deferred (unchanged)

L2 (a bare noun in a message about something else is read as a report request: "thanks for the summary!"), L4, L7 to L11 (L11 pre-existing, flagged for the owner), RQ-R3-2 to RQ-R3-5; RQ-R3-1 is closed for the report case only. See `docs/backlog.md`.

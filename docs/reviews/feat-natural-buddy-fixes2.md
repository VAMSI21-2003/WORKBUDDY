# feat/natural-buddy: fixes for review round 2

Review: `docs/reviews/feat-natural-buddy-review.md`, round 2, at `e645c84`. Verdict: DON'T MERGE.
- New HIGHs N1–N5, all in the claim checker.
- R2-L1 to R2-L3 deferred.
- H1–H5 and M1 from round 1 are confirmed fixed.

## The mechanism is replaced, not patched

Rounds 1 and 2 both found HIGHs in the same mechanism: a word-list checker that read the model's own wording of the facts. `docs/process/review-loop.md` says not to patch it a third time but to replace it with something simpler that can't make the false claim. The reviewer's replacement touches two decided behaviours, so I asked the owner. **Owner's decisions (2026-10-24, in this session):**

1. **File turns:** code writes the fact sentences. The model chooses which to show, in what order and phrasing, and writes only the social lines, held to a closed social vocabulary.
2. **Chat while a file is uploaded:** a new closed Brain action for questions about the file. Ordinary chat stays free.

`CLAUDE.md`'s "Decided behaviour" is updated to say both.

## What changed

| Part | Now |
|---|---|
| `buddy/facts.py` | Each `Fact` carries `variants`: the template reply's own sentence first, then a few phrasings code builds from the same data. Examples: "2 exact duplicate rows gone", "I took out 2 exact duplicate rows", "Done: removed …" only when the template's lead word is "Done", the values-only sentence in three wordings, and "No changes were made" in three. Every phrasing of a fact holds the same numbers and file names, and a test checks this for every fact on four turn kinds. The checker metadata from rounds 0–1 is gone: quantities, needs, stems, `complete`/`done`/`clear`/`all_clear`. |
| `buddy/social.py` (new) | The model's own words on a file turn must be in `SOCIAL_WORDS`, a closed list of grammar, reactions, encouragement, everyday life and some Hinglish. It holds no digit and no word for the file, rows, columns, values, the copy, a report, order, sorting, sending, done, gone, clean, yes or yep. A few social phrases that answer or close a question about the work are also refused ("all set", "on it", "here you go", "it's good now"). Words from the user's message are **not** added, because they can name the very thing that wasn't done. |
| `buddy/claims.py` | File turn: the messages are slots (`{F1b}`) plus social words. `check_reply` renders the slots and refuses an unknown slot, a fact shown twice, a required fact left out, or own words that aren't social. Chat turn: the earlier chat checks only (file name, link, count of rows, done-claims, file talk, other scripts with a file). The rest of the round 0–1 checker is deleted: claim vocabulary, numbers, firm "not" and the rest. |
| `buddy/writer.py` | The prompt lists each fact's phrasings by slot. A fact quoting the file is marked FROM THE FILE and its phrasings are JSON-quoted. The rules say: show MUST SHOW facts by slot, own words only social. Lowercase writers get each fact's first letter in lowercase, except names and "I". |
| `buddy/actions.py`, `brain.py`, `think.py`, `planner.py` | New closed intent `question_about_file`. Think never asks about it. The planner runs the analysis of the file the turn reads, as for a report. The template reply gives what that check finds ("Here's what I see in the latest copy right now: …"), plus "No changes were made". |

## Each finding, with this design

| Finding | Why it can't happen now | Test |
|---|---|---|
| N1 (statements without a listed done-word) | The model's own words can't hold a word for the file or the work at all. Every statement about the file is a code sentence. | `test_claims_round1.py::test_own_words_about_the_work_are_refused_on_a_delivered_turn` (all of N1's drafts, plus round 1's H1 and H3) |
| N2 (a not-done part said as done) | The not-done fact is required and is code's sentence. The model's own words can't name the part or a recipient. | `test_own_words_about_what_wasnt_done_are_refused` |
| N3 (chat turn with a file) | A question about the file is now `question_about_file`, a file turn, so "yep!" in the model's own words is refused there. On a true chat turn the chat checks stand. | `test_a_question_about_the_file_gets_no_answer_in_the_models_own_words`; backend `test_a_question_after_the_work_is_answered_from_the_check_of_the_latest_copy`; `test_a_done_on_a_chat_turn_is_refused` |
| N4 (file text paraphrased) | The model's own words can't hold "password", "login" or any such word. File text reaches a reply only inside code's finding sentence, quoted, as the template already showed it. | the same delivered-turn test (both phishing drafts) and `test_natural_voice.py::test_an_injection_in_a_column_header_…` |
| N5 (a number matching any fact) | The model's own words can't hold a digit. Every count is in a code sentence. | the same delivered-turn test |

## The round 2 lows

- **R2-L1 (over-refusal):** the word lists that over-refused (stems, the claim vocabulary, the phone pattern on file turns) are gone. On a file turn the model can no longer refuse a true reply through its wording of facts. It can still write too few social words, which costs a rewrite.
- **R2-L2 (mutation survivors):** most survivors were in the deleted checker.
- **R2-L3 (deadline):** unchanged and still deferred.

## What I traded, stated plainly

- On file turns, the facts read like code's sentences, though the model picks among a few phrasings and frames them in its own social lines. The owner chose this knowing it.
- On chat turns, the model writes freely. A request Brain misreads as plain chat (not as a question about the file) still has only the chat checks, which are word lists. That's the residual risk the owner accepted when choosing the Brain action over social-only chat.
- The social vocabulary is still a list. It is an allowlist, though, so a missing word costs a rewrite, never a false claim. Its one blocklist part is the phrases.

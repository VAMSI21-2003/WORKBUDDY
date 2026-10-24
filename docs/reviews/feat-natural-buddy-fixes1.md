# feat/natural-buddy: fixes for review round 1

Review: `docs/reviews/feat-natural-buddy-review.md`, round 1, at `da2c927`. Verdict: DON'T MERGE (H1–H5, M1). The round 1 reviewer stopped on a usage limit after it had written its review file; the file is committed as written.

The owner asked for every HIGH to be fixed and for three lines to be added to Buddy's character (below). M1 is fixed too. The regression tests for every case the reviewer quoted are in `tests/buddy/test_claims_round1.py`: 27 of its 51 tests fail on `da2c927`; the others are true drafts and guards that pass on both.

| Finding | Fix | Where |
|---|---|---|
| **H1.** On a complete turn, any "done" about something other than duplicates, empty rows or the report passed. Missing verbs weren't read. Other scripts weren't read. | (a) The `sheet.complete` shortcut is gone. A claimed clause may hold only the facts' own vocabulary: grammar and chat filler, the done-words, counts, the file name, the generic words for the file, and the words for an object that was done or measured at zero (`_leftover_words`, `_CLAIM_VOCABULARY`). (b) Only the deeds a skill can do count as done: remove, make or save the copy, run the check (`_SKILL_DEEDS_RE`). Any other deed, including **any "-ed" verb** on a file turn (`_PAST_TENSE_RE`, minus observation words like "noticed"), is "something no step did". (c) "clean", "spotless", "perfect" and "no problems/issues/errors" need an all-clear check of that file: `FactSheet.all_clear`, which requires no finding, the analyzer's own all-clear, and nothing left that was asked for. (d) On any file turn, a message with letters outside the Latin script is refused (`SCRIPT`). (e) On a turn that handed nothing back, nothing may say a copy is ready or in the downloads. (f) `file_work_ran` leaves out the report. | `buddy/claims.py`, `buddy/facts.py` |
| **H2.** "Not done" was met by a claim that it was done. | (a) A part no skill can do (`NO_SKILL` with the user's words) counts as said only when one of its words is within three words of a **firm** "not" ("can't", "didn't", "isn't", "never"; a bare "no" doesn't count), with no verb that reverses it ("didn't forget to email"), or when the user's own words are quoted in a sentence that says "not" (`SAID_UNABLE`, `ASKED_TOKEN`). (b) Any other clause that mentions such a part (not a question or an offer) is refused (`NOT_DONE_MENTION`). The same firm-"not" rule applies to an action or a report that wasn't done. | `_firmly_not`, `_check_not_done_mentions`, `_not_done_need` |
| **H3.** Wrong counts passed. | (a) A finding's numbers carry the noun its own words give them (`counted_quantities`): "2 rows repeat an earlier row" backs 2 rows, never 2 duplicate rows, and "1 missing value" backs only a 1 with no counted noun. (b) A count of rows with no phase is what's left in the file. It is what was removed only when its own clause says something was removed, or when its sentence does and the clause doesn't describe the file ("there are 2 duplicate rows in it" counts what's left). (c) Counts that can't be read exactly ("twenty-one", "a couple", "dozens") followed by a counted noun are refused, and the prompt asks for digits. (d) "no / zero / not any / aren't any" before a duplicate, empty or problem noun counts as 0 left, which a fact must back. | `_numbers`, `_matches`, `buddy/facts.py` |
| **H4.** On a chat turn, "done" with a pronoun, in a phrase, or in another language passed. | On a chat turn, every statement with a done-word is refused, unless "you", "he" or "she" is just before it ("you handled it well"). "they" no longer exempts ("they're gone"). Non-Latin script is refused when a file is uploaded. Duplicate words now cover "duplicados" (`dupli[ck]\w*`). | `_check_chat_claims`, `_claim_words` |
| **H5.** Text from the file steered the reply. | A link, domain, email address or phone number the user didn't write is refused. So is a run of 4 or more words from the parts of a finding that came from the file (the analyzer's quoted text), when it appears outside quotes (`UNBACKED`). | `_check_unbacked` |
| **M1.** A slow writer could fail a verified turn. | The writer's providers are separate instances with a 12 s timeout and no SDK retries (`WRITER_TIMEOUT_S`, `GroqProvider(max_retries=0)`). The rewrite budget is 15 s. Worst case, about 24 s for a draft that times out on both providers (then the template reply), or about 39 s with a rewrite. Brain and Think keep the SDK's retries. | `buddy/llm.py`, `buddy/writer.py` |

**Kept true.** The FactEchoWriter sweep (the code's own fact sentences sent back as the draft, across `test_reply_quality`, `test_user_state`, `test_follow_up_files`, `test_nothing_ran_and_from_original`, `test_residual_done_paths`, `test_multi_sheet` and `test_file_text_in_replies`) refuses nothing. To keep it that way:
- `Fact.describes` lets the refusal text and the order sentence use their own words ("password-protected", "the steps ran in this order");
- "your cleaned copy" counts as naming the copy, on a delivered turn only;
- "saved values", "merged cells", "handed back" and "what I did" are read as descriptions, not deeds.

**The reviewer's probes on this code:**
- `p2_drafts.py`: 0 of 33 false drafts shown, 0 of 4 true drafts refused, original unchanged in all.
- `p5_checker_unit.py`: all 8 one-line false drafts refused, worst check time 3.7 ms.

**Prompt.** The writer's rules now also ask for digits, a firm "can't" for what wasn't done, no "clean" unless the check found nothing, no links, file text only in quotes, and English letters.

**Character (owner's request, 2026-10-24).** Three lines added under HOW YOU TEXT in `buddy/character/buddy.md`:
- no swearing, even if the user does;
- calm and kind boundaries when the user is rude, and keep helping;
- light, well-known meme references sometimes, never offensive ones.

**Still open, as the reviewer said:**
- These are word lists. The fixes close every case found, and the allowlist parts (deeds, claim vocabulary, firm "not") close classes rather than single wordings. A model writing an untested phrasing can still find a gap, most likely in Latin-script languages other than English on a chat turn.
- The reviewer's alternative design is in its "For the owner" section: the model writes only the social lines, and code renders the facts.
- L2: the character's example "so the duplicates stay gone" is the owner's own text, so I left it.
- L3: no live runs here, by the owner's instruction for this branch.

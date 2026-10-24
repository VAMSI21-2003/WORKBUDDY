"""Round 3: one-place mutations of the replacement (``358a88a``: slots, the social vocabulary,
``question_about_file``), against the branch's new and changed tests.

    python mutations_r3.py [NAME ...]          # the four natural-buddy test files
    python mutations_r3.py --full NAME ...     # the whole suite (slow), for the survivors

Reuses ``mutations.run`` (exports HEAD to a temp dir, applies one replacement, runs pytest).
"""
import sys

import mutations

mutations.TESTS[:] = ["tests/buddy/test_natural_voice.py", "tests/buddy/test_claims_round1.py",
                      "tests/backend/test_natural_replies.py", "tests/web/test_reply_messages.py"]
C, F, S, W = "buddy/claims.py", "buddy/facts.py", "buddy/social.py", "buddy/writer.py"
V, P, B = "buddy/voice.py", "buddy/planner.py", "buddy/brain.py"

M3 = {
    "social_digit_off": (S, '    if re.search(r"\\d", text):', "    if False:"),
    "social_words_off": (S, "    if any(word not in SOCIAL_WORDS for word in words):", "    if False:"),
    "refused_phrases_off": (S, "    if _REFUSED_PHRASES.search(text.replace(\"’\", \"'\")):", "    if False:"),
    "yes_done_allowed": (S, '    hua hui ya kuch', "    yes yep done gone removed\n    hua hui ya kuch"),
    "slot_repeat_ok": (C, "    if repeated:", "    if False:"),
    "missing_ok": (C, "    if missing:", "    if False:"),
    "unknown_slot_ok": (C, '                problems.append(Problem(SLOT, f"{{{slot}}} names no fact phrasing."))', "                pass"),
    "empty_message_ok": (C, "    if any(not text for text in rendered):", "    if False:"),
    "lowercase_off": (C, "    if lowercase and text[:1].isalpha()", "    if False and text[:1].isalpha()"),
    "lowercase_I_too": (C, ' and not re.match(r"^I(?:\'\\w+)?$", first)', ""),
    "chat_checks_off": (C, "        problems = _check_chat(list(messages), sheet, user_message)", "        problems = ()"),
    "values_only_optional": (F, "            \"widths or other sheet settings.\",\n        ],\n        required=True,",
                             "            \"widths or other sheet settings.\",\n        ],\n        required=False,"),
    "not_done_optional": (F, "    builder.add(KIND_NOT_DONE, variants, required=True)", "    builder.add(KIND_NOT_DONE, variants, required=False)"),
    "file_used_optional": (F, "    builder.add(KIND_FILE, variants, required=True, names=(name,) if name else ())",
                           "    builder.add(KIND_FILE, variants, required=False, names=(name,) if name else ())"),
    "took_out_off_by_one": (F, '        variants.append(f"I took out {rows}{blank}.")',
                            '        variants.append(f"I took out {count_noun(measurement.removed + 1, wording.row_noun)}{blank}.")'),
    "gone_variant_no_blank": (F, '        variants.append(_sentence(f"{rows} gone{blank}"))', '        variants.append(_sentence(f"{rows} gone"))'),
    "facts_question_off": (F, "    if asks_about_file(understanding) and not clarifying:", "    if False:"),
    "facts_question_lead_original": (F, "            lead = FILE_STATE_LEAD_COPY if on_copy else FILE_STATE_LEAD_ORIGINAL\n            builder.add(",
                                     "            lead = FILE_STATE_LEAD_ORIGINAL\n            builder.add("),
    "template_question_off": (V, "        if asks_about_file(understanding) and not (", "        if False and not ("),
    "planner_question_no_analysis": (P, "        report_asked = asks_for_report(understanding) or asks_about_file(understanding)",
                                     "        report_asked = asks_for_report(understanding)"),
    "brain_question_not_in_vocab": (B, "        CONVERSATION,\n        QUESTION_ABOUT_FILE,\n", "        CONVERSATION,\n"),
    "from_file_not_quoted": (W, "                shown = quoted(text, len(text) + 1) if fact.from_file else text", "                shown = text"),
    "rewrite_budget_off": (W, "            if attempt == 2 or self._clock() - started > self._budget:", "            if attempt == 2:"),
}
mutations.M.update(M3)

if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["--full"]:
        mutations.TESTS[:] = ["tests", "--deselect",
                              "tests/deploy/test_release_scripts.py::test_the_logs_folder_is_git_ignored"]
        args = args[1:]
    for n in args or M3:
        print(mutations.run(n), flush=True)

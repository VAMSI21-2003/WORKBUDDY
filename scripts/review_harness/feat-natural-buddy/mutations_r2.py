"""Round 2: one-place mutations of the round-1 fixes (``e645c84``), against the branch's new tests.

    python mutations_r2.py [NAME ...]          # the four new test files (incl. test_claims_round1)
    python mutations_r2.py --full NAME ...     # the whole suite (slow), for the survivors

Reuses ``mutations.run`` (exports HEAD to a temp dir, applies one replacement, runs pytest).
"""
import sys

import mutations

mutations.TESTS[:] = mutations.TESTS + ["tests/buddy/test_claims_round1.py"]
C, F, L, W = "buddy/claims.py", "buddy/facts.py", "buddy/llm.py", "buddy/writer.py"

M2 = {
    "turned_round_off": (C, "    if segment.question or _TURNED_ROUND_RE.search(segment.text):", "    if segment.question:"),
    "firm_not_is_any_not": (C, "    nots = [i for i, token in enumerate(tokens) if _FIRM_NOT_RE.search(token)]",
                            "    nots = [i for i, token in enumerate(tokens) if _NEGATION_RE.search(token)]"),
    "firm_not_far": (C, "    return any(abs(hit - nope) <= 3 for hit in hits for nope in nots)",
                     "    return any(abs(hit - nope) <= 30 for hit in hits for nope in nots)"),
    "not_done_mentions_off": (C, "    problems += _check_not_done_mentions(segments, sheet)\n", ""),
    "unbacked_off": (C, "    problems += _check_unbacked(prepared, file_texts, user_message)\n", ""),
    "contact_off": (C, "        if found and found not in user_message.casefold():", "        if False:"),
    "file_run_off": (C, "_RUN = 4  #", "_RUN = 40  #"),
    "script_off": (C, "    if _foreign_script(text) and (", "    if False and ("),
    "script_chat_needs_no_file": (C, "(sheet.turn != TURN_CHAT or sheet.has_file)", "(sheet.turn != TURN_CHAT)"),
    "leftover_off": (C, "    if _leftover_words(segment, sheet):", "    if False:"),
    "skill_deeds_any": (C, "any(not _SKILL_DEEDS_RE.match(deed) for deed in deeds)", "False"),
    "past_tense_off": (C, "    segments = _segments(prepared, past_tense=sheet.turn != TURN_CHAT)",
                       "    segments = _segments(prepared, past_tense=False)"),
    "all_clear_ignored": (C, "for w in words) and not sheet.all_clear:", "for w in words) and False:"),
    "issues_zero_always_ok": (C, "            if number.value == 0 and sheet.all_clear:", "            if True:"),
    "nothing_handed_back_off": (C, "        problems += _check_nothing_handed_back(segments)\n", "        pass\n"),
    "chat_claims_off": (C, "        problems += _check_chat_claims(segments, sheet)\n", "        pass\n"),
    "they_exempt": (C, '    "you you\'ve youve', '    "they you you\'ve youve'),
    "vague_off": (C, "            if _VAGUE_COUNT_RE.match(token):", "            if False:"),
    "zero_word_off": (C, '    if lowered in ("no", "zero"):', "    if False:"),
    "holds_ignored": (C, "        removal = own or (removal_by_sentence[segment.sentence] and not holds)",
                      "        removal = own or removal_by_sentence[segment.sentence]"),
    "noun_any_matches_counted": (C, "    if quantity.noun != number.noun:\n        return False",
                                 "    if quantity.noun not in (number.noun, \"any\"):\n        return False"),
    "asked_any_quote": (C, "        if inner and _bare_text(inner) in wanted:", "        if inner:"),
    "cleaned_copy_any_turn": (C, "    if delivered:\n        text = re.sub(", "    if True:\n        text = re.sub("),
    "file_work_ran_counts_report": (F, "        return bool(self.done - {OBJECT_REPORT})", "        return bool(self.done)"),
    "nothing_found_always": (F, "    return not open_findings and NO_ANALYSIS_FINDINGS in notes", "    return True"),
    "zero_left_quantity_dropped": (F, "            quantities.append(Quantity(0, noun, PHASE_LEFT))\n", ""),
    "groq_writer_is_reasoning": (L, "            groq_writer = GroqProvider(timeout=WRITER_TIMEOUT_S, max_retries=0)",
                                 "            groq_writer = groq"),
    "gemini_writer_is_reasoning": (L, "            gemini_writer = GeminiProvider(timeout=WRITER_TIMEOUT_S)",
                                   "            gemini_writer = gemini"),
    "max_retries_ignored": (L, '    retries = {} if max_retries is None else {"max_retries": max_retries}', "    retries = {}"),
    "rewrite_budget_40": (W, "REWRITE_BUDGET_S = 15.0", "REWRITE_BUDGET_S = 40.0"),
}
mutations.M.update(M2)

if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["--full"]:
        # git-ignore checks fail in an exported copy (no .git): leave them out
        mutations.TESTS[:] = ["tests", "--deselect",
                              "tests/deploy/test_release_scripts.py::test_the_logs_folder_is_git_ignored"]
        args = args[1:]
    for n in args or M2:
        print(mutations.run(n), flush=True)

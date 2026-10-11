"""Round 5 mutation check of the report_on_file mechanism (buddy/actions.py, brain.py, planner.py, outcome.py, voice.py).

    python r5_mutations.py --tree DIR_OF_A_THROWAWAY_WORKTREE [--tests FILE ...] [NAME_PREFIX ...]

One place is edited in the throwaway worktree, the relevant tests run with -x, the tree is restored with `git checkout -- .`.
Prints KILLED / SURVIVED / MISSING-TARGET.  NEVER point --tree at the branch itself.
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--tree", required=True)
ap.add_argument("--tests", nargs="*", default=None)
ap.add_argument("names", nargs="*")
A = ap.parse_args()
TREE = Path(A.tree).resolve()
assert (TREE / ".git").exists() and TREE != Path(__file__).resolve().parents[3], "use a throwaway worktree"

TESTS = ["tests/buddy/test_reply_quality.py", "tests/buddy/test_reply_quality_units.py", "tests/buddy/test_actions.py",
         "tests/buddy/test_outcome.py", "tests/buddy/test_voice.py", "tests/buddy/test_planner.py",
         "tests/buddy/test_brain.py", "tests/buddy/test_engine_factory.py"]
M = []


def m(name, file, old, new):
    M.append((name, file, old, new))


# ---- buddy/actions.py
m("asks_for_report-false", "buddy/actions.py", "return any(entry.action == REPORT_ON_FILE for entry in brain_ledger(understanding))", "return False")
m("asks_for_report-true", "buddy/actions.py", "return any(entry.action == REPORT_ON_FILE for entry in brain_ledger(understanding))", "return True")
m("asks_for_report-any-entry", "buddy/actions.py", "return any(entry.action == REPORT_ON_FILE for entry in brain_ledger(understanding))", "return any(entry.action in (REPORT_ON_FILE, UNSUPPORTED) for entry in brain_ledger(understanding))")
m("report_phrases-needs-found", "buddy/actions.py", "if entry.action == REPORT_ON_FILE and entry.phrase_found and entry.source_phrase", "if entry.action == REPORT_ON_FILE and entry.source_phrase")
m("report_phrases-empty", "buddy/actions.py", "    return tuple(\n        entry.source_phrase\n        for entry in brain_ledger(understanding)", "    return () and tuple(\n        entry.source_phrase\n        for entry in brain_ledger(understanding)")
m("is_conversation-ledger-off", "buddy/actions.py", "        and not requested_ledger(understanding)\n        and not mentioned_actions(message)", "        and not mentioned_actions(message)")
m("is_conversation-mention-off", "buddy/actions.py", "        and not requested_ledger(understanding)\n        and not mentioned_actions(message)", "        and not requested_ledger(understanding)")
m("except_inside-ignored", "buddy/actions.py", "    for phrase in except_inside:", "    for phrase in ():")
m("except_inside-overlap-touching", "buddy/actions.py", "if not any(m.start() < end and start < m.end() for start, end in spans)", "if not any(m.start() <= end and start <= m.end() for start, end in spans)")
m("except_inside-case-sensitive", "buddy/actions.py", "found = re.search(re.escape(phrase), message, re.IGNORECASE) if phrase else None", "found = re.search(re.escape(phrase), message) if phrase else None")
m("intent-capabilities-all", "buddy/actions.py", "for a in ACTIONS if a.changes_file}", "for a in ACTIONS}")
m("by-target-all", "buddy/actions.py", "for a in ACTIONS if a.target}", "for a in ACTIONS}")
# ---- buddy/brain.py
m("brain-except_inside-off", "buddy/brain.py", "request.user_message, except_inside=report_phrases(understanding)", "request.user_message")
m("brain-intent-vocab-no-report", "buddy/brain.py", "        REPORT_ON_FILE,\n        \"multi_step_request\",", "        \"multi_step_request\",")
# ---- buddy/planner.py
m("planner-report_asked-false", "buddy/planner.py", "report_asked = asks_for_report(understanding)", "report_asked = False")
m("planner-skip-analysed-even-if-report", "buddy/planner.py", "if target_file in state.context.analyzed_files and not report_asked:", "if target_file in state.context.analyzed_files:")
m("planner-conversation-gate-off", "buddy/planner.py", "            is_conversation(understanding, state.user_message or \"\")\n            or (state.reasoning", "            False\n            or (state.reasoning")
m("planner-clarification-gate-off", "buddy/planner.py", "or (state.reasoning is not None and state.reasoning.clarification_required)", "or False")
m("same_words-substring", "buddy/planner.py", "return bool(key) and any(key == \" \".join(other.casefold().split()) for other in others)", "return bool(key) and any(key in \" \".join(other.casefold().split()) for other in others)")
m("same_words-case", "buddy/planner.py", "key = \" \".join(phrase.casefold().split())", "key = \" \".join(phrase.split())")
m("same_words-empty-ok", "buddy/planner.py", "return bool(key) and any(", "return any(")
m("planner-rename-any-capability", "buddy/planner.py", "                    and capability not in INTENT_CAPABILITIES.values()\n", "")
m("planner-no-rename", "buddy/planner.py", "and _same_words(str(raw_step.get(\"source_phrase\") or \"\"), report_phrases)", "and False")
# ---- buddy/outcome.py
m("report-outcome-recheck-not-required", "buddy/outcome.py", "v.capability == _ANALYSIS_CAPABILITY and v.recheck and v.passed for v in verdicts", "v.capability == _ANALYSIS_CAPABILITY and v.passed for v in verdicts")
m("report-outcome-passed-not-required-recheck", "buddy/outcome.py", "v.capability == _ANALYSIS_CAPABILITY and v.recheck and v.passed for v in verdicts", "v.capability == _ANALYSIS_CAPABILITY and v.recheck for v in verdicts")
m("report-outcome-original-passed-not-required", "buddy/outcome.py", "v.capability == _ANALYSIS_CAPABILITY and not v.recheck and v.passed for v in verdicts", "v.capability == _ANALYSIS_CAPABILITY and not v.recheck for v in verdicts")
m("report-outcome-analysis_ran-not-required", "buddy/outcome.py", "given = analysis_ran and any(", "given = any(")
m("report-outcome-changed-branch-off", "buddy/outcome.py", "    if changed_a_file:\n        given = any(", "    if False:\n        given = any(")
m("report-outcome-reason-recheck", "buddy/outcome.py", "    if any(v.recheck and not v.passed for v in verdicts):\n        reason = REPORT_RECHECK_FAILED", "    if False:\n        reason = REPORT_RECHECK_FAILED")
m("report-outcome-reason-analysis", "buddy/outcome.py", "    elif any(v.read_only and not v.recheck and not v.passed for v in verdicts):", "    elif False:")
m("report-done-placement-swapped", "buddy/outcome.py", "(report_done if report.status == DONE else not_done).append(report)", "(not_done if report.status == DONE else report_done).append(report)")
m("ledger_by_capability-all", "buddy/outcome.py", "if entry.action in ACTIONS_BY_NAME and ACTIONS_BY_NAME[entry.action].changes_file", "if entry.action in ACTIONS_BY_NAME")
m("render_outcome-includes-report", "buddy/outcome.py", "done = [a for a in report.actions if a.status == DONE and a.capability != _ANALYSIS_CAPABILITY]", "done = [a for a in report.actions if a.status == DONE]")
m("report_given-always", "buddy/outcome.py", "return any(a.capability == _ANALYSIS_CAPABILITY and a.status == DONE for a in self.actions)", "return self.report_asked")
m("report_unavailable-none", "buddy/outcome.py", "            return _REPORT_SENTENCES[action.reason or REPORT_NOT_RUN]\n    return None", "            return None\n    return None")
m("not_done_sentences-include-report-default", "buddy/outcome.py", "include_report: bool = True", "include_report: bool = False")
m("residuals-target-guard-off", "buddy/outcome.py", "if action is None or not action.target:", "if action is None:")
m("action_with_verb-mention-guard-off", "buddy/outcome.py", "    for action in ACTIONS_BY_NAME.values()\n    if action.mention is not None\n}", "    for action in ACTIONS_BY_NAME.values()\n}")
m("lead-word-ignores-report", "buddy/outcome.py", "        *report_done,\n", "")
# ---- buddy/voice.py
m("voice-report-given-gate-off", "buddy/voice.py", "        if report.report_given:\n            parts.append(_report_text(open_findings, analysis_notes, REPORT_LEAD_COPY))", "        parts.append(_report_text(open_findings, analysis_notes, REPORT_LEAD_COPY))")
m("voice-report-requested-false", "buddy/voice.py", "report_requested=asks_for_report(state.understanding or Understanding()),", "report_requested=False,")
m("voice-failed-initial-guard-off", "buddy/voice.py", "    if not report.report_asked or report.report_given:\n        if _initial_analysis_failed", "    if True:\n        if _initial_analysis_failed")
m("voice-nochange-include-report", "buddy/voice.py", "not_done_sentences(outcome, nothing_done=True, include_report=False)", "not_done_sentences(outcome, nothing_done=True)")
m("voice-nochange-unavailable-fallback", "buddy/voice.py", "parts.append(unavailable or REPORT_NOT_RUN_SENTENCE)", "parts.append(unavailable or \"\")")
m("voice-nochange-given-gate", "buddy/voice.py", "if outcome.report_given and findings is not None:", "if findings is not None:")
m("voice-nochange-initial-failed-guard", "buddy/voice.py", "if _initial_analysis_failed(request.verification_result) and not outcome.report_asked:", "if _initial_analysis_failed(request.verification_result):")
m("voice-nochange-label-original", "buddy/voice.py", "_report_text(findings[0], findings[1], REPORT_LEAD_ORIGINAL)", "_report_text(findings[0], findings[1], REPORT_LEAD_COPY)")
m("voice-delivered-label-copy", "buddy/voice.py", "_report_text(open_findings, analysis_notes, REPORT_LEAD_COPY)", "_report_text(open_findings, analysis_notes, REPORT_LEAD_ORIGINAL)")

# ---- the prompts (recall is Brain's; a prompt edit is the one change no behavioural test sees)
m("prompt-brain-no-report-intent-line", "buddy/brain.py", '- "report_on_file": the user only wants a report, summary, analysis or overview of the file:\n  what is in it or what is wrong with it. Nothing is changed.\n', "")
m("prompt-brain-report-is-unsupported-example", "buddy/brain.py", "(for example sorting, a chart or\nsending the file somewhere)", "(for example sorting, a report or a chart)")
m("prompt-brain-no-report-action-line", "buddy/brain.py", '"report_on_file" for a request for a report, summary, analysis or overview of the file,\nwhatever it is about ("a report on the duplicate rows" is a report, not a removal).\n', "")
m("prompt-planner-no-analyze-sentence", "buddy/planner.py", 'A request for a report, summary, analysis or overview\nof the file is the capability "spreadsheet.analyze". ', "")
m("prompt-think-no-report-sentence", "buddy/think.py", " A request for a report, summary\n  or overview of the file is answered with its findings; it needs no question\n  either.", "")


def sh(cmd, cwd):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)


def main():
    sel = [x for x in M if not A.names or any(x[0].startswith(n) for n in A.names)]
    for name, file, old, new in sel:
        path = TREE / file
        src = path.read_text()
        if old not in src:
            print(f"{name:50s} MISSING-TARGET ({file})", flush=True)
            continue
        path.write_text(src.replace(old, new, 1))
        try:
            r = sh([sys.executable, "-m", "pytest", "-x", "-q", "-p", "no:cacheprovider", *(A.tests or TESTS)], TREE)
            tail = r.stdout.strip().splitlines()
            summary = next((ln for ln in reversed(tail) if "passed" in ln or "failed" in ln or "error" in ln), tail[-1] if tail else "")
            first_fail = next((ln for ln in tail if ln.startswith("FAILED") or ln.startswith("ERROR")), "")
            status = "KILLED" if r.returncode != 0 else "SURVIVED"
            print(f"{name:50s} {status:9s} {summary[:60]} {first_fail[:110]}", flush=True)
        finally:
            sh(["git", "checkout", "--", "."], TREE)


main()

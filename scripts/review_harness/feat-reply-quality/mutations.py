"""Round-1 mutation check: does a test fail when a guard in the new code is removed?

    python mutations.py --tree DIR_OF_A_THROWAWAY_WORKTREE [NAME ...]

DIR must be a `git worktree` of the branch head (it is edited and restored with `git checkout -- .`).
Each mutation edits one place, runs the named tests with `-x -q`, and prints KILLED (a test failed) or
SURVIVED (all passed). A survivor is re-run on the whole `tests/buddy tests/backend tests/skills tests/web`
set with --full.
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--tree", required=True)
ap.add_argument("--full", action="store_true")
ap.add_argument("names", nargs="*")
A = ap.parse_args()
TREE = Path(A.tree).resolve()

RQ = ["tests/buddy/test_reply_quality.py", "tests/buddy/test_reply_quality_units.py"]
VO = ["tests/buddy/test_voice.py", "tests/buddy/test_voice_catalog.py", "tests/buddy/test_outcome.py"]
DN = ["tests/backend/test_download_names.py"]
ALL = RQ + VO + DN + ["tests/buddy/test_planner.py", "tests/buddy/test_think.py", "tests/buddy/test_brain.py",
                      "tests/buddy/test_actions.py", "tests/buddy/test_inspector.py", "tests/skills/excel/test_analyze.py"]

M = []  # (name, file, old, new, tests)


def m(name, file, old, new, tests=ALL):
    M.append((name, file, old, new, tests))


m("conv-ignore-mentions", "buddy/actions.py", "        and not mentioned_actions(message)\n", "")
m("conv-ignore-ledger", "buddy/actions.py", "        and not requested_ledger(understanding)\n", "")
m("conv-ignore-report", "buddy/actions.py", "        and not mentions_report(message)\n    )", "    )")
m("report-no-negation-guard", "buddy/actions.py", "        and _REPORT_NEGATED.search(message) is None\n", "")
m("report-no-elsewhere-guard", "buddy/actions.py", "        and _REPORT_ELSEWHERE.search(message) is None\n", "")
m("think-reason-passthrough", "buddy/think.py",
  "        return raw_reason.strip().casefold()\n    return CLARIFY_OTHER", "        return raw_reason.strip().casefold()\n    return str(raw_reason)")
m("think-reason-ignore-required", "buddy/think.py", "    if not clarification_required:\n        return \"\"\n", "")
m("plan-analysis-for-conversation", "buddy/planner.py",
  "            is_conversation(understanding, state.user_message or \"\")\n            or (state.reasoning is not None and state.reasoning.clarification_required)",
  "            False")
m("plan-report-not-forced", "buddy/planner.py",
  "        if target_file in state.context.analyzed_files and not report_asked:",
  "        if target_file in state.context.analyzed_files:")
m("plan-report-step-not-mapped", "buddy/planner.py",
  "                if capability != _ANALYZE_CAPABILITY and is_only_a_report_request(",
  "                if False and is_only_a_report_request(")
m("plan-refusal-dropped", "buddy/planner.py", "        state.file_refusal = refusal\n", "        state.file_refusal = None\n")
m("voice-greeting-first-off", "buddy/voice.py", "        if is_conversation(understanding, request.user_message):\n", "        if False:\n")
m("voice-nofile-off", "buddy/voice.py", "        if not request.has_file:\n", "        if False:\n")
m("voice-refusal-off", "buddy/voice.py", "        if request.file_refusal:\n", "        if False:\n")
m("voice-failed-reason-as-question", "buddy/voice.py", "            if reasoning.clarification_reason == CLARIFY_FAILED:\n", "            if False:\n")
m("voice-no-changes-sentence-dropped", "buddy/voice.py", "            parts.append(NO_CHANGES_SENTENCE)\n        return _plain_words", "            pass\n        return _plain_words")
m("voice-report-always-delivered", "buddy/voice.py",
  "    report_delivered = request.report_requested and _analysis_describes_final_file(\n        verification_result\n    )",
  "    report_delivered = request.report_requested")
m("voice-report-never-requested", "buddy/voice.py", "            report_requested=mentions_report(state.user_message or \"\"),", "            report_requested=False,")
m("voice-report-unchanged-unavailable-dropped", "buddy/voice.py",
  "        if findings is None:\n            return _report_unavailable_sentence(request)", "        if findings is None:\n            return ''")
m("voice-analysis-findings-without-pass", "buddy/voice.py",
  "    if not any(\n        v.capability == _ANALYZE_CAPABILITY and v.passed and not v.recheck\n        for v in verification_result.steps\n    ):\n        return None",
  "")
m("voice-stop-always-partway", "buddy/voice.py",
  "                STOPPED_BEFORE_ANY_CHANGE\n                if not _an_action_step_succeeded(execution_result)\n                else \"I ran into a problem partway through.\"",
  "                \"I ran into a problem partway through.\"")
m("voice-stopped-no-report-sentence", "buddy/voice.py", "        if report_requested:\n            parts.append(REPORT_NOT_PRODUCED_SENTENCE)\n", "")
m("voice-personality-plain", "buddy/voice.py", "            return TemplateSpeaker().compose(request, style=style_for(request.mode))", "            return TemplateSpeaker()(request)")
m("outcome-report-not-stripped", "buddy/outcome.py", "    if report_stated:\n", "    if False:\n")
m("outcome-removed-clause-plural", "buddy/outcome.py", "    clause = f\"removed {count_noun(measurement.removed or 0, row_noun)}\"",
  "    clause = f\"removed {measurement.removed or 0} {row_noun}s\"")
m("text-count-noun-always-plural", "buddy/text.py", "    noun = singular if count == 1 else", "    noun = singular if False else")
m("inspector-dup-finding-off", "buddy/inspector.py", "    count = _duplicate_rows(df)\n    if count == 0:\n        return ()", "    return ()\n    count = 0")
m("analyze-dup-analyzer-off", "skills/excel/analyze.py", "        count = int(df.duplicated().sum())\n        if count == 0:\n            return ()", "        return ()\n        count = 0")
m("names-no-strip", "backend/names.py", "    name = suffix.sub(\"\", stored_name)", "    name = stored_name")
m("names-no-numbering", "backend/names.py", "        if count == 1:", "        if True:")
m("names-friendly-off", "backend/names.py", "_FRIENDLY_OPERATIONS = ((\"_no_empty_rows\", \"_cleaned\"),)", "_FRIENDLY_OPERATIONS = ()")
m("routes-download-uses-stored-name", "backend/routes.py", "    name = shown or sanitize_name(delivered.name) or \"download.xlsx\"", "    name = sanitize_name(delivered.name) or \"download.xlsx\"")
m("views-file-list-stored", "backend/views.py", "            name=names[d.file_id],", "            name=d.name,")
m("views-turn-stored", "backend/views.py", "            delivered_file(session_id, d, names.get(d.file_id, d.name))", "            delivered_file(session_id, d, d.name)")

# -- round 1, second set: more guards of the new code
m("r2-unchanged-lead-says-copy", "buddy/voice.py", "    return _report_text(open_findings, notes, REPORT_LEAD_ORIGINAL)", "    return _report_text(open_findings, notes, REPORT_LEAD_COPY)")
m("r2-delivered-lead-says-original", "buddy/voice.py", "            _report_text(open_findings, analysis_notes, REPORT_LEAD_COPY)", "            _report_text(open_findings, analysis_notes, REPORT_LEAD_ORIGINAL)")
m("r2-final-needs-no-recheck", "buddy/voice.py", "        s.capability == _ANALYZE_CAPABILITY and s.recheck and s.passed\n        for s in verification_result.steps\n    )\n\n\nNO_CHANGES", "        s.capability == _ANALYZE_CAPABILITY and s.passed\n        for s in verification_result.steps\n    )\n\n\nNO_CHANGES")
m("r2-nochange-ignores-errors", "buddy/voice.py", "    if execution_result.errors or execution_result.stopped_at is not None:\n        return False\n    if execution_result.outputs:", "    if execution_result.stopped_at is not None:\n        return False\n    if execution_result.outputs:")
m("r2-nochange-ignores-outputs", "buddy/voice.py", "    if execution_result.outputs:\n        # A file came back: even with no per-step breakdown, something may have changed one.\n        return False\n", "")
m("r2-nochange-no-question-after-findings", "buddy/voice.py", "                if open_findings:\n                    parts.append(ANALYSIS_QUESTION)", "                pass")
m("r2-report-not-produced-always", "buddy/voice.py", "        if report_requested:\n            parts.append(REPORT_NOT_PRODUCED_SENTENCE)\n", "        parts.append(REPORT_NOT_PRODUCED_SENTENCE)\n")
m("r2-catalog-greeting-next-step-ignores-file", "buddy/voice_catalog.py", "(NEXT_STEP_WITH_FILE if has_file else NEXT_STEP_WITHOUT_FILE)[style]", "NEXT_STEP_WITH_FILE[style]")
m("r2-plan-refusal-ignored-for-report", "buddy/planner.py", "        refusal = cleaning_refusal(target_file)\n        if refusal is not None:", "        refusal = None\n        if refusal is not None:")
m("r2-think-reason-no-strip", "buddy/think.py", "    if isinstance(raw_reason, str) and raw_reason.strip().casefold() in CLARIFY_MODEL_REASONS:\n        return raw_reason.strip().casefold()", "    if isinstance(raw_reason, str) and raw_reason in CLARIFY_MODEL_REASONS:\n        return raw_reason")
m("r2-think-list-shape", "buddy/think.py", "        except (AttributeError, TypeError, ValueError):", "        except (TypeError, ValueError):")
m("r2-brain-conversation-removed", "buddy/brain.py", "{\"remove_duplicate_rows\", \"remove_empty_rows\", \"multi_step_request\", CONVERSATION, \"unclear\"}", "{\"remove_duplicate_rows\", \"remove_empty_rows\", \"multi_step_request\", \"unclear\"}")
m("r2-names-casefold", "backend/names.py", "taken[base.casefold()] = taken.get(base.casefold(), 0) + 1\n        count = taken[base.casefold()]", "taken[base] = taken.get(base, 0) + 1\n        count = taken[base]")
m("r2-names-sanitize-dropped", "backend/names.py", "    return sanitize_name(name) or stored_name", "    return name or stored_name")
m("r2-names-ext-handling", "backend/names.py", "names[file.file_id] = f\"{stem} ({count}).{extension}\" if dot else f\"{base} ({count})\"", "names[file.file_id] = f\"{base} ({count})\"")
m("r2-actions-report-verbless", "buddy/actions.py", "    r\"(?:\\b(?:give|send|show|make|create|write|produce|generate|prepare|get|run|do|need|want)\\s+\"", "    r\"(?:\\b(?:give)\\s+\"")
m("r2-inspector-dup-wrong-count", "buddy/inspector.py", "    count = _duplicate_rows(df)\n    if count == 0:\n        return ()\n    verb", "    count = _duplicate_rows(df) + 1\n    if count == 0:\n        return ()\n    verb")
m("r2-analyzer-singular", "skills/excel/analyze.py", "        verb = \"repeats\" if count == 1 else \"repeat\"\n        return (f\"{count} {'row' if count == 1 else 'rows'} {verb} an earlier row exactly\",)", "        return (f\"{count} rows repeat an earlier row exactly\",)")
m("r2-inspector-singular", "buddy/inspector.py", "    verb = \"repeats\" if count == 1 else \"repeat\"\n    return (f\"{count_noun(count, 'row')} {verb} an earlier row exactly\",)", "    return (f\"{count} rows repeat an earlier row exactly\",)")


def sh(cmd, cwd):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)


def main():
    sel = [x for x in M if not A.names or any(x[0].startswith(n) for n in A.names)]
    for name, file, old, new, tests in sel:
        path = TREE / file
        src = path.read_text()
        if old not in src:
            print(f"{name:45s} MISSING-TARGET ({file})")
            continue
        path.write_text(src.replace(old, new, 1))
        try:
            if A.full:
                tests = ["tests/buddy", "tests/backend", "tests/skills", "tests/web"]
            r = sh([sys.executable, "-m", "pytest", "-x", "-q", "-p", "no:cacheprovider", *tests], TREE)
            tail = r.stdout.strip().splitlines()
            summary = next((ln for ln in reversed(tail) if "passed" in ln or "failed" in ln or "error" in ln), tail[-1] if tail else "")
            first_fail = next((ln for ln in tail if ln.startswith("FAILED") or ln.startswith("ERROR")), "")
            status = "KILLED" if r.returncode != 0 else "SURVIVED"
            print(f"{name:45s} {status:9s} {summary[:70]} {first_fail[:110]}", flush=True)
        finally:
            sh(["git", "checkout", "--", "."], TREE)


main()

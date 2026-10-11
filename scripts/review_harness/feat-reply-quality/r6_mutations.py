"""Round 6: one-place mutations of the code changed since 5fc73c6. Needs a THROWAWAY worktree of the head.
   python r6_mutations.py --tree $THROWAWAY      (restores each file after each mutation)"""
import argparse, subprocess, sys
from pathlib import Path
ap = argparse.ArgumentParser(); ap.add_argument("--tree", required=True); a = ap.parse_args()
T = Path(a.tree)
TESTS = ["tests/buddy/test_reply_quality.py", "tests/buddy/test_reply_quality_units.py", "tests/buddy/test_voice.py",
         "tests/buddy/test_brain.py", "tests/buddy/test_actions.py", "tests/buddy/test_outcome.py"]
M = [
 ("brain-cross-check-blind-again", "buddy/brain.py", "for action, text in mentioned_actions(request.user_message)\n",
  "for action, text in mentioned_actions(request.user_message, except_inside=report_phrases(understanding))\n", "from buddy.actions import"),
 ("voice-only-analysis-failed-always-true", "buddy/voice.py", "v.passed or v.capability == _ANALYZE_CAPABILITY for v in verification_result.steps", "True for v in verification_result.steps", None),
 ("voice-only-analysis-failed-always-false", "buddy/voice.py", "v.passed or v.capability == _ANALYZE_CAPABILITY for v in verification_result.steps", "False for v in verification_result.steps", None),
 ("voice-failure-branch-drops-report-sentence", "buddy/voice.py", "            parts.append(TemplateSpeaker._failure_message(remaining))\n            if report_unavailable is not None:\n                parts.append(report_unavailable)\n", "            parts.append(TemplateSpeaker._failure_message(remaining))\n", None),
 ("voice-findings-branch-drops-report-sentence", "buddy/voice.py", "            parts.append(joined[0].upper() + joined[1:] + \".\")\n            if report_unavailable is not None:\n                parts.append(report_unavailable)\n", "            parts.append(joined[0].upper() + joined[1:] + \".\")\n", None),
 ("voice-report-requested-gate-removed", "buddy/voice.py", "                    if request.report_requested\n                    else None,", "                    if True\n                    else None,", None),
 ("voice-report-requested-gate-inverted", "buddy/voice.py", "                    if request.report_requested\n                    else None,", "                    if not request.report_requested\n                    else None,", None),
 ("voice-first-branch-first-condition-dropped", "buddy/voice.py", "        if report_unavailable is not None and only_analysis_failed:", "        if False:", None),
 ("brain-prompt-extra-sentence-removed", "buddy/brain.py", "Anything the user asks for around a report that Buddy cannot do (a file format such as\npdf, a language, a place to put it, someone to send it to) is its own \"unsupported\" entry;\nthe \"source_phrase\" of \"report_on_file\" is only the words that ask for the report itself.\n", "", None),
 ("brain-prompt-extra-sentence-weakened", "buddy/brain.py", "is its own \"unsupported\" entry;", "may be an \"unsupported\" entry;", None),
]
def run():
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", *TESTS], cwd=T, capture_output=True, text=True)
    return r.returncode, r.stdout.strip().splitlines()[-1]
print("baseline", run())
for name, f, old, new, extra in M:
    p = T / f; src = p.read_text()
    if old not in src: print(name, "PATTERN NOT FOUND"); continue
    s2 = src.replace(old, new, 1)
    if extra: s2 = s2.replace("    mentioned_actions,\n", "    mentioned_actions,\n    report_phrases,\n", 1) if "    report_phrases,\n" not in s2 else s2
    p.write_text(s2)
    try: rc, last = run()
    finally: p.write_text(src)
    print(name, "KILLED" if rc else "SURVIVED", "|", last)

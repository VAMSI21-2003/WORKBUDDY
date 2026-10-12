# ruff: noqa: E501
"""Round 3: one-place mutations of the round-2 FIXES (251d003) in a THROWAWAY worktree; each must make some test fail.
    git worktree add --detach $WT HEAD ; python mutations3.py $WT [name ...]
Never run against the branch itself. Output: out/mutations3.txt
"""
import subprocess, sys, time
from pathlib import Path

WT = Path(sys.argv[1]).resolve()
ONLY = set(sys.argv[2:])
CR = "tests/backend/test_conversation_repair.py"
FN = "tests/buddy/test_file_names.py"
RT = "tests/buddy/test_retry.py"
CORE = [CR, FN, RT]
REPLY = ["tests/buddy/test_outcome.py", "tests/buddy/test_voice.py", "tests/buddy/test_reply_quality.py", "tests/buddy/test_follow_up_files.py", "tests/buddy/test_brain.py", "tests/buddy/test_actions.py"]
V = "buddy/voice.py"
RS = '''                if request.resumed:
                    # The one no-change reply in which the earlier request was carried out. On
                    # every other (a question, a read that failed, a request Buddy can't do)
                    # nothing went on, so the sentence would be false (review round 2, F5).
                    parts.append(RESUMED_SENTENCE)
'''
M = [
 ("p01-report-branch-no-sentence", V, RS, "", CORE + REPLY),
 ("p02-report-sentence-even-if-not-given", V, "            else:\n                parts.append(unavailable or REPORT_NOT_RUN_SENTENCE)\n", "            else:\n                parts.append(unavailable or REPORT_NOT_RUN_SENTENCE)\n                if request.resumed:\n                    parts.append(RESUMED_SENTENCE)\n", CORE + REPLY),
 ("p03-old-condition-restored", V, "        if not asked:\n            parts.append(NO_CHANGES_SENTENCE)\n        return _plain_words(\" \".join(parts))\n\n    @staticmethod\n    def _stopped_message", "        if request.resumed and not asked:\n            parts.append(RESUMED_SENTENCE)\n        if not asked:\n            parts.append(NO_CHANGES_SENTENCE)\n        return _plain_words(\" \".join(parts))\n\n    @staticmethod\n    def _stopped_message", CORE + REPLY),
 ("p04-sentence-on-unsupported-branch", V, "            parts.extend(stated)\n", "            parts.extend(stated)\n            if request.resumed:\n                parts.append(RESUMED_SENTENCE)\n", CORE + REPLY),
 ("p05-sentence-on-failed-sentence", V, "            if reasoning.clarification_reason == CLARIFY_FAILED:\n                parts.append(UNDERSTANDING_FAILED_SENTENCE)\n", "            if reasoning.clarification_reason == CLARIFY_FAILED:\n                parts.append(UNDERSTANDING_FAILED_SENTENCE)\n                if request.resumed:\n                    parts.append(RESUMED_SENTENCE)\n", CORE + REPLY),
 ("p06-file-names-empty", "buddy/brain.py", "file_names=tuple(Path(p).name for p in state.uploaded_files),", "file_names=(),", CORE + REPLY),
 ("p07-file-names-full-path", "buddy/brain.py", "file_names=tuple(Path(p).name for p in state.uploaded_files),", "file_names=tuple(str(p) for p in state.uploaded_files),", CORE + REPLY),
 ("p08-file-names-first-only", "buddy/brain.py", "file_names=tuple(Path(p).name for p in state.uploaded_files),", "file_names=tuple(Path(p).name for p in state.uploaded_files)[:1],", CORE + REPLY),
 ("p09-crosscheck-ignores-names", "buddy/brain.py", "mentioned_actions(message, except_inside=request.file_names)", "mentioned_actions(message)", CORE + REPLY),
 ("p10-crosscheck-names-only-on-pending", "buddy/brain.py", "mentioned_actions(message, except_inside=request.file_names)", "mentioned_actions(message, except_inside=request.file_names if message is not request.user_message else ())", CORE + REPLY),
 ("p11-names-case-sensitive", "buddy/actions.py", "found = re.search(re.escape(phrase), message, re.IGNORECASE) if phrase else None", "found = re.search(re.escape(phrase), message) if phrase else None", CORE + REPLY + ["tests/buddy/test_actions.py"]),
 ("p12-exc-info-interpreter", "buddy/brain.py", 'logger.debug("ClaudeInterpreter: LLM call raised")', 'logger.debug("ClaudeInterpreter: LLM call raised", exc_info=True)', CORE),
 ("p13-exc-info-thinker", "buddy/think.py", 'logger.debug("ClaudeThinker: LLM call raised")', 'logger.debug("ClaudeThinker: LLM call raised", exc_info=True)', CORE),
 ("p14-nothing-left-no-sentence", V, "        if request.resumed:\n            parts.append(RESUMED_SENTENCE)\n        parts.append(NO_CHANGES_SENTENCE)\n        return _plain_words(\" \".join(parts))\n\n    @staticmethod\n    def _no_change_reply", "        parts.append(NO_CHANGES_SENTENCE)\n        return _plain_words(\" \".join(parts))\n\n    @staticmethod\n    def _no_change_reply", CORE + REPLY),
 ("p15-delivery-no-sentence", V, "    if request.resumed:\n        parts.append(RESUMED_SENTENCE)\n    if report.report_asked:", "    if report.report_asked:", CORE + REPLY),
 ("p16-resumed-never-reset", "buddy/state.py", "        self.pending_request = None\n        self.resumed_request = False", "        self.pending_request = None", CORE + ["tests/buddy/test_state.py"]),
 ("p17-brain-fail-twice-clears-pending", "backend/turns.py", "        or understanding is None\n        or understanding.intent is None\n", "", CORE),
]
out = []
for name, f, old, new, tests in M:
    if ONLY and name not in ONLY:
        continue
    p = WT / f
    src = p.read_text()
    if src.count(old) != 1:
        out.append(f"{name}: PATTERN COUNT {src.count(old)} (not applied)"); print(out[-1]); continue
    p.write_text(src.replace(old, new))
    t = time.time()
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", *tests], cwd=WT, capture_output=True, text=True)
    p.write_text(src)
    last = [l[:150] for l in r.stdout.splitlines() if l.startswith("FAILED")][:2]
    verdict = "KILLED" if r.returncode != 0 else "SURVIVED"
    out.append(f"{name}: {verdict} ({time.time()-t:.0f}s) {last}")
    print(out[-1], flush=True)
Path(__file__).parent.joinpath("out", "mutations3.txt").write_text("\n".join(out) + "\n")

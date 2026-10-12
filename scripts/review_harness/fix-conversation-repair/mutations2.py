# ruff: noqa: E501
"""Round 2: one-place mutations of buddy/ and backend/ in a THROWAWAY worktree; each must make some test fail.
    git worktree add --detach $WT HEAD ; python mutations.py $WT [name ...]
Never run against the branch itself. Output: out/mutations2.txt
"""
import subprocess, sys, time
from pathlib import Path

WT = Path(sys.argv[1]).resolve()
ONLY = set(sys.argv[2:])
CR = "tests/backend/test_conversation_repair.py"
FN = "tests/buddy/test_file_names.py"
RT = "tests/buddy/test_retry.py"
ST = "tests/backend/test_store.py"
CORE = [CR, FN, RT, ST]
ANALYSIS = ["tests/skills", "tests/buddy/test_inspector.py", "tests/buddy/test_engine_factory.py", "tests/buddy/test_reply_quality_units.py"]
REPLY = ["tests/buddy/test_outcome.py", "tests/buddy/test_voice.py", "tests/buddy/test_reply_quality.py", "tests/buddy/test_follow_up_files.py", "tests/backend/test_turns.py"]
M = [  # name, file, old, new, tests  (round 2: the code added in 15f5c92)
 ("n01-pending-only-when-matched", "backend/turns.py", "if names_a_file and record.pending is not None:", "if named_id is not None and record.pending is not None:", CORE),
 ("n02-readnothing-no-failure", "backend/turns.py", "        state.failure is not None\n        or (reasoning", "        False\n        or (reasoning", CORE),
 ("n03-readnothing-no-clarify-failed", "backend/turns.py", "reasoning is not None and reasoning.clarification_reason == CLARIFY_FAILED", "False", CORE),
 ("n04-readnothing-no-intent", "backend/turns.py", "        or understanding is None\n        or understanding.intent is None\n", "", CORE),
 ("n05-crosscheck-message-only", "buddy/brain.py", "texts.insert(0, request.pending_request)", "texts[:] = [request.pending_request]", CORE),
 ("n06-crosscheck-pending-dropped", "buddy/brain.py", "texts.insert(0, request.pending_request)", "pass", CORE),
 ("n07-delivery-no-sentence", "buddy/voice.py", "    if request.resumed:\n        parts.append(RESUMED_SENTENCE)\n    if report.report_asked:", "    if report.report_asked:", CORE + REPLY),
 ("n08-nothing-left-no-sentence", "buddy/voice.py", "        if request.resumed:\n            parts.append(RESUMED_SENTENCE)\n        parts.append(NO_CHANGES_SENTENCE)\n        return _plain_words(\" \".join(parts))\n\n    @staticmethod\n    def _no_change_reply", "        parts.append(NO_CHANGES_SENTENCE)\n        return _plain_words(\" \".join(parts))\n\n    @staticmethod\n    def _no_change_reply", CORE + REPLY),
 ("n09-no-change-sentence-even-when-asked", "buddy/voice.py", "if request.resumed and not asked:", "if request.resumed:", CORE + REPLY),
 ("n10-no-change-no-sentence", "buddy/voice.py", "        if request.resumed and not asked:\n            parts.append(RESUMED_SENTENCE)  # not when the reply asks again: nothing went on\n", "", CORE + REPLY),
 ("n11-resumed-flag-not-set", "buddy/brain.py", "            state.resumed_request = True\n", "", CORE),
 ("n12-unknown-always-asks", "backend/turns.py", "return None, len(record.files) > 1, True", "return None, True, True", CORE),
 ("n13-unknown-never-asks", "backend/turns.py", "return None, len(record.files) > 1, True", "return None, False, True", CORE),
 ("n14-close-not-used", "backend/turns.py", "positions = found.close if found.unclear else found.indices", "positions = found.indices", CORE),
 ("n15-copy-of-one-upload-still-asks", "backend/turns.py", "    if len(ids) > 1 and found.unclear:", "    if found.unclear:", CORE),
 ("n16-source-unknown-false", "buddy/source.py", "return NameMatch(unknown=any(_looks_like_spreadsheet(t) for t in tokens))", "return NameMatch(unknown=False)", CORE),
 ("n17-unknown-not-naming-a-file", "backend/turns.py", "return None, len(record.files) > 1, True", "return None, len(record.files) > 1, False", CORE),
 ("n18-exc-info-back-think", "buddy/think.py", 'logger.debug("ClaudeThinker: LLM call raised")', 'logger.debug("ClaudeThinker: LLM call raised", exc_info=True)', CORE),
 ("n19-resumed-not-reset", "buddy/state.py", "        self.pending_request = None\n        self.resumed_request = False", "        self.pending_request = None", CORE + ["tests/buddy/test_state.py"]),
 ("n20-unclear-close-includes-all-near", "buddy/source.py", "together = sorted(i for score, n in close if score >= floor for i in by_name[n])", "together = sorted(i for score, n in close for i in by_name[n])", CORE),
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
    last = [l for l in r.stdout.splitlines() if l.startswith(("FAILED", "ERROR"))][:2]
    verdict = "KILLED" if r.returncode != 0 else "SURVIVED"
    out.append(f"{name}: {verdict} ({time.time()-t:.0f}s) {last}")
    print(out[-1], flush=True)
Path(__file__).parent.joinpath("out", "mutations2.txt").write_text("\n".join(out) + "\n")

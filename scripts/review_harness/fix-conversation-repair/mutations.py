# ruff: noqa: E501
"""One-place mutations of buddy/ and backend/ in a THROWAWAY worktree; each must make some test fail.
    git worktree add --detach $WT HEAD ; python mutations.py $WT [name ...]
Never run against the branch itself. Output: out/mutations.txt
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
M = [  # name, file, old, new, tests
 ("m01-answers-truthy", "buddy/brain.py", 'data.get("answers_pending") is True and pending_request is not None', 'bool(data.get("answers_pending")) and pending_request is not None', CORE),
 ("m02-answers-without-pending", "buddy/brain.py", 'data.get("answers_pending") is True and pending_request is not None', 'data.get("answers_pending") is True', CORE),
 ("m03-which-file-not-dropped", "buddy/think.py", "if asks_file and state.file_used is not None:", "if False:", CORE),
 ("m04-unclear-not-forced", "buddy/think.py", "        return replace(\n            reasoning, clarification_required=True, clarification_reason=CLARIFY_WHICH_FILE\n        )", "        return reasoning", CORE),
 ("m05-name-min-low", "buddy/source.py", "NAME_MATCH_MIN = 0.85", "NAME_MATCH_MIN = 0.5", CORE),
 ("m06-margin-zero", "buddy/source.py", "NAME_MATCH_MARGIN = 0.04", "NAME_MATCH_MARGIN = 0.0", CORE),
 ("m07-looks-like-always", "buddy/source.py", "    if not dot or not stem or len(extension) < 3:\n        return False", "    return True", CORE),
 ("m08-think-one-attempt", "buddy/think.py", "ATTEMPTS = 2", "ATTEMPTS = 1", CORE),
 ("m09-brain-one-attempt", "buddy/brain.py", "ATTEMPTS = 2", "ATTEMPTS = 1", CORE),
 ("m10-keep-analyzed-on-delivery", "backend/turns.py", "    if delivered and job.offer is not None:\n        kept.discard(str(job.offer.upload.path))", "    if False:\n        pass", CORE),
 ("m11-always-drop-analyzed", "backend/turns.py", "    if delivered and job.offer is not None:", "    if job.offer is not None:", CORE),
 ("m12-pending-always-saved", "backend/turns.py", "if state.user_message and asked_which_file(state)", "if state.user_message", CORE),
 ("m13-pending-never-saved", "backend/turns.py", "if state.user_message and asked_which_file(state)", "if False", CORE),
 ("m14-store-requires-key", "backend/store.py", "_SESSION_OPTIONAL_KEYS = {\"pending_request\"}", "_SESSION_OPTIONAL_KEYS = set()", CORE + ["tests/deploy/test_saved_data_survives.py"]),
 ("m15-loose-includes-exact-analyzer", "skills/excel/analyze.py", "(loosened.duplicated() & ~df.duplicated())", "(loosened.duplicated())", CORE + ANALYSIS),
 ("m16-loose-includes-exact-verifier", "buddy/inspector.py", "folded.duplicated() & ~df.duplicated()", "folded.duplicated()", CORE + ANALYSIS),
 ("m17-loose-no-casefold-analyzer", "skills/excel/analyze.py", "value.strip().casefold()", "value.strip()", CORE + ANALYSIS),
 ("m18-loose-no-strip-verifier", "buddy/inspector.py", "cell.strip().casefold()", "cell.casefold()", CORE + ANALYSIS),
 ("m19-file-sentence-no-name", "buddy/voice.py", 'return template.format(file=_file_label(Path(used.path).name, fallback="your file"))', 'return template.format(file="your file")', CORE + ["tests/buddy/test_voice.py", "tests/buddy/test_follow_up_files.py"]),
 ("m20-brain-no-swap", "buddy/brain.py", "            state.user_message = state.pending_request\n", "            pass\n", CORE),
 ("m21-crosscheck-ignores-pending", "buddy/brain.py", "if understanding.answers_pending and request.pending_request", "if False and request.pending_request", CORE),
 ("m22-pending-offered-always", "backend/turns.py", "                    if record.pending is not None:\n                        work.pending_request = record.pending.message", "                    pass\n                if record.pending is not None:\n                    work.pending_request = record.pending.message", CORE),
 ("m23-asked-ignores-greeting", "buddy/voice.py", "        and not is_conversation(request.understanding, request.user_message)\n    )\n\n\nclass PersonalityVoice", "    )\n\n\nclass PersonalityVoice", CORE + ["tests/buddy/test_voice.py"]),
 ("m24-asked-ignores-refusal", "buddy/voice.py", "        and request.file_refusal is None\n        and not is_conversation", "        and not is_conversation", CORE + ["tests/buddy/test_voice.py"]),
 ("m25-log-raw-reply", "buddy/think.py", 'logger.debug("ClaudeThinker: LLM call raised", exc_info=True)', 'logger.warning("ClaudeThinker raised: %s", exc)', CORE),
 ("m26-failed-retry-adds-nothing-ok", "buddy/think.py", "    if reasoning.clarification_reason == CLARIFY_FAILED:\n        return reasoning\n", "", CORE),
 ("m27-exact-not-beating-near", "buddy/source.py", "floor = best if best == 1.0 else best - NAME_MATCH_MARGIN", "floor = best - NAME_MATCH_MARGIN", CORE),
 ("m28-delivered-names-not-matched", "backend/turns.py", "    for delivered in record.delivered():\n        upload_id = record.upload_of_delivered(delivered.file_id)", "    for delivered in []:\n        upload_id = record.upload_of_delivered(delivered.file_id)", CORE),
 ("m29-pending-not-cleared", "backend/turns.py", "                else None\n            )\n            state.context.record_buddy_reply", "                else record.pending\n            )\n            state.context.record_buddy_reply", CORE),
 ("m30-exact-dup-sentence-reverted", "buddy/actions.py", 'row_noun="exact duplicate row"', 'row_noun="duplicate row"', CORE + REPLY),
 ("m31-file-settled-flag-false", "buddy/think.py", "file_settled=_file_settled(state),", "file_settled=False,", CORE),
 ("m32-named-ignored", "backend/turns.py", "_named_upload(record, message) if file_id is None else (None, False)", "(None, False)", CORE),
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
Path(__file__).parent.joinpath("out", "mutations.txt").write_text("\n".join(out) + "\n")

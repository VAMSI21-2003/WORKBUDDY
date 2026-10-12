# ruff: noqa: E501
"""One-place mutations of the branch head, run in a THROWAWAY worktree (never the branch). Usage:
    git worktree add --detach $WT HEAD ; python mutations.py --tree $WT [NAME ...]
Each mutation edits one file in $WT, runs the follow-up tests, reports KILLED (some test failed) or SURVIVED, then restores the file.
"""
import subprocess, sys
from pathlib import Path

args = sys.argv[1:]
tree = Path(args[args.index("--tree") + 1]); only = [a for a in args if a != "--tree" and not a.startswith("/")]
TESTS = ["tests/buddy/test_follow_up_files.py", "tests/backend/test_follow_up_turns.py", "tests/web/test_next_file_line.py", "tests/deploy/test_saved_data_survives.py", "tests/backend/test_turns.py", "tests/buddy/test_state.py"]

M = {
 "choose-never-copy": ("buddy/source.py", "if copy_offered and not (choice.pinned or start_over):", "if False:"),
 "choose-ignores-pinned": ("buddy/source.py", "not (choice.pinned or start_over)", "not start_over"),
 "choose-ignores-start-over": ("buddy/source.py", "not (choice.pinned or start_over)", "not choice.pinned"),
 "choose-start-over-never-flag": ("buddy/source.py", "            start_over=start_over,\n        )\n    state.file_used = used", "            start_over=False,\n        )\n    state.file_used = used"),
 "choose-copy-skipped-never": ("buddy/source.py", "copy_skipped=copy_offered or choice.copy_unavailable,", "copy_skipped=False,"),
 "choose-unavailable-not-flagged": ("buddy/source.py", "copy_skipped=copy_offered or choice.copy_unavailable,", "copy_skipped=copy_offered,"),
 "parse-accepts-anything": ("buddy/brain.py", "if isinstance(raw, str) and raw.strip().casefold() in FILE_VOCABULARY:\n        return raw.strip().casefold()", "if isinstance(raw, str):\n        return raw.strip().casefold()"),
 "parse-no-casefold": ("buddy/brain.py", "raw.strip().casefold() in FILE_VOCABULARY", "raw in FILE_VOCABULARY"),
 "brain-leaks-copy-name": ("buddy/brain.py", 'lines.append("A copy of the file that Buddy handed back earlier is available.")', 'lines.append("A copy of the file that Buddy handed back earlier is available: " + str(request.active_file))'),
 "satisfied-ignores-removed": ("buddy/outcome.py", "if measurement is None or measurement.removed != 0:", "if measurement is None:"),
 "satisfied-ignores-passed": ("buddy/outcome.py", "or not action.changes_file or not _step_passed(step, verification_result):", "or not action.changes_file:"),
 "satisfied-ignores-stopped": ("buddy/outcome.py", "    if execution_result.stopped_at is not None:\n        return ()\n    steps = [s for s in execution_result.steps if _is_action_step(s)]", "    steps = [s for s in execution_result.steps if _is_action_step(s)]"),
 "satisfied-removed-lt-2": ("buddy/outcome.py", "measurement.removed != 0:", "measurement.removed > 1:"),
 "nothing-left-any-file": ("buddy/voice.py", "        and used.kind == FILE_KIND_COPY\n        and bool(already_satisfied", "        and bool(already_satisfied"),
 "nothing-left-never": ("buddy/voice.py", "        _nothing_left(request)\n        or _no_changes_made", "        _no_changes_made"),
 "sentence-copy-as-original": ("buddy/voice.py", "    if used.kind == FILE_KIND_COPY:\n        return WORKED_ON_COPY_TEMPLATE", "    if False:\n        return WORKED_ON_COPY_TEMPLATE"),
 "sentence-skipped-ignored": ("buddy/voice.py", "    if used.copy_skipped:\n        return WORKED_ON_ORIGINAL_SKIPPING_COPY_SENTENCE", "    if False:\n        return WORKED_ON_ORIGINAL_SKIPPING_COPY_SENTENCE"),
 "report-lead-original-on-copy": ("buddy/voice.py", "lead = REPORT_LEAD_USED if on_copy else REPORT_LEAD_ORIGINAL", "lead = REPORT_LEAD_ORIGINAL"),
 "latest-any-upload": ("backend/sessions.py", "if turn.finished and turn.target_file_id == upload_id:", "if turn.finished:"),
 "latest-takes-first": ("backend/sessions.py", "latest = turn.delivered_files[-1]", "latest = latest or turn.delivered_files[-1]"),
 "offer-no-sha-check": ("backend/turns.py", "if checked is None or hashlib.sha256(checked[1]).hexdigest() != latest.sha256:", "if checked is None:"),
 "offer-reads-delivered-directly": ("backend/turns.py", "copy=str(work_path), copy_name=shown, pinned=pinned", "copy=str(latest.path), copy_name=shown, pinned=pinned"),
 "offer-no-unavailable-flag": ("backend/turns.py", "FileChoice(original=str(upload.path), copy_unavailable=True, pinned=pinned), upload", "FileChoice(original=str(upload.path), pinned=pinned), upload"),
 "offer-no-integrity-expected": ("backend/turns.py", "                latest.file_id: (latest.path, latest.sha256),\n                f\"{latest.file_id}:input\": (work_path, latest.sha256),", ""),
 "offer-pinned-false": ("backend/turns.py", "self._offer(record, upload, turn_dir, pinned=file_id is not None)", "self._offer(record, upload, turn_dir, pinned=False)"),
 "restore-focus-removed": ("backend/turns.py", "    _restore_upload_focus(job, state.context, record)\n", "    pass\n"),
 "restore-analyzed-kept": ("backend/turns.py", "    context.analyzed_files = frozenset(p for p in context.analyzed_files if p in uploads)", "    pass"),
 "record-on-failed": ("backend/turns.py", "if result.outcome in (DELIVERED, NOT_DELIVERED) and state is not None:", "if state is not None:"),
 "store-no-files-used-check": ("backend/store.py", "    _check_files_used(turns, files)\n", ""),
 "view-next-ignores-latest": ("backend/views.py", "    if latest is not None:\n        return NextFile(", "    if False:\n        return NextFile("),
 "workdir-name-raw": ("backend/turns.py", "name = download_name(latest.name, record.key)", "name = latest.name"),
 # round 2 (head 5be03f1)
 "r2-latest-honours-start-over": ("backend/sessions.py", "            if turn.finished and turn.target_file_id == upload_id:\n                if turn.outcome", "            if turn.finished and turn.target_file_id == upload_id:\n                if turn.file_used is not None and turn.file_used.start_over:\n                    latest = None\n                if turn.outcome"),
 "r2-start-over-recorded-when-not-delivered-upload": ("backend/turns.py", "FileUsedRecord(USED_UPLOAD, offer.upload.file_id, used.start_over and delivered)", "FileUsedRecord(USED_UPLOAD, offer.upload.file_id, used.start_over)"),
 "r2-start-over-recorded-when-not-delivered-copy": ("backend/turns.py", "FileUsedRecord(USED_DELIVERED, offer.source.file_id, used.start_over and delivered)", "FileUsedRecord(USED_DELIVERED, offer.source.file_id, used.start_over)"),
 "r2-record-never-start-over": ("backend/turns.py", "FileUsedRecord(USED_UPLOAD, offer.upload.file_id, used.start_over and delivered)", "FileUsedRecord(USED_UPLOAD, offer.upload.file_id, False)"),
 "r2-delivered-name-from-input-ref": ("buddy/voice.py", "    if used is not None and used.kind == FILE_KIND_COPY and _name_in_reply(used.copy_name):", "    if False:"),
 "r2-delivered-name-unsanitised": ("buddy/voice.py", "        return _name_in_reply(used.copy_name)\n    for step", "        return used.copy_name\n    for step"),
 "r2-store-writes-null": ("backend/store.py", "        if used is not None:\n            # Which file", "        data[\"file_used\"] = None\n        if used is not None:\n            # Which file"),
}

def run(name):
    f, old, new = M[name]
    p = tree / f
    src = p.read_text()
    assert old in src, f"{name}: pattern not found"
    p.write_text(src.replace(old, new, 1))
    try:
        r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", *TESTS], cwd=tree, capture_output=True, text=True)
        tail = [l for l in r.stdout.splitlines() if l.startswith("FAILED") or "passed" in l or "failed" in l or "error" in l.lower()][:3]
        return ("KILLED" if r.returncode != 0 else "SURVIVED"), " | ".join(tail)[:200]
    finally:
        p.write_text(src)

for n in (only or list(M)):
    print(n, *run(n), flush=True)

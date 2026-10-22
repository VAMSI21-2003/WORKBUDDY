"""Round-1 mutations of feat/urgency-tone: one-place edits in a scratch copy; the relevant tests must fail.

    python mutations.py SRC_TREE [NAME ...]      # SRC_TREE: a worktree of the branch head (not edited)

Each mutation runs in its own copy (no .git) under a temp dir, up to 4 at once. Prints KILLED/SURVIVED.
"""
import concurrent.futures as cf, shutil, subprocess, sys, tempfile
from pathlib import Path

U = "tests/buddy/test_user_state.py"
CP = "tests/backend/test_confirmation_provider.py"
CA = "tests/backend/test_confirmation_api.py"
AB = "tests/backend/test_app_basics.py"
RL = "tests/backend/test_ratelimit.py"

M = {
 "mode-never-brief": ("buddy/voice_catalog.py", "    if mode is not None and wants_brief(user_state):\n        return BRIEF_MODE\n    return mode", "    return mode", [U]),
 "mode-always-brief": ("buddy/voice_catalog.py", "    if mode is not None and wants_brief(user_state):", "    if mode is not None:", [U]),
 "brief-mode-is-friend": ("buddy/voice_catalog.py", 'BRIEF_MODE = "focus"', 'BRIEF_MODE = "friend"', [U]),
 "wants-brief-urgent-only": ("buddy/models.py", "return user_state in (USER_STATE_URGENT, USER_STATE_FRUSTRATED)", "return user_state == USER_STATE_URGENT", [U, CP]),
 "wants-brief-frustrated-only": ("buddy/models.py", "return user_state in (USER_STATE_URGENT, USER_STATE_FRUSTRATED)", "return user_state == USER_STATE_FRUSTRATED", [U, CP]),
 "parse-accepts-anything": ("buddy/brain.py", "if isinstance(raw, str) and raw.strip().casefold() in USER_STATES:\n        return raw.strip().casefold()", "if isinstance(raw, str) and raw.strip():\n        return raw.strip().casefold()", [U]),
 "parse-no-casefold": ("buddy/brain.py", "raw.strip().casefold() in USER_STATES:\n        return raw.strip().casefold()", "raw in USER_STATES:\n        return raw", [U]),
 "parse-substring": ("buddy/brain.py", "if isinstance(raw, str) and raw.strip().casefold() in USER_STATES:\n        return raw.strip().casefold()", "if isinstance(raw, str) and 'urgent' in raw.casefold():\n        return 'urgent'\n    if isinstance(raw, str) and raw.strip().casefold() in USER_STATES:\n        return raw.strip().casefold()", [U]),
 "parse-always-calm": ("buddy/brain.py", "user_state=_parse_user_state(data.get(\"user_state\")),", "user_state=USER_STATE_CALM,", [U]),
 "parse-reads-message-words": ("buddy/brain.py", "user_state=_parse_user_state(data.get(\"user_state\")),", "user_state=('urgent' if 'asap' in user_message.lower() else _parse_user_state(data.get('user_state'))),", [U]),
 "crosscheck-drops-state": ("buddy/brain.py", "return replace(understanding, requested_actions=brain + added)", "return replace(understanding, requested_actions=brain + added, user_state='calm')", [U]),
 "voice-nochange-uses-request-mode": ("buddy/voice.py", "return TemplateSpeaker().compose(request, style=style_for(mode))", "return TemplateSpeaker().compose(request, style=style_for(request.mode))", [U]),
 "voice-delivered-uses-request-mode": ("buddy/voice.py", "return _verified_delivery_reply(request, file_style=FILE_STYLE_PLAIN, mode=mode)", "return _verified_delivery_reply(request, file_style=FILE_STYLE_PLAIN, mode=request.mode)", [U]),
 "voice-brief-also-template": ("buddy/voice.py", "return TemplateSpeaker().compose(request, style=style_for(mode))", "return TemplateSpeaker().compose(request, style=style_for(mode if mode else 'focus'))", [U]),
 "hands-never-brief": ("buddy/hands.py", "brief = understanding is not None and wants_brief(understanding.user_state)", "brief = False", [U, CP, CA]),
 "hands-always-brief": ("buddy/hands.py", "brief = understanding is not None and wants_brief(understanding.user_state)", "brief = True", [U, CP, CA]),
 "hands-first-step-only": ("buddy/hands.py", "                        brief=brief,", "                        brief=brief and not steps,", [U, CP, CA]),
 "provider-ignores-brief": ("backend/confirmation.py", "                brief=self._brief,", "                brief=False,", [CP, CA]),
 "provider-brief-all": ("backend/confirmation.py", "self._brief = any(step.brief for step in steps)", "self._brief = all(step.brief for step in steps) and bool(steps) and False", [CP, CA]),
 "note-brief-drops-final-file": ("backend/confirmation.py", 'PLAN_NOTE_CHAIN_BRIEF = "In order, each on the previous result. Only the final file is handed back."', 'PLAN_NOTE_CHAIN_BRIEF = "In order, each on the previous result."', [CP, CA, U]),
 "note-brief-drops-order": ("backend/confirmation.py", 'PLAN_NOTE_CHAIN_BRIEF = "In order, each on the previous result. Only the final file is handed back."', 'PLAN_NOTE_CHAIN_BRIEF = "Each on the previous result. Only the final file is handed back."', [CP, CA, U]),
 "brief-step-loses-number": ("backend/confirmation.py", '"spreadsheet.remove_empty_rows": "Then remove empty rows from step {previous}\'s result."', '"spreadsheet.remove_empty_rows": "Then remove empty rows."', [CP, CA]),
 "brief-dedupe-step-says-empty": ("backend/confirmation.py", '"spreadsheet.deduplicate": "Then remove duplicate rows from step {previous}\'s result."', '"spreadsheet.deduplicate": "Then remove empty rows from step {previous}\'s result."', [CP, CA]),
 "brief-note-on-single": ("backend/confirmation.py", "    if step_count < 2:\n        return None", "    if step_count < 1:\n        return None", [CP, CA]),
 "focus-opener-added": ("buddy/voice_catalog.py", 'Phrase("focus.opener.none", "focus", OPENER, "", _always),', 'Phrase("focus.opener.none", "focus", OPENER, "Sorry about that.", _always),', [U]),
 "focus-closer-apology": ("buddy/voice_catalog.py", 'CLOSER,\n        "Anything else?",', 'CLOSER,\n        "Sorry for the trouble. Anything else?",', [U]),
 "friend-opener-feeling": ("buddy/voice_catalog.py", '"Here\'s where things landed."', '"I\'m sorry you are frustrated."', [U]),
 "healthz-head-unlimited": ("backend/routes.py", '    include_in_schema=False,\n    dependencies=[Depends(limits(HEALTHZ, credential=False))],\n)\ndef healthz_head', '    include_in_schema=False,\n)\ndef healthz_head', [AB, RL]),
 "healthz-head-500": ("backend/routes.py", "return Response(status_code=200)", "return Response(status_code=204)", [AB]),
}

SRC = Path(sys.argv[1]).resolve()
only = sys.argv[2:]


def one(name):
    f, old, new, tests = M[name]
    d = Path(tempfile.mkdtemp(prefix="mut-"))
    shutil.copytree(SRC, d / "t", ignore=shutil.ignore_patterns(".git", "__pycache__", "out", "workspace", "scratch"))
    p = d / "t" / f
    s = p.read_text()
    if s.count(old) != 1:
        return name, f"BAD-MUTATION (matches {s.count(old)})", ""
    p.write_text(s.replace(old, new))
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", *tests],
                       cwd=d / "t", capture_output=True, text=True)
    tail = [l for l in r.stdout.splitlines() if l.startswith(("FAILED", "ERROR"))][:2]
    shutil.rmtree(d, ignore_errors=True)
    return name, "SURVIVED" if r.returncode == 0 else "KILLED", " | ".join(tail)[:230]


names = [n for n in M if not only or n in only]
with cf.ThreadPoolExecutor(4) as ex:
    for name, verdict, why in ex.map(one, names):
        print(f"{verdict:9} {name:34} {why}", flush=True)

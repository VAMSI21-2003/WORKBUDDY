"""Round-2 mutation check of what the fix commit (e51b2fc) changed, plus the round-1 survivors it claims to have closed.

    python r2_mutations.py --tree DIR_OF_A_THROWAWAY_WORKTREE [NAME ...]

Same method as mutations.py (edit one place in the throwaway worktree, run the relevant tests with -x, restore with
`git checkout -- .`). Prints KILLED / SURVIVED / MISSING-TARGET.
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--tree", required=True)
ap.add_argument("names", nargs="*")
A = ap.parse_args()
TREE = Path(A.tree).resolve()

RQ = ["tests/buddy/test_reply_quality.py", "tests/buddy/test_reply_quality_units.py"]
VO = ["tests/buddy/test_voice.py", "tests/buddy/test_voice_catalog.py", "tests/buddy/test_outcome.py"]
DN = ["tests/backend/test_download_names.py"]
ALL = RQ + VO + DN + ["tests/buddy/test_planner.py", "tests/buddy/test_think.py", "tests/buddy/test_brain.py",
                      "tests/buddy/test_actions.py", "tests/buddy/test_inspector.py", "tests/skills/excel/test_analyze.py"]
M = []


def m(name, file, old, new, tests=ALL):
    M.append((name, file, old, new, tests))


# -- the M1 fix
m("m1-early-return-removed", "buddy/outcome.py",
  "    if report_stated and is_only_a_report_request(phrase):\n", "    if False:\n")
m("m1-about-removed", "buddy/actions.py", "rf\"\\b{_REPORT_NOUN}\\b{_REPORT_ABOUT}\"", "rf\"\\b{_REPORT_NOUN}\\b\"")
m("m1-filler-additions-removed", "buddy/actions.py", "    \"be great thanks thank short brief quick full detailed written tell\".split()", "    \"\".split()")
m("m1-check-look-alternative-removed", "buddy/actions.py", "    r\"|\\b(?:check|look\\s+(?:at|over)|take\\s+a\\s+look\\s+at|review|inspect)\\s+\"", "    r\"|\\b(?:xxxxcheck)\\s+\"")
m("m1-whats-wrong-alternative-removed", "buddy/actions.py", "    r\"|\\bwhat'?s\\s+wrong\\s+with\\s+", "    r\"|\\bxxwhat'?s\\s+wrong\\s+with\\s+")
m("m1-rundown-overview-removed", "buddy/actions.py", "    r\"rundown|overview)\"", "    r\"xxrundown)\"")
m("m1-about-determiners-removed", "buddy/actions.py", "(?:(?:the|this|that|my|our|your|all|any|every|these|those)\\s+)*\"\n    r\"(?:file|sheet", "(?:(?:xxthe)\\s+)*\"\n    r\"(?:file|sheet")
m("m1-about-no-of-on", "buddy/actions.py", "(?:(?:of|on|about|for)\\s+)?\"", "\"")
# -- L3
m("l3-failed-think-no-nochange-sentence", "buddy/voice.py", "        if not asked:\n            parts.append(NO_CHANGES_SENTENCE)", "        if True:\n            pass")
m("l3-asked-for-failed", "buddy/voice.py", "                parts.append(UNDERSTANDING_FAILED_SENTENCE)\n            else:", "                parts.append(UNDERSTANDING_FAILED_SENTENCE)\n                asked = True\n            else:")
# -- L5 names
m("l5-keep-stem-off", "backend/names.py", "        return found[\"stem\"][: max(1, MAX_NAME_CHARS - len(tail))] + tail", "        return found[\"stem\"][:0] + \"x\" + tail")
m("l5-no-cut", "backend/names.py", "found[\"stem\"][: max(1, MAX_NAME_CHARS - len(tail))]", "found[\"stem\"]")
m("l5-op-words-not-friendly", "backend/names.py", "{\"deduplicated\": \"deduplicated\", \"no_empty_rows\": \"cleaned\"}", "{\"deduplicated\": \"deduplicated\", \"no_empty_rows\": \"no_empty_rows\"}")
m("l5-fallthrough-keeps-ids", "backend/names.py", "    return re.sub(rf\"_{key}-task-\\d+\", \"\", stored_name)", "    return stored_name")
m("l5-all-steps-not-translated", "backend/names.py", "        words = re.findall(rf\"_({operation})_{key}-task-\\d+\", found[\"steps\"])", "        words = re.findall(rf\"_({operation})_{key}-task-\\d+\", found[\"steps\"])[:1]")
m("l5-extension-lost", "backend/names.py", "[\"extension\"]\n        return", "[\"extension\"][:0]\n        return")
# -- L1: the round-1 survivors, which the fix claims to have closed
m("l1-unchanged-unavailable-dropped", "buddy/voice.py",
  "        if findings is None:\n            return _report_unavailable_sentence(request)", "        if findings is None:\n            return ''")
m("l1-analysis-findings-without-pass", "buddy/voice.py",
  "    if not any(\n        v.capability == _ANALYZE_CAPABILITY and v.passed and not v.recheck\n        for v in verification_result.steps\n    ):\n        return None",
  "")
m("l1-names-casefold", "backend/names.py", "taken[base.casefold()] = taken.get(base.casefold(), 0) + 1\n        count = taken[base.casefold()]", "taken[base] = taken.get(base, 0) + 1\n        count = taken[base]")
m("l1-inspector-singular", "buddy/inspector.py", "    verb = \"repeats\" if count == 1 else \"repeat\"\n    return (f\"{count_noun(count, 'row')} {verb} an earlier row exactly\",)", "    return (f\"{count} rows repeat an earlier row exactly\",)")


def sh(cmd, cwd):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)


def main():
    sel = [x for x in M if not A.names or any(x[0].startswith(n) for n in A.names)]
    for name, file, old, new, tests in sel:
        path = TREE / file
        src = path.read_text()
        if old not in src:
            print(f"{name:45s} MISSING-TARGET ({file})", flush=True)
            continue
        path.write_text(src.replace(old, new, 1))
        try:
            r = sh([sys.executable, "-m", "pytest", "-x", "-q", "-p", "no:cacheprovider", *tests], TREE)
            tail = r.stdout.strip().splitlines()
            summary = next((ln for ln in reversed(tail) if "passed" in ln or "failed" in ln or "error" in ln), tail[-1] if tail else "")
            first_fail = next((ln for ln in tail if ln.startswith("FAILED") or ln.startswith("ERROR")), "")
            status = "KILLED" if r.returncode != 0 else "SURVIVED"
            print(f"{name:45s} {status:9s} {summary[:70]} {first_fail[:130]}", flush=True)
        finally:
            sh(["git", "checkout", "--", "."], TREE)


main()

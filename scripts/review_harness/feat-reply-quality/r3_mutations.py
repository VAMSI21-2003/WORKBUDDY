"""Round-3 mutation check of what the fix commit (efaf27d) changed: the closed list `_OTHER_REQUEST_WORDS` word by word and its
early return, the mention detector's report spans, the new N2/N1 guards, and the names safe-character change.

    python r3_mutations.py --tree DIR_OF_A_THROWAWAY_WORKTREE [--words | --others] [NAME_PREFIX ...]

Same method as r2_mutations.py: edit one place in the throwaway worktree, run the relevant tests with -x, restore with `git checkout -- .`.
Prints KILLED / SURVIVED / MISSING-TARGET.
"""
import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--tree", required=True)
ap.add_argument("--words", action="store_true")
ap.add_argument("--others", action="store_true")
ap.add_argument("names", nargs="*")
A = ap.parse_args()
TREE = Path(A.tree).resolve()

TESTS = ["tests/buddy/test_reply_quality.py", "tests/buddy/test_reply_quality_units.py", "tests/buddy/test_outcome.py",
         "tests/buddy/test_actions.py", "tests/backend/test_download_names.py", "tests/buddy/test_voice.py"]
M = []


def m(name, file, old, new):
    M.append((name, file, old, new))


# -- the closed list, word by word
LIST_LINES = """sort|filter|chart|graph|plot|highlight|colou?r|merge|split|rename|translate|convert|e-?mail|mail|print|upload|share|export|pivot|delete|drop|insert|replace|capitali[sz]e|trim|rotate|freeze|hide|unhide|dashboard|forecast|predict|schedule|attach|forward|save|download|paint|bold|italic|underline|resize|reorder|rearrange|transpose|reformat|fix|correct|remove|add|clean|dedupe|deduplicate|post|publish""".split("|")
for w in LIST_LINES:
    m("word-" + w, "buddy/outcome.py", "|" + w + "|" if ("|" + w + "|") in open(TREE / "buddy/outcome.py").read().replace("\"\n    r\"", "|").replace("\\b(?:", "|") else w, "zzqx" + w)
# -- the early return and the send alternative
m("early-return-off", "buddy/outcome.py", "            if not _OTHER_REQUEST_WORDS.search(lowered):", "            if False:")
m("early-return-always", "buddy/outcome.py", "            if not _OTHER_REQUEST_WORDS.search(lowered):", "            if True:")
m("send-alternative-off", "buddy/outcome.py", "    r\"|\\bsend(?:s|ing)?\\s+(?:it|them|this|that|to)\\b\",", "    r\"|\\bzzsend\\b\",")
m("suffix-off", "buddy/outcome.py", ")(?:s|d|ed|ing)?\\b\"", ")\\b\"")
# -- the mention detector
m("spans-off", "buddy/actions.py", "[m.span() for m in _REPORT_REQUEST.finditer(message)] if mentions_report(message) else []", "[]")
m("spans-only-if-report-off", "buddy/actions.py", "if mentions_report(message) else []", "if True else []")
m("overlap-never", "buddy/actions.py", "if not any(m.start() < end and start < m.end() for start, end in report_spans)", "if True")
m("overlap-touching", "buddy/actions.py", "m.start() < end and start < m.end()", "m.start() <= end and start <= m.end()")
# -- N2 guards
m("by-person-off", "buddy/actions.py", "        and _REPORT_BY_THE_PERSON.search(message) is None\n", "")
m("by-person-myself-off", "buddy/actions.py", "r\"\\bmyself\\b|\\b(?:i'll", "r\"\\bzzmyself\\b|\\b(?:i'll")
m("by-person-ican-off", "buddy/actions.py", "(?:i'll|i\\s+will|i'm\\s+going\\s+to|i\\s+can)", "(?:i'll|i\\s+will|i'm\\s+going\\s+to)")
m("by-person-ill-off", "buddy/actions.py", "(?:i'll|i\\s+will|i'm\\s+going\\s+to|i\\s+can)", "(?:i\\s+can)")
m("negated-verbs-off", "buddy/actions.py", "    r\"|\\b(?:don't|dont|do\\s+not|won't|never|no\\s+need\\s+to)\\s+(?:\\w+\\s+){0,2}\"\n    r\"(?:check|look|review|inspect|analy[sz]e|summari[sz]e|give|send|show)\\b\",", "    r\"|\\bzzzz\\b\",")
m("negated-noneed-off", "buddy/actions.py", "won't|never|no\\s+need\\s+to)", "won't|never)")
m("negated-wont-off", "buddy/actions.py", "do\\s+not|won't|never|no\\s+need\\s+to)", "do\\s+not|never|no\\s+need\\s+to)")
m("negated-nominal-only", "buddy/actions.py", "{{0,24}}\\b{_REPORT_NOMINAL}\\b\"", "{{0,24}}\\b{_REPORT_NOUN}\\b\"")
m("about-buddy-off", "buddy/actions.py", "{_ABOUT_BUDDY}{_REPORT_ABOUT}", "{_REPORT_ABOUT}")
m("about-buddy-offer-off", "buddy/actions.py", "are\\s+able\\s+to\\s+do|offer)", "are\\s+able\\s+to\\s+do)")
m("about-buddy-your-off", "buddy/actions.py", "|your\\b|yourself\\b))\"", "|zzyour\\b))\"")
m("part-of-file-off", "buddy/actions.py", "{_REPORT_NOMINAL}{_A_PART_OF_THE_FILE}|", "{_REPORT_NOMINAL}|")
m("part-of-file-tab-off", "buddy/actions.py", "(?!\\s+(?:sheet|tab|worksheet|columns?|page)\\b)", "(?!\\s+(?:sheet|worksheet|columns?|page)\\b)")
m("quality-check-off", "buddy/actions.py", "rundown|overview|\"\n    r\"(?:quality|health|sanity)\\s+check)\"", "rundown|overview)\"")
m("whats-wrong-forms-off", "buddy/actions.py", "what(?:'?s|\\s+is|\\s+are)\\s+wrong", "what'?s\\s+wrong")
m("for-duplicates-off", "buddy/actions.py", "mistakes?|duplicates?|duplicate\\s+rows?))?\"", "mistakes?))?\"")
# -- names
m("names-safe-main-off", "backend/names.py", "        stem = _SAFE_CHARS.sub(\"_\", found[\"stem\"])", "        stem = found[\"stem\"]")
m("names-safe-fallback-off", "backend/names.py", "    return _SAFE_CHARS.sub(\"_\", re.sub(rf\"_{key}-task-\\d+\", \"\", stored_name))", "    return re.sub(rf\"_{key}-task-\\d+\", \"\", stored_name)")
m("names-dotall-off", "backend/names.py", "re.fullmatch(shape, stored_name, re.DOTALL)", "re.fullmatch(shape, stored_name)")
m("names-safe-chars-wider", "backend/names.py", "re.compile(r\"[^A-Za-z0-9._ -]\")", "re.compile(r\"[^A-Za-z0-9._ -é]\")")


def sh(cmd, cwd):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)


def main():
    sel = M
    if A.words:
        sel = [x for x in M if x[0].startswith("word-")]
    elif A.others:
        sel = [x for x in M if not x[0].startswith("word-")]
    sel = [x for x in sel if not A.names or any(x[0].startswith(n) for n in A.names)]
    for name, file, old, new in sel:
        path = TREE / file
        src = path.read_text()
        if name.startswith("word-"):
            w = name[5:]
            # replace the word as one alternative of the closed list only (inside _OTHER_REQUEST_WORDS)
            start = src.index("_OTHER_REQUEST_WORDS = re.compile(")
            end = src.index("\n)\n", start)
            block = src[start:end]
            pat = re.compile(rf"(?<![A-Za-z\]?\-])({re.escape(w)})(?=[|)\"])")
            if not pat.search(block):
                print(f"{name:45s} MISSING-TARGET ({file})", flush=True)
                continue
            new_block = pat.sub("zzqx" + w, block, count=1)
            path.write_text(src[:start] + new_block + src[end:])
        else:
            if old not in src:
                print(f"{name:45s} MISSING-TARGET ({file})", flush=True)
                continue
            path.write_text(src.replace(old, new, 1))
        try:
            r = sh([sys.executable, "-m", "pytest", "-x", "-q", "-p", "no:cacheprovider", *TESTS], TREE)
            tail = r.stdout.strip().splitlines()
            summary = next((ln for ln in reversed(tail) if "passed" in ln or "failed" in ln or "error" in ln), tail[-1] if tail else "")
            first_fail = next((ln for ln in tail if ln.startswith("FAILED") or ln.startswith("ERROR")), "")
            status = "KILLED" if r.returncode != 0 else "SURVIVED"
            print(f"{name:45s} {status:9s} {summary[:60]} {first_fail[:120]}", flush=True)
        finally:
            sh(["git", "checkout", "--", "."], TREE)


main()

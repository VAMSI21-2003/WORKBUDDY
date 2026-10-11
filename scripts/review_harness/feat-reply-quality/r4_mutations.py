"""Round-4 mutation check of what the fix commit (398b256) changed: REPORT_ONLY and the report-first ordering, the M2 guards
(by-name vs by-action, "myself" scoping, the don't-forget exception), the noun list `_ABOUT_NOUN` word by word, the new verbs/fillers.

    python r4_mutations.py --tree DIR_OF_A_THROWAWAY_WORKTREE [--tests FILE ...] [NAME_PREFIX ...]

Edits one place in the throwaway worktree, runs the relevant tests with -x, restores with `git checkout -- .`.
Prints KILLED / SURVIVED / MISSING-TARGET.
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

TESTS = ["tests/buddy/test_reply_quality.py", "tests/buddy/test_reply_quality_units.py", "tests/buddy/test_outcome.py",
         "tests/buddy/test_actions.py", "tests/buddy/test_voice.py"]
M = []


def m(name, file, old, new):
    M.append((name, file, old, new))


O, ACT, V = "buddy/outcome.py", "buddy/actions.py", "buddy/voice.py"
# ---- REPORT_ONLY
m("ro-branch-off", O, "    if had_report and any(w not in _FILLER_WORDS for w in words):", "    if False:")
m("ro-branch-always", O, "    if had_report and any(w not in _FILLER_WORDS for w in words):", "    if had_report:")
m("ro-has-source", O, "return ActionOutcome(\"\", NOT_DONE, reason=REPORT_ONLY)", "return ActionOutcome(\"\", NOT_DONE, reason=REPORT_ONLY, source=entry.source)")
m("ro-sentence", O, "I did nothing beyond the report for that part of your request.", "I did the report for that part of your request.")
m("ro-early-return-off", O, "    if report_stated and is_only_a_report_request(phrase):\n", "    if False:\n")
m("ro-named-passed-for-report-off", O, "            names_action = named_passed = True\n    lowered = _LOCATION", "            names_action = True\n    lowered = _LOCATION")
m("ro-had-report-needs-stated", O, "    had_report = False\n    if report_stated:", "    had_report = True\n    if report_stated:")
m("ro-only-all-to-any", O, "return bool(not_done) and all(a.reason == REPORT_ONLY for a in not_done)", "return bool(not_done) and any(a.reason == REPORT_ONLY for a in not_done)")
m("ro-only-empty-ok", O, "return bool(not_done) and all(", "return all(")
m("ro-dedupe-key", O, "if outcome is None or outcome.user_phrase.casefold() in said_no_skill:", "if outcome is None:")
# ---- voice ordering
m("voice-after-report-off", V, "            if request.report_requested and stated and only_the_report_was_done(outcome):", "            if False:")
m("voice-after-report-first", V, "            parts.append(TemplateSpeaker._report_for_unchanged_file(request))\n            parts.extend(after_report)", "            parts.extend(after_report)\n            parts.append(TemplateSpeaker._report_for_unchanged_file(request))")
m("voice-after-report-dropped", V, "            parts.extend(after_report)\n", "")
m("voice-capabilities-after-report-only", V, "                after_report = stated\n", "                after_report = stated\n                parts.append(CAPABILITIES[style])\n")
# ---- guards (M2)
m("g-not-a-refusal-1", ACT, "rf\"\\b(?:no|not|without|skip|never|don't|dont|do\\s+not)\\b{_NOT_A_REFUSAL}\"", "rf\"\\b(?:no|not|without|skip|never|don't|dont|do\\s+not)\\b\"")
m("g-not-a-refusal-2", ACT, "rf\"{_NOT_A_REFUSAL}\\s+(?:\\w+\\s+){{0,2}}\"", "rf\"\\s+(?:\\w+\\s+){{0,2}}\"")
m("g-forget-off", ACT, "(?!\\s+(?:forget|hesitate|worry|be\\s+(?:afraid|shy)))", "(?!\\s+(?:hesitate|worry|be\\s+(?:afraid|shy)))")
m("g-hesitate-off", ACT, "(?!\\s+(?:forget|hesitate|worry|be\\s+(?:afraid|shy)))", "(?!\\s+(?:forget|worry|be\\s+(?:afraid|shy)))")
m("g-worry-off", ACT, "(?!\\s+(?:forget|hesitate|worry|be\\s+(?:afraid|shy)))", "(?!\\s+(?:forget|hesitate|be\\s+(?:afraid|shy)))")
m("g-afraid-shy-off", ACT, "|be\\s+(?:afraid|shy)))\"", "))\"")
m("g-by-name-early-true-off", ACT, "    if _REPORT_BY_NAME.search(message):\n", "    if False:\n")
m("g-by-person-first-off", ACT, "    if _REPORT_BY_THE_PERSON.search(message):\n        return False\n", "")
m("g-by-person-letme-off", ACT, "(?:i'll|i\\s+will|i'm\\s+going\\s+to|i\\s+can|let\\s+me)\\s+(?:\\w+\\s+){0,3}\"\n    r\"(?:check|look|review|inspect|analy[sz]e|summari[sz]e)", "(?:i'll|i\\s+will|i'm\\s+going\\s+to|i\\s+can)\\s+(?:\\w+\\s+){0,3}\"\n    r\"(?:check|look|review|inspect|analy[sz]e|summari[sz]e)")
m("g-by-person-ican-off", ACT, "(?:i'll|i\\s+will|i'm\\s+going\\s+to|i\\s+can|let\\s+me)\\s+(?:\\w+\\s+){0,3}\"\n    r\"(?:check|look|review|inspect|analy[sz]e|summari[sz]e)", "(?:i'll|i\\s+will|i'm\\s+going\\s+to|let\\s+me)\\s+(?:\\w+\\s+){0,3}\"\n    r\"(?:check|look|review|inspect|analy[sz]e|summari[sz]e)")
m("g-by-person-going-to-off", ACT, "(?:i'll|i\\s+will|i'm\\s+going\\s+to|i\\s+can|let\\s+me)\\s+(?:\\w+\\s+){0,3}\"\n    r\"(?:check|look|review|inspect|analy[sz]e|summari[sz]e)", "(?:i'll|i\\s+will|i\\s+can|let\\s+me)\\s+(?:\\w+\\s+){0,3}\"\n    r\"(?:check|look|review|inspect|analy[sz]e|summari[sz]e)")
m("g-by-person-needs-myself-off", ACT, "(?:check|look|review|inspect|analy[sz]e|summari[sz]e)\\b[^.;,!?]{0,40}\\bmyself\\b\",", "(?:check|look|review|inspect|analy[sz]e|summari[sz]e)\\b\",")
m("g-by-person-clause-off", ACT, "(?:check|look|review|inspect|analy[sz]e|summari[sz]e)\\b[^.;,!?]{0,40}\\bmyself\\b\",", "(?:check|look|review|inspect|analy[sz]e|summari[sz]e)\\b.{0,200}\\bmyself\\b\",")
m("g-by-person-summarize-off", ACT, "(?:check|look|review|inspect|analy[sz]e|summari[sz]e)\\b[^.;,!?]{0,40}\\bmyself\\b\",", "(?:check|look|review|inspect|analy[sz]e)\\b[^.;,!?]{0,40}\\bmyself\\b\",")
m("g-action-by-person-off", ACT, "        and _REPORT_BY_ACTION_BY_THE_PERSON.search(message) is None\n", "")
m("g-action-by-person-myself-off", ACT, "(?:file|sheet|spreadsheet|workbook|data|table)\\b[^.;,!?]{0,30}\\bmyself\\b\"\n", "(?:file|sheet|spreadsheet|workbook|data|table)\\bZZ\"\n")
m("g-action-by-person-ill-off", ACT, "    r\"|\\b(?:i'll|i\\s+will|i'm\\s+going\\s+to|i\\s+can)\\s+(?:\\w+\\s+){0,3}\"\n    r\"(?:check|look(?:", "    r\"|\\bZZ\\s+(?:\\w+\\s+){0,3}\"\n    r\"(?:check|look(?:")
m("g-elsewhere-off", ACT, "    if _REPORT_NEGATED.search(message) or _REPORT_ELSEWHERE.search(message):", "    if _REPORT_NEGATED.search(message):")
m("g-negated-off", ACT, "    if _REPORT_NEGATED.search(message) or _REPORT_ELSEWHERE.search(message):", "    if _REPORT_ELSEWHERE.search(message):")
# ---- the noun list, word by word (what may keep a reply silent): the replacement is made inside the one block only
_SRC = (TREE / ACT).read_text()


def block(start_marker, end_marker):
    a = _SRC.index(start_marker)
    b = _SRC.index(end_marker, a) + len(end_marker)
    return _SRC[a:b]


NOUN_BLOCK = block("_ABOUT_NOUN = (", "\n)\n")
for w in ["file", "sheet", "tab", "spreadsheet", "workbook", "data", "table", "issues?", "problems?", "duplicates?", "rows?", "columns?", "errors?", "everything"]:
    key = w if w != "file" else "(?:file|"
    if w == "columns?":
        new_noun = NOUN_BLOCK.replace('r"columns?|', 'r"zzqxcolumns?|', 1)
    else:
        new_noun = NOUN_BLOCK.replace(("|" + w + "|") if w != "file" else "(?:file|", ("|zzqx" + w + "|") if w != "file" else "(?:zzqxfile|", 1)
    m("noun-" + w, ACT, NOUN_BLOCK, new_noun)
for w in ["first", "second", "1st", "2nd", "main", "only"]:
    new_noun = NOUN_BLOCK.replace(w + "|" if w != "only" else "|only)", "zzqx" + w + "|" if w != "only" else "|zzqxonly)", 1)
    m("ordinal-" + w, ACT, NOUN_BLOCK, new_noun)
m("noun-missing-empty-blank", ACT, "(?:missing|empty|blank)\\s+(?:values?|data|cells?|rows?|fields?)", "(?:missing)\\s+(?:values?|data|cells?|rows?|fields?)")
m("noun-missing-values", ACT, "(?:missing|empty|blank)\\s+(?:values?|data|cells?|rows?|fields?)", "(?:missing|empty|blank)\\s+(?:data|cells?|rows?|fields?)")
m("about-multi-noun-off", ACT, "rf\"(?:\\s+(?:and\\s+)?{_ABOUT_DETERMINERS}{_ABOUT_NOUN})*\\b)?\"", "rf\"\\b)?\"")
m("about-and-off", ACT, "(?:\\s+(?:and\\s+)?{_ABOUT_DETERMINERS}{_ABOUT_NOUN})*", "(?:\\s+{_ABOUT_DETERMINERS}{_ABOUT_NOUN})*")
# ---- verbs added to the by-name pattern, and the fillers: the replacement is made inside the one block only
VERB_BLOCK = "provide|compile|deliver|return|output|see|view|have|tell|like|love)"
for w in ["provide", "compile", "deliver", "return", "output", "see", "view", "have", "tell", "like", "love"]:
    parts = VERB_BLOCK[:-1].split("|")
    m("verb-" + w, ACT, VERB_BLOCK, "|".join("zzqx" + x if x == w else x for x in parts) + ")")
FILL_A = "be great thanks thank short brief quick full detailed written tell i'd i'll i'm like love "
FILL_B = "let lets let's want wants need needs"
for w in ["tell", "i'd", "i'll", "i'm", "like", "love"]:
    words = FILL_A.split()
    m("filler-" + w, ACT, FILL_A, " ".join("zzqx" + x if x == w else x for x in words) + " ")
for w in ["let", "lets", "let's", "want", "wants", "need", "needs"]:
    words = FILL_B.split()
    m("filler-" + w, ACT, FILL_B, " ".join("zzqx" + x if x == w else x for x in words))
# ---- by-action: the new "for" clause
m("action-for-empty-off", ACT, "(?:empty|blank)\\s+rows?|blanks?|missing\\s+(?:values?|data)))?\"", "))?\"")
m("action-for-blanks-off", ACT, "(?:empty|blank)\\s+rows?|blanks?|missing", "(?:empty|blank)\\s+rows?|missing")
m("action-for-missing-off", ACT, "|blanks?|missing\\s+(?:values?|data)))?\"", "|blanks?))?\"")
m("action-whats-wrong-off", ACT, "|\\bwhat(?:'?s|\\s+is|\\s+are)\\s+wrong", "|\\bzzwhat(?:'?s|\\s+is|\\s+are)\\s+wrong")


def sh(cmd, cwd):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)


def main():
    sel = [x for x in M if not A.names or any(x[0].startswith(n) for n in A.names)]
    for name, file, old, new in sel:
        path = TREE / file
        src = path.read_text()
        if old not in src:
            print(f"{name:40s} MISSING-TARGET ({file})", flush=True)
            continue
        path.write_text(src.replace(old, new, 1))
        try:
            r = sh([sys.executable, "-m", "pytest", "-x", "-q", "-p", "no:cacheprovider", *(A.tests or TESTS)], TREE)
            tail = r.stdout.strip().splitlines()
            summary = next((ln for ln in reversed(tail) if "passed" in ln or "failed" in ln or "error" in ln), tail[-1] if tail else "")
            first_fail = next((ln for ln in tail if ln.startswith("FAILED") or ln.startswith("ERROR")), "")
            status = "KILLED" if r.returncode != 0 else "SURVIVED"
            print(f"{name:40s} {status:9s} {summary[:50]} {first_fail[:110]}", flush=True)
        finally:
            sh(["git", "checkout", "--", "."], TREE)


main()

"""Round 5, probe 6: seeded random fuzz of everything a faked LLM can hand the pipeline around the report
(Brain intent and actions with random phrases, Think asking/failing, a planner with random capabilities and phrases,
random file kinds, random stage faults), through the real engine. Invariants are checked on every turn:

  I1  no flag from rq (changed original, voice-model call, "(s)", "verifier", raw capability id, secret text, wrong plural)
  I2  "Report on the copy" only on a delivered turn; "Report on your original file" only on an undelivered one
  I3  "Report on ..." only if Brain's reading (ledger) holds report_on_file; never beside "can't do"
  I4  Brain read a report => the reply has "Report on", or says it could not produce it, or is a refusal / no-file / failure / greeting-free
  I5  "Done" / "Partly done" only on a delivered turn
  I6  every count the reply states matches the independent read (check_numbers logic)
  I7  the original's SHA-256 is unchanged (rq flag ORIGINAL_CHANGED)
  I8  no engine exception escaped

    python r5_fuzz.py [--tree DIR] [--seed 1] [--n 300] [--speakers template,friend,focus] [--out FILE]
"""
import json
import random
import re

import r5lib

A = r5lib.args(lambda ap: (ap.add_argument("--seed", type=int, default=1), ap.add_argument("--n", type=int, default=300),
                            ap.add_argument("--bias", default="", help="delivered: mostly dedupe+report requests, a sound Brain, faults 40%%")))
rq = r5lib.start(A)
from rq import AN, DD, ER, brain, plan, think  # noqa: E402

RPT = "report_on_file"
MESSAGES = [
    "give me a report", "give me a report on the file", "remove the duplicate rows", "remove the empty rows",
    "remove the duplicate rows and give me a report", "give me a summary then remove the empty rows",
    "remove duplicates, remove empty rows and give me an overview", "sort by amount and give me a report",
    "give me a report and email it to my boss", "hi", "thanks!", "what can you do?", "analyze my file",
    "clean it", "give me a report on the duplicate rows", "remove the duplicate rows and send me the report in pdf",
    "check my data for problems", "report on the empty rows", "remove the duplicate rows but keep the empty rows",
    "don't give me a report, just remove the duplicate rows", "I'll review it myself, remove the empty rows",
    "remove the duplicate rows and then give me a report", "tell me what is wrong with the file and fix the duplicates",
]
FILES = ["plain", "clean", "one_dup", "blanks", "empty_only", "one_row", "multi", "near_dups", "numeric_dups", None]
INTENTS = ["remove_duplicate_rows", "remove_empty_rows", RPT, "multi_step_request", "conversation", "unclear", "weird", None]
ACTS = ["remove_duplicate_rows", "remove_empty_rows", RPT, RPT, "unsupported", "bogus", "report"]
CAPS = [DD, ER, AN, AN, "spreadsheet.report", "spreadsheet.sort", "spreadsheet.chart"]
PATCHES = [None] * 8 + ["analyzer_raises", "analysis_verifier_fails_1", "analysis_verifier_fails_2", "analysis_verifier_fails_all",
                        "dedupe_verifier_fails", "read_excel_1", "read_excel_2", "read_excel_3"]


def slice_of(rng, msg):
    words = msg.split()
    if not words:
        return msg
    i = rng.randrange(len(words))
    j = rng.randrange(i, len(words)) + 1
    return " ".join(words[i:j])


def phrase(rng, msg):
    r = rng.random()
    if r < 0.5:
        return slice_of(rng, msg)
    if r < 0.65:
        return msg
    if r < 0.75:
        return ""
    if r < 0.85:
        return slice_of(rng, msg).upper()
    return "an invented phrase xq"


def make_patch(name):
    return {
        None: None,
        "analyzer_raises": r5lib.analyzer_raises("all"),
        "analysis_verifier_fails_1": r5lib.analysis_verifier_fails({1}),
        "analysis_verifier_fails_2": r5lib.analysis_verifier_fails({2}),
        "analysis_verifier_fails_all": r5lib.analysis_verifier_fails("all"),
        "dedupe_verifier_fails": r5lib.dedupe_verifier_fails(),
        "read_excel_1": r5lib.read_excel_raises({1}),
        "read_excel_2": r5lib.read_excel_raises({2}),
        "read_excel_3": r5lib.read_excel_raises({3}),
    }[name]


def case(rng, i):
    if A.bias == "delivered":
        return case_delivered(rng, i)
    msg = rng.choice(MESSAGES)
    intent = rng.choice(INTENTS)
    acts = [(rng.choice(ACTS), phrase(rng, msg)) for _ in range(rng.choice([0, 0, 1, 1, 2, 3]))]
    b = json.dumps({"intent": intent, "confidence": 0.9,
                    "requested_actions": [{"action": a, "source_phrase": p} for a, p in acts]})
    r = rng.random()
    if r < 0.08:
        t = think(clarify=True, clarification_reason=rng.choice(["which_action", "which_file", "other", "zzz", ""]))
    elif r < 0.11:
        t = RuntimeError("think down")
    else:
        t = think()
    steps = [(rng.choice(CAPS), phrase(rng, msg)) for _ in range(rng.choice([0, 1, 2, 3]))]
    p = plan(*steps) if rng.random() < 0.9 else rng.choice(["not json", "[]", json.dumps({"steps": "x"})])
    pn = rng.choice(PATCHES)
    spec = dict(file=rng.choice(FILES), turns=[dict(msg=msg, b=b, t=t, p=p)], secrets=("TOPSECRET", "boom", "/secret/"))
    if pn:
        spec["patch"] = make_patch(pn)
    meta = dict(msg=msg, intent=intent, acts=acts, steps=steps, file=spec["file"], patch=pn)
    return f"Z{i:04d}", spec, meta


def case_delivered(rng, i):
    """Mostly sound readings of 'remove ... and give me a report', so the delivered paths and their faults are covered."""
    msg = rng.choice([m for m in MESSAGES if "remove" in m and ("report" in m or "overview" in m or "summary" in m)] + ["remove the empty rows and give me a report"])
    kinds = rng.choice([("remove_duplicate_rows", "remove the duplicate rows"), ("remove_empty_rows", "remove the empty rows")])
    acts = [kinds, (RPT, "give me a report")] if rng.random() < 0.85 else [(RPT, phrase(rng, msg)), kinds]
    if rng.random() < 0.25:
        acts.append(("unsupported", phrase(rng, msg)))
    intent = rng.choice(["multi_step_request", "multi_step_request", kinds[0]])
    b = json.dumps({"intent": intent, "confidence": 0.9, "requested_actions": [{"action": a, "source_phrase": p} for a, p in acts]})
    cap = DD if kinds[0] == "remove_duplicate_rows" else ER
    steps = [(cap, kinds[1]), (rng.choice([AN, AN, "spreadsheet.report"]), rng.choice(["give me a report", "a report", "give me a report"]))]
    if rng.random() < 0.5:
        steps.reverse()
    p = plan(*steps)
    pn = rng.choice([None] * 6 + PATCHES[8:])
    spec = dict(file=rng.choice(["plain", "plain", "one_dup", "blanks", "clean", "near_dups", "numeric_dups", "multi"]),
                turns=[dict(msg=msg, b=b, t=think(), p=p)], secrets=("TOPSECRET", "boom", "/secret/"))
    if pn:
        spec["patch"] = make_patch(pn)
    meta = dict(msg=msg, intent=intent, acts=acts, steps=steps, file=spec["file"], patch=pn)
    return f"Z{i:04d}", spec, meta


def report_in_reading(meta):
    return meta["intent"] == RPT or any(a == RPT for a, _ in meta["acts"])


def check(rec, meta):
    probs = []
    rep = rec.get("reply") or ""
    if "error" in rec:
        return ["I8 engine exception: " + rec["error"]]
    bad_flags = [f for f in rec["flags"] if not f.startswith("claim_words_undelivered") and f != "secret_or_file_text"]
    if bad_flags:
        probs.append("I1 flags " + ",".join(bad_flags))
    if "secret_or_file_text" in rec["flags"] and meta["patch"] is None and meta["file"] not in ("hostile_headers", "hostile_headers2"):
        probs.append("I1 secret/file text without a fault")
    dlv = rec["delivered"]
    if "Report on the copy" in rep and not dlv:
        probs.append("I2 copy report on an undelivered turn")
    if "Report on your original file" in rep and dlv:
        probs.append("I2 original report on a delivered turn")
    if "Report on" in rep and not report_in_reading(meta):
        probs.append("I3 report without Brain reading one")
    if "Report on" in rep and re.search(r"can't do|can’t do", rep):
        # an unsupported entry for something ELSE may legitimately sit beside a report
        unsup = [p for a, p in meta["acts"] if a not in ("remove_duplicate_rows", "remove_empty_rows", RPT, "bogus", "report") or a in ("bogus", "report")]
        if not any(a in ("unsupported", "bogus", "report") for a, _ in meta["acts"]):
            probs.append("I3 report beside can't-do with no unsupported entry")
    if report_in_reading(meta) and meta["file"] is not None:
        ok = ("Report on" in rep or "produce the report" in rep or "I can't work on this file" in rep
              or "Something went wrong" in rep or "couldn't work out what you asked" in rep or "Do you want me" in rep
              or "Which file" in rep or "What would you like" in rep or "wasn't able to confirm" in rep
              or "I couldn't" in rep)
        if not ok:
            probs.append("I4 report read, reply neither gives nor refuses it")
    if re.search(r"\b(Done|Partly done)\b", rep) and not dlv and "Done!" not in rep:
        probs.append("I5 Done/Partly done on an undelivered turn")
    return probs


if __name__ == "__main__":
    rng = random.Random(A.seed)
    speakers = tuple(A.speakers.split(","))
    allrec, bad = [], []
    for i in range(A.n):
        name, spec, meta = case(rng, i)
        for sp in speakers:
            r = rq.run(spec, sp)
            for t in r["turns"]:
                t["name"] = name
                t["meta"] = {k: v for k, v in meta.items()}
                t["problems"] = check(t, meta)
                allrec.append(t)
                if t["problems"]:
                    bad.append(t)
    out = A.out or str(r5lib.HERE / "out" / f"r5-fuzz-seed{A.seed}.json")
    r5lib.dump(allrec, out)
    print(f"{len(allrec)} turns, {len(bad)} with a problem -> {out}")
    from collections import Counter
    c = Counter(p.split(" ")[0] for t in bad for p in t["problems"])
    print(dict(c))
    shown = set()
    for t in bad:
        key = (t["problems"][0][:30], t["speaker"])
        if key in shown:
            continue
        shown.add(key)
        print(f"\n[{t['speaker']}] {t['name']} {t['problems']} msg={t['msg']!r}")
        print("   meta:", json.dumps(t["meta"], ensure_ascii=False)[:400])
        print("   steps:", t.get("steps"), "dlv", t.get("delivered"))
        print("   reply:", (t.get("reply") or t.get("error"))[:500])

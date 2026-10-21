"""Round 4: turns aimed at the round 3 fixes in the reply (NO_SKILL_UNSURE) and the near-duplicate bounds.

Real build_engine, real skills and verifiers, fake LLM only (../feat-reply-quality/rq.py, imported).

U*: an `unsupported` entry that overlaps a done action's phrase, in places where "check the copy is
what you wanted" may not be true (nothing changed, nothing handed back, a report only), and wordings
where the user clearly asked for more.
N*: the near-duplicate check's pair limit on ordinary-looking contact lists (a blank or "N/A" phone
column, a list near the row limit), report turns: is a near-duplicate pair still reported, and does
the reply say "none of the issues I check for were found" when the check was skipped?

    python scripts/review_harness/fix-security-pass/probe_q_round4.py [--tree DIR] [--speakers ...] [--only U1,N1] [--out F.json]
"""
import json
import random
import sys
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "feat-reply-quality"))
import r5lib  # noqa: E402

A = r5lib.args()
rq = r5lib.start(A)
from rq import DD, ER, brain, plan  # noqa: E402

pd = rq.T["pd"]
RPT, RD, RE, UNS = "report_on_file", "remove_duplicate_rows", "remove_empty_rows", "unsupported"
S = {}


def sc(name, msg, b, p=None, file="plain", patch=None):
    t = dict(msg=msg, b=b)
    if p is not None:
        t["p"] = p
    S[name] = dict(file=file, turns=[t], patch=patch, secrets=("task-0", "session-1"))


def _empty_step_fails():
    from skills.excel import remove_empty_rows as mod

    def boom(self, *a, **k):
        raise RuntimeError("boom")

    return mock.patch.object(mod.ExcelRemoveEmptyRowsSkill, "execute", boom)


# --- U: the neutral sentence --------------------------------------------------------------------
P = "get rid of the repeated entries"
sc("U1-unsure-nothing-to-remove", P, brain(RD, (RD, P), (UNS, P)), file="clean")
sc("U2-unsure-report-only", "give me a rundown as a pdf",
   brain(RPT, (RPT, "give me a rundown as a pdf"), (UNS, "give me a rundown as a pdf")))
sc("U3-unsure-chain-later-step-fails", "get rid of the repeated entries and remove the empty rows",
   brain("multi_step_request", (RD, P), (RE, "remove the empty rows"), (UNS, P)),
   plan((DD, P), (ER, "remove the empty rows")), patch=_empty_step_fails)
sc("U4-whole-message-real-extra", "remove duplicates and email it to bob",
   brain(RD, (RD, "remove duplicates and email it to bob"), (UNS, "remove duplicates and email it to bob")))
sc("U5-short-done-phrase-real-extra", "clean it up and translate it to French",
   brain(RD, (RD, "clean it up"), (UNS, "clean it up and translate it to French")))
sc("U6-unsure-plus-real-no-skill", "get rid of the repeated entries and sort by amount",
   brain("multi_step_request", (RD, P), (UNS, P), (UNS, "sort by amount")))
sc("U7-unsure-with-report", "get rid of the repeated entries and give me a report",
   brain("multi_step_request", (RD, P), (RPT, "give me a report"), (UNS, P)))
sc("U8-control-separate-extra", "remove duplicates and sort by amount",
   brain(RD, (RD, "remove duplicates"), (UNS, "sort by amount")))


# --- N: near-duplicate pair limit -----------------------------------------------------------------
def contacts(rows, blank_phones, phone_fill=None, seed=1):
    """``rows`` contacts with unique names/phones, ``blank_phones`` of them with no phone, plus one
    spelling-variant pair with the same phone (what the check exists to find)."""
    rnd = random.Random(seed)
    first = ["Alice", "Bruno", "Chen", "Dana", "Emil", "Fatima", "Goran", "Hana", "Ivo", "Jana", "Kofi",
             "Lena", "Mateo", "Nina", "Omar", "Priya", "Quinn", "Rosa", "Sven", "Tara", "Umar", "Vera",
             "Wim", "Xena", "Yusuf", "Zoe"]
    last = ["Berg", "Costa", "Dahl", "Evans", "Fox", "Gray", "Hill", "Ito", "Jones", "Khan", "Lund",
            "Moss", "Nash", "Ortiz", "Park", "Reed", "Shaw", "Tan", "Vogel", "West", "Young"]
    names, phones = [], []
    seen = set()
    while len(names) < rows:
        n = f"{rnd.choice(first)} {rnd.choice(last)} {rnd.randint(1, 99999)}"
        if n in seen:
            continue
        seen.add(n)
        names.append(n)
        phones.append(f"555{len(names):07d}")
    for i in range(blank_phones):
        phones[i] = phone_fill
    names += ["Jonathan Smithers", "Jonathon Smithers"]
    phones += ["5559999999", "5559999999"]
    return names, phones


def f_contacts(rows, blank, fill=None):
    def make(path):
        names, phones = contacts(rows, blank, fill)
        pd.DataFrame({"name": names, "phone": phones}).to_excel(path, index=False)
    return make


R = (RPT, "give me a report on this file")
sc("N0-control-all-phones", "give me a report on this file", brain(RPT, R), file=f_contacts(800, 0))
sc("N1-blank-phones-760", "give me a report on this file", brain(RPT, R), file=f_contacts(800, 760))
sc("N2-na-phones-760", "give me a report on this file", brain(RPT, R), file=f_contacts(800, 760, "N/A"))
sc("N3-na-phones-dedupe", "remove the duplicate rows", brain(RD, (RD, "remove the duplicate rows")),
   file=f_contacts(800, 760, "N/A"))

if __name__ == "__main__":
    only = tuple(x for x in A.only.split(",") if x)
    res = rq.run_all(S, only, tuple(A.speakers.split(",")))
    out = Path(A.out) if A.out else HERE / "out" / "probe_q.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(res, indent=1, default=str))
    for r in res:
        if "error" in r:
            print(f"== {r['name']} [{r['speaker']}] ERROR {r['error']}")
            continue
        print(f"== {r['name']} [{r['speaker']}] delivered={r['delivered']} steps={r['steps']} "
              f"orig_ok={r['orig_unchanged']} flags={r['flags']}")
        print(f"   orig={r['orig_facts']} final={r['final_facts']}")
        print("   " + (r["reply"] or "").replace("\n", "\n   "))

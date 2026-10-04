"""Re-review 3 scenarios for verified-outcome PR-B (fixes commit 688f62b).

Uses harness.py's mechanics (real build_engine, real skills and verifiers, fake LLM routed
by system prompt; template + friend x12 + focus = 14 turns per scenario). Two sets:

- rr2-*: the 32 adversarial scenarios from "Re-review 2", section 4, rebuilt from its table.
- z-*:   new scenarios aimed at the 688f62b code (removal-verb strip, location strip,
         joiner rule, negation/keep rule).

Usage: python rereview3.py [--tree DIR] [--out FILE] [--summary] [ID-prefix ...]
A scenario with ``sheets=2`` writes a second sheet (Q2, 2 more duplicates) into the
fixture, to check what a "Done" means on a two-sheet workbook.
"""
import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

_ap = argparse.ArgumentParser()
_ap.add_argument("--tree", default=str(Path(__file__).resolve().parents[2]))
_ap.add_argument("--out", default="rereview3_results.json")
_ap.add_argument("--summary", action="store_true")
_ap.add_argument("only", nargs="*")
ARGS = _ap.parse_args()
OUT = Path(ARGS.out).resolve()  # before the runs chdir into temp dirs
# harness.py parses argv at import; give it only the tree.
sys.argv = [sys.argv[0], "--tree", ARGS.tree]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness as H  # noqa: E402
import pandas as pd  # noqa: E402

brain, plan, DD, ER, DEF, NODUP = H.brain, H.plan, H.DD, H.ER, H.DEF, H.NODUP
RD, RE = "remove_duplicate_rows", "remove_empty_rows"
U = "unsupported"
S = {}


def sc(name, msg, b, pl=None, rows=DEF, sheets=1):
    S[name] = dict(msg=msg, brain=b, plan=pl, rows=rows, sheets=sheets)


# ---------------------------------------------------------------- Re-review 2, section 4
M = "remove duplicates"
for nm, msg in [
    ("v01-delete-sheet", "remove duplicates and delete the sheet"),
    ("v02-rename-file", "remove duplicates and rename the file"),
    ("v03-archive-it", "remove duplicates and archive it"),
    ("v04-mixed-case", "Remove Duplicates And DELETE The File"),
    ("v05-punct", "remove duplicates, then delete the file!"),
]:
    sc("rr2-" + nm, msg, brain(RD, (RD, M), (U, msg)))
sc("rr2-v06-unsup-only-drop-sheet", "Remove duplicates; drop the sheet.",
   brain(RD, (U, "Remove duplicates; drop the sheet.")))
for nm, msg in [("f07-and-the-sheet", "remove duplicates and the sheet"),
                ("f08-remove-it", "remove duplicates and remove it"),
                ("f09-remove-all", "remove duplicates and then remove all")]:
    sc("rr2-" + nm, msg, brain(RD, (RD, M), (U, msg)))
sc("rr2-f10-keep-only-dups", "keep only the duplicate rows",
   brain(RD, (U, "keep only the duplicate rows")))
sc("rr2-f11-keep-only-dups-multi", "keep only the duplicate rows",
   brain("multi_step_request", (U, "keep only the duplicate rows")), plan((DD, "duplicate rows")))
sc("rr2-f12-plain-filler", "Please, just remove the DUPLICATES.",
   brain(RD, (RD, "remove the DUPLICATES"), (U, "Please, just remove the DUPLICATES.")))
sc("rr2-r13-remove-formatting", "remove duplicates and remove the formatting",
   brain(RD, (RD, M), (U, "remove the formatting")))
sc("rr2-r14-remove-column-b", "remove duplicates and remove column B",
   brain(RD, (RD, M), (U, "remove column B")))
sc("rr2-r15-remove-dups-and-col", "remove duplicates and column B",
   brain(RD, (RD, M), (U, "remove duplicates and column B")))
sc("rr2-n16-curly-dont-delete", "remove duplicates but don’t delete the file",
   brain(RD, (RD, M), (U, "don’t delete the file")))
sc("rr2-n17-dont-delete-whole", "remove duplicates but don't delete the file",
   brain(RD, (RD, M), (U, "remove duplicates but don't delete the file")))
sc("rr2-n18-curly-dont-remove-dups", "remove the empty rows but don’t remove the duplicates",
   brain(RE, (RE, "remove the empty rows"), (U, "don’t remove the duplicates")),
   plan((DD, "the duplicates"), (ER, "remove the empty rows")))
sc("rr2-n19-never-delete-anything", "remove duplicates and never delete anything",
   brain(RD, (RD, M), (U, "remove duplicates and never delete anything")))
sc("rr2-s20-substring-dups", "remove the duplicate rows",
   brain(RD, (RD, "remove the duplicate rows"), (U, "duplicate rows")))
sc("rr2-s21-substring-remove-the", "remove the duplicate rows",
   brain(RD, (RD, "remove the duplicate rows"), (U, "remove the")))
_s22 = "get rid of the repeated entries and delete the file"
sc("rr2-s22-done-phrase-is-whole-msg", _s22, brain(RD, (RD, _s22), (U, _s22)))
sc("rr2-t23-paraphrase-empty-not-run", "dedupe and get rid of rows with nothing in them",
   brain(RD, (RD, "dedupe"), (U, "remove empty rows")))
sc("rr2-t24-found-empty-not-run", "remove duplicates and remove empty rows",
   brain(RD, (RD, M), (U, "remove empty rows")))
sc("rr2-t25-whole-msg-empty-not-run", "remove duplicates and remove empty rows",
   brain(RD, (RD, M), (U, "remove duplicates and remove empty rows")))
sc("rr2-g26-remove-all-dups", "please remove all duplicates",
   brain(RD, (RD, "remove all duplicates"), (U, "please remove all duplicates")))
sc("rr2-g27-nearmiss-rich-phrase", "remove all the duplicate rows from my sheet",
   brain(RD, ("remove_duplicates", "remove all the duplicate rows from my sheet")))
sc("rr2-g28-delete-the-dups", "delete the duplicates",
   brain(RD, (RD, "delete the duplicates"), (U, "delete the duplicates")))
sc("rr2-g29-can-you", "can you remove the duplicates",
   brain(RD, ("deduplicate", "can you remove the duplicates")))
sc("rr2-g30-empty-nearmiss-rich", "remove the empty rows from this sheet",
   brain(RE, ("remove_blank_rows", "remove the empty rows from this sheet")))
sc("rr2-p31-n2-planner-extra", "remove the empty rows and sort by amount",
   brain("multi_step_request", (U, "sort by amount")), plan((DD, "remove the empty rows")))
sc("rr2-p32-n2-no-mention", "sort by amount please",
   brain("multi_step_request", (U, "sort by amount please")), plan((DD, "sort by amount please")))

# ---------------------------------------------------------------- new, aimed at 688f62b
def z(nm, msg, *req, intent=RD, pl=None, **kw):
    sc("z" + nm, msg, brain(intent, *req), pl, **kw)


# Verbs and objects across a joiner.
_m = "remove duplicates and delete the file from the sheet"
z("01-del-file-from-sheet", _m, (RD, M), (U, "delete the file from the sheet"))
z("02-del-file-from-sheet-whole", _m, (RD, M), (U, _m))
z("03-delete-dups-email-it", "delete the duplicates and email it",
  (RD, "delete the duplicates"), (U, "email it"))
z("04-delete-dups-email-it-whole", "delete the duplicates and email it",
  (RD, "delete the duplicates"), (U, "delete the duplicates and email it"))
z("05-remove-and-delete-the-dups", "remove and delete the duplicates",
  (RD, "remove and delete the duplicates"), (U, "remove and delete the duplicates"))
z("06-dups-and-delete", "delete duplicates and delete",
  (RD, "delete duplicates"), (U, "delete duplicates and delete"))
# A location phrase next to a real unsupported part.
_m = "remove duplicates from the sheet and sort it"
z("07-loc-and-sort", _m, (RD, "remove duplicates from the sheet"), (U, _m))
z("08-loc-and-sort-unsup-only", _m, (U, _m))
z("09-loc-across-workbook-2sheets", "remove duplicates across the workbook",
  (RD, M), (U, "remove duplicates across the workbook"), sheets=2)
z("10-loc-across-workbook-1sheet", "remove duplicates across the workbook",
  (RD, M), (U, "remove duplicates across the workbook"))
z("11-loc-every-sheet", "remove duplicates in every sheet",
  (RD, M), (U, "remove duplicates in every sheet"), sheets=2)
z("12-loc-on-the-file-move", "remove duplicates and move them on the file",
  (RD, M), (U, "remove duplicates and move them on the file"))
# An object plus a verb for a step that did not run.
z("13-drop-empty-not-run", "remove duplicates and drop the empty rows",
  (RD, M), (U, "drop the empty rows"))
z("14-drop-empty-not-run-paraphrase", "remove duplicates and drop the rows with nothing in them",
  (RD, M), (U, "drop the empty rows"))
z("15-drop-empty-only-misread", "drop the empty rows", (U, "drop the empty rows"))
z("16-dups-and-empty-lumped", "remove duplicates and empty rows",
  (RD, "remove duplicates and empty rows"), (U, "remove duplicates and empty rows"))
# Negation plus keep.
z("17-dont-keep-empty-found", "remove duplicates and don't keep the empty rows",
  (RD, M), (U, "don't keep the empty rows"))
z("18-dont-keep-empty-paraphrase", "remove duplicates and don't keep rows with nothing in them",
  (RD, M), (U, "don't keep the empty rows"))
z("19-keep-dups-misread", "keep the duplicates", (U, "keep the duplicates"))
z("20-do-not-remove-dups-misread", "do not remove the duplicates",
  (U, "do not remove the duplicates"))
z("21-keep-dups-delete-file", "keep the duplicates, delete the file",
  (U, "keep the duplicates, delete the file"))
z("22-not-the-empty-rows", "remove the duplicates, not the empty rows",
  (RD, "remove the duplicates"), (U, "not the empty rows"))
z("23-but-keep-blank-rows", "remove duplicates but keep the blank rows",
  (RD, M), (U, "keep the blank rows"))
z("24-dont-leave-dups-curly", "Don’t leave any duplicates",
  (RD, "Don’t leave any duplicates"), (U, "Don’t leave any duplicates"))
# Mixed case and punctuation.
z("25-caps-punct-delete-file", "REMOVE DUPLICATES, AND DELETE THE FILE!!",
  (RD, "REMOVE DUPLICATES"), (U, "REMOVE DUPLICATES, AND DELETE THE FILE!!"))
z("26-caps-semicolon-erase", "Remove the Duplicates; Erase The Sheet.",
  (RD, "Remove the Duplicates"), (U, "Remove the Duplicates; Erase The Sheet."))
z("27-hyphen-de-dupe-nearmiss", "de-dupe the sheet", ("dedupe", "de-dupe the sheet"))
# Done action's quoted words as part of a longer phrase.
_m = "please remove the duplicate rows and then archive the workbook"
z("28-said-in-longer-archive", _m, (RD, "remove the duplicate rows"), (U, _m))
_m = "get rid of the repeated entries and then rename it"
z("29-said-paraphrase-rename", _m, (RD, "get rid of the repeated entries"), (U, _m))
_m = "get rid of the repeated entries and sort by amount"
z("30-said-not-substring-whole", _m, (RD, "remove duplicate rows"), (U, _m))
z("31-said-delete-the-file-residual", "remove duplicates and delete the file",
  (RD, "delete the file"), (U, "delete the file"))
z("32-said-with-joiner-lumped", "clean the repeated entries and the rest",
  (RD, "clean the repeated entries and the rest"), (U, "clean the repeated entries and the rest"))
# Objects that look like the action's object but are not rows.
z("33-dup-columns-misread", "remove duplicate columns", (U, "remove duplicate columns"))
z("34-dup-sheet-misread", "delete the duplicate sheet", (U, "delete the duplicate sheet"))
z("35-dups-of-my-file", "delete the duplicates of my file",
  (RD, "delete the duplicates"), (U, "delete the duplicates of my file"))
# Q1 class: ordinary phrasing around a done action (near-miss names and whole-message flags).
z("36-nearmiss-from-my-excel-file", "remove the duplicates from my excel file",
  ("remove_duplicates", "remove the duplicates from my excel file"))
z("37-nearmiss-removing", "removing duplicates please",
  ("remove_duplicates", "removing duplicates"))
z("38-nearmiss-for-me", "could you please remove the duplicate rows for me",
  ("remove_duplicates", "could you please remove the duplicate rows for me"))
z("39-nearmiss-dedupe-this-sheet", "dedupe this sheet", ("dedupe", "dedupe this sheet"))
z("40-nearmiss-in-the-data", "remove duplicates in the data",
  ("remove_duplicates", "remove duplicates in the data"))
z("41-nearmiss-get-rid-all", "get rid of all the duplicates",
  ("remove_duplicates", "get rid of all the duplicates"))
z("42-whole-flag-remove-them", "find the duplicates and remove them",
  (RD, "find the duplicates and remove them"), (U, "find the duplicates and remove them"))
z("45-loc-in-the-workbook-2sheets", "remove duplicates in the workbook",
  (RD, M), (U, "remove duplicates in the workbook"), sheets=2)
# Multi-step: both done; a whole-message unsupported flag.
z("43-multi-both-done-whole-flag", "remove duplicates and empty rows from the sheet",
  (RD, M), (RE, "empty rows"), (U, "remove duplicates and empty rows from the sheet"),
  intent="multi_step_request", pl=plan((DD, M), (ER, "empty rows")))
z("44-multi-both-done-delete-tail", "remove duplicates and empty rows, then delete the sheet",
  (RD, M), (RE, "empty rows"), (U, "remove duplicates and empty rows, then delete the sheet"),
  intent="multi_step_request", pl=plan((DD, M), (ER, "empty rows")))


def run(name, spec, speaker, sid):
    d = Path(tempfile.mkdtemp())
    os.chdir(d)
    (d / "uploads").mkdir()
    path = d / "uploads/report.xlsx"
    with pd.ExcelWriter(path) as w:
        first = "Q1" if spec["sheets"] == 2 else "Sheet1"
        pd.DataFrame(spec["rows"]).to_excel(w, index=False, sheet_name=first)
        if spec["sheets"] == 2:
            q2 = {"name": ["Dan", "Dan", "Eve", "Eve"], "amount": [1, 1, 2, 2]}
            pd.DataFrame(q2).to_excel(w, index=False, sheet_name="Q2")
    h0 = hashlib.sha256(path.read_bytes()).hexdigest()
    orig = pd.read_excel(path)
    st = H.BuddyState.new(session_id=sid, workspace_id="w")
    st.user_message = spec["msg"]
    st.context.add_uploaded_file("uploads/report.xlsx")
    st.context.switch_active_file("uploads/report.xlsx")
    p = H.P(spec["brain"], spec["plan"])
    sp = None
    if speaker != "template":
        st.context.set_mode(speaker)
        sp = H.PersonalityVoice(p)
    try:
        H.build_engine(llm_provider=p, speaker=sp).run(st)
    except Exception as e:  # noqa: BLE001
        return dict(name=name, speaker=speaker, sid=sid, error=repr(e))
    er = st.execution_result
    out = er.outputs if er else ()
    rec = dict(name=name, speaker=speaker, sid=sid, msg=spec["msg"], brain=spec["brain"],
               reply=st.reply, voice_calls=p.voice_calls, delivered=bool(out),
               steps=[(s.capability, s.status) for s in (er.steps if er else ())],
               orig_unchanged=hashlib.sha256(path.read_bytes()).hexdigest() == h0,
               orig=(len(orig), int(orig.duplicated().sum()), int(orig.isna().all(axis=1).sum())))
    if out:
        df = pd.read_excel(out[0])
        rec["final"] = (len(df), int(df.duplicated().sum()), int(df.isna().all(axis=1).sum()))
        rec["final_sheets"] = pd.ExcelFile(out[0]).sheet_names
    return rec


if __name__ == "__main__":
    res = []
    for name, spec in S.items():
        if ARGS.only and not any(name.startswith(i) for i in ARGS.only):
            continue
        for speaker in ["template", "friend", "focus"]:
            sids = [f"s{i}" for i in range(1, 13)] if speaker == "friend" else ["session-1"]
            for sid in sids:
                res.append(run(name, spec, speaker, sid))
    OUT.write_text(json.dumps(res, indent=1))
    print(len(res), "turns ->", OUT)
    if ARGS.summary:
        H.summarize(res)

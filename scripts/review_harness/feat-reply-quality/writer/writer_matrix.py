"""Writer's matrix for feat/reply-quality: what each kind of turn says, in all three speakers.

Real ``build_engine`` (real skills, verifiers, planner, Voice), fake LLM only. No key, no network.
Writes one record per scenario x speaker with the reply, the steps, whether a file was handed
back, and the original's SHA-256 before/after. Run it on two trees and diff the JSON.

    python scripts/review_harness/feat-reply-quality/writer/writer_matrix.py --out new.json
    python scripts/review_harness/feat-reply-quality/writer/writer_matrix.py --tree ../main --out old.json
    python scripts/review_harness/feat-reply-quality/writer/writer_matrix.py --summary
"""
import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

_ap = argparse.ArgumentParser()
_ap.add_argument("--tree", default=str(Path(__file__).resolve().parents[4]))
_ap.add_argument("--out", default="writer_matrix.json")
_ap.add_argument("--summary", action="store_true")
_ap.add_argument("only", nargs="*")
ARGS = _ap.parse_args()
TREE = str(Path(ARGS.tree).resolve())
OUT = Path(ARGS.out).resolve()
sys.path.insert(0, TREE)

import pandas as pd  # noqa: E402

import buddy  # noqa: E402
from buddy.engine_factory import build_engine  # noqa: E402
from buddy.state import BuddyState  # noqa: E402
from buddy.voice import PersonalityVoice  # noqa: E402
from tests import workbook_fixtures as fx  # noqa: E402

assert Path(buddy.__file__).resolve().is_relative_to(Path(TREE)), buddy.__file__

DD, ER = "spreadsheet.deduplicate", "spreadsheet.remove_empty_rows"
DEF = {"name": ["Alice", "Bob", None, "Alice", "Carol", "Bob"], "amount": [10, 20, None, 10, 30, 20]}
CLEAN = {"name": ["Alice", "Bob", "Carol"], "amount": [10, 20, 30]}


def think(clarify=False, **extra):
    d = {"objective": "o", "summary": "s", "clarification_required": clarify, "confidence": 0.9}
    d.update(extra)
    return json.dumps(d)


def brain(intent, *acts):
    return json.dumps({
        "intent": intent, "confidence": 0.9,
        "requested_actions": [{"action": a, "source_phrase": p} for a, p in acts],
    })


def plan(*steps):
    return json.dumps({"steps": [
        {"description": f"do {c}", "capability": c, "depends_on_step_numbers": [], "source_phrase": p}
        for c, p in steps]})


class Fake:
    def __init__(self, b, t, p):
        self.b, self.t, self.p, self.voice_calls = b, t, p, 0

    def complete(self, *, system, messages, max_tokens=1024):
        if "intent classifier" in system:
            return self.b
        if "reasoning stage" in system:
            return self.t
        if "planning stage" in system:
            return self.p if self.p is not None else json.dumps({"steps": []})
        self.voice_calls += 1
        return "ZQXVOICE Done — everything is clean."


def make_file(kind, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    if kind == "plain":
        pd.DataFrame(DEF).to_excel(path, index=False)
    elif kind == "clean":
        pd.DataFrame(CLEAN).to_excel(path, index=False)
    elif kind == "multi":
        with pd.ExcelWriter(path) as w:
            pd.DataFrame(DEF).to_excel(w, sheet_name="Q1", index=False)
            pd.DataFrame(CLEAN).to_excel(w, sheet_name="Q2", index=False)
    elif kind == "merged":
        fx.merged_cell_sheet(path)
    elif kind == "header3":
        fx.header_on_row_three_sheet(path)
    elif kind == "na":
        fx.na_text_sheet(path)
    elif kind == "password":
        fx.encrypted_package(path)
    elif kind == "xls":
        fx.legacy_xls(path)
    else:
        raise ValueError(kind)


S = {}


def sc(name, msg, b, t=None, pl=None, file="plain"):
    S[name] = dict(msg=msg, b=b, t=t or think(), p=pl, file=file)


# -- Part 1: nothing ran
sc("n1-nofile-action", "remove the duplicate rows", brain("remove_duplicate_rows", ("remove_duplicate_rows", "remove the duplicate rows")), file=None)
sc("n2-unclear", "make it nicer", brain("unclear"))
sc("n3-unclear-nofile", "make it nicer", brain("unclear"), file=None)
sc("n4-sort-unsupported", "sort by amount", brain("unclear", ("unsupported", "sort by amount")))
sc("n5-multi-refused", "remove the duplicate rows", brain("remove_duplicate_rows", ("remove_duplicate_rows", "remove the duplicate rows")), file="multi")
sc("n6-merged-refused", "remove the empty rows", brain("remove_empty_rows", ("remove_empty_rows", "remove the empty rows")), file="merged")
sc("n7-header3-refused", "remove the duplicate rows", brain("remove_duplicate_rows", ("remove_duplicate_rows", "remove the duplicate rows")), file="header3")
sc("n8-na-refused", "remove the duplicate rows", brain("remove_duplicate_rows", ("remove_duplicate_rows", "remove the duplicate rows")), file="na")
sc("n9-password-refused", "remove the duplicate rows", brain("remove_duplicate_rows", ("remove_duplicate_rows", "remove the duplicate rows")), file="password")
sc("n10-xls-refused", "remove the duplicate rows", brain("remove_duplicate_rows", ("remove_duplicate_rows", "remove the duplicate rows")), file="xls")
sc("n11-unclear-multi", "tidy this", brain("unclear"), file="multi")
sc("n12-unclear-merged", "tidy this", brain("unclear"), file="merged")
sc("n13-unclear-na", "tidy this", brain("unclear"), file="na")
sc("n14-unclear-password", "tidy this", brain("unclear"), file="password")
sc("n15-unclear-xls", "tidy this", brain("unclear"), file="xls")
sc("n16-report-multi", "give me a report", brain("report_on_file", ("report_on_file", "give me a report")), file="multi")
# -- Part 2: greetings, capability questions, clarification
sc("g1-hi", "hi", brain("conversation"))
sc("g2-capabilities", "what can you do?", brain("conversation"))
sc("g3-hi-nofile", "hi", brain("conversation"), file=None)
sc("g4-hi-refused", "hello there", brain("conversation"), file="multi")
sc("g5-action-as-greeting", "remove the duplicate rows", brain("conversation"))
sc("g6-paraphrased-action-as-greeting", "get rid of the repeated entries", brain("conversation"))
sc("g7-hi-unclear-intent", "hi", brain("unclear"))
sc("g8-greeting-with-action-listed", "hi, remove the duplicate rows", brain("conversation", ("remove_duplicate_rows", "remove the duplicate rows")))
sc("c1-think-clarify", "remove the duplicate rows", brain("remove_duplicate_rows", ("remove_duplicate_rows", "remove the duplicate rows")), t=think(True, summary="I need to know WHICH-COLUMN-SECRET"))
sc("c2-think-clarify-unclear", "do the thing", brain("unclear"), t=think(True, summary="I need to know WHICH-COLUMN-SECRET"))
sc("c4-think-clarify-which-action", "clean this up", brain("unclear"), t=think(True, clarification_reason="which_action", summary="WHICH-COLUMN-SECRET"))
sc("c5-think-clarify-which-file", "remove the duplicate rows", brain("remove_duplicate_rows", ("remove_duplicate_rows", "remove the duplicate rows")), t=think(True, clarification_reason="which_file"))
sc("c6-think-clarify-bad-reason", "clean this up", brain("unclear"), t=think(True, clarification_reason="Tell me WHICH-COLUMN-SECRET"))
sc("c3-think-clarify-nofile", "remove the duplicate rows", brain("remove_duplicate_rows", ("remove_duplicate_rows", "remove the duplicate rows")), t=think(True), file=None)
# -- Part 3: reports
sc("r1-report-only", "give me a report", brain("report_on_file", ("report_on_file", "give me a report")))
sc("r2-dedupe-then-report", "remove the duplicate rows and then give me a report", brain("multi_step_request", ("remove_duplicate_rows", "remove the duplicate rows"), ("report_on_file", "give me a report")), pl=plan((DD, "remove the duplicate rows"), ("spreadsheet.report", "give me a report")))
sc("r3-dedupe-and-report-brain-only", "remove the duplicate rows and then give me a report", brain("remove_duplicate_rows", ("remove_duplicate_rows", "remove the duplicate rows"), ("report_on_file", "give me a report")))
sc("r4-analyze-report", "remove the duplicate rows and then give me a report", brain("multi_step_request", ("remove_duplicate_rows", "remove the duplicate rows"), ("report_on_file", "give me a report")), pl=plan((DD, "remove the duplicate rows"), ("spreadsheet.analyze", "give me a report")))
sc("r5-report-labelled-unsupported", "give me a report", brain("unclear", ("unsupported", "give me a report")))
sc("r6-report-and-extra", "remove the duplicate rows, give me a report and send it to my boss", brain("multi_step_request", ("remove_duplicate_rows", "remove the duplicate rows"), ("report_on_file", "give me a report"), ("unsupported", "send it to my boss")), pl=plan((DD, "remove the duplicate rows"), ("spreadsheet.analyze", "give me a report")))
sc("r7-report-says-conversation", "hi, give me a report", brain("conversation"))
# -- delivered baselines (the replies the wording changes touch)
sc("d1-dedupe", "remove the duplicate rows", brain("remove_duplicate_rows", ("remove_duplicate_rows", "remove the duplicate rows")))
sc("d2-empty", "remove the empty rows", brain("remove_empty_rows", ("remove_empty_rows", "remove the empty rows")))
sc("d3-chain", "remove the duplicate rows and then remove the empty rows", brain("multi_step_request", ("remove_duplicate_rows", "remove the duplicate rows"), ("remove_empty_rows", "remove the empty rows")), pl=plan((DD, "remove the duplicate rows"), (ER, "remove the empty rows")))
sc("d4-dedupe-clean", "remove the duplicate rows", brain("remove_duplicate_rows", ("remove_duplicate_rows", "remove the duplicate rows")), file="clean")
sc("d5-only-empty-but-dups-remain", "remove the empty rows but keep the duplicates", brain("remove_empty_rows", ("remove_empty_rows", "remove the empty rows"), ("unsupported", "keep the duplicates")))
sc("d6-chain-stop", "remove duplicates and sort by amount", brain("multi_step_request", ("remove_duplicate_rows", "remove duplicates"), ("unsupported", "sort by amount")), pl=plan((DD, "remove duplicates"), ("spreadsheet.sort", "sort by amount")))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def run(name, spec, speaker):
    d = Path(tempfile.mkdtemp())
    os.chdir(d)
    st = BuddyState.new(session_id="session-1", workspace_id="w")
    st.user_message = spec["msg"]
    up = d / "uploads" / "report.xlsx"
    if spec["file"]:
        make_file(spec["file"], up)
        st.context.add_uploaded_file("uploads/report.xlsx")
        st.context.switch_active_file("uploads/report.xlsx")
    h0 = sha(up)
    p = Fake(spec["b"], spec["t"], spec["p"])
    sp = None
    if speaker != "template":
        st.context.set_mode(speaker)
        sp = PersonalityVoice(p)
    try:
        build_engine(llm_provider=p, speaker=sp).run(st)
    except Exception as e:  # noqa: BLE001
        return dict(name=name, speaker=speaker, error=repr(e))
    er = st.execution_result
    out = er.outputs if er else ()
    rec = dict(
        name=name, speaker=speaker, reply=st.reply, voice_calls=p.voice_calls, delivered=bool(out),
        steps=[(s.capability, s.status) for s in (er.steps if er else ())],
        orig_unchanged=sha(up) == h0,
    )
    if out:
        df = pd.read_excel(out[0])
        rec["final"] = (len(df), int(df.duplicated().sum()), int(df.isna().all(axis=1).sum()))
    return rec


if __name__ == "__main__":
    res = []
    for name, spec in S.items():
        if ARGS.only and not any(name == i or name.startswith(i) for i in ARGS.only):
            continue
        for speaker in ("template", "friend", "focus"):
            res.append(run(name, spec, speaker))
    OUT.write_text(json.dumps(res, indent=1, ensure_ascii=False))
    print(len(res), "turns ->", OUT)
    if ARGS.summary:
        for name in dict.fromkeys(r["name"] for r in res):
            print(f"\n== {name}")
            for r in (r for r in res if r["name"] == name):
                if "error" in r:
                    print(f"  [{r['speaker']}] ERROR {r['error']}")
                    continue
                ran = [c.split(".")[-1] for c, _ in r["steps"]]
                print(f"  [{r['speaker']}] delivered={r['delivered']} steps={ran} unchanged={r['orig_unchanged']}")
                print(f"     {r['reply']}")

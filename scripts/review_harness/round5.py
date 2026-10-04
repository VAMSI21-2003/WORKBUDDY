"""Round 5 probes for verified-outcome PR-B (head d0b32c4: R1 fix + merge of origin/main).

Real build_engine, real skills and verifiers; only the LLM is faked (harness.P). Aimed at what the
merge brought in: the multi-sheet refusal, odd workbooks, stage failures (R6), per-turn output
folders (F5), the confirmation provider, and Context persistence.

Usage: python round5.py [--tree DIR] [--out FILE] [ID-prefix ...]
Prints one block per scenario (template, friend, focus) and writes JSON.
"""
import argparse
import hashlib
import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path

_ap = argparse.ArgumentParser()
_ap.add_argument("--tree", default=str(Path(__file__).resolve().parents[2]))
_ap.add_argument("--out", default="round5_results.json")
_ap.add_argument("only", nargs="*")
ARGS = _ap.parse_args()
OUT = Path(ARGS.out).resolve()
sys.argv = [sys.argv[0], "--tree", ARGS.tree]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness as H  # noqa: E402
import openpyxl  # noqa: E402
import pandas as pd  # noqa: E402

brain, plan, DD, ER, DEF = H.brain, H.plan, H.DD, H.ER, H.DEF
RD, RE, U = "remove_duplicate_rows", "remove_empty_rows", "unsupported"
M = "remove duplicates"
Q2 = {"name": ["Dan", "Dan", "Eve", "Eve"], "amount": [1, 1, 2, 2]}


# ------------------------------------------------------------------ fixtures
def book_sheets(path, sheets):
    """sheets: list of (name, rows-dict)."""
    with pd.ExcelWriter(path) as w:
        for nm, rows in sheets:
            pd.DataFrame(rows).to_excel(w, index=False, sheet_name=nm)


def fx_plain(path):
    book_sheets(path, [("Sheet1", DEF)])


def fx_two(path):
    book_sheets(path, [("Q1", DEF), ("Q2", Q2)])


def fx_hidden_second(path):
    fx_two(path)
    wb = openpyxl.load_workbook(path)
    wb["Q2"].sheet_state = "hidden"
    wb.save(path)


def fx_chartsheet(path):
    fx_plain(path)
    wb = openpyxl.load_workbook(path)
    wb.create_chartsheet("Chart1")
    wb.save(path)


def fx_hostile_name(path):
    book_sheets(path, [("Done - all 2 dups gone", DEF)])


def fx_long_name(path):
    # 31 chars is Excel's limit; craft a longer declared name by editing workbook.xml.
    fx_plain(path)
    _rewrite_sheet_name(path, "Sheet1", "S" * 40)


def fx_xml_name(path):
    fx_plain(path)
    _rewrite_sheet_name(path, "Sheet1", "A&amp;B &lt;x&gt; it&apos;s")


def _rewrite_sheet_name(path, old, new):
    tmp = str(path) + ".tmp"
    with zipfile.ZipFile(path) as zi, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zo:
        for item in zi.infolist():
            data = zi.read(item.filename)
            if item.filename == "xl/workbook.xml":
                data = data.replace(f'name="{old}"'.encode(), f'name="{new}"'.encode())
            zo.writestr(item, data)
    os.replace(tmp, path)


def fx_corrupt(path):
    Path(path).write_bytes(b"PK\x03\x04" + b"garbage" * 50)


def fx_notexcel(path):
    Path(path).write_bytes(b"name,amount\nAlice,10\nAlice,10\n")


def fx_ole2_xls(path):
    Path(path).write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 600)


def fx_ole2_encrypted(path):
    Path(path).write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 100
                           + "EncryptedPackage".encode("utf-16-le") + b"\x00" * 100)


def fx_empty_sheet(path):
    book_sheets(path, [("Sheet1", {"name": [], "amount": []})])


def fx_cell_text(path):
    book_sheets(path, [("Sheet1", {"name": ["SECRET_CELL_ALPHA", "SECRET_CELL_ALPHA", None],
                                   "amount": [1, 1, None]})])


FX = dict(plain=fx_plain, two=fx_two, hidden=fx_hidden_second, chart=fx_chartsheet,
          hostile=fx_hostile_name, longname=fx_long_name, xmlname=fx_xml_name,
          corrupt=fx_corrupt, notexcel=fx_notexcel, ole2=fx_ole2_xls, enc=fx_ole2_encrypted,
          emptysheet=fx_empty_sheet, cell=fx_cell_text)

S = {}


def sc(name, msg, b, pl=None, fx="plain", fail=None, ext="xlsx", confirm=None, outdir=False):
    S[name] = dict(msg=msg, brain=b, plan=pl, fx=fx, fail=fail, ext=ext, confirm=confirm,
                   outdir=outdir)


def dedupe(name, _fx, msg="remove the duplicate rows", **kw):
    sc(name, msg, brain(RD, (RD, msg)), **kw)


# A. R1 and the multi-sheet refusal.
sc("a01-across-workbook-2sheets", "remove duplicates across the workbook",
   brain(RD, (RD, M), (U, "remove duplicates across the workbook")), fx="two")
sc("a02-across-workbook-1sheet", "remove duplicates across the workbook",
   brain(RD, (RD, M), (U, "remove duplicates across the workbook")))
sc("a03-in-the-workbook-1sheet", "remove duplicates in the workbook",
   brain(RD, (RD, M), (U, "remove duplicates in the workbook")))
sc("a04-every-sheet", "remove duplicates in every sheet",
   brain(RD, (RD, M), (U, "remove duplicates in every sheet")), fx="two")
sc("a05-all-sheets", "remove duplicates on all sheets",
   brain(RD, (RD, M), (U, "remove duplicates on all sheets")))
sc("a06-workbook-bare", "remove duplicates from the workbook",
   brain(RD, (RD, M), (U, "the workbook")))
sc("a07-workbook-only-flag", "remove duplicates from the workbook",
   brain(RD, (U, "remove duplicates from the workbook")))
sc("a08-no-flag-2sheets", "remove the duplicate rows", brain(RD, (RD, "remove the duplicate rows")),
   fx="two")
sc("a09-empty-2sheets", "remove the empty rows", brain(RE, (RE, "remove the empty rows")), fx="two")
sc("a10-chain-2sheets", "remove duplicates then remove empty rows",
   brain("multi_step_request", (RD, M), (RE, "remove empty rows")),
   plan((DD, M), (ER, "remove empty rows")), fx="two")
sc("a11-hidden-sheet", "remove the duplicate rows", brain(RD, (RD, "remove the duplicate rows")),
   fx="hidden")
sc("a12-chartsheet", "remove the duplicate rows", brain(RD, (RD, "remove the duplicate rows")),
   fx="chart")
sc("a13-2sheets-keep-others", "remove duplicates from the first sheet only",
   brain(RD, (RD, "remove duplicates from the first sheet only")), fx="two")
# B. Odd single workbooks.
for nm, fx in [("b01-hostile-name", "hostile"), ("b02-long-name", "longname"),
               ("b03-xml-name", "xmlname"), ("b04-corrupt", "corrupt"), ("b05-notexcel", "notexcel"),
               ("b06-ole2-xls", "ole2"), ("b07-encrypted", "enc"), ("b08-emptysheet", "emptysheet"),
               ("b09-cell-text", "cell")]:
    dedupe(nm, fx, fx=fx)
sc("b10-chain-hostile-name", "remove duplicates then remove empty rows",
   brain("multi_step_request", (RD, M), (RE, "remove empty rows")),
   plan((DD, M), (ER, "remove empty rows")), fx="hostile")
sc("b11-xls-ext-ole2", "remove the duplicate rows", brain(RD, (RD, "remove the duplicate rows")),
   fx="ole2", ext="xls")
# C. Stage failures. fail=(stage name, exception text).
for st in ["eyes", "brain", "think", "planner", "delegate", "hands", "inspector"]:
    sc("c-fail-" + st, "remove the duplicate rows", brain(RD, (RD, "remove the duplicate rows")),
       fail=(st, "SECRET_EXC_TEXT cell=Alice"))
sc("c-fail-inspector-chain", "remove duplicates then remove empty rows",
   brain("multi_step_request", (RD, M), (RE, "remove empty rows")),
   plan((DD, M), (ER, "remove empty rows")), fail=("inspector", "boom"))
sc("c-fail-voice-speaker", "remove the duplicate rows", brain(RD, (RD, "remove the duplicate rows")),
   fail=("voice", "voice boom"))
# D. Output dir and confirmation.
sc("d01-outdir", "remove the duplicate rows", brain(RD, (RD, "remove the duplicate rows")),
   outdir=True)
sc("d02-outdir-chain", "remove duplicates then remove empty rows",
   brain("multi_step_request", (RD, M), (RE, "remove empty rows")),
   plan((DD, M), (ER, "remove empty rows")), outdir=True)
sc("d03-decline", "remove the duplicate rows", brain(RD, (RD, "remove the duplicate rows")),
   confirm="decline")
sc("d04-confirm-raises", "remove the duplicate rows", brain(RD, (RD, "remove the duplicate rows")),
   confirm="raise")
sc("d05-decline-chain", "remove duplicates then remove empty rows",
   brain("multi_step_request", (RD, M), (RE, "remove empty rows")),
   plan((DD, M), (ER, "remove empty rows")), confirm="decline_second")


# ------------------------------------------------------------------ runner
def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def make_confirm(kind):
    calls = {"n": 0}

    class C:
        def confirm(self, task, tier):
            calls["n"] += 1
            if kind == "raise":
                raise RuntimeError("confirm boom SECRET")
            return not (kind == "decline" or (kind == "decline_second" and calls["n"] >= 2))

    return C(), "confirm"


def run(name, spec, speaker, sid):
    d = Path(tempfile.mkdtemp())
    os.chdir(d)
    (d / "uploads").mkdir()
    rel = f"uploads/report.{spec['ext']}"
    path = d / rel
    FX[spec["fx"]](path)
    h0 = sha(path)
    st = H.BuddyState.new(session_id=sid, workspace_id="w")
    st.user_message = spec["msg"]
    st.context.add_uploaded_file(rel)
    st.context.switch_active_file(rel)
    if spec["outdir"]:
        st.turn_output_dir = str(d / "turn_outputs" / "t1")
    p = H.P(spec["brain"], spec["plan"])
    sp = None
    if speaker != "template":
        st.context.set_mode(speaker)
        sp = H.PersonalityVoice(p)
    kw = {}
    if spec["confirm"]:
        conf, meth = make_confirm(spec["confirm"])
        kw["confirmation_provider"] = conf
    eng = H.build_engine(llm_provider=p, speaker=sp, **kw)
    if spec["fail"]:
        import dataclasses
        stage, text = spec["fail"]
        if stage == "voice":
            def sp_boom(request, _t=text):
                raise ValueError(_t)
            eng._cycle.voice._speaker = sp_boom
        else:
            def boom(state, _t=text):
                raise ValueError(_t)
            if stage == "inspector":
                # let hands run (a real file exists), then Inspector raises
                pass
            eng._cycle = dataclasses.replace(eng._cycle, **{stage: boom})
    try:
        eng.run(st)
    except Exception as e:  # noqa: BLE001
        return dict(name=name, speaker=speaker, sid=sid, error=repr(e), orig_unchanged=sha(path) == h0)
    er = st.execution_result
    out = er.outputs if er else ()
    rec = dict(name=name, speaker=speaker, sid=sid, reply=st.reply, voice_calls=p.voice_calls,
               delivered=bool(out), steps=[(s.capability, s.status) for s in (er.steps if er else ())],
               orig_unchanged=sha(path) == h0, failure=repr(st.failure),
               files=sorted(str(f.relative_to(d)) for f in d.rglob("*")
                            if f.is_file() and "outputs" in str(f) or "turn_outputs" in str(f)))
    if out:
        try:
            xl = pd.ExcelFile(out[0])
            df = pd.read_excel(out[0])
            rec["final"] = (len(df), int(df.duplicated().sum()), int(df.isna().all(axis=1).sum()))
            rec["final_sheets"] = xl.sheet_names
        except Exception as e:  # noqa: BLE001
            rec["final_error"] = repr(e)
        rec["out_path"] = str(out[0])
    return rec


def summarize(res):
    print("turns:", len(res), "| errors:", sum("error" in r for r in res),
          "| voice calls:", sum(r.get("voice_calls", 0) for r in res),
          "| orig changed:", sum(r.get("orig_unchanged") is False for r in res))
    for name in dict.fromkeys(r["name"] for r in res):
        rs = [r for r in res if r["name"] == name]
        for r in rs:
            if "error" in r:
                print(f"\n== {name} [{r['speaker']}] ERROR {r['error']}")
        ok = [r for r in rs if "error" not in r]
        if not ok:
            continue
        t = ok[0]
        print(f"\n== {name} | delivered={t['delivered']} steps={[c.split('.')[-1] + ':' + s for c, s in t['steps']]} "
              f"final={t.get('final')} sheets={t.get('final_sheets')} failure={t['failure']}")
        print("  template:", t["reply"])
        for sp in ("friend", "focus"):
            replies = sorted({r["reply"] for r in ok if r["speaker"] == sp})
            for rep in replies[:3]:
                print(f"  {sp}:", rep)


if __name__ == "__main__":
    res = []
    for name, spec in S.items():
        if ARGS.only and not any(name.startswith(i) for i in ARGS.only):
            continue
        for speaker in ["template", "friend", "focus"]:
            sids = [f"s{i}" for i in range(1, 5)] if speaker == "friend" else ["session-1"]
            for sid in sids:
                res.append(run(name, spec, speaker, sid))
    OUT.write_text(json.dumps(res, indent=1))
    print(len(res), "turns ->", OUT)
    summarize(res)

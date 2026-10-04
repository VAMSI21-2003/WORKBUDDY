"""Round 1 probes for fix/values-only-copy: real build_engine, real skills and verifiers, fake LLM.

Fixtures are odd one-sheet workbooks (formulas saved / unsaved, merged cells, title row, duplicate
headers, wrong <dimension>, NA-like strings, ...). Each is run through dedupe, remove-empty-rows and
a two-step chain as template, friend and focus. For each run it prints the reply, whether a file was
handed back, an independent read of that file (raw XML, no pandas), and the original's SHA-256
before/after.

Usage (repo root):
    python scripts/review_harness/values_only_probe.py [--tree DIR] [--out FILE] [--speakers t,f,o] [FIXTURE ...]
Run it with --tree on a worktree of origin/main to compare. No key, no network.
"""
import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import zipfile
from pathlib import Path

_ap = argparse.ArgumentParser()
_ap.add_argument("--tree", default=str(Path(__file__).resolve().parents[2]))
_ap.add_argument("--out", default="values_only_results.json")
_ap.add_argument("--speakers", default="template,friend,focus")
_ap.add_argument("only", nargs="*")
ARGS = _ap.parse_args()
OUT = Path(ARGS.out).resolve()
sys.argv = [sys.argv[0], "--tree", ARGS.tree]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness as H  # noqa: E402
import openpyxl  # noqa: E402
from openpyxl.comments import Comment  # noqa: E402
from openpyxl.styles import Font, PatternFill  # noqa: E402
from openpyxl.worksheet.datavalidation import DataValidation  # noqa: E402
from openpyxl.worksheet.table import Table  # noqa: E402

RD, RE = "remove_duplicate_rows", "remove_empty_rows"
ROWS = [("Alice", 10), ("Bob", 20), ("Alice", 10), None, ("Carol", 30)]


def rewrite(src, dst, edits):
    with zipfile.ZipFile(src) as a, zipfile.ZipFile(dst, "w") as b:
        for it in a.infolist():
            if it.filename in edits:
                b.writestr(it.filename, edits[it.filename])
            else:
                b.writestr(it, a.read(it.filename))


def patch_sheet(path, fn):
    with zipfile.ZipFile(path) as z:
        xml = z.read("xl/worksheets/sheet1.xml").decode()
    new = fn(xml)
    assert new != xml
    tmp = str(path) + ".tmp"
    rewrite(path, tmp, {"xl/worksheets/sheet1.xml": new.encode()})
    os.replace(tmp, path)


def base_book(header=("name", "amount"), rows=ROWS, start=1):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(list(header))
    for r in rows:
        ws.append(list(r) if r else [])
    return wb, ws


FX = {}


def fx(name):
    def deco(f):
        FX[name] = f
        return f
    return deco


@fx("plain")
def _(p):
    wb, ws = base_book(); wb.save(p)


@fx("formulas_saved")
def _(p):
    wb, ws = base_book(("name", "amount", "double"))
    for i in (2, 3, 4, 6):
        ws[f"C{i}"] = f"=B{i}*2"
    ws["A1"].font = Font(bold=True, color="FF0000"); ws["B2"].fill = PatternFill("solid", fgColor="FFFF00")
    ws.column_dimensions["A"].width = 40
    wb.save(p)
    vals = {"C2": 20, "C3": 40, "C4": 20, "C6": 60}
    def f(xml):
        for c, v in vals.items():
            xml = re.sub(rf'(<c r="{c}"[^>]*><f>[^<]*</f>)<v\s*/>', rf"\g<1><v>{v}</v>", xml)
        return xml
    patch_sheet(p, f)


@fx("formulas_unsaved")
def _(p):
    wb, ws = base_book(("name", "amount", "double"))
    for i in (2, 3, 4, 6):
        ws[f"C{i}"] = f"=B{i}*2"
    wb.save(p)


@fx("formulas_partly_saved")
def _(p):
    wb, ws = base_book(("name", "amount", "double"))
    for i in (2, 3, 4, 6):
        ws[f"C{i}"] = f"=B{i}*2"
    wb.save(p)
    patch_sheet(p, lambda x: re.sub(r'(<c r="C2"[^>]*><f>[^<]*</f>)<v\s*/>', r"\g<1><v>20</v>", x))


@fx("formula_only_row_unsaved")
def _(p):
    # row 4 has data only in formula cells (no saved value) and rows 3/5 differ only by formula
    wb, ws = base_book(("name", "amount", "calc"), rows=[("Alice", 10, "=B2*2"), ("Alice", 10, "=B2*3"),
                                                          (None, None, "=1+1"), ("Carol", 30, None)])
    wb.save(p)


@fx("merged")
def _(p):
    wb, ws = base_book(); ws["D1"] = "note"; ws.merge_cells("D1:E1"); wb.save(p)


@fx("title_row")
def _(p):
    wb, ws = base_book(); ws.insert_rows(1); ws["A1"] = "Q3 report"; wb.save(p)


@fx("header_row3")
def _(p):
    wb, ws = base_book(); ws.insert_rows(1, 2); ws["A1"] = "Q3 report"; ws["A2"] = "generated"; wb.save(p)


@fx("dup_header")
def _(p):
    wb, ws = base_book(("Total", "Total")); wb.save(p)


@fx("header_gap")
def _(p):
    wb, ws = base_book(("name", None, "amount"), rows=[("Alice", None, 10), ("Alice", None, 10)]); wb.save(p)


@fx("data_right_of_header")
def _(p):
    wb, ws = base_book(); ws["D3"] = "stray"; wb.save(p)


@fx("col_a_empty")
def _(p):
    wb, ws = base_book(); ws.insert_cols(1); wb.save(p)


@fx("wrong_dimension")
def _(p):  # <dimension> claims A1:B6 but there is data in column D
    wb, ws = base_book(); ws["D1"] = "extra"; ws["D2"] = "x"; ws["D3"] = "y"; wb.save(p)
    patch_sheet(p, lambda x: re.sub(r'<dimension ref="[^"]*"', '<dimension ref="A1:B6"', x))


@fx("wrong_dimension_rows")
def _(p):  # <dimension> claims A1:B2 but there are 6 rows
    wb, ws = base_book(); wb.save(p)
    patch_sheet(p, lambda x: re.sub(r'<dimension ref="[^"]*"', '<dimension ref="A1:B2"', x))


@fx("no_dimension")
def _(p):
    wb, ws = base_book(); ws["D1"] = "extra"; ws["D2"] = "x"; wb.save(p)
    patch_sheet(p, lambda x: re.sub(r'<dimension ref="[^"]*"\s*/>', '', x))


@fx("na_strings")
def _(p):
    wb, ws = base_book(("country", "note"), rows=[("NA", "N/A"), ("Namibia", "null"), ("NA", "None"), ("US", "nan")])
    wb.save(p)


@fx("text_equals")
def _(p):
    wb, ws = base_book(("name", "note"), rows=[("Alice", "x"), ("Bob", "y"), ("Alice", "x")])
    wb.save(p)
    # make B3 a *text* cell whose content starts with '=' (inline string)
    patch_sheet(p, lambda x: re.sub(r'<c r="B3"[^>]*>.*?</c>',
                '<c r="B3" t="inlineStr"><is><t>=HYPERLINK("http://evil.example","click")</t></is></c>', x, count=1, flags=re.S))


@fx("extras")
def _(p):  # filter, validation, comment, table, freeze panes, hidden col, number format, cond. format
    wb, ws = base_book(("name", "amount"), rows=[("Alice", 10), ("Bob", 20), ("Alice", 10), ("Carol", 30)])
    ws.auto_filter.ref = "A1:B5"
    ws.freeze_panes = "A2"
    dv = DataValidation(type="whole", operator="between", formula1="0", formula2="100"); ws.add_data_validation(dv); dv.add("B2:B5")
    ws["A2"].comment = Comment("hi", "me")
    ws["B2"].number_format = "0.00%"
    ws.column_dimensions["B"].hidden = True
    wb.save(p)


@fx("excel_table")
def _(p):
    wb, ws = base_book(("name", "amount"), rows=[("Alice", 10), ("Bob", 20), ("Alice", 10)])
    ws.add_table(Table(displayName="T1", ref="A1:B4")); wb.save(p)


@fx("header_only")
def _(p):
    wb, ws = base_book(rows=[]); wb.save(p)


@fx("empty_sheet")
def _(p):
    wb = openpyxl.Workbook(); wb.save(p)


@fx("numeric_headers")
def _(p):
    wb, ws = base_book((2023, 2024), rows=[(1, 2), (1, 2), (3, 4)]); wb.save(p)


@fx("int_and_str_header")
def _(p):
    wb, ws = base_book((1, "1"), rows=[(1, 2), (1, 2), (3, 4)]); wb.save(p)


@fx("big_ints")
def _(p):
    wb, ws = base_book(("id", "name"), rows=[(12345678901234567, "a"), (12345678901234567, "a"), (None, None), (9007199254740993, "b")])
    wb.save(p)


@fx("whitespace_header")
def _(p):
    wb, ws = base_book((" ", "amount")); wb.save(p)


@fx("formula_header")
def _(p):
    wb, ws = base_book(("name", "=\"am\"&\"ount\"")); wb.save(p)


@fx("date_cells")
def _(p):
    import datetime as dt
    wb, ws = base_book(("name", "when"), rows=[("a", dt.date(2024, 1, 5)), ("a", dt.date(2024, 1, 5)), ("b", dt.datetime(2024, 1, 6, 13, 30))])
    wb.save(p)


def raw_scan(path):
    """Independent read of an xlsx: formula count, <f> without saved value, merged count, sheet cells."""
    try:
        with zipfile.ZipFile(path) as z:
            xml = z.read("xl/worksheets/sheet1.xml").decode()
    except Exception as e:  # noqa: BLE001
        return {"unreadable": repr(e)[:80]}
    return {"formulas": len(re.findall(r"<f[ >/]", xml)), "merged": len(re.findall(r"<mergeCell ", xml))}


def cells(path):
    try:
        wb = openpyxl.load_workbook(path)
        ws = wb.worksheets[0]
        return [[c.value for c in r] for r in ws.iter_rows()]
    except Exception as e:  # noqa: BLE001
        return f"unreadable {e!r}"[:80]


@fx("formulas_wrong_dim")
def _(p):  # formulas saved in column C, but <dimension> says A1:B6 (the formula column is outside it)
    FX["formulas_saved"](p)
    patch_sheet(p, lambda x: re.sub(r'<dimension ref="[^"]*"', '<dimension ref="A1:B6"', x))


@fx("gap_dim_a1")
def _(p):  # header gap (B empty), <dimension ref="A1"/>: a non-plain sheet the detector can't see
    wb, ws = base_book(("name", None, "amount"), rows=[("Alice", None, 10), ("Alice", None, 10), ("Bob", None, 20)]); wb.save(p)
    patch_sheet(p, lambda x: re.sub(r'<dimension ref="[^"]*"', '<dimension ref="A1"', x))


REQS = {
    "dedupe": ("remove the duplicate rows", H.brain("remove_duplicate_rows", (RD, "remove the duplicate rows")), None),
    "empty": ("remove the empty rows", H.brain("remove_empty_rows", (RE, "remove the empty rows")), None),
    "analyze_only": ("what is in this file", H.brain("unclear", None), H.plan(("spreadsheet.analyze", "what is in this file"))),
    "wrong_step": ("remove the duplicate rows", H.brain("remove_duplicate_rows", (RD, "remove the duplicate rows")), H.plan((H.ER, "remove the duplicate rows"))),
    "chain": ("remove duplicates then remove empty rows",
              H.brain("multi_step_request", (RD, "remove duplicates"), (RE, "remove empty rows")),
              H.plan((H.DD, "remove duplicates"), (H.ER, "remove empty rows"))),
}


def run_one(fxname, reqname, speaker, sid, tweak=None):
    d = Path(tempfile.mkdtemp()); os.chdir(d); (d / "uploads").mkdir()
    p = d / "uploads" / "report.xlsx"
    FX[fxname](p)
    h0 = hashlib.sha256(p.read_bytes()).hexdigest()
    orig_scan, orig_cells = raw_scan(p), cells(p)
    msg, br, pl = REQS[reqname]
    st = H.BuddyState.new(session_id=sid, workspace_id="w"); st.user_message = msg
    st.context.add_uploaded_file("uploads/report.xlsx"); st.context.switch_active_file("uploads/report.xlsx")
    pr = H.P(br, pl); sp = None
    if speaker != "template":
        st.context.set_mode(speaker); sp = H.PersonalityVoice(pr)
    rec = dict(fx=fxname, req=reqname, speaker=speaker, sid=sid, orig_scan=orig_scan)
    try:
        H.build_engine(llm_provider=pr, speaker=sp).run(st)
    except Exception as e:  # noqa: BLE001
        rec["error"] = repr(e)[:200]
        return rec
    er = st.execution_result
    out = er.outputs if er else ()
    rec.update(reply=st.reply, voice_calls=pr.voice_calls, delivered=bool(out),
               steps=[(s.capability.split(".")[-1], s.status) for s in (er.steps if er else ())],
               orig_unchanged=hashlib.sha256(p.read_bytes()).hexdigest() == h0, failure=bool(getattr(st, "failure", None)))
    rec["orig_cells"] = orig_cells
    if out:
        rec["out_cells"] = cells(out[0]); rec["out_scan"] = raw_scan(out[0])
    return rec


if __name__ == "__main__":
    spk = {"t": "template", "template": "template", "f": "friend", "friend": "friend", "o": "focus", "focus": "focus"}
    speakers = [spk[s] for s in ARGS.speakers.split(",")]
    res = []
    for fxname in FX:
        if ARGS.only and fxname not in ARGS.only:
            continue
        for reqname in REQS:
            for speaker in speakers:
                sids = ["s1", "s2", "s3"] if speaker == "friend" else ["session-1"]
                for sid in sids:
                    res.append(run_one(fxname, reqname, speaker, sid))
    OUT.write_text(json.dumps(res, indent=1, default=str))
    print(len(res), "turns ->", OUT)
    bad = [r for r in res if r.get("orig_unchanged") is False]
    print("original changed in", len(bad), "runs; errors:", sum("error" in r for r in res))

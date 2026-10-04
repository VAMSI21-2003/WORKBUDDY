"""Round 2: for each odd cell kind in a data cell, does pandas read it as missing while the plain-table
check (skills.excel.workbook.plain_table_problem) neither refuses nor sees it as empty?
  python scripts/review_harness/differential_nan_probe.py
"""
import os, re, sys, tempfile, zipfile, warnings
from pathlib import Path
warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import openpyxl, pandas as pd
from skills.excel.workbook import plain_table_problem, _is_empty

CASES = {
 "num_nan": '<c r="B2" t="n"><v>nan</v></c>', "num_NaN": '<c r="B2"><v>NaN</v></c>',
 "num_inf": '<c r="B2"><v>inf</v></c>', "num_-inf": '<c r="B2"><v>-inf</v></c>', "num_1.#IND": '<c r="B2"><v>1.#IND</v></c>',
 "bool": '<c r="B2" t="b"><v>1</v></c>', "date_t_d": '<c r="B2" t="d"><v>2024-01-01T00:00:00</v></c>',
 "date_bad": '<c r="B2" t="d"><v>garbage</v></c>', "date_epoch0": '<c r="B2" s="1"><v>0</v></c>',
 "str_empty": '<c r="B2" t="str"><v></v></c>', "str_space": '<c r="B2" t="inlineStr"><is><t xml:space="preserve"> </t></is></c>',
 "inline_empty": '<c r="B2" t="inlineStr"><is><t></t></is></c>', "n_no_v": '<c r="B2" t="n"/>', "n_empty_v": '<c r="B2"><v></v></c>',
 "e_ref": '<c r="B2" t="e"><v>#REF!</v></c>', "e_na": '<c r="B2" t="e"><v>#N/A</v></c>', "e_bogus": '<c r="B2" t="e"><v>bogus</v></c>',
 "s_NA_inline": '<c r="B2" t="inlineStr"><is><t>NA</t></is></c>', "s_NA_str": '<c r="B2" t="str"><v>NA</v></c>',
 "s_nullstr": '<c r="B2" t="inlineStr"><is><t>#NA</t></is></c>', "s_neg_nan": '<c r="B2" t="inlineStr"><is><t>-NaN</t></is></c>',
 "s_N/A_ws": '<c r="B2" t="inlineStr"><is><t xml:space="preserve">N/A </t></is></c>',
 "s_unicode_na": '<c r="B2" t="inlineStr"><is><t>NA</t></is></c>',
 "s_empty_shared": None,
 "f_str_NA": '<c r="B2" t="str"><f>"x"</f><v>n/a</v></c>',
 "f_err": '<c r="B2" t="e"><f>1/0</f><v>#DIV/0!</v></c>',
 "n_garbage": '<c r="B2"><v>abc</v></c>',
}

def build(xml_cell, d):
    p = d / "a.xlsx"
    wb = openpyxl.Workbook(); ws = wb.active
    ws.append(["name", "amount"]); ws.append(["Alice", 1]); ws.append(["Bob", 2]); wb.save(p)
    with zipfile.ZipFile(p) as z:
        sx = z.read("xl/worksheets/sheet1.xml").decode()
    m = re.search(r'<c r="B2"[^>]*>.*?</c>', sx)
    sx = sx.replace(m.group(0), xml_cell)
    q = d / "b.xlsx"
    with zipfile.ZipFile(p) as a, zipfile.ZipFile(q, "w") as b:
        for it in a.infolist():
            b.writestr(it, sx.encode() if it.filename == "xl/worksheets/sheet1.xml" else a.read(it.filename))
    return q

def main():
    rows = []
    for name, cell in CASES.items():
        if cell is None: continue
        d = Path(tempfile.mkdtemp())
        q = build(cell, d)
        try:
            df = pd.read_excel(q); pv = df.iloc[0, 1]; pd_na = bool(pd.isna(pv)); pd_err = ""
        except Exception as e:
            pd_na, pd_err = None, type(e).__name__
        try:
            prob = plain_table_problem(str(q))
        except Exception as e:
            prob = "RAISED " + type(e).__name__
        try:
            ws = openpyxl.load_workbook(q, read_only=True, data_only=True).worksheets[0]
            c = [r for r in ws.iter_rows()][1][1]; ov = (c.value, c.data_type); emp = _is_empty(c.value)
        except Exception as e:
            ov, emp = type(e).__name__, None
        flag = "  <-- pandas NaN, detector silent and not 'empty'" if (pd_na and not prob and emp is False) else ""
        print(f"{name:14} pandas_na={pd_na!s:5} {pd_err:10} detector={str(prob)[:38]!r:42} openpyxl={ov!s:28}{flag}")

main()

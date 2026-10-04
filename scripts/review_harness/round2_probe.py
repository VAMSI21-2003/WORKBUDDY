"""Round 2 probes for fix/values-only-copy (head 8222c7f): cells pandas reads as missing, <dimension>.

  python scripts/review_harness/round2_probe.py [--tree DIR] [--out FILE] [FIXTURE ...]

Imports values_only_probe (same directory), adds fixtures aimed at the round-1 fix, and runs each as
dedupe / empty / analyze_only / chain x template, friend, focus. Prints replies and checks.
"""
import re
import sys
import json
import time

_a = sys.argv[1:]
_fwd, _only, _out = [], [], "round2_results.json"
i = 0
while i < len(_a):
    if _a[i] == "--tree":
        _fwd = ["--tree", _a[i + 1]]; i += 2
    elif _a[i] == "--out":
        _out = _a[i + 1]; i += 2
    else:
        _only.append(_a[i]); i += 1
sys.argv = [sys.argv[0]] + _fwd + ["--out", _out]
import values_only_probe as V  # noqa: E402

FX = V.FX
base_book, patch_sheet = V.base_book, V.patch_sheet


def _sub(pattern, repl):
    return lambda x: re.sub(pattern, repl, x)


@V.fx("r2_na_header_only")  # header cells "NA"/"null", data fine
def _(p):
    wb, ws = base_book(("NA", "null")); wb.save(p)


@V.fx("r2_formula_text_na")  # formula whose saved result is the text N/A (t="str")
def _(p):
    wb, ws = base_book(("name", "amount", "lookup"))
    for i in (2, 3, 4, 6):
        ws[f"C{i}"] = f'=IF(B{i}>15,"N/A","ok")'
    wb.save(p)
    def f(x):
        for c, v in {"C2": "ok", "C3": "N/A", "C4": "ok", "C6": "N/A"}.items():
            x = re.sub(rf'(<c r="{c}"[^>]*?)(?: t="[a-z]+")?(>\s*<f>[^<]*</f>)<v\s*/>', rf'\g<1> t="str"\g<2><v>{v}</v>', x)
        return x
    patch_sheet(p, f)


@V.fx("r2_num_nan")  # numeric cell whose <v> is nan
def _(p):
    wb, ws = base_book(); wb.save(p)
    patch_sheet(p, _sub(r'(<c r="B3"[^>]*>)<v>20</v>', r"\g<1><v>nan</v>"))


@V.fx("r2_ws_na")  # " NA", "NA ", "Na" : pandas does not read these as missing
def _(p):
    wb, ws = base_book(("code", "note"), rows=[(" NA", "NA "), ("Na", "nA"), ("n/a.", "NAN")]); wb.save(p)


@V.fx("r2_inline_na")  # inline string NA and rich-text N/A
def _(p):
    wb, ws = base_book(("code", "note"), rows=[("US", "x"), ("DE", "y")]); wb.save(p)
    def f(x):
        x = x.replace('<c r="B2" t="s"><v>', '<c r="B2" t="inlineStr"><is><r><t>N</t></r><r><t>/A</t></r></is></c><!--', 1)
        return x
    # simpler: rewrite B2 as inline rich text
    import zipfile
    with zipfile.ZipFile(p) as z:
        xml = z.read("xl/worksheets/sheet1.xml").decode()
    m = re.search(r'<c r="B2"[^>]*>.*?</c>', xml)
    xml = xml.replace(m.group(0), '<c r="B2" t="inlineStr"><is><r><t>N</t></r><r><t>/A</t></r></is></c>')
    m = re.search(r'<c r="B3"[^>]*>.*?</c>', xml)
    xml = xml.replace(m.group(0), '<c r="B3" t="inlineStr"><is><t>NA</t></is></c>')
    tmp = str(p) + ".tmp"
    V.rewrite(p, tmp, {"xl/worksheets/sheet1.xml": xml.encode()})
    import os; os.replace(tmp, p)


@V.fx("r2_error_header")  # an error value in the header row
def _(p):
    wb, ws = base_book(("name", "amount")); wb.save(p)
    patch_sheet(p, lambda x: re.sub(r'<c r="B1"[^>]*>.*?</c>', '<c r="B1" t="e"><v>#REF!</v></c>', x))


@V.fx("r2_error_in_empty_row")  # an error value alone in the 'empty' row (row 5, in order)
def _(p):
    wb, ws = base_book(); wb.save(p)
    def f(x):
        out = x.replace('<row r="6"', '<row r="5"><c r="B5" t="e"><v>#N/A</v></c></row><row r="6"', 1)
        assert '<row r="5"><c r="B5"' in out
        return out
    patch_sheet(p, f)


@V.fx("r2_huge_dim")  # dimension claims the whole grid
def _(p):
    wb, ws = base_book(); wb.save(p)
    patch_sheet(p, _sub(r'<dimension ref="[^"]*"', '<dimension ref="A1:XFD1048576"'))


@V.fx("r2_ragged")  # sparse rows, correct dimension, trailing empty column in dimension
def _(p):
    wb, ws = base_book(("a", "b", "c"), rows=[("x", None, 1), ("x", None, 1), (None, "q", None), ("y", None, None)])
    wb.save(p)


@V.fx("r2_wrongdim_small_na")  # NA text outside the stated dimension
def _(p):
    wb, ws = base_book(); ws["B4"] = "N/A"; wb.save(p)
    patch_sheet(p, _sub(r'<dimension ref="[^"]*"', '<dimension ref="A1:B2"'))


@V.fx("r2_dim_missing_na")  # no <dimension> at all and NA text
def _(p):
    wb, ws = base_book(); ws["B4"] = "NULL"; wb.save(p)
    patch_sheet(p, _sub(r'<dimension ref="[^"]*"\s*/>', ''))


@V.fx("r2_formula_error_unsaved_mix")  # formulas saved with numbers only + one error elsewhere in a hidden row
def _(p):
    FX["formulas_saved"](p)
    patch_sheet(p, _sub(r'(<c r="C6")([^>]*>)(<f>[^<]*</f>)<v>60</v>', r'\1 t="e"\2\3<v>#DIV/0!</v>'))


def main():
    names = _only or [n for n in FX if n.startswith("r2_") or n in ("plain", "na_strings", "na_error_results", "gap_dim_a1", "formulas_wrong_dim", "formulas_saved", "wrong_dimension", "numeric_headers", "extras")]
    res = []
    for fx in names:
        for req in V.REQS:
            for sp in ("template", "friend", "focus"):
                sids = ["s1", "s2"] if sp == "friend" else ["session-1"]
                for sid in sids:
                    rec = V.run_one(fx, req, sp, sid)
                    res.append(rec)
    V.OUT.write_text(json.dumps(res, indent=1, default=str))
    bad = [r for r in res if r.get("orig_unchanged") is False]
    print(len(res), "turns; original changed in", len(bad), "; errors", sum("error" in r for r in res),
          "; voice calls", sum(r.get("voice_calls", 0) or 0 for r in res))


if __name__ == "__main__":
    t = time.time(); main(); print("%.0fs" % (time.time() - t))

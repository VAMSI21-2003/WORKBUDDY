# ruff: noqa: E501
"""Probe A: the two readings (skill's `extract`, Inspector's `verify`) on crafted PDFs.

For each PDF: what each reading returned, whether they agree (the verifier passes only then), and
how the agreed cells compare with the text that was actually drawn. A case where both agree but the
cells differ from what was drawn is a delivered file the reply would call the PDF's text.

    python probe_a_worker.py [case ...]      # output: out/probe_a.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

import pdfs  # noqa: E402
from skills.pdf import sandbox  # noqa: E402

BASE = [["Name", "Qty"], ["Apple", "10"], ["Pear", "3"]]
UNI = [["Item", "Size"], ["Pipe", "2½ in"], ["Room", "12 m²"], ["Power", "10³ W"], ["Brand", "Acme™"], ["Step", "①"], ["Unit", "µg"], ["Code", "Ⅳ"], ["Width", "１２"], ["Note", "ﬁle"]]


def cases():
    f = pdfs._font()
    yield "baseline", pdfs.grid_pdf(BASE), BASE
    yield "unicode_nfkc", pdfs.grid_pdf(UNI, font=f), UNI
    yield "winansi_half_sup", pdfs.canvas_grid([["Size", "Area"], ["2½", "12 m²"], ["¼", "3³"]]), [["Size", "Area"], ["2½", "12 m²"], ["¼", "3³"]]
    yield "page_rotate_90", pdfs.canvas_grid(BASE, rotate_page=90), BASE
    yield "landscape_content_rotated", pdfs.canvas_grid(BASE, rotate_page=90, rotate_content=True), BASE
    yield "invisible_text_cell", pdfs.canvas_grid([["Name", "Qty"], ["Apple", "10"], ["Pear", "SECRET"]], invisible={(2, 1)}), [["Name", "Qty"], ["Apple", "10"], ["Pear", ""]]
    yield "overflow_text", pdfs.canvas_grid([["Name", "Qty"], ["Supercalifragilistic-expialidocious", "10"], ["Pear", "3"]], cell_w=90), [["Name", "Qty"], ["Supercalifragilistic-expialidocious", "10"], ["Pear", "3"]]
    yield "double_spaces", pdfs.canvas_grid([["Name", "Code"], ["A  B", "x   y"], ["C", "z"]]), [["Name", "Code"], ["A  B", "x   y"], ["C", "z"]]
    yield "formula_text", pdfs.canvas_grid([["Name", "F"], ["a", "=SUM(A1:A2)"], ["b", "+1"], ["c", "-2"]]), [["Name", "F"], ["a", "=SUM(A1:A2)"], ["b", "+1"], ["c", "-2"]]
    yield "empty_last_row_col", pdfs.canvas_grid([["Name", "Qty", ""], ["Apple", "10", ""], ["", "", ""]]), [["Name", "Qty", ""], ["Apple", "10", ""], ["", "", ""]]
    yield "leading_zeros", pdfs.canvas_grid([["Id", "Amt"], ["007", "1,234.50"], ["0012", "2.50"]]), [["Id", "Amt"], ["007", "1,234.50"], ["0012", "2.50"]]
    yield "dejavu_cjk_hebrew", pdfs.grid_pdf([["Name", "Word"], ["he", "שלום"], ["ar", "مرحبا"], ["gr", "Ωμέγα"]], font=f), [["Name", "Word"], ["he", "שלום"], ["ar", "مرحبا"], ["gr", "Ωμέγα"]]
    def fake_bold(c):  # the header drawn a second time, 0.3 pt to the right ("fake bold")
        c.setFont("Helvetica", 10)
        c.drawString(50 + 4.3, 780 - 20 + 6, "Name")
        c.drawString(140 + 4.3, 780 - 20 + 6, "Qty")
    yield "fake_bold_header", pdfs.canvas_grid(BASE, extra=fake_bold), BASE

    def exact_overprint(c):  # a cell's text drawn twice in exactly the same place
        c.setFont("Helvetica", 10)
        c.drawString(50 + 4, 780 - 40 + 6, "Apple")
    yield "exact_overprint", pdfs.canvas_grid(BASE, extra=exact_overprint), BASE

    def white_box(c):  # a white box drawn over a cell's text (a failed redaction)
        c.setFillColorRGB(1, 1, 1)
        c.rect(141, 721, 88, 18, stroke=0, fill=1)
    yield "covered_text", pdfs.canvas_grid([["Name", "Qty"], ["Apple", "10"], ["Pear", "HIDDEN"]], extra=white_box), [["Name", "Qty"], ["Apple", "10"], ["Pear", ""]]
    from reportlab.lib import colors
    zebra = [("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.lightgrey, colors.white])]
    yield "zebra_no_grid", pdfs.grid_pdf(BASE, style_extra=zebra), BASE
    # A frame round the table and a rule under each row, no rules between columns (a common report style).
    frame = pdfs.grid_pdf(BASE, style_extra=())  # placeholder replaced below
    import io as _io
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle
    buf = _io.BytesIO()
    t = Table([list(r) for r in BASE], hAlign="LEFT")
    t.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.5, colors.black), ("LINEBELOW", (0, 0), (-1, -1), 0.5, colors.black)]))
    SimpleDocTemplate(buf).build([t])
    frame = buf.getvalue()
    yield "frame_and_row_rules_only", frame, BASE
    lo_rows = [["Name", "Qty", "Price"], ["Apple", "10", "2.50"], ["Pear", "3", "10.00"], ["Apple", "10", "2.50"], ["Plum", "7", "1.25"]]
    for kind in ("calc", "writer"):
        data = pdfs.libreoffice_pdf(lo_rows, kind=kind)
        if data:
            yield f"libreoffice_{kind}", data, lo_rows
    lo_uni = [["Item", "Size"], ["Pipe", "2½ in"], ["Room", "12 m²"], ["Note", "café – naïve"]]
    data = pdfs.libreoffice_pdf(lo_uni, kind="calc")
    if data:
        yield "libreoffice_calc_unicode", data, lo_uni
    for name, rows in (
        ("libreoffice_calc_last_col_overflow", [["Name", "Comment"], ["Alice", "Paid in full on delivery"], ["Bob", "Pending"]]),
        ("libreoffice_calc_mid_col_overflow", [["Name", "Note", "Qty"], ["Alice", "a fairly long note that will not fit", "10"], ["Bob", "short", "3"]]),
        ("libreoffice_calc_mid_col_overflow_next_empty", [["Name", "Note", "Qty"], ["Alice", "a fairly long note that will not fit", ""], ["Bob", "short", "3"]]),
    ):
        data = pdfs.libreoffice_pdf(rows, kind="calc")
        if data:
            yield name, data, rows
    multi = [["Name", "Address"], ["Alice", "1 Main St, Springfield, a long address that wraps"], ["Bob", "2 High St"]]
    data = pdfs.libreoffice_pdf(multi, kind="writer")
    if data:
        yield "libreoffice_writer_wrapped", data, multi


def main() -> None:
    only = sys.argv[1:]
    results = []
    for name, data, drawn in cases():
        if only and not any(name.startswith(o) for o in only):
            continue
        ex = sandbox.run("extract", data)
        ve = sandbox.run("verify", data)
        ex_rows = ex.data.get("rows") if ex.ok else None
        ve_rows = ve.data.get("rows") if ve.ok else None
        agree = ex.ok and ve.ok and ve.data.get("tables") == 1 and ve.data.get("plain") is True and ex_rows == ve_rows
        drawn_norm = [[" ".join(c.split()) for c in r] for r in drawn]
        differs = None
        if agree:
            differs = [(r, c, drawn_norm[r][c], ex_rows[r][c]) for r in range(min(len(drawn_norm), len(ex_rows))) for c in range(min(len(drawn_norm[0]), len(ex_rows[0]))) if drawn_norm[r][c] != ex_rows[r][c]]
        rec = {"case": name, "extract": ex.reason or "ok", "verify": ve.reason or ("ok tables=%s plain=%s" % (ve.data.get("tables"), ve.data.get("plain"))),
               "agree(verifier would pass)": agree, "extract_rows": ex_rows, "verify_rows": ve_rows,
               "agreed_but_not_what_was_drawn": differs}
        results.append(rec)
        print(json.dumps({k: rec[k] for k in ("case", "extract", "verify", "agree(verifier would pass)", "agreed_but_not_what_was_drawn")}, ensure_ascii=False))
        if not agree:
            print("   extract:", json.dumps(ex_rows, ensure_ascii=False)[:300])
            print("   verify: ", json.dumps(ve_rows, ensure_ascii=False)[:300])
    out = HERE / "out" / "probe_a.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(results, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()

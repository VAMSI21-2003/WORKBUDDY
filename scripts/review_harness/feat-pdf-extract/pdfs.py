# ruff: noqa: E501
"""PDF builders for the feat/pdf-extract review probes. Reviewer tooling, not product code.

Every PDF is real (reportlab, LibreOffice, or hand-written bytes). Nothing here imports product code
except through `tests/pdf_fixtures.py` (read, not edited).
"""
from __future__ import annotations

import io
import shutil
import subprocess
import tempfile
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle

DEJAVU = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
_registered = False


def _font() -> str:
    global _registered
    if not _registered:
        pdfmetrics.registerFont(TTFont("DejaVu", DEJAVU))
        _registered = True
    return "DejaVu"


def grid_pdf(rows, *, font=None, pagesize=A4, style_extra=(), col_widths=None) -> bytes:
    """One ruled table (GRID on every cell) built by reportlab platypus."""
    buf = io.BytesIO()
    style = [("GRID", (0, 0), (-1, -1), 0.5, colors.black), *style_extra]
    if font:
        style.append(("FONTNAME", (0, 0), (-1, -1), font))
    t = Table([list(r) for r in rows], colWidths=col_widths, hAlign="LEFT")
    t.setStyle(TableStyle(style))
    SimpleDocTemplate(buf, pagesize=pagesize).build([t])
    return buf.getvalue()


def canvas_grid(rows, *, rotate_page=0, rotate_content=False, cell_w=90, cell_h=20, x0=50, y0=780,
                font="Helvetica", size=10, text_dx=4, invisible=None, overflow=None, extra=None) -> bytes:
    """A grid drawn by hand on a canvas. ``rotate_content`` draws it rotated 90 degrees (a landscape
    table on a portrait page) and sets /Rotate so a viewer shows it upright."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    if rotate_page:
        c.setPageRotation(rotate_page)
    if rotate_content:
        c.translate(A4[0], 0)
        c.rotate(90)
        y0 = A4[0] - 60
    nr, nc = len(rows), len(rows[0])
    c.setLineWidth(0.5)
    for j in range(nr + 1):
        c.line(x0, y0 - j * cell_h, x0 + nc * cell_w, y0 - j * cell_h)
    for i in range(nc + 1):
        c.line(x0 + i * cell_w, y0, x0 + i * cell_w, y0 - nr * cell_h)
    c.setFont(font, size)
    for j, row in enumerate(rows):
        for i, text in enumerate(row):
            mode = 3 if invisible and (j, i) in invisible else 0
            t = c.beginText(x0 + i * cell_w + text_dx, y0 - (j + 1) * cell_h + 6)
            t.setTextRenderMode(mode)
            t.setFont(font, size)
            t.textOut(text)
            c.drawText(t)
    if extra:
        extra(c)
    c.showPage()
    c.save()
    return buf.getvalue()


def libreoffice_pdf(rows, *, kind="calc") -> bytes | None:
    """Export a ruled table from LibreOffice (calc: an .xlsx with borders; writer: an .html table)."""
    if shutil.which("soffice") is None:
        return None
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        if kind == "calc":
            import openpyxl
            from openpyxl.styles import Border, Side
            wb = openpyxl.Workbook()
            ws = wb.active
            thin = Side(style="thin")
            for r, row in enumerate(rows, 1):
                for c, v in enumerate(row, 1):
                    cell = ws.cell(r, c, v)
                    cell.data_type = "s"
                    cell.border = Border(left=thin, right=thin, top=thin, bottom=thin)
            src = d / "t.xlsx"
            wb.save(src)
        else:
            cells = "".join("<tr>" + "".join(f"<td>{v}</td>" for v in row) + "</tr>" for row in rows)
            src = d / "t.html"
            src.write_text(f"<html><body><table border='1' cellspacing='0'>{cells}</table></body></html>", encoding="utf-8")
        target = "pdf:writer_pdf_Export" if kind != "calc" else "pdf"
        subprocess.run(["soffice", "--headless", "--convert-to", target, "--outdir", str(d), str(src)],
                       capture_output=True, timeout=120, env={"HOME": str(d), "PATH": "/usr/bin:/bin"})
        out = d / (src.stem + ".pdf")
        return out.read_bytes() if out.exists() else None

"""PDFs for the PDF skill's tests: real tables built with reportlab, and hostile ones by hand.

Nothing here is a stand-in for the parsers: every PDF is a real file that pdfplumber and PDFium
read. The hostile builders write the bytes themselves (no PDF library), so they can make files no
generator would: a page tree a hundred thousand levels deep, a stream that inflates to gigabytes, a
cross-reference table that points nowhere.
"""

from __future__ import annotations

import zlib
from collections.abc import Sequence
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.pdfgen import canvas
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

Rows = Sequence[Sequence[str]]

# A table with a duplicate row, for the "now remove the duplicates" follow-up.
FRUIT = [
    ["Name", "Qty", "Price"],
    ["Apple", "10", "2.50"],
    ["Pear", "3", "10.00"],
    ["Apple", "10", "2.50"],
    ["Plum", "7", "1.25"],
]


def _grid_table(rows: Rows, spans: Sequence[tuple[tuple[int, int], tuple[int, int]]] = ()) -> Table:
    style = [("GRID", (0, 0), (-1, -1), 0.5, colors.black)]
    style += [("SPAN", start, stop) for start, stop in spans]
    table = Table([list(row) for row in rows], hAlign="LEFT")
    table.setStyle(TableStyle(style))
    return table


def write_table_pdf(
    path: Path,
    rows: Rows = FRUIT,
    *,
    spans: Sequence[tuple[tuple[int, int], tuple[int, int]]] = (),
    title: str | None = "Report",
    text_pages_after: int = 0,
    password: tuple[str, str] | None = None,
) -> Path:
    """A PDF whose page 1 has a title (text) and one ruled table; optional text-only pages after.

    ``password`` is ``(user_password, owner_password)``; an empty user password still encrypts the
    file (it opens without a password but has an ``/Encrypt`` entry).
    """
    from reportlab.lib.pdfencrypt import StandardEncryption

    styles = getSampleStyleSheet()
    story: list = []
    if title:
        story += [Paragraph(title, styles["Title"]), Spacer(1, 12)]
    story.append(_grid_table(rows, spans))
    for number in range(text_pages_after):
        story += [PageBreak(), Paragraph(f"Notes, page {number + 2}.", styles["Normal"])]
    encrypt = StandardEncryption(password[0], ownerPassword=password[1]) if password else None
    SimpleDocTemplate(str(path), pagesize=A4, encrypt=encrypt).build(story)
    return path


def write_two_tables_pdf(path: Path, same_page: bool = True) -> Path:
    """Two ruled tables: both on page 1, or one on each of two pages."""
    story: list = [_grid_table(FRUIT)]
    story.append(Spacer(1, 40) if same_page else PageBreak())
    story.append(_grid_table([["Id", "Colour"], ["1", "Red"], ["2", "Green"]]))
    SimpleDocTemplate(str(path), pagesize=A4).build(story)
    return path


def write_table_across_pages_pdf(path: Path, pages: int = 20) -> Path:
    """A table with a ruled block on every page (``pages`` of them): many tables by the count."""
    story: list = []
    for number in range(pages):
        if number:
            story.append(PageBreak())
        story.append(_grid_table([["Page", "Value"], [str(number + 1), f"v{number + 1}"]]))
    SimpleDocTemplate(str(path), pagesize=A4).build(story)
    return path


def write_text_only_pdf(path: Path) -> Path:
    """Text, no ruled table."""
    normal = getSampleStyleSheet()["Normal"]
    story = [Paragraph("Just a paragraph of text with no table in it.", normal)]
    SimpleDocTemplate(str(path), pagesize=A4).build(story)
    return path


def write_scanned_pdf(path: Path) -> Path:
    """A page that is only a picture of a table: no text layer at all."""
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (400, 200), "white")
    draw = ImageDraw.Draw(image)
    for x in (10, 130, 260, 390):
        draw.line([(x, 10), (x, 190)], fill="black", width=2)
    for y in (10, 70, 130, 190):
        draw.line([(10, y), (390, y)], fill="black", width=2)
    picture = path.with_suffix(".png")
    image.save(picture)
    page = canvas.Canvas(str(path), pagesize=A4)
    page.drawImage(str(picture), 50, 500, width=400, height=200)
    page.save()
    picture.unlink()
    return path


def write_cells_pdf(path: Path, rows: Rows) -> Path:
    """A one-table PDF with no title, for cell-content cases."""
    return write_table_pdf(path, rows, title=None)


# -- Hostile files, written byte by byte -------------------------------------------------------


def build_pdf(
    objects: dict[int, bytes],
    *,
    root: int = 1,
    xref: str = "good",
    header: bytes = b"%PDF-1.4\n",
) -> bytes:
    """A PDF from numbered object bodies (``b"<< /Type /Catalog ... >>"``).

    ``xref`` chooses the cross-reference section: ``"good"`` (correct offsets), ``"wrong"`` (every
    offset off), ``"none"`` (``startxref`` points into the middle of the file), ``"huge"`` (a
    subsection declaring 99999999999 entries) or ``"loop"`` (a ``/Prev`` that points at itself).
    """
    out = bytearray(header)
    offsets: dict[int, int] = {}
    for number in sorted(objects):
        offsets[number] = len(out)
        out += f"{number} 0 obj\n".encode() + objects[number] + b"\nendobj\n"
    size = max(objects) + 1
    start = len(out)
    trailer = f"trailer\n<< /Size {size} /Root {root} 0 R".encode()
    if xref == "good" or xref == "wrong":
        shift = 7 if xref == "wrong" else 0
        out += f"xref\n0 {size}\n".encode()
        out += b"0000000000 65535 f \n"
        for number in range(1, size):
            out += f"{offsets.get(number, 0) + shift:010d} 00000 n \n".encode()
        out += trailer + b" >>\n"
    elif xref == "huge":
        out += b"xref\n0 99999999999\n0000000000 65535 f \n" + trailer + b" >>\n"
    elif xref == "loop":
        out += f"xref\n0 {size}\n".encode() + b"0000000000 65535 f \n"
        for number in range(1, size):
            out += f"{offsets.get(number, 0):010d} 00000 n \n".encode()
        out += trailer + f" /Prev {start} >>\n".encode()
    else:  # "none"
        out += b"xref\nnonsense\n" + trailer + b" >>\n"
        start = max(len(header), start // 2)
    out += f"startxref\n{start}\n%%EOF\n".encode()
    return bytes(out)


def _minimal_pages(count: int = 1, content: bytes = b"") -> dict[int, bytes]:
    objects = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Kids ["
        + b" ".join(f"{3 + i} 0 R".encode() for i in range(count))
        + f"] /Count {count} >>".encode(),
    }
    for i in range(count):
        objects[3 + i] = b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] >>"
    return objects


def hostile_deep_nesting(depth: int = 200_000) -> bytes:
    """An object holding ``depth`` nested arrays, referenced from the catalog."""
    objects = _minimal_pages()
    objects[1] = b"<< /Type /Catalog /Pages 2 0 R /Deep 4 0 R >>"
    objects[4] = b"[" * depth + b"]" * depth
    return build_pdf(objects)


def hostile_deep_page_tree(depth: int = 20_000) -> bytes:
    """A page tree ``depth`` levels deep: each node's only kid is the next node."""
    objects: dict[int, bytes] = {1: b"<< /Type /Catalog /Pages 2 0 R >>"}
    for level in range(depth):
        number = 2 + level
        kid = number + 1
        objects[number] = f"<< /Type /Pages /Kids [{kid} 0 R] /Count 1 >>".encode()
    leaf = 2 + depth
    objects[leaf] = b"<< /Type /Page /MediaBox [0 0 200 200] >>"
    return build_pdf(objects)


def hostile_page_tree_cycle() -> bytes:
    """A page tree whose node lists itself as a kid."""
    objects = _minimal_pages()
    objects[2] = b"<< /Type /Pages /Kids [2 0 R 3 0 R] /Count 2 >>"
    return build_pdf(objects)


def hostile_many_pages(count: int = 25_000, declared: int | None = None) -> bytes:
    """``count`` real (empty) pages; ``/Count`` says ``declared`` (default: the truth)."""
    objects = _minimal_pages(count)
    if declared is not None:
        objects[2] = objects[2].rsplit(b"] /Count", 1)[0] + f"] /Count {declared} >>".encode()
    return build_pdf(objects)


def hostile_lying_page_count(declared: int = 1_000_000) -> bytes:
    """One page; the page tree claims ``declared``."""
    return hostile_many_pages(1, declared=declared)


def hostile_compression_bomb(inflated_bytes: int = 1_500 * 1024 * 1024) -> bytes:
    """A page whose content stream is a FlateDecode stream of ``inflated_bytes`` zeros.

    Zeros deflate about 1000 to 1, so the default (1.5 GiB) is a file of about 1.5 MB.
    """
    compressor = zlib.compressobj(9)
    chunk = b"\x00" * (1 << 20)
    parts = []
    for _ in range(inflated_bytes >> 20):
        parts.append(compressor.compress(chunk))
    parts.append(compressor.flush())
    stream = b"".join(parts)
    objects = _minimal_pages()
    objects[3] = b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Contents 4 0 R >>"
    objects[4] = (
        f"<< /Length {len(stream)} /Filter /FlateDecode >>\nstream\n".encode()
        + stream
        + b"\nendstream"
    )
    return build_pdf(objects)


def hand_table_pdf(
    rows: Rows,
    *,
    active_content: bool = False,
    cell_width: int = 90,
    row_height: int = 20,
    skip_lines: Sequence[str] = (),
) -> bytes:
    """A one-page PDF with a ruled table, written by hand (no library).

    Lines are drawn one by one and each cell's text with the standard Helvetica font.
    ``skip_lines`` leaves out named rules (``"v:1:2"`` is the vertical rule at column edge 1 between
    row edges 2 and 3, ``"h:2:0"`` the horizontal rule on row edge 2 above column 0): a missing
    interior rule is how a merged cell looks in a PDF.

    With ``active_content`` the file also carries what a reader must never act on: an
    ``/OpenAction`` running JavaScript that submits a form to a local address, a ``/Launch``
    action, a ``/URI`` link annotation, and an embedded file.
    """
    n_rows, n_cols = len(rows), len(rows[0])
    x0, y0 = 50, 700
    ops: list[str] = ["0.5 w"]
    for i in range(n_rows + 1):
        for j in range(n_cols):
            if f"h:{i}:{j}" in skip_lines:
                continue
            y = y0 - i * row_height
            ops.append(f"{x0 + j * cell_width} {y} m {x0 + (j + 1) * cell_width} {y} l S")
    for j in range(n_cols + 1):
        for i in range(n_rows):
            if f"v:{j}:{i}" in skip_lines:
                continue
            x = x0 + j * cell_width
            ops.append(f"{x} {y0 - i * row_height} m {x} {y0 - (i + 1) * row_height} l S")
    for i, row in enumerate(rows):
        for j, text in enumerate(row):
            safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            ops.append(
                f"BT /F1 10 Tf {x0 + j * cell_width + 4} {y0 - (i + 1) * row_height + 6} Td "
                f"({safe}) Tj ET"
            )
    content = "\n".join(ops).encode("cp1252")
    objects = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        3: (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
            b"/Resources << /Font << /F1 5 0 R >> >> >>"
        ),
        4: f"<< /Length {len(content)} >>\nstream\n".encode() + content + b"\nendstream",
        5: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    }
    if active_content:
        objects[1] = (
            b"<< /Type /Catalog /Pages 2 0 R /OpenAction 6 0 R "
            b"/Names << /EmbeddedFiles << /Names [(a.txt) 8 0 R] >> >> >>"
        )
        objects[3] = objects[3][:-2] + b" /Annots [7 0 R 9 0 R] >>"
        objects[6] = (
            b"<< /S /JavaScript /JS (app.alert\\(1\\); "
            b"this.submitForm\\('http://127.0.0.1:9/x'\\);) >>"
        )
        objects[7] = (
            b"<< /Type /Annot /Subtype /Link /Rect [0 0 100 100] "
            b"/A << /S /URI /URI (http://127.0.0.1:9/click) >> >>"
        )
        objects[8] = b"<< /Type /Filespec /F (a.txt) /EF << /F 10 0 R >> >>"
        objects[9] = (
            b"<< /Type /Annot /Subtype /Link /Rect [100 0 200 100] "
            b"/A << /S /Launch /F (/bin/sh) >> >>"
        )
        objects[10] = b"<< /Type /EmbeddedFile /Length 5 >>\nstream\nhello\nendstream"
    return build_pdf(objects)

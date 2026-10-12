"""The PDF worker: the only process that opens an untrusted PDF.

Started by ``skills/pdf/sandbox.py`` as ``python -I main.py <mode> <address space> <cpu seconds>
<output bytes>``, with the PDF's bytes on stdin and the answer, one JSON object, on stdout. It puts
the limits and filters of ``lockdown`` on itself before it imports a parser, so everything below
runs inside them. Its answer is data the caller validates; it never contains a message meant for a
person, only a closed ``reason`` code, and never an exception's text (that could hold the file's
words).

Modes:

- ``probe``: PDFium counts the pages and says whether the file is encrypted. Used at upload.
- ``extract``: pdfplumber (built on pdfminer.six) finds the tables and reads the one plain table.
- ``verify``: PDFium and ``geometry`` find the tables and read them again, sharing no code with
  ``extract``. Used by the Inspector.
- ``selftest``: tries what the sandbox must refuse (a socket, a program) and reports it.

Standard library at the top; the parsers are imported inside the modes, after the lockdown.
"""

from __future__ import annotations

import io
import json
import os
import sys

# This file is run by path in isolated mode, so its folder is not on sys.path.
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import geometry  # noqa: E402
import lockdown  # noqa: E402
import textnorm  # noqa: E402

MAX_PAGES = 20
MAX_INPUT_BYTES = 8 * 1024 * 1024
# A page with more drawn lines and shapes than this is not a page of a table report, and finding
# its tables would take longer than a person should wait.
MAX_SHAPES_PER_PAGE = 20_000
# A segment whose ends differ by less than this in one direction counts as straight.
STRAIGHT = 1.0

# The closed set of reasons the worker may refuse for. The caller maps each to a fixed sentence.
ENCRYPTED = "encrypted"
NO_TEXT = "no_text"
NO_TABLE = "no_table"
MANY_TABLES = "many_tables"
NOT_PLAIN = "not_plain"
TOO_MANY_PAGES = "too_many_pages"
TOO_LARGE = "too_large"
UNREADABLE = "unreadable"
SANDBOX = "sandbox_unavailable"


class Refused(Exception):
    """Stop and answer ``refused`` with this reason."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _is_password_error(exc: BaseException | None) -> bool:
    """Whether ``exc`` (or what it wraps) is a parser saying the file needs a password."""
    seen = 0
    while exc is not None and seen < 5:
        if "password" in type(exc).__name__.lower():
            return True
        inner = exc.args[0] if exc.args and isinstance(exc.args[0], BaseException) else None
        exc = inner or exc.__cause__
        seen += 1
    return False


# -- probe: PDFium ------------------------------------------------------------------------


def _open_pdfium(data: bytes):  # noqa: ANN202
    import pypdfium2 as pdfium
    import pypdfium2.raw as raw

    try:
        return pdfium.PdfDocument(data), raw
    except pdfium.PdfiumError as exc:
        if getattr(exc, "err_code", None) == raw.FPDF_ERR_PASSWORD:
            raise Refused(ENCRYPTED) from None
        raise Refused(UNREADABLE) from None


def probe(data: bytes) -> dict:
    try:
        pdf, raw = _open_pdfium(data)
    except Refused as refused:
        if refused.reason == ENCRYPTED:
            return {"pages": None, "encrypted": True}
        raise
    pages = len(pdf)
    if pages < 1:
        raise Refused(UNREADABLE)
    return {"pages": pages, "encrypted": raw.FPDF_GetSecurityHandlerRevision(pdf.raw) != -1}


# -- extract: pdfplumber ------------------------------------------------------------------

_TABLE_SETTINGS = {
    "vertical_strategy": "lines",
    "horizontal_strategy": "lines",
    "snap_tolerance": 3,
    "join_tolerance": 3,
    "edge_min_length": 3,
    "intersection_tolerance": 3,
}


def extract(data: bytes) -> dict:
    import itertools

    import pdfplumber
    from pdfminer.pdfpage import PDFPage

    try:
        pdf = pdfplumber.open(io.BytesIO(data))
    except BaseException as exc:  # noqa: BLE001 - a parser error is a refusal, never text
        if _is_password_error(exc):
            raise Refused(ENCRYPTED) from None
        raise Refused(UNREADABLE) from None
    with pdf:
        if getattr(pdf.doc, "encryption", None) is not None:
            raise Refused(ENCRYPTED)
        counted = sum(1 for _ in itertools.islice(PDFPage.create_pages(pdf.doc), MAX_PAGES + 1))
        if counted > MAX_PAGES:
            raise Refused(TOO_MANY_PAGES)
        if counted == 0:
            raise Refused(UNREADABLE)
        pages = pdf.pages
        if len(pages) != counted:
            raise Refused(UNREADABLE)
        characters = 0
        found = []
        for page in pages:
            characters += len(page.chars)
            if len(page.rects) + len(page.lines) + len(page.curves) > MAX_SHAPES_PER_PAGE:
                raise Refused(UNREADABLE)
            found.extend(
                (page, table)
                for table in page.find_tables(_TABLE_SETTINGS)
                if len(table.cells) >= 2
            )
        if characters == 0:
            raise Refused(NO_TEXT)
        if not found:
            raise Refused(NO_TABLE)
        if len(found) > 1:
            raise Refused(MANY_TABLES)
        _, table = found[0]
        plain, rows, columns = geometry.cells_are_plain_grid(list(table.cells))
        if not plain:
            raise Refused(NOT_PLAIN)
        if rows * columns > textnorm.MAX_CELLS:
            raise Refused(TOO_LARGE)
        grid = table.extract(x_tolerance=3, y_tolerance=3)
        if len(grid) != rows or any(len(row) != columns for row in grid):
            raise Refused(NOT_PLAIN)
        return {"pages": counted, "rows": _clean(grid)}


def _clean(grid: list[list]) -> list[list[str]]:
    cleaned = []
    for row in grid:
        out = []
        for cell in row:
            if not isinstance(cell, str):
                raise Refused(NOT_PLAIN)
            text = textnorm.normalize_cell(cell)
            if len(text) > textnorm.MAX_CELL_CHARS:
                raise Refused(TOO_LARGE)
            out.append(text)
        cleaned.append(out)
    return cleaned


# -- verify: PDFium and geometry ----------------------------------------------------------


def _segments(page, raw):  # noqa: ANN001, ANN202
    """The straight horizontal and vertical segments a page's paths draw, in page space."""
    import ctypes

    h_segments, v_segments = [], []
    x_out, y_out = ctypes.c_float(), ctypes.c_float()
    objects = 0
    for obj in page.get_objects(filter=[raw.FPDF_PAGEOBJ_PATH], max_depth=0):
        objects += 1
        if objects > MAX_SHAPES_PER_PAGE:
            raise Refused(UNREADABLE)
        m = obj.get_matrix()
        points = []
        for index in range(raw.FPDFPath_CountSegments(obj.raw)):
            segment = raw.FPDFPath_GetPathSegment(obj.raw, index)
            raw.FPDFPathSegment_GetPoint(segment, x_out, y_out)
            x, y = x_out.value, y_out.value
            points.append(
                (
                    raw.FPDFPathSegment_GetType(segment),
                    m.a * x + m.c * y + m.e,
                    m.b * x + m.d * y + m.f,
                    bool(raw.FPDFPathSegment_GetClose(segment)),
                )
            )
        start = current = None
        for kind, x, y, closes in points:
            if kind == raw.FPDF_SEGMENT_MOVETO:
                start = current = (x, y)
            elif kind == raw.FPDF_SEGMENT_LINETO and current is not None:
                _record(current, (x, y), h_segments, v_segments)
                current = (x, y)
            else:  # a curve: not a rule
                current = (x, y)
            if closes and start is not None and current is not None:
                _record(current, start, h_segments, v_segments)
                current = start
    return h_segments, v_segments


def _record(a, b, h_segments, v_segments) -> None:  # noqa: ANN001
    if abs(a[1] - b[1]) <= STRAIGHT:
        h_segments.append(((a[1] + b[1]) / 2, a[0], b[0]))
    elif abs(a[0] - b[0]) <= STRAIGHT:
        v_segments.append(((a[0] + b[0]) / 2, a[1], b[1]))


def verify(data: bytes) -> dict:
    pdf, raw = _open_pdfium(data)
    if raw.FPDF_GetSecurityHandlerRevision(pdf.raw) != -1:
        raise Refused(ENCRYPTED)
    pages = len(pdf)
    if pages > MAX_PAGES:
        raise Refused(TOO_MANY_PAGES)
    if pages < 1:
        raise Refused(UNREADABLE)
    characters = 0
    grids = []  # (textpage, grid)
    for index in range(pages):
        page = pdf[index]
        textpage = page.get_textpage()
        characters += textpage.count_chars()
        h_segments, v_segments = _segments(page, raw)
        grids.extend((textpage, grid) for grid in geometry.find_grids(h_segments, v_segments))
    result = {"pages": pages, "text": characters > 0, "tables": len(grids)}
    if len(grids) == 1:
        textpage, grid = grids[0]
        result["plain"] = grid.plain
        if grid.plain:
            if grid.rows * grid.columns > textnorm.MAX_CELLS:
                raise Refused(TOO_LARGE)
            rows = []
            for r in range(grid.rows):
                row = []
                for c in range(grid.columns):
                    left, bottom, right, top = grid.cell_box(r, c)
                    inset = min(1.0, (right - left) / 4, (top - bottom) / 4)
                    text = textpage.get_text_bounded(
                        left + inset, bottom + inset, right - inset, top - inset
                    )
                    text = textnorm.normalize_cell(text)
                    if len(text) > textnorm.MAX_CELL_CHARS:
                        raise Refused(TOO_LARGE)
                    row.append(text)
                rows.append(row)
            result["rows"] = rows
    return result


# -- selftest, and the process -------------------------------------------------------------


def selftest(data: bytes) -> dict:
    return lockdown.selftest()


MODES = {"probe": probe, "extract": extract, "verify": verify, "selftest": selftest}


def _memory_peaks() -> dict:
    """This process's peak resident and peak virtual memory in KiB, from ``/proc``.

    ``VmHWM`` and ``VmPeak`` belong to the process's current address space, which is new at
    ``exec``. ``getrusage`` would not do: a child started from a big parent starts with the
    parent's peak.
    """
    peaks = {}
    try:
        with open("/proc/self/status", encoding="ascii") as status:
            for line in status:
                key, _, rest = line.partition(":")
                if key in ("VmHWM", "VmPeak"):
                    peaks["peak_kb" if key == "VmHWM" else "vm_peak_kb"] = int(rest.split()[0])
    except (OSError, ValueError, IndexError):
        pass
    return peaks


def _emit(result: dict) -> None:
    result.update(_memory_peaks())
    sys.stdout.write(json.dumps(result))
    sys.stdout.flush()


def main(argv: list[str]) -> int:
    try:
        mode = argv[1]
        limits = lockdown.Limits(int(argv[2]), int(argv[3]), int(argv[4]))
        function = MODES[mode]
    except (IndexError, KeyError, ValueError):
        _emit({"status": "refused", "reason": UNREADABLE})
        return 2
    data = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    try:
        lockdown.apply(limits)
    except lockdown.LockdownUnavailable:
        _emit({"status": "refused", "reason": SANDBOX})
        return 0
    if len(data) > MAX_INPUT_BYTES:
        _emit({"status": "refused", "reason": TOO_LARGE})
        return 0
    try:
        _emit({"status": "ok", "data": function(data)})
    except Refused as refused:
        _emit({"status": "refused", "reason": refused.reason})
    except MemoryError:
        _emit({"status": "refused", "reason": TOO_LARGE})
    except BaseException:  # noqa: BLE001 - never the exception's text
        _emit({"status": "refused", "reason": UNREADABLE})
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

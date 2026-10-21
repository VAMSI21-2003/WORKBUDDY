"""Round 2: workbooks aimed at the round 1 fix of check_workbook_limits (grid, row numbers, every part).

For each file: does check_workbook_limits (of --tree) refuse it, and, if it passes, what grid does one
pandas read build (in a child process capped at --cap-mib of address space, so a bomb can't take the
machine). Run from the repo root with the image's parser:

    OPENPYXL_LXML=False python scripts/review_harness/fix-security-pass/probe_k_round2.py [--tree DIR] [--read] [--only K1,K2]

Files are written to out/k_*.xlsx (probe_l_turns.py runs whole turns on them).
"""
import argparse
import os
import subprocess
import sys
import zipfile
from pathlib import Path

from openpyxl import Workbook
from openpyxl.utils import get_column_letter

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
OUT.mkdir(exist_ok=True)
sys.path.insert(0, str(HERE))
from make_sparse_bomb import build  # noqa: E402

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def parts_of(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as z:
        return {i.filename: z.read(i.filename) for i in z.infolist()}


def write(path: Path, parts: dict[str, bytes], first: dict[str, bytes] | None = None) -> None:
    """``first`` parts are written ahead of the others (zip order is the scan's order)."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in {**(first or {}), **parts}.items():
            z.writestr(name, data)


def grid_bomb(path: Path, rows: int = 20_000, cols: int = 1000) -> None:
    """1,000 headers in row 1 and one cell in row ``rows``: refused on head by the grid check."""
    build(str(path), rows, cols, full_header=True)


def k1_row_r_mismatch(path: Path, rows: int) -> None:
    """Header row of 1,000 cells; then <row r=ROWS> holding a cell whose own reference says A1.

    pandas reads with openpyxl read_only, which places a row's cells at the <row r>, not at the
    cell's reference; the grid check counts only the cell's reference.
    """
    header = "".join(
        f'<c r="{get_column_letter(i)}1" t="inlineStr"><is><t>h{i}</t></is></c>' for i in range(1, 1001)
    )
    sheet = (f'<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="{NS}"><sheetData>'
             f'<row r="1">{header}</row><row r="{rows}"><c r="A1"><v>1</v></c></row>'
             "</sheetData></worksheet>")
    grid_bomb(path)
    p = parts_of(path)
    p["xl/worksheets/sheet1.xml"] = sheet.encode()
    write(path, p)


def with_first_part(path: Path, name: str, data: bytes) -> None:
    grid_bomb(path)
    write(path, parts_of(path), first={name: data})


def corrupt_first_part(path: Path) -> None:
    """An unreferenced part, first in the zip, whose deflate stream is damaged."""
    with_first_part(path, "xl/aaa.xml", b"<a>" + b"x" * 4000 + b"</a>")
    raw = bytearray(path.read_bytes())
    with zipfile.ZipFile(path) as z:
        info = z.getinfo("xl/aaa.xml")
    start = info.header_offset + 30 + len(info.filename.encode()) + len(info.extra)
    for i in range(start + 2, start + 12):
        raw[i] ^= 0xFF
    path.write_bytes(bytes(raw))


def other_extension(path: Path, new: str) -> None:
    """The grid-bomb sheet kept at a part name that doesn't end .xml (the rels point at it)."""
    grid_bomb(path)
    p = parts_of(path)
    sheet = p.pop("xl/worksheets/sheet1.xml")
    for key in list(p):
        p[key] = p[key].replace(b"worksheets/sheet1.xml", new.removeprefix("xl/").encode())
    p[new] = sheet
    write(path, p)


def calc_chain(path: Path, rows: int, far_col: int) -> None:
    """An ordinary two-sheet workbook with formulas, plus the calcChain.xml Excel writes for it.

    Sheet1: ``rows`` rows, A a number, B =A*2. Sheet2: one formula at column ``far_col`` row 1.
    Each sheet's own grid is small; calcChain lists every formula cell of both sheets in one part.
    """
    wb = Workbook()
    ws = wb.active
    ws.append(["n", "double"])
    for r in range(2, rows + 2):
        ws.append([r, f"=A{r}*2"])
    ws2 = wb.create_sheet("Summary")
    ws2.cell(1, far_col, "=SUM(Sheet1!A:A)")
    wb.save(path)
    entries = [f'<c r="B{r}" i="1"/>' if r == 2 else f'<c r="B{r}"/>' for r in range(2, rows + 2)]
    entries.append(f'<c r="{get_column_letter(far_col)}1" i="2"/>')
    p = parts_of(path)
    p["xl/calcChain.xml"] = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                             f'<calcChain xmlns="{NS}">{"".join(entries)}</calcChain>').encode()
    p["[Content_Types].xml"] = p["[Content_Types].xml"].replace(
        b"</Types>",
        b'<Override PartName="/xl/calcChain.xml" ContentType="application/vnd.openxmlformats-'
        b'officedocument.spreadsheetml.calcChain+xml"/></Types>')
    write(path, p)


def formulas_one_sheet(path: Path, rows: int) -> None:
    """One sheet: ``rows`` rows of A number, B =A*2 (2*rows cells), and its calcChain (rows more)."""
    calc_chain(path, rows, 2)


def long_shared_string(path: Path, rows: int = 1000, chars: int = 10_000_000) -> None:
    """Every row's cell points at one shared string of ``chars`` characters (tiny once zipped)."""
    grid_bomb(path)
    p = parts_of(path)
    body = "".join(f'<row r="{r}"><c r="A{r}" t="s"><v>1</v></c><c r="B{r}"><v>{r}</v></c></row>'
                   for r in range(2, rows + 2))
    p["xl/worksheets/sheet1.xml"] = (
        f'<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="{NS}"><sheetData><row r="1">'
        '<c r="A1" t="s"><v>0</v></c><c r="B1" t="inlineStr"><is><t>n</t></is></c></row>'
        f"{body}</sheetData></worksheet>").encode()
    p["xl/sharedStrings.xml"] = (f'<sst xmlns="{NS}"><si><t>text</t></si><si><t>'
                                 + "a" * chars + "</t></si></sst>").encode()
    p["xl/_rels/workbook.xml.rels"] = p["xl/_rels/workbook.xml.rels"].replace(
        b"</Relationships>",
        b'<Relationship Id="rId9" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
        b'relationships/sharedStrings" Target="sharedStrings.xml"/></Relationships>')
    p["[Content_Types].xml"] = p["[Content_Types].xml"].replace(
        b"</Types>",
        b'<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-'
        b'officedocument.spreadsheetml.sharedStrings+xml"/></Types>')
    write(path, p)


def ordinary(path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.append(["name", "amount"])
    for r in range(50):
        ws.append([f"n{r % 40}", r % 40])
    wb.save(path)


CASES = {
    "K0_ordinary_control": ordinary,
    "K0_grid_bomb_control": grid_bomb,
    "K1_row_r_mismatch_20k": lambda p: k1_row_r_mismatch(p, 20_000),
    "K1_row_r_mismatch_150k": lambda p: k1_row_r_mismatch(p, 150_000),
    "K2_malformed_part_first": lambda p: with_first_part(p, "xl/aaa.xml", b"<a>"),
    "K2_vml_br_first": lambda p: with_first_part(
        p, "xl/drawings/vmlDrawing1.vml",
        b'<xml xmlns:v="urn:schemas-microsoft-com:vml"><v:shape><div>note<br></div></v:shape></xml>'),
    "K3_long_digit_ref_first": lambda p: with_first_part(
        p, "xl/aaa.xml", f'<x xmlns="{NS}"><c r="A{"1" * 5000}"/></x>'.encode()),
    "K4_corrupt_deflate_first": corrupt_first_part,
    "K5_sheet_as_bin": lambda p: other_extension(p, "xl/worksheets/sheet1.bin"),
    "K5_sheet_no_extension": lambda p: other_extension(p, "xl/worksheets/sheet1"),
    "K6_calcchain_two_sheets_60k": lambda p: calc_chain(p, 60_000, 26),
    "K6_formulas_one_sheet_340k": lambda p: formulas_one_sheet(p, 340_000),
    "K7_long_shared_string": long_shared_string,
}

READ = r"""
import resource, sys, time
cap = int(sys.argv[2]) * 1024 * 1024
resource.setrlimit(resource.RLIMIT_AS, (cap, cap))
import pandas as pd
t = time.time()
try:
    df = pd.read_excel(sys.argv[1], dtype=object)
    shape = df.shape
except MemoryError:
    shape = "MemoryError"
except Exception as e:
    shape = type(e).__name__
print(shape, f"{resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024:.0f}MiB", f"{time.time() - t:.1f}s")
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tree", default=str(HERE.parents[2]))
    ap.add_argument("--read", action="store_true")
    ap.add_argument("--cap-mib", type=int, default=3072)
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    sys.path.insert(0, a.tree)
    from skills.excel.workbook import check_workbook_limits

    only = set(filter(None, a.only.split(",")))
    for name, make in CASES.items():
        if only and name.split("_")[0] not in only and name not in only:
            continue
        path = OUT / f"k_{name}.xlsx"
        if not path.exists():
            make(path)
        try:
            check_workbook_limits(str(path))
            verdict = "passes"
        except Exception as e:  # noqa: BLE001
            verdict = f"refused ({type(e).__name__}: {e})"
        line = f"{name:30s} {path.stat().st_size:9d} B  {verdict}"
        if a.read and verdict == "passes":
            env = dict(os.environ, OPENPYXL_LXML="False")
            r = subprocess.run([sys.executable, "-c", READ, str(path), str(a.cap_mib)],
                               capture_output=True, text=True, env=env, timeout=1200)
            line += f"  | pandas read: {(r.stdout.strip() or r.stderr.strip()[-200:])}"
        print(line, flush=True)


if __name__ == "__main__":
    main()

"""Odd zip and XML layouts against check_workbook_limits, then pandas and a whole turn.

Cases: a worksheet stored outside xl/worksheets/ (the rels say where it is); a corrupt deflate stream;
an unsupported compression method; a sheet whose XML expat can't finish. Run from the repo root:

    python scripts/review_harness/fix-security-pass/probe_f_odd_zips.py [--tree BASE] [--turns]
"""
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
TURNS = "--turns" in sys.argv
sys.argv = [a for a in sys.argv if a != "--turns"]
sys.path.insert(0, str(HERE.parent / "feat-reply-quality"))
sys.path.insert(0, str(HERE))
import r5lib  # noqa: E402
from make_sparse_bomb import build  # noqa: E402

A = r5lib.args()
rq = r5lib.start(A)
from rq import brain  # noqa: E402

pd = rq.T["pd"]
OUT = HERE / "out"
OUT.mkdir(exist_ok=True)


def repack(src: Path, dst: Path, rename=None, compress=None, mangle=None):
    with zipfile.ZipFile(src) as z:
        parts = {i.filename: z.read(i.filename) for i in z.infolist()}
    out = {}
    for name, data in parts.items():
        if rename:
            name = rename.get(name, name)
            for old, new in rename.items():
                data = data.replace(old.encode(), new.encode())
        out[name] = data
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in out.items():
            z.writestr(name, data)
    if mangle:
        mangle(dst)


def dense(path: Path, rows: int):
    """``rows`` rows of one numbered cell each (dense: every row is real)."""
    build(str(path), 2, 1)
    with zipfile.ZipFile(path) as z:
        parts = {i.filename: z.read(i.filename) for i in z.infolist()}
    body = "".join(f'<row r="{r}"><c r="A{r}"><v>{r}</v></c></row>' for r in range(2, rows + 2))
    parts["xl/worksheets/sheet1.xml"] = (
        '<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/'
        'spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>n</t></is></c>'
        f"</row>{body}</sheetData></worksheet>").encode()
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in parts.items():
            z.writestr(name, data)


def corrupt_deflate(path: Path):
    raw = bytearray(path.read_bytes())
    with zipfile.ZipFile(path) as z:
        info = z.getinfo("xl/worksheets/sheet1.xml")
    start = info.header_offset + 30 + len(info.filename.encode()) + len(info.extra)
    for i in range(start + 5, start + 25):
        raw[i] ^= 0xFF
    path.write_bytes(bytes(raw))


def method_99(path: Path):
    raw = bytearray(path.read_bytes())
    name = b"xl/worksheets/sheet1.xml"
    # local header: compression method at offset 8; central directory: at offset 10
    for sig, off in ((b"PK\x03\x04", 8), (b"PK\x01\x02", 10)):
        pos = 0
        while (pos := raw.find(sig, pos)) != -1:
            nlen_off = 26 if sig == b"PK\x03\x04" else 28
            nlen = int.from_bytes(raw[pos + nlen_off:pos + nlen_off + 2], "little")
            fname_off = 30 if sig == b"PK\x03\x04" else 46
            if raw[pos + fname_off:pos + fname_off + nlen] == name:
                raw[pos + off:pos + off + 2] = (99).to_bytes(2, "little")
            pos += 4
    path.write_bytes(bytes(raw))


CASES = {}
base300k = OUT / "odd_dense_300k.xlsx"
dense(base300k, 300_000)
CASES["dense-300k-rows-standard-path (control)"] = base300k
p = OUT / "odd_dense_300k_elsewhere.xlsx"
repack(base300k, p, rename={"xl/worksheets/sheet1.xml": "xl/data/sheet1.xml", "worksheets/sheet1.xml": "data/sheet1.xml"})
CASES["dense-300k-rows-sheet-outside-xl/worksheets"] = p
small = OUT / "odd_small.xlsx"
build(str(small), 3, 2, full_header=True)
p = OUT / "odd_corrupt_deflate.xlsx"
repack(small, p, mangle=corrupt_deflate)
CASES["corrupt-deflate-in-sheet"] = p
p = OUT / "odd_method99.xlsx"
repack(small, p, mangle=method_99)
CASES["compression-method-99"] = p

from skills.excel.workbook import check_workbook_limits, sheet_names  # noqa: E402

for label, path in CASES.items():
    try:
        check_workbook_limits(str(path))
        lim = "passes"
    except Exception as e:  # noqa: BLE001
        lim = f"{type(e).__name__}: {str(e)[:70]}"
    try:
        sn = sheet_names(str(path))
    except Exception as e:  # noqa: BLE001
        sn = f"{type(e).__name__}: {str(e)[:70]}"
    try:
        shape = pd.read_excel(path, dtype=object).shape
    except Exception as e:  # noqa: BLE001
        shape = f"{type(e).__name__}"
    print(f"{label:46} limits={lim!s:40} sheet_names={sn!s:40} pandas_shape={shape}")
    if TURNS:
        spec = dict(file=lambda dst, src=path: dst.write_bytes(src.read_bytes()),
                    turns=[dict(msg="remove the duplicate rows",
                                b=brain("remove_duplicate_rows", ("remove_duplicate_rows", "remove the duplicate rows")))])
        rec = rq.run(spec, "template")["turns"][0]
        print(f"    turn: delivered={rec.get('delivered')} orig_ok={rec.get('orig_unchanged')} steps={rec.get('steps')}")
        print("    reply:", (rec.get("reply") or rec.get("error") or "")[:300])

"""Round 4: workbooks aimed at the round 3 fixes (head `43c9d1b`).

The round 3 fixes cap `workbook.xml` and `styles.xml` by declared size and count `<si>` only in a
part named `sharedStrings.xml`, all by part NAME. openpyxl finds the workbook part and the shared
strings by [Content_Types].xml, so they can be called anything. It also parses other parts whole on
every load that nothing caps: [Content_Types].xml, the workbook's rels, each sheet's rels, and
docProps/custom.xml. Plus the new caps measured at their edge (10 M characters of text, styles just
under 8 MiB, many sheets under the 1 MiB workbook cap).

Each file goes through the upload route's check_workbook and then check_workbook_limits in a capped
child (probe_m_round3's CHECK and READ). Files are written as out/m_P*.xlsx, so probe_n_turns.py runs
whole turns on them unchanged:

    OPENPYXL_LXML=False python scripts/review_harness/fix-security-pass/probe_p_round4.py [--tree DIR] [--only P1,P3] [--read]
    OPENPYXL_LXML=False python scripts/review_harness/fix-security-pass/probe_n_turns.py P1_workbook_part_renamed --no-facts
"""
import argparse
import os
import subprocess
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import probe_m_round3 as m  # noqa: E402  (CHECK, READ, table_sheet, NS, R, PR, CT_SHEET)

OUT = HERE / "out"
CT_WB = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"
CT_SST = "application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"


def build(path: Path, sheet: str, *, wb_name: str = "xl/workbook.xml", sheets: int = 1,
          wb_extra: str = "", rels_extra: str = "", ct_extra: str = "",
          parts: dict[str, bytes] | None = None) -> None:
    """A minimal workbook with the workbook part at ``wb_name`` (found through the content types)."""
    folder, _, base = wb_name.rpartition("/")
    sheet_entries = "".join(f'<sheet name="S{i}" sheetId="{i}" r:id="rId{i}"/>' for i in range(1, sheets + 1))
    rels = "".join(f'<Relationship Id="rId{i}" Type="{m.R}/worksheet" Target="worksheets/sheet1.xml"/>'
                   for i in range(1, sheets + 1))
    files = {
        "[Content_Types].xml": (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            f'<Override PartName="/{wb_name}" ContentType="{CT_WB}"/>'
            f'<Override PartName="/xl/worksheets/sheet1.xml" ContentType="{m.CT_SHEET}"/>'
            f"{ct_extra}</Types>").encode(),
        "_rels/.rels": (f'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="{m.PR}">'
                        f'<Relationship Id="rId1" Type="{m.R}/officeDocument" Target="{wb_name}"/>'
                        "</Relationships>").encode(),
        wb_name: (f'<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="{m.NS}" xmlns:r="{m.R}">'
                  f"<sheets>{sheet_entries}</sheets>{wb_extra}</workbook>").encode(),
        f"{folder}/_rels/{base}.rels": (f'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="{m.PR}">'
                                        f"{rels}{rels_extra}</Relationships>").encode(),
        "xl/worksheets/sheet1.xml": sheet.encode(),
        **(parts or {}),
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for name, data in files.items():
            z.writestr(name, data)


SMALL = m.table_sheet([["name", "amount"], ["a", 1], ["a", 1], ["b", 2]])


def p1_workbook_renamed(path: Path) -> None:
    """Round 3's M8 (1.4 M defined names) with the workbook part at xl/book.xml."""
    names = '<definedName name="n">S1!$A$1</definedName>' * 1_400_000
    build(path, SMALL, wb_name="xl/book.xml", wb_extra=f"<definedNames>{names}</definedNames>")


def p2_strings_renamed(path: Path) -> None:
    """Round 3's M3 (3.6 M shared strings) with the part at xl/strings.xml."""
    sst = (f'<sst xmlns="{m.NS}">' + "<si><t>ab</t></si>" * 3_600_000 + "</sst>").encode()
    sheet = m.table_sheet([["name"], ["a"]]).replace('t="inlineStr"><is><t>a</t></is>', 't="s"><v>0</v>')
    build(path, sheet, rels_extra=f'<Relationship Id="rIdS" Type="{m.R}/sharedStrings" Target="strings.xml"/>',
          ct_extra=f'<Override PartName="/xl/strings.xml" ContentType="{CT_SST}"/>',
          parts={"xl/strings.xml": sst})


def p3_content_types(path: Path, n: int = 1_400_000) -> None:
    """[Content_Types].xml with ``n`` extra Override entries: openpyxl's Manifest parses it whole."""
    build(path, SMALL, ct_extra='<Override PartName="/a" ContentType="b"/>' * n)


def p4_workbook_rels(path: Path, n: int = 1_300_000) -> None:
    """The workbook's rels part with ``n`` extra relationships: parsed whole on every load."""
    build(path, SMALL, rels_extra='<Relationship Id="x" Type="x" Target="x"/>' * n)


def p5_sheet_rels(path: Path, n: int = 1_300_000) -> None:
    """The sheet's own rels part with ``n`` relationships: parsed whole on every load, read-only too."""
    rels = (f'<Relationships xmlns="{m.PR}">' + '<Relationship Id="x" Type="x" Target="x"/>' * n
            + "</Relationships>").encode()
    build(path, SMALL, parts={"xl/worksheets/_rels/sheet1.xml.rels": rels})


def p6_custom_props(path: Path, n: int = 600_000) -> None:
    """docProps/custom.xml with ``n`` properties (all alike, so it zips small): parsed whole on every load."""
    vt = "http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"
    props = ('<property fmtid="{D5CDD505-2E9C-101B-9397-08002B2CF9AE}" pid="2" name="p">'
             "<vt:lpwstr>v</vt:lpwstr></property>") * n
    custom = (f'<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/custom-properties" '
              f'xmlns:vt="{vt}">{props}</Properties>').encode()
    build(path, SMALL, parts={"docProps/custom.xml": custom})


def styles_part(inner: str) -> bytes:
    return (f'<styleSheet xmlns="{m.NS}">{inner}</styleSheet>').encode()


def p7_borders_under_cap(path: Path, n: int = 900_000) -> None:
    """styles.xml just under the 8 MiB cap, made of empty <border/> (9 bytes each)."""
    inner = (f'<fonts count="1"><font/></fonts><fills count="1"><fill/></fills><borders count="{n}">'
             + "<border/>" * n + '</borders><cellStyleXfs count="1"><xf/></cellStyleXfs>'
             '<cellXfs count="1"><xf/></cellXfs>')
    build(path, SMALL, parts={"xl/styles.xml": styles_part(inner)})


def p7b_xfs_under_cap(path: Path, n: int = 1_600_000) -> None:
    """styles.xml just under the 8 MiB cap, made of empty <xf/> (5 bytes each)."""
    inner = ('<fonts count="1"><font/></fonts><fills count="1"><fill/></fills><borders count="1"><border/>'
             f'</borders><cellStyleXfs count="1"><xf/></cellStyleXfs><cellXfs count="{n}">'
             + "<xf/>" * n + "</cellXfs>")
    build(path, SMALL, parts={"xl/styles.xml": styles_part(inner)})


def text_at_cap(path: Path, char: str, per_cell: int, cells: int) -> None:
    """``cells`` distinct shared strings of ``per_cell`` characters (the new 10 M cap), one per row."""
    sst = (f'<sst xmlns="{m.NS}"><si><t>text</t></si><si><t>id</t></si>'
           + "".join(f"<si><t>{char * (per_cell - 7)}{i:07d}</t></si>" for i in range(cells)) + "</sst>").encode()
    rows = ['<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>']
    rows += [f'<row r="{r}"><c r="A{r}" t="s"><v>{r}</v></c><c r="B{r}"><v>{r % 200}</v></c></row>'
             for r in range(2, cells + 2)]
    sheet = (f'<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="{m.NS}"><sheetData>{"".join(rows)}'
             "</sheetData></worksheet>")
    build(path, sheet, rels_extra=f'<Relationship Id="rIdS" Type="{m.R}/sharedStrings" Target="sharedStrings.xml"/>',
          ct_extra=f'<Override PartName="/xl/sharedStrings.xml" ContentType="{CT_SST}"/>',
          parts={"xl/sharedStrings.xml": sst})


def p9_one_huge_unreferenced_string(path: Path, chars: int = 60_000_000) -> None:
    """One shared string of ``chars`` characters that no cell uses: openpyxl loads it on every load."""
    sst = f'<sst xmlns="{m.NS}"><si><t>a</t></si><si><t>{"z" * chars}</t></si></sst>'.encode()
    sheet = m.table_sheet([["name"], ["a"]]).replace('t="inlineStr"><is><t>a</t></is>', 't="s"><v>0</v>')
    build(path, sheet, rels_extra=f'<Relationship Id="rIdS" Type="{m.R}/sharedStrings" Target="sharedStrings.xml"/>',
          ct_extra=f'<Override PartName="/xl/sharedStrings.xml" ContentType="{CT_SST}"/>',
          parts={"xl/sharedStrings.xml": sst})


def p10_sheets_with_rels(path: Path, sheets: int = 20_000, rels: int = 2_000) -> None:
    """``sheets`` sheets (workbook.xml under 1 MiB) all on one part, whose own rels part has ``rels``
    entries: openpyxl parses that rels part once per sheet (sheets x rels objects)."""
    sheet_rels = (f'<Relationships xmlns="{m.PR}">' + f'<Relationship Id="x" Type="{m.R}/x" Target="x"/>' * rels
                  + "</Relationships>").encode()
    build(path, m.table_sheet([["name"]]), sheets=sheets,
          parts={"xl/worksheets/_rels/sheet1.xml.rels": sheet_rels})


CASES = {
    "P0_ordinary_control": lambda p: build(p, SMALL),
    "P1_workbook_part_renamed": p1_workbook_renamed,
    "P2_shared_strings_renamed": p2_strings_renamed,
    "P3_content_types_1_4m": p3_content_types,
    "P4_workbook_rels_1_3m": p4_workbook_rels,
    "P5_sheet_rels_1_3m": p5_sheet_rels,
    "P6_custom_props_600k": p6_custom_props,
    "P7_styles_borders_under_cap": p7_borders_under_cap,
    "P7b_styles_xfs_under_cap": p7b_xfs_under_cap,
    "P8_text_cap_ascii": lambda p: text_at_cap(p, "y", 32_767, 305),
    "P8b_text_cap_emoji": lambda p: text_at_cap(p, "\U0001F600", 32_767, 305),
    "P8c_text_cap_cjk": lambda p: text_at_cap(p, "中", 32_767, 305),
    # at the prototype's weighted cap (prototype_fix_r4.py: 4,000,000 bytes of stored text)
    "P11_proto_cap_ascii": lambda p: text_at_cap(p, "y", 32_767, 122),
    "P11b_proto_cap_emoji": lambda p: text_at_cap(p, "\U0001F600", 32_767, 30),
    "P9_huge_unused_string": p9_one_huge_unreferenced_string,
    "P10_sheets_with_rels": p10_sheets_with_rels,
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tree", default=str(HERE.parents[2]))
    ap.add_argument("--read", action="store_true")
    ap.add_argument("--cap-mib", type=int, default=4096)
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--only", default="")
    ap.add_argument("--rebuild", action="store_true")
    a = ap.parse_args()
    only = set(filter(None, a.only.split(",")))
    env = dict(os.environ, OPENPYXL_LXML="False")
    for name, make in CASES.items():
        if only and name.split("_")[0] not in only and name not in only:
            continue
        path = OUT / f"m_{name}.xlsx"
        if a.rebuild or not path.exists():
            make(path)
        with zipfile.ZipFile(path) as z:
            unpacked = sum(i.file_size for i in z.infolist())
        line = f"{name:28s} {path.stat().st_size:9d} B zipped, {unpacked / 2**20:6.1f} MiB unpacked | "
        try:
            r = subprocess.run([sys.executable, "-c", m.CHECK, str(path), a.tree, str(a.cap_mib)],
                               capture_output=True, text=True, env=env, timeout=a.timeout)
            line += r.stdout.strip() or ("ERR " + r.stderr.strip()[-300:])
        except subprocess.TimeoutExpired:
            line += f"check still running at {a.timeout}s (killed)"
        if a.read and "passes" in line:
            try:
                r = subprocess.run([sys.executable, "-c", m.READ, str(path), str(a.cap_mib)],
                                   capture_output=True, text=True, env=env, timeout=a.timeout)
                line += " | " + (r.stdout.strip() or r.stderr.strip()[-200:])
            except subprocess.TimeoutExpired:
                line += f" | pandas still running at {a.timeout}s"
        print(line, flush=True)


if __name__ == "__main__":
    main()

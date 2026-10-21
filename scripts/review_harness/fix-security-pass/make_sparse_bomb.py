"""Build a tiny .xlsx whose few cells sit at the far corner of the allowed grid.

Usage: python make_sparse_bomb.py OUT.xlsx ROWS COLS [ROW_ATTR]  (build(..., full_header=True) from code)
ROW_ATTR, if given, is written verbatim as the last row's r attribute (e.g. "1e6", " 300000").
"""
import sys
import zipfile

from openpyxl.utils import get_column_letter


def build(out: str, rows: int, cols: int, row_attr: str | None = None, full_header: bool = False) -> None:
    """``full_header``: every column of row 1 gets a header, so the sheet is a plain table."""
    last = get_column_letter(cols)
    r_attr = row_attr if row_attr is not None else str(rows)
    if full_header:
        header = "".join(
            f'<c r="{get_column_letter(i)}1" t="inlineStr"><is><t>h{i}</t></is></c>' for i in range(1, cols + 1)
        )
    else:
        header = (f'<c r="A1" t="inlineStr"><is><t>h</t></is></c>'
                  f'<c r="{last}1" t="inlineStr"><is><t>z</t></is></c>')
    sheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
        f'<row r="1">{header}</row>'
        f'<row r="{r_attr}"><c t="n"><v>1</v></c></row>'
        "</sheetData></worksheet>"
    )
    parts = {
        "[Content_Types].xml": (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            "</Types>"
        ),
        "_rels/.rels": (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            "</Relationships>"
        ),
        "xl/workbook.xml": (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>'
        ),
        "xl/_rels/workbook.xml.rels": (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            "</Relationships>"
        ),
        "xl/worksheets/sheet1.xml": sheet,
    }
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in parts.items():
            z.writestr(name, text)


if __name__ == "__main__":
    build(sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4] if len(sys.argv) > 4 else None)

"""Round 3: one-place mutations of the round 2 fixes (head c766fa6), run with mutations.py's runner.

    python scripts/review_harness/fix-security-pass/mutations_r3.py [NAME ...]   # from the repo root
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutations as m  # noqa: E402

WB = "skills/excel/workbook.py"
ST = "skills/excel/shown_text.py"
IN = "buddy/inspector.py"
AN = "skills/excel/analyze.py"
GUARD = "    if len(value) > _LONGEST_DATE_CHARS:  # no listed format is this long; strptime would crawl\n        return None\n"
NEW = {
    "walk-off": (WB, "    return _reader_verdict(input_path)", "    return None"),
    "walk-no-reset-dimensions": (WB, "                    sheet.reset_dimensions()  # as pandas does: a wrong <dimension> hides nothing\n", ""),
    "walk-rows-off": (WB, "                        if rows > MAX_ROWS:", "                        if False:"),
    "walk-columns-off": (WB, "                        if widest > MAX_COLUMNS:", "                        if False:"),
    "walk-grid-off": (WB, "                        if grids + rows * widest > MAX_CELLS:", "                        if False:"),
    "walk-grid-not-summed": (WB, "                        if grids + rows * widest > MAX_CELLS:",
                             "                        if rows * widest > MAX_CELLS:"),
    "walk-cell-chars-off": (WB, "                                if len(value) > MAX_CELL_CHARS:", "                                if False:"),
    "walk-total-text-off": (WB, "                                if chars > MAX_TEXT_CHARS:", "                                if False:"),
    "walk-first-sheet-only": (WB, "                for sheet in book.worksheets:", "                for sheet in book.worksheets[:1]:"),
    "entities-check-off": (WB, "                if _declares_entities(archive, entry):", "                if False:"),
    "entity-handler-off": (WB, "    parser.EntityDeclHandler = declared\n", ""),
    "doctype-handler-off": (WB, "    parser.StartDoctypeDeclHandler = declared\n", ""),
    "date-guard-inspector-off": (IN, GUARD, ""),
    "date-guard-analyze-off": (AN, GUARD, ""),
    "shown-round3-quotes-dropped": (
        ST, "'\"\\u201c\\u201d\\u201e\\u201f\\u00ab\\u00bb\\u2039\\u203a\\u2033\\u2036\\u275d\\u275e\\u301d\\u301e\\u301f\\uff02'",
        "'\"\\u201c\\u201d\\u201e\\u201f\\u00ab\\u00bb\\u301d\\u301e\\u301f\\uff02'"),
}
m.M.update(NEW)
m.TESTS.update({name: [m.HW] for name in NEW if NEW[name][0] == WB})
m.TESTS.update({"date-guard-inspector-off": [m.HW, "tests/buddy/test_inspector.py"],
                "date-guard-analyze-off": [m.HW, "tests/skills/excel/test_analyze.py"],
                "shown-round3-quotes-dropped": [m.FT]})

if __name__ == "__main__":
    for n in sys.argv[1:] or list(NEW):
        print(m.run(n), flush=True)

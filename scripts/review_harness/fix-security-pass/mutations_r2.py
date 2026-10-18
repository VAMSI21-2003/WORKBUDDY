"""Round 2: one-place mutations of the round 1 fixes (head 9d17990), run with mutations.py's runner.

    python scripts/review_harness/fix-security-pass/mutations_r2.py [NAME ...]   # from the repo root
Round 1's survivors that the fixes were meant to kill are re-run too (hands-dispatch-msg-back,
splink-revert-to-headers, analysis-missing-str-alert).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutations as m  # noqa: E402

WB = "skills/excel/workbook.py"
ST = "skills/excel/shown_text.py"
NEW = {
    "grid-off": (WB, 'if state["top_row"] * state["top_column"] > MAX_CELLS:', "if False:"),
    "row-number-isdigit": (WB, 'state["row"] = _row_number(given) if given is not None else state["row"] + 1',
                           'state["row"] = int(given) if (given or "").isdigit() else state["row"] + 1'),
    "row-float-off": (WB, "        value = float(text)\n        if not value.is_integer():\n            raise\n        return int(value)",
                      "        return 0"),
    "row-unreadable-off": (WB, "            except ValueError:\n                refuse(_UNREADABLE)",
                           "            except ValueError:\n                pass"),
    "namespace-filter-off": (WB, "        if namespace not in _SPREADSHEET_NAMESPACES:\n            return",
                             "        if False:\n            return"),
    "foreign-ns-counted": (WB, "        if namespace not in _SPREADSHEET_NAMESPACES:\n            return",
                           "        if namespace not in _SPREADSHEET_NAMESPACES and False:\n            return"),
    "strict-ns-dropped": (WB, '        "http://purl.oclc.org/ooxml/spreadsheetml/main",\n', ""),
    "parts-back-to-worksheets": (WB, "if _XML_PART.search(entry.filename):",
                                 'if entry.filename.lower().startswith("xl/worksheets/"):'),
    "entity-decl-off": (WB, "    parser.EntityDeclHandler = lambda *_: refuse(_NOT_ALLOWED)\n", ""),
    "doctype-off": (WB, "    parser.StartDoctypeDeclHandler = lambda *_: refuse(_NOT_ALLOWED)\n    parser.EntityDeclHandler",
                    "    parser.EntityDeclHandler"),
    "doctype-and-entity-off": (WB, [
        ("    parser.StartDoctypeDeclHandler = lambda *_: refuse(_NOT_ALLOWED)\n    parser.EntityDeclHandler",
         "    parser.EntityDeclHandler"),
        ("    parser.EntityDeclHandler = lambda *_: refuse(_NOT_ALLOWED)\n", ""),
    ]),
    "cellref-unreadable-off":(WB, "                if match is None:\n                    refuse(_UNREADABLE)\n",
                               "                if match is None:\n                    return\n"),
    "dollar-ref-old-regex": (WB, r'_CELL_REFERENCE = re.compile(r"^\$?([A-Za-z]{1,3})\$?(\d+)$")',
                             r'_CELL_REFERENCE = re.compile(r"^([A-Za-z]{1,3})(\d+)$")'),
    "catch-narrow-again": (WB, "    except Exception:  # noqa: BLE001 — damaged or unreadable: the callers' own reads refuse it",
                           "    except (zipfile.BadZipFile, OSError, expat.ExpatError, ValueError, RuntimeError, EOFError):"),
    "shown-curly-quotes-kept": (ST, "elif char in _QUOTES:", "elif char == '\"':"),
    "shown-leading-mark-kept": (ST, '    while text and unicodedata.category(text[0])[0] == "M":',
                                "    while False:"),
    "shown-trailing-mark-kept": (ST, '    while text and unicodedata.category(text[-1])[0] == "M":',
                                 "    while False:"),
    "shown-no-nfc": (ST, 'unicodedata.normalize("NFC", "".join(pieces))', '"".join(pieces)'),
}
m.M.update(NEW)
# entity-decl-off and doctype-off each leave the other handler in place; this one takes both.
m.TESTS.update({name: [m.HW] for name in NEW if NEW[name][0] == WB})
m.TESTS.update({name: [m.FT] for name in NEW if NEW[name][0] == ST})
AGAIN = ["hands-dispatch-msg-back", "splink-revert-to-headers", "analysis-missing-str-alert"]

if __name__ == "__main__":
    for n in sys.argv[1:] or [*NEW, *AGAIN]:
        print(m.run(n), flush=True)

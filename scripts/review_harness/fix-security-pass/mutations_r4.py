"""Round 4: one-place mutations of the round 3 fixes (head 43c9d1b), run with mutations.py's runner.

    python scripts/review_harness/fix-security-pass/mutations_r4.py [NAME ...]   # from the repo root
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutations as m  # noqa: E402

WB = "skills/excel/workbook.py"
AN = "skills/excel/analyze.py"
OC = "buddy/outcome.py"
TA = "tests/skills/excel/test_analyze.py"
TO = "tests/buddy/test_outcome.py"
EF = "tests/buddy/test_engine_factory.py"
NEW = {
    # workbook.py
    "rows-per-sheet-again": (WB, "                        if all_rows > MAX_ROWS:", "                        if rows > MAX_ROWS:"),
    "workbook-part-cap-off": (WB, 'if name.endswith("workbook.xml") and entry.file_size > MAX_WORKBOOK_PART_BYTES:',
                              "if False:"),
    "styles-part-cap-off": (WB, 'if name.endswith("styles.xml") and entry.file_size > MAX_STYLES_PART_BYTES:',
                            "if False:"),
    "strings-cap-off": (WB, "            if strings > MAX_SHARED_STRINGS:", "            if False:"),
    "strings-handler-off": (WB, "        parser.StartElementHandler = counting\n", "        pass\n"),
    "walk-keep-links-default": (WB, "keep_links=False\n            )\n            try:\n                grids",
                                ")\n            try:\n                grids"),
    "sheetnames-keep-links-default": (WB, "book = openpyxl.load_workbook(handle, read_only=True, keep_links=False)",
                                      "book = openpyxl.load_workbook(handle, read_only=True)"),
    "formula-keep-links-default": (WB, "shown_handle, read_only=True, data_only=False, keep_links=False",
                                   "shown_handle, read_only=True, data_only=False"),
    "text-cap-back-to-50m": (WB, "MAX_TEXT_CHARS = 10_000_000", "MAX_TEXT_CHARS = 50_000_000"),
    # analyze.py
    "field-slice-off": (AN, "column.astype(str).str.slice(0, _NEAR_DUPLICATE_FIELD_CHARS).to_numpy()",
                        "column.astype(str).to_numpy()"),
    "pair-limit-off": (AN, "        if pairs > _NEAR_DUPLICATE_PAIR_LIMIT:", "        if False:"),
    "pair-count-name-only": (AN, "for column in (working[_NAME_FIELD].str.slice(0, 3), working[_KEY_FIELD])",
                             "for column in (working[_NAME_FIELD].str.slice(0, 3),)"),
    "pair-count-key-only": (AN, "for column in (working[_NAME_FIELD].str.slice(0, 3), working[_KEY_FIELD])",
                            "for column in (working[_KEY_FIELD],)"),
    # outcome.py
    "unsure-counts-for-partly": (OC, "a.source in BRAIN_SOURCES and a.reason != NO_SKILL_UNSURE",
                                 "a.source in BRAIN_SOURCES"),
    "unsure-becomes-no-skill": (OC, 'return ActionOutcome("", NOT_DONE, reason=NO_SKILL_UNSURE, source=entry.source)',
                                'return ActionOutcome("", NOT_DONE, reason=NO_SKILL, source=entry.source)'),
    "two-request-phrase-absorbed": (OC, "            names_action = unabsorbed = True\n        elif said in lowered:",
                                    "            names_action = named_passed = True\n        elif said in lowered:"),
    "said-key-without-reason": (OC, 'key = f"{outcome.reason}:{outcome.user_phrase.casefold()}" if outcome else ""',
                                'key = outcome.user_phrase.casefold() if outcome else ""'),
    "whole-is-stripped": (OC, "        if not said or said not in whole:", "        if not said or said not in lowered:"),
}
m.M.update(NEW)
m.TESTS.update({name: [m.HW] for name, spec in NEW.items() if spec[0] == WB})
m.TESTS.update({name: [TA] for name, spec in NEW.items() if spec[0] == AN})
m.TESTS.update({name: [TO, m.RD, EF, m.FT] for name, spec in NEW.items() if spec[0] == OC})

if __name__ == "__main__":
    for n in sys.argv[1:] or list(NEW):
        print(m.run(n), flush=True)

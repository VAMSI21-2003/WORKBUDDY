"""Round 6: one-place mutations of the round 5 fixes (head 8705ad4), run with mutations.py's runner.

    python scripts/review_harness/fix-security-pass/mutations_r6.py [NAME ...]   # from the repo root
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutations as m  # noqa: E402
import mutations_r5 as r5  # noqa: E402,F401  (registers round 5's names for the re-runs)

WB = "skills/excel/workbook.py"
SC = "tests/skills/excel/test_scan_child.py"
NEW = {
    "formula-count-off": (WB, "        formula_cell_counts(input_path)\n    except MemoryError:",
                          "    except MemoryError:"),
    "formula-count-before-tables": (WB,
        "        sheet_names(input_path)\n        frames = (pd.read_excel(input_path), pd.read_excel(input_path, dtype=object))\n        del frames\n",
        "        sheet_names(input_path)\n"),
    "kept-everything": (WB, "                if verdict not in _NOT_KEPT:", "                if True:"),
    "timeout-kept": (WB, "_NOT_KEPT = (TOO_COSTLY, CHECK_STOPPED)", "_NOT_KEPT = (CHECK_STOPPED,)"),
    "crash-kept": (WB, "_NOT_KEPT = (TOO_COSTLY, CHECK_STOPPED)", "_NOT_KEPT = (TOO_COSTLY,)"),
    "limit-not-kept": (WB, "_NOT_KEPT = (TOO_COSTLY, CHECK_STOPPED)",
                       "_NOT_KEPT = (TOO_COSTLY, CHECK_STOPPED, TOO_MANY_CELLS)"),
    "env-whole": (WB, "    environment = {\n        name: value\n        for name, value in os.environ.items()\n"
                      "        if name in _CHILD_ENVIRONMENT or name.startswith(\"LC_\")\n    }\n",
                  "    environment = dict(os.environ)\n"),
    "env-no-lc": (WB, ' or name.startswith("LC_")', ""),
    "lxml-mode-unset": (WB, '    environment["OPENPYXL_LXML"] = _xml_reader_mode()\n', ""),
    "lxml-mode-fixed-false": (WB, '    return "True" if LXML else "False"', '    return "False"'),
    "not-run-old-sentence": (WB, "        raise WorkbookRefused(CHECK_NOT_RUN) from exc",
                             "        raise WorkbookRefused(CHECK_STOPPED) from exc"),
    "wait-ten": (WB, "SCAN_WAIT_SECONDS = SCAN_SECONDS + 15", "SCAN_WAIT_SECONDS = 10"),
    "cells-1m": (WB, "MAX_CELLS = 250_000", "MAX_CELLS = 1_000_000"),
    "seconds-60": (WB, "SCAN_SECONDS = 30\n", "SCAN_SECONDS = 60\n"),
    # round 5's memerr-read-as-skills, re-anchored on the line the fix added
    "memerr-read-as-skills-r6": (WB, "        formula_cell_counts(input_path)\n    except MemoryError:\n        raise\n",
                                 "        formula_cell_counts(input_path)\n"),
}
m.M.update(NEW)
m.TESTS.update({name: [SC, m.HW] for name in NEW})

RERUN = ("r4-4-old-sentence",)

if __name__ == "__main__":
    for n in sys.argv[1:] or [*NEW, *RERUN]:
        print(m.run(n), flush=True)

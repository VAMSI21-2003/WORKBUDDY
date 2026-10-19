"""Round 5: one-place mutations of the child-process check (head 2ac4f8a), run with mutations.py's runner.

    python scripts/review_harness/fix-security-pass/mutations_r5.py [NAME ...]   # from the repo root
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutations as m  # noqa: E402

WB = "skills/excel/workbook.py"
SC = "tests/skills/excel/test_scan_child.py"
OC = "buddy/outcome.py"
NEW = {
    "memerr-sheet-names": (WB, "    except (WorkbookRefused, MemoryError):\n        raise",
                           "    except WorkbookRefused:\n        raise"),
    "memerr-walk": (WB, '    except MemoryError:\n        raise  # not "can\'t be read": the file needs more than the check was given\n', ""),
    "memerr-read-as-skills": (WB, "        del frames\n    except MemoryError:\n        raise\n", "        del frames\n"),
    "status-check-off": (WB, "    if done.returncode != 0:", "    if False:"),
    "unknown-answer-passes": (WB, "        return _CHILD_REFUSALS[int(index)]\n    return CHECK_STOPPED",
                              "        return _CHILD_REFUSALS[int(index)]\n    return None"),
    "tables-not-built": (WB, "        if verdict is None:\n            _read_as_the_skills_do(input_path)",
                         "        if verdict is None:\n            pass"),
    "one-table-only": (WB, "frames = (pd.read_excel(input_path), pd.read_excel(input_path, dtype=object))",
                       "frames = (pd.read_excel(input_path, dtype=object),)"),
    "no-sheet-listing-in-child": (WB, "    try:\n        sheet_names(input_path)\n        frames",
                                  "    try:\n        frames"),
    "limits-not-passed": (WB, "json.dumps({name: globals()[name] for name in _LIMIT_NAMES}),", '"{}",'),
    "semaphore-two": (WB, "_scan_slot = threading.Semaphore(1)", "_scan_slot = threading.Semaphore(2)"),
    "timeout-passes": (WB, '        logger.info("%r: the check ran past %d s", input_path, SCAN_SECONDS)\n        return TOO_COSTLY',
                       '        logger.info("%r: the check ran past %d s", input_path, SCAN_SECONDS)\n        return None'),
    "memerr-in-child-passes": (WB, "    except MemoryError:\n        verdict = TOO_COSTLY",
                               "    except MemoryError:\n        verdict = None"),
    "no-rlimit": (WB, '    "    resource.setrlimit(resource.RLIMIT_AS, (cap, cap))\\n"', '    "    pass\\n"'),
    "no-oom-score": (WB, "open('/proc/self/oom_score_adj', 'w').write('1000')", "open('/proc/self/oom_score_adj', 'w').write('0')"),
    "cannot-start-is-cached": (WB, "        raise WorkbookRefused(CHECK_STOPPED) from exc", "        return CHECK_STOPPED"),
    "bytes-weight-off": (WB, "chars += len(value) * _bytes_a_character(value)", "chars += len(value)"),
    "content-types-cap-off": (WB, "                    if entry.file_size > _MAX_PART_BYTES:\n                        return _UNREADABLE",
                              "                    if False:\n                        return _UNREADABLE"),
    "in-child-guard-off": (WB, "    if _in_scan_child:\n        return\n", ""),
    "blas-threads-off": (WB, '    environment.update(OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1", MKL_NUM_THREADS="1")\n', ""),
    "wait-recheck-off": (WB, "            if not known:  # another thread may have checked this very file while this one waited",
                         "            if True:"),
    "busy-wait-forever": (WB, "if not _scan_slot.acquire(timeout=SCAN_WAIT_SECONDS):", "if not _scan_slot.acquire():"),
    "isolated-off": (WB, '                "-I",\n', ""),
    "r4-4-old-sentence": (OC, "Some of your wording overlaps what I did. If you asked for anything beyond that, ",
                          "Some of your wording overlaps what I did, so I can't tell if you asked for anything more; if you did, "),
}
m.M.update(NEW)
m.TESTS.update({name: [SC, m.HW] for name, spec in NEW.items() if spec[0] == WB})
m.TESTS.update({name: ["tests/buddy/test_outcome.py", m.RD, "tests/buddy/test_engine_factory.py", m.FT]
                for name, spec in NEW.items() if spec[0] == OC})

if __name__ == "__main__":
    for n in sys.argv[1:] or list(NEW):
        print(m.run(n), flush=True)

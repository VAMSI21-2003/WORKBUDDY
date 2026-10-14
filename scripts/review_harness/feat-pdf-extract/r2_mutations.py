# ruff: noqa: E501
"""Round 2: one-place mutations of the round-1 fixes, each run against the tests that should catch it.

    git worktree add --detach $WT HEAD          # a throwaway copy, never the branch
    python r2_mutations.py $WT [name ...]       # output: out/r2_mutations.txt

Same mechanics as `mutations.py` (round 1). "SURVIVED" = a guard no test holds.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
F1 = "tests/buddy/test_pdf_extract_fixes1.py"
PDF = ["tests/skills/pdf", "tests/buddy/test_pdf_extract.py", F1, "tests/backend/test_pdf_turns.py"]
SBX = ["tests/skills/pdf/test_sandbox.py"]
XL = ["tests/skills/excel", "tests/buddy/test_inspector.py", F1]
W = "skills/pdf/_worker/main.py"
L = "skills/pdf/_worker/lockdown.py"

M = [
    ("r01-overflow-check-off", W, "        if result[\"plain\"] and _text_overflows(textpage, raw, grid):", "        if False:", PDF),
    ("r02-overflow-beside-off", W, "            return True  # text on a row of the table, beside it", "            continue", PDF),
    ("r03-overflow-crossing-off", W, "            return True  # a character crossing a rule", "            pass", PDF),
    ("r04-overflow-run-gap-off", W, "                return True  # one run of text across two cells", "                pass", PDF),
    ("r05-nfkc-back", "skills/pdf/_worker/textnorm.py", "unicodedata.normalize(\"NFC\", text)", "unicodedata.normalize(\"NFKC\", text)", PDF),
    ("r06-ligatures-kept", "skills/pdf/_worker/textnorm.py", ".translate(_LIGATURES)", "", PDF),
    ("r07-hands-exception-text", "buddy/hands.py", "                errors.append(couldnt_run)", "                errors.append(str(exc))", [F1, "tests/buddy"]),
    ("r08-hands-summary-exception", "buddy/hands.py", "                        summary=couldnt_run,", "                        summary=str(exc),", [F1, "tests/buddy"]),
    ("r09-counts-include-header", "buddy/outcome.py", "    below = measurement.output_rows - 1", "    below = measurement.output_rows", PDF),
    ("r10-analysis-converts", "skills/excel/analyze.py", "findings = analyzer(cells if check_name in _CELL_CHECKS else df)", "findings = analyzer(df)", XL),
    ("r11-analysis-verifier-converts", "buddy/inspector.py", "(\"duplicate_rows\", _independent_duplicate_row_findings(cells)),", "(\"duplicate_rows\", _independent_duplicate_row_findings(df)),", XL),
    ("r12-loose-verifier-converts", "buddy/inspector.py", "_independent_loose_duplicate_row_findings(cells)),", "_independent_loose_duplicate_row_findings(df)),", XL),
    ("r13-extract-one-column-off", W, "        if columns < 2:\n            raise Refused(ONE_COLUMN)", "        pass", PDF),
    ("r14-verify-one-column-off", W, "result[\"plain\"] = grid.plain and grid.columns >= 2", "result[\"plain\"] = grid.plain", PDF),
    ("r15-io-uring-allowed-x86", L, "        \"execveat\": 322,\n        \"io_uring_setup\": 425,\n", "        \"execveat\": 322,\n", SBX),
    ("r16-x32-not-killed", L, "        asm.op(_BPF_JGE_K, _X32_BIT, jt=\"kill\")", "        pass", SBX),
    ("r17-clone-any", L, "asm.op(_BPF_JSET_K, _CLONE_THREAD, jt=\"allow\", jf=\"eperm\")", "asm.op(_BPF_JSET_K, _CLONE_THREAD, jt=\"allow\", jf=\"allow\")", SBX),
    ("r18-no-landlock", W, "        _CONFINEMENT = lockdown.confine_filesystem()", "        _CONFINEMENT = lockdown.Confinement(False, \"mutated\")", SBX),
    ("r19-process-vm-allowed", L, "        \"process_vm_readv\": 310,\n        \"process_vm_writev\": 311,\n", "", SBX),
    ("r20-landlock-reads-everything", L, "        if os.path.isdir(real) and real not in seen and real != \"/\":", "        if os.path.isdir(real) and real not in seen:\n            seen.append(\"/\")", SBX),
    # Round-1 survivors m13/m14/m23, re-run with the round-1 fix tests included.
    ("r21-m13-inspector-no-dtype", "buddy/inspector.py", "    return pd.read_excel(path, dtype=object)", "    return pd.read_excel(path)", [F1]),
    ("r22-m14-empty-rows-no-dtype", "skills/excel/remove_empty_rows.py", "input_path, sheet_name=sheet_name, dtype=object", "input_path, sheet_name=sheet_name", [F1]),
    ("r23-m23-busy-cap-3", "skills/pdf/sandbox.py", "MAX_CONCURRENT_WORKERS = 2", "MAX_CONCURRENT_WORKERS = 3", [F1, "tests/skills/pdf/test_sandbox.py"]),
]


def run(wt: Path, names: list[str]) -> None:
    lines = []
    for name, rel, old, new, tests in M:
        if names and not any(name.startswith(n) for n in names):
            continue
        path = wt / rel
        src = path.read_text()
        if src.count(old) != 1:
            lines.append(f"{name}: PATTERN NOT FOUND ({src.count(old)})")
            print(lines[-1], flush=True)
            continue
        path.write_text(src.replace(old, new))
        try:
            p = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", *tests],
                               cwd=wt, capture_output=True, text=True, timeout=2400)
            tail = (p.stdout.strip().splitlines() or ["?"])[-1]
            verdict = "killed" if p.returncode != 0 else "SURVIVED"
            failed = [ln for ln in p.stdout.splitlines() if ln.startswith("FAILED")][:2]
            lines.append(f"{name}: {verdict} ({tail}) {failed}")
        finally:
            path.write_text(src)
        print(lines[-1], flush=True)
    out = HERE / "out" / "r2_mutations.txt"
    out.parent.mkdir(exist_ok=True)
    out.write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    run(Path(sys.argv[1]), sys.argv[2:])

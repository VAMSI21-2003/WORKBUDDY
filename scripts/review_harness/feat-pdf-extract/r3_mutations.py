# ruff: noqa: E501
"""Round 3: one-place mutations of the round-2 fixes, each run against the tests that should catch it.

    git worktree add --detach $WT HEAD          # a throwaway copy, never the branch
    python r3_mutations.py $WT [name ...]       # output: out/r3_mutations.txt

Same mechanics as `r2_mutations.py`. "SURVIVED" = a guard no test holds.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
F2 = "tests/buddy/test_pdf_extract_fixes2.py"
PDF = ["tests/skills/pdf", "tests/buddy/test_pdf_extract.py", "tests/buddy/test_pdf_extract_fixes1.py", F2, "tests/backend/test_pdf_turns.py"]
SBX = ["tests/skills/pdf/test_sandbox.py", F2]
PLAN = [F2, "tests/buddy/test_planner.py", "tests/buddy/test_pdf_extract.py", "tests/backend/test_pdf_turns.py"]
W = "skills/pdf/_worker/main.py"
L = "skills/pdf/_worker/lockdown.py"
P = "buddy/planner.py"

M = [
    ("s01-object-identity-off", W, "            return TEXT_OVERFLOWS  # one string drawn across two cells", "            pass", PDF),
    ("s02-run-gap-off", W, "                return TEXT_OVERFLOWS  # one run of text across two cells", "                pass", PDF),
    ("s03-column-gap-off", W, "                return COLUMNS_NOT_RULED", "                pass", PDF),
    ("s04-column-reason-swapped", W, "                return COLUMNS_NOT_RULED", "                return TEXT_OVERFLOWS", PDF),
    ("s05-beside-table-off", W, "            return TEXT_OVERFLOWS  # text on a row of the table, beside it", "            continue", PDF),
    ("s06-analysable-always", P, "    return target is None or target.lower().endswith(_SUPPORTED_ANALYSIS_EXTENSIONS)", "    return True", PLAN),
    ("s07-drop-keeps-dependencies", P, "        replace(step, dependencies=tuple(d for d in step.dependencies if d not in dropped))", "        step", PLAN),
    ("s08-analysable-ignores-active-file", P, "    target = request.active_file or (", "    target = None or (", PLAN + ["tests/buddy"]),
    ("s09-kill-any-pid", L, "asm.op(_BPF_JEQ_K, os.getpid(), jt=\"allow\", jf=\"eperm\")", "asm.op(_BPF_JEQ_K, os.getpid(), jt=\"allow\", jf=\"allow\")", SBX),
    ("s10-tgkill-unchecked", L, "    asm.op(_BPF_JEQ_K, tgkill, jt=\"own_pid\")\n", "", SBX),
    ("s11-tkill-allowed-x86", L, "        \"tkill\": 200,\n", "", SBX),
    ("s12-rt-sigqueueinfo-allowed-x86", L, "        \"rt_sigqueueinfo\": 129,\n", "", SBX),
    ("s13-pidfd-send-signal-allowed-x86", L, "        \"rt_tgsigqueueinfo\": 297,\n        \"pidfd_send_signal\": 424,\n", "        \"rt_tgsigqueueinfo\": 297,\n", SBX),
    ("s14-pidfd-getfd-allowed-x86", L, "        \"rt_tgsigqueueinfo\": 297,\n        \"pidfd_send_signal\": 424,\n        \"pidfd_open\": 434,\n        \"pidfd_getfd\": 438,\n", "        \"rt_tgsigqueueinfo\": 297,\n        \"pidfd_send_signal\": 424,\n        \"pidfd_open\": 434,\n", SBX),
    ("s15-fail-closed-off", W, "    if mode in (\"probe\", \"extract\", \"verify\") and not _CONFINEMENT.confined:", "    if False:", SBX + ["tests/skills/pdf"]),
    ("s16-release-check-ignores-landlock", "skills/pdf/__main__.py", "    holds = outcome.ok and bool(outcome.data) and all(v is True for v in outcome.data.values())", "    holds = outcome.ok and bool(outcome.data) and all(v is True for k, v in outcome.data.items() if k != \"filesystem_confined\")", SBX),
    ("s17-kill-number-wrong-x86", L, "    True,\n    62,\n    234,", "    True,\n    63,\n    234,", SBX),
    # Round-2 survivors the fixes say are now guarded
    ("s18-r18-no-landlock", W, "        _CONFINEMENT = lockdown.confine_filesystem()", "        _CONFINEMENT = lockdown.Confinement(False, \"mutated\")", SBX),
    ("s19-r19-process-vm-allowed", L, "        \"process_vm_readv\": 310,\n        \"process_vm_writev\": 311,\n", "", SBX),
    ("s20-r10-analysis-converts", "skills/excel/analyze.py", "findings = analyzer(cells if check_name in _CELL_CHECKS else df)", "findings = analyzer(df)", ["tests/skills/excel", "tests/buddy/test_inspector.py", "tests/buddy/test_pdf_extract_fixes1.py", F2]),
    ("s21-r22-empty-rows-no-dtype", "skills/excel/remove_empty_rows.py", "input_path, sheet_name=sheet_name, dtype=object", "input_path, sheet_name=sheet_name", ["tests/buddy/test_pdf_extract_fixes1.py", F2]),
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
    out = HERE / "out" / "r3_mutations.txt"
    out.parent.mkdir(exist_ok=True)
    out.write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    run(Path(sys.argv[1]), sys.argv[2:])

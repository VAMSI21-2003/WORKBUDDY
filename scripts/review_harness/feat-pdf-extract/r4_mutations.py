# ruff: noqa: E501
"""Round 4: one-place mutations of the round-3 fixes, each run against the tests that should catch it.

    git worktree add --detach $WT HEAD          # a throwaway copy, never the branch
    python r4_mutations.py $WT [name ...]       # output: out/r4_mutations.txt

Same mechanics as `r3_mutations.py` (imported). "SURVIVED" = a guard no test holds.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import r3_mutations as r3  # noqa: E402

F3 = "tests/buddy/test_pdf_extract_fixes3.py"
LAYOUT = ["tests/skills/pdf/test_text_layout.py", F3, "tests/buddy/test_pdf_extract_fixes2.py", "tests/skills/pdf"]
SBX = ["tests/skills/pdf/test_seccomp_filter.py", "tests/skills/pdf/test_sandbox.py", "tests/skills/pdf/test_release_check.py"]
G = "skills/pdf/_worker/geometry.py"
W = "skills/pdf/_worker/main.py"
L = "skills/pdf/_worker/lockdown.py"

M = [
    ("n01-run-em-0.35", G, "RUN_EM = 0.5", "RUN_EM = 0.35", LAYOUT),
    ("n02-column-em-1.3", G, "COLUMN_EM = 1.0", "COLUMN_EM = 1.3", LAYOUT),
    ("n03-larger-em-of-the-two", G, "            em = min(first.size, second.size)", "            em = max(first.size, second.size)", LAYOUT),
    ("n04-one-line-per-row", G, "        if line and line[0].baseline - glyph.baseline > 0.5 * min(line[0].size, glyph.size):", "        if False:", LAYOUT),
    ("n05-object-rule-off", W, "            return TEXT_OVERFLOWS  # one string drawn across two cells", "            pass", LAYOUT),
    ("n06-crossing-rule-off", W, "            return TEXT_OVERFLOWS  # a character crossing a rule", "            pass", LAYOUT),
    ("n07-tight-box-not-advance", W, "            geometry.Glyph(origin_y.value, advance.left, advance.right, size, cell)", "            geometry.Glyph(origin_y.value, left_x, right_x, size, cell)", LAYOUT),
    ("n08-em-from-glyph-height", W, "        size = raw.FPDFText_GetFontSize(textpage.raw, index) or (t - b)", "        size = (t - b)", LAYOUT),
    ("n09-self-or-zero-any-pid", L, "    asm.op(_BPF_JEQ_K, 0, jt=\"allow\")\n    asm.op(_BPF_JEQ_K, os.getpid(), jt=\"allow\", jf=\"eperm\")", "    asm.op(_BPF_JEQ_K, 0, jt=\"allow\")\n    asm.op(_BPF_JEQ_K, os.getpid(), jt=\"allow\", jf=\"allow\")", SBX),
    ("n10-prlimit64-allowed-x86", L, "    own_process_only={\n        \"prlimit64\": 302,\n", "    own_process_only={\n", SBX),
    ("n11-setpriority-allowed-x86", L, "        \"io_uring_register\": 427,\n        \"setpriority\": 141,\n", "        \"io_uring_register\": 427,\n", SBX),
    ("n12-oom-not-set", L, "    prefer_to_be_killed_first()\n    _install_seccomp()", "    _install_seccomp()", SBX),
    ("n13-oom-failure-ignored", L, "        raise LockdownUnavailable(\"oom_score_adj can't be set\") from exc", "        return", SBX),
    ("n14-release-ignores-landlock", "skills/pdf/__main__.py", "    holds = outcome.ok and bool(outcome.data) and all(v is True for v in outcome.data.values())", "    holds = outcome.ok and bool(outcome.data) and all(v is True for k, v in outcome.data.items() if k != \"filesystem_confined\")", SBX),
]


if __name__ == "__main__":
    r3.M[:] = M
    # the round-3 runner writes out/r3_mutations.txt; keep round 4's apart
    wt = Path(sys.argv[1])
    orig = Path.write_text

    def write_text(self, data, *a, **k):  # noqa: ANN001, ANN002, ANN003, ANN202
        if self.name == "r3_mutations.txt":
            self = self.with_name("r4_mutations.txt")
        return orig(self, data, *a, **k)

    Path.write_text = write_text  # type: ignore[method-assign]
    r3.run(wt, sys.argv[2:])

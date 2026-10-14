# ruff: noqa: E501
"""One-place mutations of the feat/pdf-extract code, each run against the tests that should catch it.

    git worktree add --detach $WT HEAD          # a throwaway copy, never the branch
    python mutations.py $WT [name ...]          # output: out/mutations.txt

A mutation that leaves its tests passing ("SURVIVED") is a guard no test holds.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PDF_TESTS = ["tests/skills/pdf", "tests/buddy/test_pdf_extract.py", "tests/backend/test_pdf_turns.py"]
EXCEL_TESTS = ["tests/skills", "tests/buddy/test_inspector.py", "tests/buddy/test_pdf_extract.py", "tests/backend/test_pdf_turns.py"]

M = [
    ("m01-verifier-skips-cell-compare", "buddy/inspector.py", "            different += actual != expected[r][c]", "            different += 0", PDF_TESTS[1:2]),
    ("m02-verifier-accepts-numbers", "buddy/inspector.py", "                    elif cell.data_type == \"s\" and isinstance(cell.value, str):", "                    elif True:", PDF_TESTS[1:2]),
    ("m03-verifier-ignores-tables-count", "buddy/inspector.py", "    if answer.get(\"tables\") != 1:", "    if False:", PDF_TESTS[1:2]),
    ("m04-verifier-ignores-plain", "buddy/inspector.py", "    if answer.get(\"plain\") is not True or not _is_text_rectangle(expected):", "    if not _is_text_rectangle(expected):", PDF_TESTS[1:2]),
    ("m05-verifier-no-larger-check", "buddy/inspector.py", "    if len(cells) > height or max(len(row) for row in cells) > width:", "    if False:", PDF_TESTS[1:2]),
    ("m06-no-text-sentence", "buddy/outcome.py", "    if table_extracted:\n        sentences.append(TABLE_TEXT_SENTENCE)", "    pass", ["tests/buddy/test_pdf_extract.py", "tests/backend/test_pdf_turns.py"]),
    ("m07-reads-upload-off", "buddy/source.py", "    if copy_offered and not (choice.pinned or start_over or reads_upload):", "    if copy_offered and not (choice.pinned or start_over):", ["tests/buddy/test_pdf_extract.py", "tests/backend/test_pdf_turns.py"]),
    ("m08-seccomp-allows-socket", "skills/pdf/_worker/lockdown.py", "        \"socket\": 41,\n", "", ["tests/skills/pdf/test_sandbox.py"]),
    ("m09-seccomp-allows-exec", "skills/pdf/_worker/lockdown.py", "        \"execve\": 59,\n", "", ["tests/skills/pdf/test_sandbox.py"]),
    ("m10-env-inherited", "skills/pdf/sandbox.py", "                env={},", "                env=dict(os.environ),", ["tests/skills/pdf/test_sandbox.py"]),
    ("m11-no-address-limit", "skills/pdf/_worker/lockdown.py", "            (\"RLIMIT_AS\", limits.address_space),\n", "", ["tests/skills/pdf"]),
    ("m12-dedupe-no-dtype", "skills/excel/dedupe.py", "input_path, sheet_name=sheet_name, dtype=object", "input_path, sheet_name=sheet_name", EXCEL_TESTS),
    ("m13-inspector-no-dtype", "buddy/inspector.py", "    return pd.read_excel(path, dtype=object)", "    return pd.read_excel(path)", EXCEL_TESTS),
    ("m14-empty-rows-no-dtype", "skills/excel/remove_empty_rows.py", "input_path, sheet_name=sheet_name, dtype=object", "input_path, sheet_name=sheet_name", EXCEL_TESTS),
    ("m15-skill-no-normal-form-check", "skills/pdf/extract_table.py", "            if textnorm.normalize_cell(cell) != cell:\n                return None", "            pass", ["tests/skills/pdf/test_extract_table.py", "tests/buddy/test_pdf_extract.py"]),
    ("m16-upload-no-magic", "backend/uploads.py", "    if not data.startswith(_PDF_MAGIC) or len(data) > sandbox.MAX_PDF_BYTES:", "    if len(data) > sandbox.MAX_PDF_BYTES:", ["tests/backend/test_pdf_turns.py", "tests/backend/test_uploads.py"]),
    ("m17-upload-page-cap-off-by-one", "backend/uploads.py", "    if pages > sandbox.MAX_PAGES:", "    if pages > sandbox.MAX_PAGES + 1:", ["tests/backend/test_pdf_turns.py"]),
    ("m18-worker-extract-no-page-cap", "skills/pdf/_worker/main.py", "        if counted > MAX_PAGES:\n            raise Refused(TOO_MANY_PAGES)", "        pass", ["tests/skills/pdf"]),
    ("m19-nfc-instead-of-nfkc", "skills/pdf/_worker/textnorm.py", "unicodedata.normalize(\"NFKC\", text)", "unicodedata.normalize(\"NFC\", text)", PDF_TESTS),
    ("m20-formula-cells", "skills/pdf/extract_table.py", "            cell.data_type = \"s\"\n", "", PDF_TESTS[:2]),
    ("m21-sentence-counts-from-skill", "buddy/inspector.py", "            rows, columns = size\n", "            rows, columns = size[0] + 1, size[1]\n", PDF_TESTS[1:]),
    ("m22-encrypted-accepted-by-extract", "skills/pdf/_worker/main.py", "        if getattr(pdf.doc, \"encryption\", None) is not None:\n            raise Refused(ENCRYPTED)", "        pass", PDF_TESTS),
    ("m23-busy-cap-3", "skills/pdf/sandbox.py", "MAX_CONCURRENT_WORKERS = 2", "MAX_CONCURRENT_WORKERS = 3", ["tests/skills/pdf/test_sandbox.py"]),
    ("m24-timeout-not-group-kill", "skills/pdf/sandbox.py", "        os.killpg(process.pid, signal.SIGKILL)", "        pass", ["tests/skills/pdf/test_sandbox.py"]),
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
                               cwd=wt, capture_output=True, text=True, timeout=1500)
            tail = (p.stdout.strip().splitlines() or ["?"])[-1]
            verdict = "killed" if p.returncode != 0 else "SURVIVED"
            failed = [ln for ln in p.stdout.splitlines() if ln.startswith("FAILED")][:2]
            lines.append(f"{name}: {verdict} ({tail}) {failed}")
        finally:
            path.write_text(src)
        print(lines[-1], flush=True)
    out = HERE / "out" / "mutations.txt"
    out.parent.mkdir(exist_ok=True)
    out.write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    run(Path(sys.argv[1]), sys.argv[2:])

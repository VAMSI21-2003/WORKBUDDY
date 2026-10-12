"""Measure the PDF worker's memory and time on 20-page PDFs (the VM sizing question).

    python scripts/measure_pdf_worker.py            # prints a table
    python scripts/measure_pdf_worker.py --markdown # the same, as a markdown table

Each PDF is read by the real worker (``skills/pdf/sandbox.py``), once per mode, in a fresh
process. Two memory figures come from the worker itself (``VmHWM`` for the peak resident set and
``VmPeak`` for the peak virtual size, the one the address-space limit counts), and a third from the
kernel: the largest resident set of any child, read by a small separate process that starts the
worker, so a big server process can't inflate it. Linux only.

The PDFs are built here with reportlab (a development dependency): a table on page 1 and 19 pages
of text (the largest PDF the skill reads), 20 pages that each hold a 25 x 8 ruled table (refused
for having several tables, but read in full first), 20 pages of dense text under a table, and
19 pages of 4-point text (about 25,000 characters a page), the heaviest text layer a 2 MiB file
is likely to hold.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from reportlab.lib import colors  # noqa: E402
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet  # noqa: E402
from reportlab.platypus import (  # noqa: E402
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Table,
    TableStyle,
)

from skills.pdf import sandbox  # noqa: E402

MODES = ("probe", "extract", "verify")


def _grid(rows: int, columns: int, seed: str) -> Table:
    data = [[f"{seed}{r}-{c}" for c in range(columns)] for r in range(rows)]
    table = Table(data, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [("GRID", (0, 0), (-1, -1), 0.25, colors.black), ("FONTSIZE", (0, 0), (-1, -1), 6)]
        )
    )
    return table


def build(folder: Path) -> dict[str, Path]:
    normal = getSampleStyleSheet()["Normal"]
    paragraph = "Lorem ipsum dolor sit amet consectetur adipiscing elit. " * 12
    tiny = ParagraphStyle("tiny", parent=normal, fontSize=4, leading=4.4)
    crowd = "Lorem ipsum dolor sit amet consectetur adipiscing elit 0123456789. " * 280
    cases: dict[str, list] = {
        "table + 19 text pages": [_grid(5, 3, "a")]
        + [item for n in range(19) for item in (PageBreak(), Paragraph(paragraph, normal))],
        "20 pages, a 25x8 table on each": [
            item
            for n in range(20)
            for item in ((PageBreak(),) if n else ()) + (_grid(25, 8, f"p{n}"),)
        ],
        "table + 19 dense text pages": [_grid(5, 3, "a")]
        + [item for n in range(19) for item in (PageBreak(), Paragraph(paragraph * 6, normal))],
        "table + 19 pages of 4-point text": [_grid(5, 3, "a")]
        + [item for n in range(19) for item in (PageBreak(), Paragraph(crowd, tiny))],
    }
    paths = {}
    for name, story in cases.items():
        path = folder / (name.replace(" ", "_").replace("+", "plus").replace(",", "") + ".pdf")
        SimpleDocTemplate(str(path), pagesize=A4).build(story)
        pages = sandbox.run("probe", path.read_bytes()).data.get("pages")
        assert pages == 20, f"{name} came out as {pages} pages, not 20"
        paths[name] = path
    return paths


_CHILD_PEAK = """
import resource, subprocess, sys
sys.path.insert(0, {root!r})
from skills.pdf import sandbox
data = open({pdf!r}, 'rb').read()
outcome = sandbox.run({mode!r}, data)
peak = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
print(outcome.ok, outcome.reason or '-', peak, outcome.peak_kb, outcome.vm_peak_kb)
"""


def measure(pdf: Path, mode: str) -> tuple[bool, str, int, int | None, int | None, float]:
    started = time.monotonic()
    done = subprocess.run(
        [sys.executable, "-I", "-c", _CHILD_PEAK.format(root=str(ROOT), pdf=str(pdf), mode=mode)],
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    elapsed = time.monotonic() - started
    ok, reason, child_peak, rss, vm = done.stdout.split()
    return (
        ok == "True",
        reason,
        int(child_peak),
        None if rss == "None" else int(rss),
        None if vm == "None" else int(vm),
        elapsed,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--markdown", action="store_true")
    args = parser.parse_args()
    rows = []
    with tempfile.TemporaryDirectory() as folder:
        for name, pdf in build(Path(folder)).items():
            size = pdf.stat().st_size
            for mode in MODES:
                ok, reason, child_peak, rss, vm, seconds = measure(pdf, mode)
                rows.append(
                    (
                        name,
                        f"{size / 1024:.0f} KiB",
                        mode,
                        "ok" if ok else reason,
                        child_peak,
                        rss,
                        vm,
                        seconds,
                    )
                )
    header = (
        "PDF",
        "size",
        "mode",
        "answer",
        "peak RSS (kernel, MiB)",
        "peak RSS (worker, MiB)",
        "peak virtual (MiB)",
        "seconds",
    )
    mib = lambda kb: "-" if kb is None else f"{kb / 1024:.0f}"  # noqa: E731
    lines = [header] + [
        (n, s, m, a, mib(c), mib(r), mib(v), f"{t:.1f}") for n, s, m, a, c, r, v, t in rows
    ]
    if args.markdown:
        print("| " + " | ".join(header) + " |")
        print("|" + "|".join("---" for _ in header) + "|")
        for line in lines[1:]:
            print("| " + " | ".join(line) + " |")
    else:
        widths = [max(len(str(line[i])) for line in lines) for i in range(len(header))]
        for line in lines:
            print("  ".join(str(cell).ljust(widths[i]) for i, cell in enumerate(line)))
    limit = sandbox.ADDRESS_SPACE_BYTES // 2**20
    print(f"\naddress-space limit: {limit} MiB, deadline: {sandbox.TIMEOUT_SECONDS:.0f} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

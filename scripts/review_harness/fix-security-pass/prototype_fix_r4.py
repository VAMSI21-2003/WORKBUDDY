"""Round 4: HEAD exported to DEST with the reviewer's prototype for R4-1 to R4-3 (workbook.py only).

    python scripts/review_harness/fix-security-pass/prototype_fix_r4.py DEST [--as-mib 768] [--seconds 60]

1. The whole scan (`_scan`: the zip checks, the expat pass and the reader walk) runs in a child process
   under an address-space cap and a wall-clock limit. Passing either refuses the file with a fixed
   sentence. What the child measured bounds every later in-process load of the same file, because those
   are the same openpyxl load, so no list of part names or element kinds has to be complete (R4-1, R4-2).
   A MemoryError in the child is re-raised (the scan's catch-alls would otherwise read it as "can't
   be read" and pass the file).
2. The text cap counts what CPython stores, not characters: 1, 2 or 4 bytes per character by the widest
   character in the cell, capped at 4,000,000 (R4-3). Measure the turn at the cap for all three widths
   before adopting the figure.
"""
import argparse
import io
import subprocess
import tarfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]

CHILD_SCAN = '''

SCAN_ADDRESS_SPACE = {as_mib} * MIB
SCAN_SECONDS = {seconds}
TOO_COSTLY = "the workbook needs more memory or time to open than I allow, so I won't open it"
_SCAN_CHILD = (
    "import resource, sys\\n"
    "cap = int(sys.argv[2])\\n"
    "resource.setrlimit(resource.RLIMIT_AS, (cap, cap))\\n"
    "sys.path.insert(0, sys.argv[3])\\n"
    "from skills.excel.workbook import _scan_here\\n"
    "verdict = _scan_here(sys.argv[1])\\n"
    "sys.stdout.write('PASS' if verdict is None else 'REFUSE:' + verdict)\\n"
)


def _scan(input_path: str) -> str | None:
    """`_scan_here` in a child process with a memory and a time limit: a file that needs more is refused."""
    import subprocess
    import sys

    root = str(Path(__file__).resolve().parents[2])
    try:
        done = subprocess.run(
            [sys.executable, "-I", "-c", _SCAN_CHILD, input_path, str(SCAN_ADDRESS_SPACE), root],
            capture_output=True, text=True, timeout=SCAN_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return TOO_COSTLY
    if done.returncode != 0:
        return TOO_COSTLY
    if done.stdout == "PASS":
        return None
    if done.stdout.startswith("REFUSE:"):
        return done.stdout[len("REFUSE:"):]
    return TOO_COSTLY


def _scan_here(input_path: str) -> str | None:'''

EDITS = [
    ('def _scan(input_path: str) -> str | None:', None),  # replaced by CHILD_SCAN below
    ("MAX_TEXT_CHARS = 10_000_000", "MAX_TEXT_CHARS = 4_000_000  # bytes CPython stores: 1, 2 or 4 per character"),
    ("                                chars += len(value)\n",
     "                                widest_char = max(value, default=\"\")\n"
     "                                width = 4 if widest_char > \"\\uffff\" else 2 if widest_char > \"\\xff\" else 1\n"
     "                                chars += len(value) * width\n"),
    # A MemoryError under the child's cap must refuse, not read as "the reader can't read it".
    ("    except Exception:  # noqa: BLE001 — damaged or unreadable: the callers' own reads refuse it\n",
     "    except MemoryError:\n        raise\n"
     "    except Exception:  # noqa: BLE001 — damaged or unreadable: the callers' own reads refuse it\n"),
    ("    except Exception:  # noqa: BLE001 — the same reader would fail in pandas, with a refusal\n",
     "    except MemoryError:\n        raise\n"
     "    except Exception:  # noqa: BLE001 — the same reader would fail in pandas, with a refusal\n"),
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("dest")
    ap.add_argument("--as-mib", type=int, default=768)
    ap.add_argument("--seconds", type=int, default=60)
    a = ap.parse_args()
    dest = Path(a.dest)
    dest.mkdir(parents=True, exist_ok=True)
    data = subprocess.run(["git", "-C", str(REPO), "archive", "HEAD"], capture_output=True, check=True).stdout
    tarfile.open(fileobj=io.BytesIO(data)).extractall(dest, filter="data")
    target = dest / "skills/excel/workbook.py"
    text = target.read_text()
    for old, new in EDITS:
        assert text.count(old) == 1, old
        if new is None:
            new = CHILD_SCAN.format(as_mib=a.as_mib, seconds=a.seconds).lstrip("\n")
        text = text.replace(old, new)
    target.write_text(text)
    print(f"prototype written to {dest}")


if __name__ == "__main__":
    main()

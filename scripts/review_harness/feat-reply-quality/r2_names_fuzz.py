"""Round 2 probe N: property fuzz of `backend.names.download_name` / `display_names` against an independent oracle
and against the round-1 implementation (a worktree of 8f0d708; `backend/names.py` does not exist on origin/main).

The oracle builds the stored name the way the skills do (`<stem>_<operation>_<session>-task-<n>[...].<ext>`) from
known parts, so the expected friendly name is known by construction (no regex on the answer).

    python r2_names_fuzz.py [--prev DIR] [--seed N] [--n N]
"""
import argparse
import importlib
import random
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ap = argparse.ArgumentParser()
ap.add_argument("--tree", default=str(HERE.parents[2]))
ap.add_argument("--prev", default=None)
ap.add_argument("--seed", type=int, default=1)
ap.add_argument("--n", type=int, default=20000)
A = ap.parse_args()


def load(tree):
    for m in [m for m in sys.modules if m == "backend" or m.startswith("backend.") or m == "buddy" or m.startswith("buddy.")]:
        del sys.modules[m]
    sys.path.insert(0, str(tree))
    mod = importlib.import_module("backend.names")
    assert Path(mod.__file__).resolve().is_relative_to(Path(tree).resolve()), mod.__file__
    sys.path.pop(0)
    return mod


head = load(A.tree)
base = load(A.prev) if A.prev else None
from backend.sessions import DeliveredFile  # noqa: E402  (the head's, loaded last if base is None)

MAX = 100
OPS = {"deduplicated": "deduplicated", "no_empty_rows": "cleaned"}
rng = random.Random(A.seed)
ALPHA = list("abcXYZ019 _-.()") + ["é", "—", "_deduplicated", "_no_empty_rows", "-task-", "task", "no_empty_rows", "deduplicated"]


def stem():
    n = rng.choice([0, 1, 2, 5, 20, 60, 90, 95, 99, 100])
    s = "".join(rng.choice(ALPHA) for _ in range(n))
    return s[:100]


def keyname():
    return rng.choice(["session-1", "0b3d5f912fc1d0afdcbd0e46f5ec7eed", "a.b+c", "k(1)", "s_s", "ab"])


problems = []
checked = 0
for _ in range(A.n):
    st, key = stem(), keyname()
    ops = [rng.choice(list(OPS)) for _ in range(rng.choice([1, 1, 1, 2, 3]))]
    ext = rng.choice([".xlsx", ".XLSX", ".xls", ".csv"])
    stored = st + "".join(f"_{o}_{key}-task-{rng.randint(0, 12)}" for o in ops) + ext
    tail = "_" + "_".join(OPS[o] for o in ops) + ext
    expected_stem = st[: max(1, MAX - len(tail))]
    got = head.download_name(stored, key)
    checked += 1
    # Where the stem itself contains a complete generated step for this key the oracle is ambiguous; skip those
    if re.search(rf"_(?:deduplicated|no_empty_rows)_{re.escape(key)}-task-\d+", st):
        continue
    want = expected_stem + tail
    if got != want:
        problems.append(("mismatch", stored, got, want))
    if re.search(rf"{re.escape(key)}-task-\d", got) and not re.search(rf"{re.escape(key)}-task-\d", st):
        problems.append(("id_left", stored, got, ""))
    if not got.endswith(ext):
        problems.append(("extension", stored, got, ""))
    if len(got) > MAX and len(tail) < MAX:
        problems.append(("too_long", stored, got, ""))
    if head.download_name(got, key) != got:
        problems.append(("not_idempotent", stored, got, head.download_name(got, key)))

print(f"checked {checked} stored names; {len(problems)} problems")
for p in problems[:15]:
    print("  ", p)

# Names that are not the generated shape
odd = [
    ("report_somethingnew_s-task-3.xlsx", "s"), ("weird-task-5.xlsx", "s"), ("report.xlsx", "s"),
    ("s-task-1.xlsx", "s"), ("_deduplicated_s-task-1.xlsx", "s"), ("a_deduplicated_s-task-1", "s"),
    ("a_deduplicated_s-task-1.tar.gz", "s"), ("a_deduplicated_s-task-1.xlsx.exe", "s"),
    ("a_deduplicated_s-task-1_deduplicated_s-task-2.xlsx", "s"), ("a_deduplicated_s-task-1_foo_s-task-2.xlsx", "s"),
    ("a_deduplicated_S-TASK-1.xlsx", "s"), ("a_deduplicated_s-task-.xlsx", "s"), ("a_deduplicated_s-task-1 .xlsx", "s"),
    ("a\nb_deduplicated_s-task-1.xlsx", "s"), ("a\"b_deduplicated_s-task-1.xlsx", "s"), ("../a_deduplicated_s-task-1.xlsx", "s"),
]
print("\nstored -> head [-> round 1]")
for stored, key in odd:
    h = head.download_name(stored, key)
    b = base.download_name(stored, key) if base else None
    print(f"  {stored!r:62} -> {h!r}" + (f"   r1: {b!r}" if base else ""))

# display_names: numbering, case folding, a person's file already called "x (2).xlsx", and the 100-character edge
from datetime import UTC, datetime  # noqa: E402


def df(i, name):
    return DeliveredFile(file_id=f"d{i}", name=name, size=1, sha256="0" * 64, path=Path("/x"), turn_id="t", delivered_at=datetime.now(UTC))


KEY = "s"
cases = {
    "three same": [f"r_deduplicated_{KEY}-task-1.xlsx"] * 3,
    "case": [f"R_deduplicated_{KEY}-task-1.xlsx", f"r_deduplicated_{KEY}-task-1.xlsx"],
    "user file already named (2)": [f"r_deduplicated_{KEY}-task-1.xlsx", f"r_deduplicated_{KEY}-task-1.xlsx", "r_deduplicated (2).xlsx"],
    "numbered vs real": ["r_deduplicated (2).xlsx", f"r_deduplicated_{KEY}-task-1.xlsx", f"r_deduplicated_{KEY}-task-1.xlsx"],
    "long": [("x" * 95) + f"_deduplicated_{KEY}-task-1.xlsx"] * 2,
    "no extension": ["abc", "abc"],
}
for label, names in cases.items():
    files = [df(i, n) for i, n in enumerate(names)]
    got = head.display_names(KEY, files)
    vals = list(got.values())
    dup = len({v.casefold() for v in vals}) != len(vals)
    lens = [len(v) for v in vals]
    print(f"\n{label}: {vals}  collision={dup} max_len={max(lens)}")
    if base:
        print("   r1:  ", list(base.display_names(KEY, files).values()))

# ruff: noqa: E501 — reviewer tooling, not product code
"""Mutations of the branch's guards, each in its own ``git archive`` copy of a commit.

    python scripts/review_harness/feat-release-safety/mutations.py [--rev 0ea40c4] [--only M01]

For each mutation: apply one text replacement (it must match exactly once), run the named tests
(or command), and record whether they failed. A guard is vacuous if its mutation still passes.
Output: one line per mutation on stdout (``out/mutations.txt``).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

SURVIVES = "tests/deploy/test_saved_data_survives.py"
LOCKS = "tests/deploy/test_dependency_locks.py"
RUFFT = "tests/deploy/test_ruff_baseline.py"

# (id, what, file, old, new, command)
MUTATIONS = [
    ("M00", "no mutation (the copy itself must pass)", "README.md", "", "",
     ["python", "-m", "pytest", "-q", "tests/deploy"]),
    ("M01", "session.json gains a key (format change, no new fixture)", "backend/store.py",
     '            "turn_ids": [t.turn_id for t in record.turns],\n',
     '            "turn_ids": [t.turn_id for t in record.turns],\n            "owner": None,\n',
     ["python", "-m", "pytest", "-q", "-x", SURVIVES + "::test_what_the_server_writes_today_has_the_shape_of_the_newest_fixture"]),
    ("M02", "turn record gains a key", "backend/store.py",
     '            "reply_source": turn.reply_source,\n',
     '            "reply_source": turn.reply_source,\n            "voice": "v2",\n',
     ["python", "-m", "pytest", "-q", "-x", SURVIVES + "::test_what_the_server_writes_today_has_the_shape_of_the_newest_fixture"]),
    ("M03", "a turn field changes type (size int -> str)", "backend/store.py",
     '                    "size": d.size,\n                    "sha256": d.sha256,\n                    "delivered_at"',
     '                    "size": str(d.size),\n                    "sha256": d.sha256,\n                    "delivered_at"',
     ["python", "-m", "pytest", "-q", "-x", SURVIVES + "::test_what_the_server_writes_today_has_the_shape_of_the_newest_fixture"]),
    ("M04", "FORMAT_VERSION bumped with no migration and no fixture", "backend/store.py",
     "FORMAT_VERSION = 1\n", "FORMAT_VERSION = 2\n",
     ["python", "-m", "pytest", "-q", SURVIVES]),
    ("M05", "reader requires a new session key (old data stops opening)", "backend/store.py",
     '{"version", "created_at", "next_seq", "active_file_id", "files", "turn_ids", "context"},',
     '{"version", "created_at", "next_seq", "active_file_id", "files", "turn_ids", "context", "owner"},',
     ["python", "-m", "pytest", "-q", SURVIVES, "-k", "not shape"]),
    ("M06", "context (buddy) gains a saved key", "buddy/context.py",
     None, None,  # filled in below from the file
     ["python", "-m", "pytest", "-q", "-x", SURVIVES + "::test_what_the_server_writes_today_has_the_shape_of_the_newest_fixture"]),
    ("M07", "Dockerfile installs without --require-hashes", "Dockerfile",
     "pip install --require-hashes --no-deps -r requirements-runtime.lock",
     "pip install --no-deps -r requirements-runtime.lock",
     ["python", "-m", "pytest", "-q", LOCKS]),
    ("M08", "Dockerfile installs the ranges, not the lock", "Dockerfile",
     "pip install --require-hashes --no-deps -r requirements-runtime.lock",
     "pip install -r requirements-runtime.txt",
     ["python", "-m", "pytest", "-q", LOCKS]),
    ("M09", "a new runtime requirement with no pin", "requirements-runtime.txt",
     "jinja2>=3.1,<4\n", "jinja2>=3.1,<4\nrich>=13\n",
     ["python", "-m", "pytest", "-q", LOCKS]),
    ("M10", "a range tightened past the locked version", "requirements-runtime.txt",
     "fastapi>=0.142,<0.143\n", "fastapi>=0.143,<0.144\n",
     ["python", "-m", "pytest", "-q", LOCKS]),
    ("M11", "a runtime-lock hash changed (dev lock left alone)", "requirements-runtime.lock",
     "    --hash=sha256:7defb6ca730676dfc99a299768e2769f51585fcb3dc960ea71aacc368929d65e \\\n",
     "    --hash=sha256:0000000000000000000000000000000000000000000000000000000000000000 \\\n",
     ["python", "-m", "pytest", "-q", LOCKS]),
    ("M12", "the build context stops letting the lock in", ".dockerignore",
     "!requirements-runtime.lock\n", "",
     ["python", "-m", "pytest", "-q", LOCKS]),
    ("M13", "a new ruff error in buddy/ (CI lint job)", "buddy/brain.py",
     None, None,  # appended below
     [sys.executable, "scripts/check_ruff_baseline.py"]),
    ("M14", "baseline compare stops seeing new files", "scripts/check_ruff_baseline.py",
     "        if n > baseline.get((file, rule), 0)\n",
     "        if (file, rule) in baseline and n > baseline[(file, rule)]\n",
     ["python", "-m", "pytest", "-q", RUFFT]),
    ("M15", "the fixture's files are rewritten by relocate (not just session.json)", "tests/deploy/data_scenario.py",
     'for record in (folder / "sessions").glob("*/session.json"):',
     'for record in [p for p in folder.rglob("*.json")]:',
     ["python", "-m", "pytest", "-q", SURVIVES]),
]


def context_mutation(tree: Path) -> tuple[str, str]:
    text = (tree / "buddy/context.py").read_text(encoding="utf-8")
    marker = "def to_dict(self)"
    assert marker in text
    # Add a key to whatever to_dict returns: wrap the return.
    start = text.index(marker)
    ret = text.index("return {", start)
    return text[ret:ret + len("return {")], 'return {"review_probe": 1, '


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--rev", default="HEAD")
    p.add_argument("--only")
    p.add_argument("--empty-dirs", action="store_true",
                   help="recreate the fixtures' empty folders, as in the implementer's checkout")
    a = p.parse_args()
    for mid, what, file, old, new, cmd in MUTATIONS:
        if a.only and mid != a.only:
            continue
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp) / "t"
            tree.mkdir()
            archive = Path(tmp) / "a.tar"
            subprocess.run(["git", "archive", "-o", str(archive), a.rev], cwd=ROOT, check=True)
            with tarfile.open(archive) as t:
                t.extractall(tree, filter="data")
            if a.empty_dirs:
                # git keeps no empty folder: recreate the ones the working tree's fixtures have,
                # so the copy matches the implementer's checkout (see the round-1 finding).
                for d in (ROOT / "tests/deploy/fixtures").rglob("*"):
                    if d.is_dir() and not any(d.iterdir()):
                        (tree / d.relative_to(ROOT)).mkdir(parents=True, exist_ok=True)
            target = tree / file
            text = target.read_text(encoding="utf-8")
            if mid == "M00":
                old, new = text, text
            elif mid == "M06":
                old, new = context_mutation(tree)
                at = text.index(old, text.index("def to_dict(self)"))
                text = text[:at] + "@@MUT@@" + text[at + len(old):]
                old, new = "@@MUT@@", new
            elif mid == "M13":
                old, new = text, text + "\nimport os\n"
            if text.count(old) != 1:
                print(f"{mid} SKIP  pattern matched {text.count(old)} times  ({what})", flush=True)
                continue
            target.write_text(text.replace(old, new), encoding="utf-8")
            done = subprocess.run(cmd, cwd=tree, capture_output=True, text=True, timeout=900)
            tail = (done.stdout.strip().splitlines() or [""])[-1][:110]
            verdict = "CAUGHT" if done.returncode != 0 else "SURVIVED"
            print(f"{mid} {verdict:8} exit={done.returncode}  {what}  | {tail}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

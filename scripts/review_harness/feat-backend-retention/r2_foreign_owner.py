# ruff: noqa: E501 — reviewer tooling, not product code
"""Round 2: ``remove_tree`` on a read-only file the process doesn't own (Linux, not root).

    python scripts/review_harness/feat-backend-retention/r2_foreign_owner.py [--tree DIR] > out/r2_foreign_owner_<tree>.json

Must be started as root: it builds, in a temporary folder, a session-like tree
owned by ``nobody`` holding one ``0o444`` file owned by root, then calls
``remove_tree`` (its source, from ``--tree``'s ``backend/retention.py``,
default: this checkout) as
``nobody``. On Linux, unlinking needs write access to the folder, not to the
file, so the round-1 code removes it; the round-2 code first tries to chmod a
file it doesn't own (``EPERM``).
"""

from __future__ import annotations

import json
import os
import pwd
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]

CHILD = r"""
import ast, json, os, stat, sys
from pathlib import Path
# Only remove_tree is taken from the tree's backend/retention.py (the rest of the
# package needs site-packages that user "nobody" can't see).
src = (Path(sys.argv[1]) / "backend" / "retention.py").read_text()
fn = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == "remove_tree")
exec(compile(ast.Module([fn], []), "remove_tree", "exec"))
target = Path(sys.argv[2])
try:
    remove_tree(target)
    print(json.dumps({"raised": None, "still_there": target.exists()}))
except OSError as e:
    print(json.dumps({"raised": type(e).__name__ + ": errno " + str(e.errno), "still_there": target.exists()}))
"""


def main() -> None:
    tree = REPO
    if "--tree" in sys.argv:
        tree = Path(sys.argv[sys.argv.index("--tree") + 1]).resolve()
    nobody = pwd.getpwnam("nobody")
    with tempfile.TemporaryDirectory() as tmp:
        os.chmod(tmp, 0o755)
        parent = Path(tmp) / "outputs"  # the data root's folder, owned by nobody
        parent.mkdir()
        session = parent / "session"
        session.mkdir()
        (session / "t0001").mkdir()
        f = session / "t0001" / "report.xlsx"
        f.write_bytes(b"x")
        os.chmod(f, 0o444)  # owned by root
        for d in (parent, session, session / "t0001"):
            os.chown(d, nobody.pw_uid, nobody.pw_gid)
        proc = subprocess.run(
            ["setpriv", f"--reuid={nobody.pw_uid}", f"--regid={nobody.pw_gid}", "--clear-groups",
             sys.executable, "-I", "-c", CHILD, str(tree), str(session)],
            capture_output=True, text=True,
        )
        out = {"tree": str(tree), "stdout": proc.stdout.strip(), "stderr_tail": proc.stderr.strip()[-300:]}
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()

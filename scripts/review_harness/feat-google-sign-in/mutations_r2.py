# ruff: noqa: E501 — reviewer tooling, not product code
"""Round-2 mutations: round 1's survivors again, and the round-1 fixes, each in a ``git archive HEAD`` copy.

    python scripts/review_harness/feat-google-sign-in/mutations_r2.py [--jobs 3] [--only N01] [--wide] > out/mutations_r2.txt

Reuses ``mutations.py`` (copy, patch, run); only the list and the test files differ. A mutation
may be a list of (file, old, new) patches, all applied. M00 = unmutated, must pass.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import mutations as r1  # noqa: E402  (same folder)

TESTS = [*r1.TESTS, "tests/web/test_shared_principals.py"]

IDX_OLD = '    if session_id is None and principal is not None and principal.kind == "google":'
IDX_NEW = "    if session_id is None and principal is not None:"
LF_OLD = "        if not owner.startswith(GOOGLE_OWNER_PREFIX):\n            return None\n"

# (id, [(file, old, new)], what)
MUTATIONS = [
    ("M00", [], "unmutated copy"),
    # Round 1's survivors of the default/wide runs, again.
    ("M08", [("backend/oidc.py", "            del self._pending[key]\n            if expires", "            if expires")], "pending login not single-use"),
    ("M12", [("backend/web/auth.py", "    clear_pointer(response)  # the last person's session isn't this person's\n", "")], "callback keeps the last person's pointer"),
    ("M19", [("backend/sessions.py", "candidates = dict(self._by_owner.get(owner, {}))", "candidates = {k: v for d in self._by_owner.values() for k, v in d.items()}")], "latest_for ignores the owner"),
    ("M26", [("backend/oidc.py", 'if document.get("issuer") not in self._accepted_issuers():', "if False:")], "discovery issuer not checked"),
    ("M30", [("backend/oidc.py", "email=email.strip().lower()", "email=email.strip()")], "email not lower-cased"),
    # The round-1 fixes.
    ("N01", [("backend/sessions.py", LF_OLD, "")], "H1: latest_for's person-only guard removed (index guard kept)"),
    ("N02", [("backend/web/routes.py", IDX_OLD, IDX_NEW)], "H1: index's kind guard removed (latest_for guard kept): expected equivalent"),
    ("N03", [("backend/sessions.py", LF_OLD, ""), ("backend/web/routes.py", IDX_OLD, IDX_NEW)], "H1: both guards removed (round-1 code)"),
    ("N04", [("backend/sessions.py", LF_OLD, '        if owner in ("demo", "dev"):\n            return None\n'), ("backend/web/routes.py", IDX_OLD, IDX_NEW)], "H1: operator left out of both guards"),
    ("N05", [("backend/sessions.py", LF_OLD, '        if owner in ("demo",):\n            return None\n'), ("backend/web/routes.py", IDX_OLD, IDX_NEW)], "H1: only demo guarded (dev and operator land)"),
    ("N06", [("backend/web/auth.py", 'return _refused(request, "no_pending_login", clear_login=False)', 'return _refused(request, "no_pending_login")')], "L3: binder cleared on no_pending_login again"),
    ("N07", [("backend/web/auth.py", "    if clear_login:\n        _clear_login_cookie(response)\n", "")], "L3 overreach: binder never cleared on any refusal"),
    ("N08", [("backend/main.py", "    if settings.oidc_issuer != GOOGLE_ISSUER:\n", "    if False:\n")], "issuer warning never logged"),
    ("N09", [("backend/web/auth.py", "    _clear_login_cookie(response)\n    clear_pointer(response)  # the last", "    clear_pointer(response)  # the last")], "binder not cleared after a successful sign-in"),
]


def run(m: tuple) -> str:
    mid, patches, what = m
    with tempfile.TemporaryDirectory() as d:
        tree = Path(d) / "tree"
        r1.copy_tree(tree)
        for path, old, new in patches:
            f = tree / path
            text = f.read_text(encoding="utf-8")
            n = text.count(old)
            if n != 1:
                return f"{mid} PATCH_FAILED ({path}: {n} matches) {what}"
            f.write_text(text.replace(old, new), encoding="utf-8")
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        p = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", "--deselect", "tests/backend/test_retention.py::test_a_process_that_is_not_root_can_delete_read_only_uploads", *TESTS], cwd=tree, capture_output=True, text=True, env=env, timeout=1800)
        tail = [line for line in p.stdout.splitlines() if line.startswith("FAILED") or " passed" in line or " failed" in line or "error" in line.lower()][:3]
        verdict = ("PASSED" if p.returncode == 0 else "FAILED") if mid == "M00" else ("SURVIVED" if p.returncode == 0 else "KILLED")
        return f"{mid} {verdict} {what} | {' | '.join(tail)}"


def main() -> None:
    a = argparse.ArgumentParser()
    a.add_argument("--jobs", type=int, default=3)
    a.add_argument("--only")
    a.add_argument("--wide", action="store_true", help="tests/backend, tests/web, tests/e2e and the saved-data test instead of TESTS")
    args = a.parse_args()
    if args.wide:
        TESTS[:] = ["tests/backend", "tests/web", "tests/e2e", "tests/deploy/test_saved_data_survives.py"]
    only = set(args.only.split(",")) if args.only else None
    todo = [m for m in MUTATIONS if not only or m[0] in only]
    with cf.ThreadPoolExecutor(args.jobs) as ex:
        for line in ex.map(run, todo):
            print(line, flush=True)


if __name__ == "__main__":
    _ = subprocess
    main()

# ruff: noqa: E501 — reviewer tooling, not product code
"""One-line mutations of the sign-in and ownership guards, each in a ``git archive HEAD`` copy.

    python scripts/review_harness/feat-google-sign-in/mutations.py [--jobs 3] [--only Mnn] > out/mutations.txt

For each mutation the copy is patched (exact text, must match once unless ``all``), the tests in
``TESTS`` run, and the line says KILLED (a test failed) or SURVIVED. M00 is the unmutated copy and
must pass. Nothing in this checkout is touched.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TESTS = [
    "tests/backend/test_signin.py",
    "tests/backend/test_ownership.py",
    "tests/backend/test_person_limits.py",
    "tests/backend/test_signin_settings.py",
    "tests/backend/test_access.py",
    "tests/backend/test_errors_and_secrets.py",
    "tests/backend/test_app_basics.py",
    "tests/backend/test_sessions.py",
    "tests/backend/test_store.py",
    "tests/backend/test_ratelimit.py",
    "tests/web/test_signin_page.py",
    "tests/web/test_gate_and_session.py",
    "tests/deploy/test_saved_data_survives.py",
]

# (id, file, old, new, replace_all, what)
MUTATIONS = [
    ("M00", None, None, None, False, "unmutated copy"),
    ("M01", "backend/gate.py", "        if declared is None or not declared.credential:\n            return None\n        session_id", "        return None\n        session_id", False, "gate never checks ownership (writer tried)"),
    ("M02", "backend/sessions.py", "        return key if owns and same else None", "        return key if owns else None", False, "authorize ignores the id stored in owner.json"),
    ("M03", "backend/oidc.py", 'raise SignInError("token_nonce")', "pass", False, "nonce not checked"),
    ("M04", "backend/oidc.py", 'raise SignInError("email_not_verified")', "pass", False, "email_verified not checked"),
    ("M05", "backend/oidc.py", 'raise SignInError("token_azp")', "pass", True, "azp not checked"),
    ("M06", "backend/oidc.py", 'if header.get("alg") != "RS256":', "if False:", False, "header alg not checked (PyJWT algorithms=[RS256] still is: expected equivalent)"),
    ("M07", "backend/oidc.py", "if not hmac.compare_digest(binder_hash, _hash(binder)):", "if False:", False, "binder cookie not compared"),
    ("M08", "backend/oidc.py", "            del self._pending[key]\n            if expires", "            if expires", False, "pending login not single-use"),
    ("M09", "backend/oidc.py", "            if expires <= self._clock():\n                return None\n            return Pending", "            return Pending", False, "pending login never expires"),
    ("M10", "backend/access.py", "if self._allowed and signin.email.lower() not in self._allowed:", "if False:", False, "allowed list not re-checked per request"),
    ("M11", "backend/web/auth.py", "if not access.email_allowed(identity.email):", "if False:", False, "allowed list not checked at sign-in"),
    ("M12", "backend/web/auth.py", "    clear_pointer(response)  # the last person's session isn't this person's\n", "", False, "callback keeps the last person's pointer"),
    ("M13", "backend/web/auth.py", "    clear_pointer(response)\n    return response\n", "    return response\n", False, "sign-out keeps the pointer"),
    ("M14", "backend/access.py", "            self.signins.revoke(token)\n", "            pass\n", False, "sign-out doesn't end the sign-in"),
    ("M15", "backend/signin_store.py", "            if signin.expires_at <= self._clock():\n                self._remove_locked(key)\n                return None\n", "", False, "sign-in never expires on lookup"),
    ("M16", "backend/signin_store.py", "            self._write(key, signin)\n", "", False, "sign-in not persisted"),
    ("M17", "backend/access.py", "if settings.demo_password is not None and not production", "if settings.demo_password is not None", False, "demo password a credential in production"),
    ("M18", "backend/ratelimit.py", "    if owner is not None:\n        charges +=", "    if False:\n        charges +=", False, "no per-person charges"),
    ("M19", "backend/sessions.py", "candidates = dict(self._by_owner.get(owner, {}))", "candidates = {k: v for d in self._by_owner.values() for k, v in d.items()}", False, "latest_for ignores the owner"),
    ("M20", "backend/web/routes.py", "        record = registry.get(session_id, principal)", "        record = next((r for r in registry._sessions.values() if r.session_id == session_id), None)", False, "GET / opens the pointer's session without the owner check"),
    ("M21", "backend/store.py", "            self.write_owner(folder.name, None, None)\n", "", False, "migration doesn't write owner.json"),
    ("M22", "backend/logging_filter.py", "(?:code|state|id_token", "(?:zzcode|zzstate|id_token", False, "callback code/state not masked in logs"),
    ("M23", "backend/logging_filter.py", '    (re.compile(r"(/ui/s/)[A-Za-z0-9_\\-%.]+"), r"\\1" + REDACTED),\n', "", False, "/ui/s/<id> not masked in logs"),
    ("M24", "backend/oidc.py", '"code_verifier": verifier,', '"code_verifier": "x" * 64,', False, "PKCE verifier not sent"),
    ("M25", "backend/oidc.py", "issuer=list(self._accepted_issuers()),", "issuer=None,", False, "token issuer not checked"),
    ("M26", "backend/oidc.py", 'if document.get("issuer") not in self._accepted_issuers():', "if False:", False, "discovery issuer not checked"),
    ("M27", "backend/oidc.py", "if not stale and not may_refetch:", "if False:", False, "JWKS refetch not throttled"),
    ("M28", "backend/access.py", 'return f"{COOKIE_NAME}={token}; Path=/; Secure; HttpOnly; SameSite=Strict"', 'return f"{COOKIE_NAME}={token}; Path=/; Secure; HttpOnly; SameSite=Lax"', False, "access cookie SameSite=Lax"),
    ("M29", "backend/settings.py", "            if not self.google_enabled:\n                raise SettingsError(", "            if False:\n                raise SettingsError(", False, "production starts without Google"),
    ("M30", "backend/oidc.py", "email=email.strip().lower()", "email=email.strip()", False, "email not lower-cased"),
    ("M31", "backend/gate.py", "        if session_id is None:\n            return None\n        result", "        if session_id is None or scope['path'].startswith('/ui/'):\n            return None\n        result", False, "gate skips page routes (session_of still checks: expected equivalent for responses)"),
    ("M32", "backend/sessions.py", "        if key in self._deleting:\n            return None\n        record = self._sessions.get(key)\n        if record is not None:\n            owner, stored_id", "        record = self._sessions.get(key)\n        if record is not None:\n            owner, stored_id", False, "authorize serves a session being deleted"),
    ("M33", "backend/web/auth.py", "    if pending is None:\n        return _refused(request, \"no_pending_login\")", "    if pending is None:\n        from backend.oidc import Pending\n        pending = Pending(nonce='', verifier='')", False, "callback without a pending login carries on"),
    ("M35", "backend/web/routes.py", "    if session_id is None and principal is not None:\n        session_id = registry.latest_for(principal.owner)", "    if session_id is None and principal is not None:\n        session_id = registry.latest_for(principal.owner)\n    if session_id is not None and pointer is None and principal is not None and principal.kind != 'google':\n        session_id = None", False, "PROTOTYPE FIX for H1 (not a mutation): latest_for only for google principals; suite must still pass"),
    ("M34", "backend/signin_store.py", "if not (isinstance(owner, str) and owner.startswith(\"google:\") and len(owner) > 7):", "if not isinstance(owner, str):", False, "a sign-in file may name any owner (e.g. operator)"),
]


def copy_tree(dest: Path) -> None:
    archive = subprocess.run(["git", "archive", "HEAD"], cwd=ROOT, check=True, capture_output=True).stdout
    tmp = dest.with_suffix(".tar")
    tmp.write_bytes(archive)
    with tarfile.open(tmp) as tar:
        tar.extractall(dest)
    tmp.unlink()


def run(m: tuple) -> str:
    mid, path, old, new, all_, what = m
    with tempfile.TemporaryDirectory() as d:
        tree = Path(d) / "tree"
        copy_tree(tree)
        if path is not None:
            f = tree / path
            text = f.read_text(encoding="utf-8")
            n = text.count(old)
            if n == 0 or (n > 1 and not all_):
                return f"{mid} PATCH_FAILED ({n} matches) {what}"
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
    a.add_argument("--wide", action="store_true", help="run tests/backend, tests/web and tests/deploy/test_saved_data_survives.py instead of TESTS")
    args = a.parse_args()
    if args.wide:
        TESTS[:] = ["tests/backend", "tests/web", "tests/deploy/test_saved_data_survives.py"]
    only = set(args.only.split(",")) if args.only else None
    todo = [m for m in MUTATIONS if not only or m[0] in only]
    with cf.ThreadPoolExecutor(args.jobs) as ex:
        for line in ex.map(run, todo):
            print(line, flush=True)


if __name__ == "__main__":
    main()

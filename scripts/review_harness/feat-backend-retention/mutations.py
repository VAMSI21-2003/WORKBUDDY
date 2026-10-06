# ruff: noqa: E501 — reviewer tooling, not product code
"""PR-4 mutations: one-line changes to the new guards, each in a throwaway copy of HEAD.

    python scripts/review_harness/feat-backend-retention/mutations.py [--jobs 4] [--only NAME] > out/mutations.txt

Each mutation replaces exactly one occurrence of a string (it refuses if the
string isn't there exactly once), then runs ``pytest -x -q tests/backend``.
KILLED names the first failing test; SURVIVED means no test noticed.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

M = [
    ("gate_no_401", "backend/gate.py", "            return _refusal(unauthorized()), declared, ip, False", "            pass"),
    ("dependency_no_401", "backend/access.py", "    if not result.valid:\n        raise unauthorized()", "    if False:\n        raise unauthorized()"),
    ("wrong_credential_costs_1", "backend/ratelimit.py", "return [(GENERAL, 2)]", "return [(GENERAL, 1)]"),
    ("poll_non200_free", "backend/ratelimit.py", "if declared.poll and valid and status != 200:", "if False:"),
    ("access_401_no_post_charge", "backend/ratelimit.py", "if status == 401 and not (declared.credential and not valid):", "if False:"),
    ("refusal_still_allowed", "backend/ratelimit.py", "            if wait:\n                return Decision(False, max(wait, 1))", "            if False:\n                return Decision(False, max(wait, 1))"),
    ("refusal_charges", "backend/ratelimit.py", "            if wait:\n                return Decision(False, max(wait, 1))", "            if wait:\n                for _, cost, bucket in states:\n                    bucket.tokens = max(bucket.tokens - cost, 0.0)\n                return Decision(False, max(wait, 1))"),
    ("retry_after_plus_one", "backend/ratelimit.py", "return Decision(False, max(wait, 1))", "return Decision(False, max(wait + 1, 1))"),
    ("xff_leftmost", "backend/ratelimit.py", "candidate = entries[-trusted_proxy_hops]", "candidate = entries[0]"),
    ("xff_ignore_hops_zero", "backend/ratelimit.py", "    if trusted_proxy_hops <= 0:\n        return peer", "    if trusted_proxy_hops < 0:\n        return peer"),
    ("origin_two_headers_ok", "backend/access.py", "    if len(values) != 1:", "    if len(values) < 1:"),
    ("origin_no_lower", "backend/access.py", '.decode("latin-1").strip().lower() == settings.public_origin', '.decode("latin-1").strip() == settings.public_origin'),
    ("origin_never_needed", "backend/access.py", 'return result.kind == "cookie" and scope.get("method")', 'return False and scope.get("method")'),
    ("gate_access_origin_off", "backend/gate.py", 'return declared.mutating and scope.get("method") not in ("GET", "HEAD", "OPTIONS")', "return False"),
    ("access_handler_origin_off", "backend/routes.py", "    if not origin_allowed(request.scope, settings_of(request)):\n        raise forbidden_origin()\n    return await exchange_password(request)", "    return await exchange_password(request)"),
    ("bearer_falls_back_to_cookie", "backend/access.py", "            return CredentialResult(False, None)\n        cookies", "            pass\n        cookies"),
    ("token_never_expires", "backend/access.py", "            if expires <= self._clock():", "            if False:"),
    ("token_store_unbounded", "backend/access.py", "            while len(self._expiry) >= self._max_tokens:", "            while False:"),
    ("claim_busy_ignored", "backend/sessions.py", "if record.busy or record.last_active > cutoff:", "if record.last_active > cutoff:"),
    ("claim_idle_ignored", "backend/sessions.py", "if record.busy or record.last_active > cutoff:", "if record.busy:"),
    ("claim_unloaded_live_ok", "backend/sessions.py", "if key in self._sessions or key in self._deleting:", "if key in self._deleting:"),
    ("deleting_still_served", "backend/sessions.py", "            if key in self._deleting:\n                return None\n", ""),
    ("touch_never_persisted", "backend/sessions.py", "            self._store.touch(record, now)", "            pass"),
    ("touch_not_on_get", "backend/sessions.py", "            if record is not None:\n                self._touch(record)", "            pass"),
    ("symlink_followed", "backend/retention.py", "        if path.is_symlink():\n            os.unlink(path)  # the link, never its target\n            return", ""),
    ("key_check_off", "backend/retention.py", "        if not _KEY.fullmatch(key):\n            raise OSError(\"not a session key\")", ""),
    ("no_chmod", "backend/retention.py", "        os.chmod(path, 0o700)", "        pass"),
    ("records_first", "backend/retention.py", "self._roots = (settings.uploads_dir, settings.outputs_dir, settings.sessions_dir)", "self._roots = (settings.sessions_dir, settings.uploads_dir, settings.outputs_dir)"),
    ("upload_count_quota_off_by_one", "backend/routes.py", "if len(record.files) >= settings.max_uploads_per_session:", "if len(record.files) > settings.max_uploads_per_session:"),
    ("upload_bytes_quota_off_by_one", "backend/routes.py", "if held + incoming > settings.max_session_upload_bytes or (", "if held + incoming > settings.max_session_upload_bytes + 1 or ("),
    ("turn_quota_off_by_one", "backend/turns.py", "if len(record.turns) >= settings.max_turns_per_session:", "if len(record.turns) > settings.max_turns_per_session:"),
    ("upload_no_storage_cap", "backend/routes.py", "        _check_upload_quota(record, settings, incoming=0)\n    if meter_of(request).full():\n        raise ApiError(ErrorCode.STORAGE_FULL)", "        _check_upload_quota(record, settings, incoming=0)"),
    ("open_without_dev", "backend/settings.py", "if self.demo_password is None and self.env != ENV_DEV:", "if False:"),
    ("http_origin_in_production", "backend/settings.py", 'if self.env == ENV_PRODUCTION and not origin.startswith("https://"):', "if False:"),
    ("poll_refill_check_off", "backend/settings.py", 'if self.rate_limits["poll"].refill_per_s < self.max_concurrent_turns:', "if False:"),
    ("r2_l1_reverted", "backend/confirmation.py", "            if self._closed_by is not None:\n                # The turn finished, or", "            if False:\n                # The turn finished, or"),
    ("no_frame_ancestors", "backend/middleware.py", "                    headers[\"Content-Security-Policy\"] = \"frame-ancestors 'none'\"", "                    pass"),
    ("hsts_always", "backend/main.py", "hsts=settings.env == ENV_PRODUCTION", "hsts=True"),
    ("public_bind_anywhere", "backend/__main__.py", "if settings.env != ENV_PRODUCTION and not is_loopback(host):", "if False:"),
    ("undeclared_route_ok", "backend/ratelimit.py", "            raise RuntimeError(f\"route {where} doesn't declare its rate-limit buckets\")", "            continue"),
    ("no_require_access_ok", "backend/ratelimit.py", "            raise RuntimeError(f\"route {where} needs a credential but lacks require_access\")", "            pass"),
    ("cookie_not_redacted", "backend/logging_filter.py", "    (re.compile(r\"(?i)__Host-wb_access\\s*=\\s*[^\\s'\\\",;]+\"), REDACTED),\n", ""),
    ("retention_hours_null", "backend/views.py", "        retention_hours=settings.retention_hours,\n    )\n\n\ndef session_state", "        retention_hours=None,\n    )\n\n\ndef session_state"),
    ("sweep_skips_disk_only", "backend/retention.py", "            if self._registry.claim_unloaded(key):", "            if False:"),
    ("loop_dies_on_error", "backend/retention.py", "        try:\n            result = self._sweeper.sweep()\n        except Exception:\n            logger.exception(\"Retention sweep failed\")", "        result = self._sweeper.sweep()"),
    ("deadline_cap_off", "backend/settings.py", "        if self.turn_deadline_s > MAX_TURN_DEADLINE_S:", "        if False:"),
]


def run(name: str, path: str, old: str, new: str) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "tree"
        subprocess.run(f"git -C {ROOT} archive HEAD | tar -x -C {tmp} --one-top-level=tree", shell=True, check=True)
        target = dest / path
        text = target.read_text()
        if text.count(old) != 1:
            return f"{name:34} NOT-APPLIED (found {text.count(old)} times)"
        target.write_text(text.replace(old, new))
        proc = subprocess.run(["python", "-m", "pytest", "-x", "-q", "-p", "no:cacheprovider", "tests/backend"], cwd=dest, capture_output=True, text=True, timeout=900)
        if proc.returncode == 0:
            return f"{name:34} SURVIVED"
        failed = re.findall(r"^FAILED (\S+)", proc.stdout, re.M) or re.findall(r"^ERROR (\S+)", proc.stdout, re.M)
        return f"{name:34} KILLED  {failed[0] if failed else proc.stdout.strip().splitlines()[-1][:120]}"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--jobs", type=int, default=4)
    p.add_argument("--only")
    a = p.parse_args()
    todo = [m for m in M if not a.only or a.only in m[0]]
    with concurrent.futures.ThreadPoolExecutor(a.jobs) as pool:
        for line in pool.map(lambda m: run(*m), todo):
            print(line, flush=True)


if __name__ == "__main__":
    shutil.which("git")
    main()

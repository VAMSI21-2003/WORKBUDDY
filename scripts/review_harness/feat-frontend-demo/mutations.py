# ruff: noqa: E501 — reviewer tooling, not product code
"""One-line mutations of FE-1's guards, each in a throwaway ``git archive HEAD`` copy.

    python scripts/review_harness/feat-frontend-demo/mutations.py [--only NAME] [--jobs 3] > out/mutations.txt

For each mutation: the copy is made, one exact string is replaced (the script stops if it isn't
found exactly once), and the named tests run there. KILLED means a test failed; SURVIVED means none
noticed. ``e2e`` mutations run the browser tests named with ``-k``.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
WEB = "tests/web tests/backend/test_turn_version.py tests/backend/test_dev_origin.py"

# name: (file, old, new, pytest args)
MUTATIONS: dict[str, tuple[str, str, str, str]] = {
    "m01_downloads_for_any_outcome": ("backend/web/views.py", 'return bool(getattr(turn, "outcome", None) == "delivered")', "return True", WEB),
    "m02_downloads_ignore_guard": ("backend/web/views.py", "    if not should_show_downloads(turn):\n        return None\n", "", WEB),
    "m03_approve_without_steps": ("backend/web/views.py", "can_approve=bool(steps),", "can_approve=True,", WEB),
    "m04_reply_trimmed": ("backend/web/templates/_parts.html", '<div class="text reply" data-slot="reply">{{ t.reply }}</div>', '<div class="text reply" data-slot="reply">{{ t.reply|trim }}</div>', WEB),
    "m05_outcome_class_on_bubble": ("backend/web/templates/_parts.html", '<div class="bubble bubble-buddy">', '<div class="bubble bubble-buddy{% if t.downloads %} ok{% endif %}">', WEB),
    "m06_reply_unescaped": ("backend/web/templates/_parts.html", '{{ t.reply }}</div>', '{{ t.reply|safe }}</div>', WEB),
    "m07_missing_field_guessed": ("backend/web/views.py", "            raise ApiError(ErrorCode.INTERNAL_ERROR)\n        return TurnView(", "            pass\n        return TurnView(", WEB),
    "m08_files_panel_counts_running_turn": ("backend/web/views.py", "if turn.turn_id == exclude_turn_id or turn.status != FINISHED:", "if turn.status != FINISHED:", WEB),
    "m09_poll_finished_as_unchanged": ("backend/web/routes.py", "if unconfirmed is None and v == version and turn.status != views.FINISHED:", "if unconfirmed is None and v == version:", WEB),
    "m10_lost_line_any_id": ("backend/web/routes.py", "            and turn.pending_confirmation.confirmation_id == unconfirmed\n", "", WEB),
    "m11_pointer_not_cleared": ("backend/web/routes.py", "    if gone:\n        clear_pointer(response)\n", "", WEB),
    "m12_no_version_bump_on_settle": ("backend/turns.py", "            turn.status = RUNNING\n            turn.version += 1\n", "            turn.status = RUNNING\n", WEB),
    "m13_no_version_bump_on_finish": ("backend/turns.py", "            turn.status = FINISHED\n            turn.version += 1\n", "            turn.status = FINISHED\n", WEB),
    "m14_localhost_alias_always": ("backend/settings.py", "        if not self.origin_is_derived:\n            return (self.public_origin,)\n", "", WEB),
    "m15_upload_413_message_path": ("backend/middleware.py", r'_UPLOAD_PATH = re.compile(r"^/(?:api/v1/sessions|ui/s)/[^/]+/files/?$")', r'_UPLOAD_PATH = re.compile(r"^/api/v1/sessions/[^/]+/files/?$")', WEB),
    "m16_copy_changed": ("backend/web/templates/_copy.html", "{%- macro working() -%}Buddy is working{%- endmacro -%}", "{%- macro working() -%}Buddy is working on your file{%- endmacro -%}", WEB),
    "m17_csp_dropped": ("backend/web/routes.py", '    response.headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY\n', "", WEB),
    "m18_session_scope_from_pointer": ("backend/web/routes.py", "    record = api.session_of(request, session_id)\n    with record.lock:\n        turn = api.get_turn(request, session_id, turn_id)", "    record = api.session_of(request, request.cookies.get(SESSION_COOKIE) or session_id)\n    session_id = request.cookies.get(SESSION_COOKIE) or session_id\n    with record.lock:\n        turn = api.get_turn(request, session_id, turn_id)", WEB),
    # app.js (browser tests)
    "m19_js_guard_no_version_check": ("backend/web/static/app.js", "if (held && Number(held.dataset.version) > version) drop = true;", "", "tests/e2e -k 'dropped or lost'"),
    "m20_js_guard_no_session_check": ("backend/web/static/app.js", "if (page !== '' && fragment.dataset.sessionId !== page) drop = true;", "", "tests/e2e -k 'dropped'"),
    "m21_js_429_counts_as_failure": ("backend/web/static/app.js", "    if (status === 429) {\n      // A refused request, not a lost connection: it doesn't count (design H2).\n      window.setTimeout(() => retryPoll(poll), retryAfterSeconds(xhr) * 1000);\n      return;\n    }\n", "", "tests/e2e -k '429'"),
    "m22_js_lost_answer_reenables": ("backend/web/static/app.js", "    window.setTimeout(() => { buttonsIn(form).forEach((button) => { button.disabled = true; }); }, 0);\n", "", "tests/e2e -k 'answer'"),
    "m23_js_ambiguous_retries_post": ("backend/web/static/app.js", "    } else if (status >= 500 && composer) {\n      ambiguousPost(composer);", "    } else if (status >= 500 && composer) {\n      composer.requestSubmit();", "tests/e2e -k 'message'"),
    "m24_js_409_plan_no_refetch": ("backend/web/static/app.js", "      if (poll) retryPoll(poll);\n", "", "tests/e2e"),
    "m25_js_no_reload_on_404": ("backend/web/static/app.js", "    if (status === 404 && GONE_CODES.includes(error.code)) {\n      reload();\n      return;\n    }\n", "", "tests/e2e -k '404'"),
    "m26_js_lost_contact_never": ("backend/web/static/app.js", "    if (failures > RETRY_DELAYS_S.length) {", "    if (false) {", "tests/e2e -k 'losing'"),
}


def run(name: str) -> str:
    file, old, new, tests = MUTATIONS[name]
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(f"git -C {ROOT} archive HEAD | tar -x -C {tmp}", shell=True, check=True)
        path = Path(tmp) / file
        text = path.read_text(encoding="utf-8")
        if text.count(old) != 1:
            return f"{name:40} NOT APPLIED (found {text.count(old)} times)"
        path.write_text(text.replace(old, new), encoding="utf-8")
        r = subprocess.run(
            f"{sys.executable} -m pytest -q -x -p no:cacheprovider {tests}",
            shell=True, cwd=tmp, capture_output=True, text=True, timeout=1800,
        )
        failed = [ln.split(" - ")[0] for ln in r.stdout.splitlines() if ln.startswith(("FAILED", "ERROR"))]
        last = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""
        verdict = "KILLED" if r.returncode != 0 else "SURVIVED"
        return f"{name:40} {verdict:9} {last}  {failed[:2]}"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--only")
    p.add_argument("--jobs", type=int, default=3)
    a = p.parse_args()
    names = [n for n in MUTATIONS if not a.only or a.only in n]
    with ThreadPoolExecutor(a.jobs) as pool:
        for line in pool.map(run, names):
            print(line, flush=True)


if __name__ == "__main__":
    main()

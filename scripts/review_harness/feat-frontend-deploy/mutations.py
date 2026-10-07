# ruff: noqa: E501 — reviewer tooling, not product code
"""One-line mutations of FE-2's guards, each in a throwaway ``git archive HEAD`` copy.

    python scripts/review_harness/feat-frontend-deploy/mutations.py [--only NAME] [--jobs 3] > out/mutations.txt

Reuses ``../feat-frontend-demo/mutations.py``'s runner (imported; only its table is swapped).
KILLED means a test failed in the mutated copy; SURVIVED means none noticed.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "feat-frontend-demo"))
import mutations as fe1  # noqa: E402

WEB = "tests/web tests/backend/test_app_basics.py"
STATIC = "tests/web/test_static.py"
RACES = "tests/e2e/test_browser_races.py"

fe1.MUTATIONS = {
    # routes.py poll: M1
    "d01_no_reload_instruction": ("backend/web/routes.py", "        if v is not None and v > version:", "        if False:", WEB),
    "d01b_no_reload_instruction_browser": ("backend/web/routes.py", "        if v is not None and v > version:", "        if False:", RACES + " -k restart"),
    "d02_reload_on_equal_version": ("backend/web/routes.py", "        if v is not None and v > version:", "        if v is not None and v >= version:", WEB),
    # static caching
    "d03_static_always_immutable": ("backend/web/routes.py", "IMMUTABLE if current else REVALIDATE", "IMMUTABLE", STATIC),
    "d04_static_never_immutable": ("backend/web/routes.py", "IMMUTABLE if current else REVALIDATE", "REVALIDATE", STATIC),
    "d05_static_marks_errors": ("backend/web/routes.py", "        if response.status_code in (200, 304):", "        if response.status_code in (200, 304, 404):", STATIC),
    "d06_middleware_keeps_any_route_cache_control": ("backend/middleware.py", 'if not (static and "cache-control" in headers):', 'if "cache-control" not in headers:', WEB + " tests/backend"),
    "d07_middleware_static_no_store": ("backend/middleware.py", 'if not (static and "cache-control" in headers):', "if True:", STATIC),
    "d08_no_referrer_policy": ("backend/middleware.py", '                headers["Referrer-Policy"] = "no-referrer"\n', "", WEB),
    "d09_url_unversioned": ("backend/web/templating.py", 'return f"/static/{name}?v={static_version(name)}"', 'return f"/static/{name}"', STATIC),
    "d10_version_ignores_content": ("backend/web/templating.py", "return hashlib.sha256((STATIC_DIR / name).read_bytes()).hexdigest()[:12]", 'return "0123456789ab"', STATIC),
    # views.py: the L4 m08 guard the new route test claims to cover
    "d11_files_panel_counts_running_turn": ("backend/web/views.py", "if turn.turn_id == exclude_turn_id or turn.status != FINISHED:", "if turn.status != FINISHED:", "tests/web/test_upload_and_turns.py -k files_panel_leaves_out"),
    # app.js (browser tests)
    "d12_js_409_old_branch": ("backend/web/static/app.js", "      refetchTurn(plan);\n", "      const poll = plan.closest('.turn').querySelector('.poll');\n      if (poll) retryPoll(poll);\n", RACES + " -k 409"),
    "d13_js_refetch_without_timeout": ("backend/web/static/app.js", "    window.setTimeout(() => {\n      htmx.ajax('GET', plan.dataset.refetchUrl, { target: turn }).catch(() => {\n        showNotice(copyText('cant-reach'), 'request');\n      });\n    }, 0);\n", "    htmx.ajax('GET', plan.dataset.refetchUrl, { target: turn }).catch(() => {\n      showNotice(copyText('cant-reach'), 'request');\n    });\n", RACES + " -k 409"),
    "d14_js_lost_answer_reenables": ("backend/web/static/app.js", "    window.setTimeout(() => { buttonsIn(form).forEach((button) => { button.disabled = true; }); }, 0);\n", "", RACES + " -k lost_plan_answer"),
    "d15_js_ambiguous_resends": ("backend/web/static/app.js", "    } else if (status >= 500 && composer) {\n      ambiguousPost(composer);", "    } else if (status >= 500 && composer) {\n      composer.requestSubmit();", RACES + " -k 5xx"),
    "d16_css_no_wrap_in_plan_steps": ("backend/web/static/styles.css", ".plan-steps li {\n  overflow-wrap: anywhere;\n}\n", "", RACES + " -k long_unbroken"),
}

if __name__ == "__main__":
    fe1.main()

# ruff: noqa: E501 — reviewer tooling, not product code
"""Round 2: one-line mutations of the round-1 fixes (``held``, the static version, the startup
message, the middleware's ``static and``), each in a throwaway ``git archive HEAD`` copy.

    python scripts/review_harness/feat-frontend-deploy/mutations_r2.py [--only NAME] [--jobs 3] > out/mutations_r2.txt

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
MW = "tests/backend/test_middleware.py"
ENTRY = "tests/backend/test_dev_origin.py"

SEEN = "        seen = v if v is not None else held\n"
TIMEOUT_OLD = (
    "    window.setTimeout(() => {\n"
    "      htmx.ajax('GET', plan.dataset.refetchUrl + '?held=' + held, { target: turn }).catch(() => {\n"
    "        showNotice(copyText('cant-reach'), 'request');\n"
    "      });\n"
    "    }, 0);\n"
)
TIMEOUT_NEW = (
    "    htmx.ajax('GET', plan.dataset.refetchUrl + '?held=' + held, { target: turn }).catch(() => {\n"
    "      showNotice(copyText('cant-reach'), 'request');\n"
    "    });\n"
)

fe1.MUTATIONS = {
    # routes.py poll: L3 (held)
    "r01_route_ignores_held": ("backend/web/routes.py", SEEN, "        seen = v\n", WEB),
    "r01b_route_ignores_held_browser": ("backend/web/routes.py", SEEN, "        seen = v\n", RACES + " -k after_a_restart"),
    "r02_held_beats_v": ("backend/web/routes.py", SEEN, "        seen = held if held is not None else v\n", WEB),
    "r03_held_reloads_on_equal": ("backend/web/routes.py", "        if seen is not None and seen > version:", "        if seen is not None and seen >= version:", WEB),
    # app.js: L3 (held in the two URLs)
    "r04_js_lost_answer_no_held": ("backend/web/static/app.js", " + '&held=' + held;", ";", RACES + " -k lost_answer_after_a_restart"),
    "r05_js_refetch_no_held": ("backend/web/static/app.js", "plan.dataset.refetchUrl + '?held=' + held", "plan.dataset.refetchUrl", RACES + " -k 409_after_a_restart"),
    "r06_js_lost_answer_held_zero": ("backend/web/static/app.js", "    const url = form.dataset.refetchUrl + '?unconfirmed=' + encodeURIComponent(id) + '&held=' + held;", "    const url = form.dataset.refetchUrl + '?unconfirmed=' + encodeURIComponent(id) + '&held=0';", RACES + " -k lost_answer_after_a_restart"),
    # app.js: L4b (round-1 d13 again, against the new code)
    "r07_js_refetch_without_timeout": ("backend/web/static/app.js", TIMEOUT_OLD, TIMEOUT_NEW, RACES + " -k 409"),
    # templating.py: L5
    "r08_version_cached_per_process": ("backend/web/templating.py", "    return _digest(name, stat.st_mtime_ns, stat.st_size)", "    return _digest(name, 0, 0)", STATIC),
    "r09_version_ignores_mtime": ("backend/web/templating.py", "    return _digest(name, stat.st_mtime_ns, stat.st_size)", "    return _digest(name, 0, stat.st_size)", STATIC),
    # __main__.py: L7
    "r10_no_permission_catch": ("backend/__main__.py", "    except PermissionError as exc:", "    except NotImplementedError as exc:", ENTRY),
    "r11_permission_exit_1": ("backend/__main__.py", "            file=sys.stderr,\n        )\n        raise SystemExit(2) from None\n    uvicorn", "            file=sys.stderr,\n        )\n        raise SystemExit(1) from None\n    uvicorn", ENTRY),
    # middleware: L4a (round-1 d06 and d07 again, now with the new direct test)
    "r12_middleware_keeps_any_route_cache_control": ("backend/middleware.py", 'if not (static and "cache-control" in headers):', 'if "cache-control" not in headers:', MW),
    "r13_middleware_static_no_store": ("backend/middleware.py", 'if not (static and "cache-control" in headers):', "if True:", MW),
}

if __name__ == "__main__":
    fe1.main()

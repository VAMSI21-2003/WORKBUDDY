# ruff: noqa: E501 — reviewer tooling, not product code
"""Round 2, written by the round-2 reviewer: checks of the round-1 fixes' doc claims and the L5
fix end to end. One JSON line per case.

    python scripts/review_harness/feat-frontend-deploy/r2_extra.py s01 [--rev REV]    # L5 end to end
    python scripts/review_harness/feat-frontend-deploy/r2_extra.py w08 [--speaker S]  # L1's doc claim
    python scripts/review_harness/feat-frontend-deploy/r2_extra.py k03b --image IMG   # L7 in the image

- s01: a ``git archive`` copy of ``--rev`` (default HEAD); a dev app in a
  subprocess; the page's ``app.js?v=``, then app.js edited (same running app), then the page again;
  the Cache-Control of the old and the new ``?v=``, and which content each serves.
- w08: password mode (as production), restart under a waiting plan; the page's 401 notice; the
  person follows the notice's link and enters the password; what the page then shows against the
  server's account (rule 1), the session kept or not, and the original's SHA-256.
- k03b: the image with a root-owned ``/data``; waits for the container to exit (no fixed sleep).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]

S01_CHILD = r'''
import json, re, sys
from pathlib import Path
from fastapi.testclient import TestClient
from backend.main import create_app
from backend.settings import Settings
from backend.web import templating
data = Path(sys.argv[1])
app = create_app(Settings(data_dir=data, port=8000, env="dev"), llm_configured=False)
c = TestClient(app)
def v_of():
    page = c.get("/").text
    m = re.search(r'/static/app\.js\?v=([0-9a-f]+)', page)
    return m.group(1) if m else None
out = {}
v1 = v_of()
out["v_before"] = v1
js = templating.STATIC_DIR / "app.js"
js.write_text(js.read_text(encoding="utf-8") + "\n// edited under a running server\n", encoding="utf-8")
v2 = v_of()
out["v_after_edit"] = v2
for label, v in (("old_v", v1), ("new_v", v2)):
    r = c.get(f"/static/app.js?v={v}")
    out[label] = {"status": r.status_code, "cache": r.headers.get("cache-control"), "serves_edited": "edited under a running server" in r.text}
print(json.dumps(out))
'''


def s01(rev: str) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "src"
        src.mkdir()
        repo = ROOT
        subprocess.run(f"git -C {repo} archive {rev} | tar -x -C {src}", shell=True, check=True)
        r = subprocess.run([sys.executable, "-c", S01_CHILD, str(Path(tmp) / "data")], cwd=src, capture_output=True, text=True, timeout=300)
        if r.returncode != 0:
            return {"PROBE_ERROR": r.stderr[-800:]}
        return {"rev": rev, **json.loads(r.stdout.strip().splitlines()[-1])}


def w08(speaker: str) -> dict[str, Any]:
    sys.path.insert(0, str(HERE.parent / "feat-frontend-demo"))
    import browser_probe as bp  # imports fe1 (consumes --tree)
    from browser_probe import h
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw, tempfile.TemporaryDirectory() as tmp:
        port = bp.free_port()
        data = Path(tmp) / "data"
        a = bp.Server(h.FakeLLM(h.single("remove_duplicate_rows")), speaker, data, port, password=bp.PASSWORD)
        browser = pw.chromium.launch()
        try:
            page = browser.new_context(viewport={"width": 1000, "height": 800}).new_page()
            w = bp.watch(page)
            bp.sign_in(page, a.base, bp.PASSWORD)
            sid = page.get_attribute("#workspace", "data-session-id")
            bp.upload(page, h.xlsx())
            before = bp.originals(a.settings)
            bp.send(page, "remove duplicate rows")
            bp.wait_plan(page)
            a.stop()
            b = bp.Server(h.FakeLLM([]), speaker, data, port, password=bp.PASSWORD)
            page.wait_for_selector('#page-notices [data-notice="auth"] a', timeout=20_000)
            notice = bp.state_of(page)["notices"]
            page.click('#page-notices [data-notice="auth"] a')
            page.fill("#password", bp.PASSWORD)
            page.click("#gate-form button[type=submit]")
            page.wait_for_selector("#workspace[data-session-id]")
            page.wait_for_timeout(3_000)
            after = bp.state_of(page)
            sid_after = page.get_attribute("#workspace", "data-session-id")
            api = b.api_turn(sid)
            result = {
                "notice_before_sign_in": notice,
                "same_session_after_sign_in": sid_after == sid,
                "page_after": after,
                "server": {"status": api.get("status"), "outcome": api.get("outcome"), "reply": api.get("reply"), "delivered": api.get("delivered_files")},
                "page_reply_equals_server": after["reply"] == api.get("reply"),
                "page_errors": w["page_errors"],
                "original_unchanged": before == bp.originals(b.settings),
                "original_sha": before,
            }
            b.stop()
            return result
        finally:
            browser.close()


def k03b(image: str) -> dict[str, Any]:
    sys.path.insert(0, str(HERE))
    import docker_probe as k

    folder = Path(tempfile.mkdtemp(prefix="wb-k03b-"))
    os.chmod(folder, 0o755)  # root-owned, as a bind mount is unless chowned
    name = "wb-review-k03b"
    try:
        k.run_container(image, name, {"PORT": str(k.free_port()), "WORKBUDDY_DEMO_PASSWORD": k.PASSWORD, "WORKBUDDY_PUBLIC_ORIGIN": k.ORIGIN}, volume=folder)
        code, log = k.wait_exit(name, 90)
        return {"exit": code, "log": log.strip().splitlines()[-3:], "traceback": "Traceback" in log, "leaks_password": k.PASSWORD in log}
    finally:
        k.docker("rm", "-f", name)
        shutil.rmtree(folder, ignore_errors=True)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("case", choices=("s01", "w08", "k03b"))
    p.add_argument("--rev", default="HEAD")
    p.add_argument("--speaker", default="template")
    p.add_argument("--image", default="workbuddy:r2rev")
    a = p.parse_args()
    sys.argv = sys.argv[:1]  # fe1 reads --tree from argv
    start = time.monotonic()
    try:
        if a.case == "s01":
            row = s01(a.rev)
        elif a.case == "w08":
            row = w08(a.speaker)
        else:
            row = k03b(a.image)
    except Exception as exc:  # noqa: BLE001
        row = {"PROBE_ERROR": repr(exc)[:600]}
    print(json.dumps({"case": a.case, "speaker": a.speaker if a.case == "w08" else None, "seconds": round(time.monotonic() - start, 1), **row}, default=str), flush=True)


if __name__ == "__main__":
    main()

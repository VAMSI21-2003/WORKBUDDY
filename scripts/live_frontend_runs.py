"""Live runs through the browser, with a real LLM (frontend design Section 10.4).

Starts ``python -m backend`` (uvicorn on 127.0.0.1, one worker, the real pipeline on
``GROQ_API_KEY`` or ``ANTHROPIC_API_KEY``) with a demo password, the design's real rate limits and
a fresh data root under ``scratch/live-frontend/``, and drives the page in Chromium the way a user
would: the gate, upload, a message, the plan card, the answer, the reply, the download. Every run
uses a fresh browser context (its own cookies, so its own session).

The runs of design 10.4:

- "1": a plain turn, "remove duplicate rows", approved;
- "2": a two-step chain, "remove duplicate rows, then remove empty rows", approved;
- "3": "remove duplicate rows", rejected;
- "4": a request the pipeline can't do, "sort the rows by amount".

For each it prints what ``CLAUDE.md`` asks for: the intent, ``clarification_required``, the steps,
whether a file was delivered, an independent check of the downloaded file (pandas, against the
original), the reply as the page shows it next to the API's own ``reply`` for the same turn, any
page notice, and the original's SHA-256 before and after. A screenshot of each finished turn is
saved, and one of the gate and one of the plan card.

It stops at the first sign of a rate limit from the LLM provider (any HTTP 429 in the server log,
even one the SDK retried) and reports how many runs were completed. The key is read by the server
from the environment and never printed.

Usage (``GROQ_API_KEY`` or ``ANTHROPIC_API_KEY`` set):
    python scripts/live_frontend_runs.py [--runs 1,2,3,4] [--port 8766] [--pause 8] \
        [--shots docs/reviews/frontend-demo-screens] [--json out.json]
"""

from __future__ import annotations

import argparse
import io
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import pandas as pd
from playwright.sync_api import Page, sync_playwright

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
sys.path.insert(0, str(REPO_ROOT))

from live_api_runs import _same, default_fixture, measure, provider_429, sha  # noqa: E402

from tests.e2e.pages import WATCH_NOTICES  # noqa: E402

SCRATCH = REPO_ROOT / "scratch" / "live-frontend"
PASSWORD = "live-run-placeholder-password"
VIEWPORT = {"width": 1100, "height": 820}
RUNS: dict[str, dict[str, Any]] = {
    "1": {"message": "remove duplicate rows", "answer": "approve", "expect": "dedupe"},
    "2": {
        "message": "remove duplicate rows, then remove empty rows",
        "answer": "approve",
        "expect": "chain",
    },
    "3": {"message": "remove duplicate rows", "answer": "reject", "expect": None},
    "4": {"message": "sort the rows by amount", "answer": None, "expect": None},
}


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class Server:
    def __init__(self, port: int) -> None:
        self.port = port
        self.data = SCRATCH / f"data-{port}"
        self.log_path = SCRATCH / f"server-{port}.log"
        self.process: subprocess.Popen[bytes] | None = None

    def start(self) -> None:
        SCRATCH.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        env.update(
            {
                "WORKBUDDY_DATA_DIR": str(self.data),
                "WORKBUDDY_DEMO_PASSWORD": PASSWORD,
                "PYTHONUNBUFFERED": "1",
            }
        )
        for name in ("WORKBUDDY_ENV", "WORKBUDDY_PUBLIC_ORIGIN", "WORKBUDDY_AUTO_APPROVE"):
            env.pop(name, None)  # unset: loopback only, a password, the origin from --port (L5)
        log = open(self.log_path, "ab")
        self.process = subprocess.Popen(
            [sys.executable, "-m", "backend", "--port", str(self.port)],
            cwd=REPO_ROOT,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                health = httpx.get(self.base + "/healthz", timeout=2, trust_env=False).json()
            except httpx.HTTPError:
                time.sleep(0.3)
                continue
            if not health["llm_configured"]:
                raise SystemExit("the server has no LLM configured")
            return
        raise SystemExit("the server didn't start")

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def stop(self) -> None:
        if self.process is not None and self.process.poll() is None:
            self.process.send_signal(signal.SIGINT)
            try:
                self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.process.kill()

    def log_text(self) -> str:
        return self.log_path.read_text(errors="replace")

    def api(self, path: str) -> dict[str, Any]:
        response = httpx.get(
            self.base + path,
            headers={"Authorization": f"Bearer {PASSWORD}"},
            timeout=20,
            trust_env=False,
        )
        response.raise_for_status()
        return dict(response.json())


def run_one(
    name: str,
    spec: dict[str, Any],
    server: Server,
    browser: Any,
    original: bytes,
    shots: Path,
    host: str,
) -> dict[str, Any]:
    base = f"http://{host}:{server.port}"
    context = browser.new_context(viewport=VIEWPORT, accept_downloads=True)
    page: Page = context.new_page()
    page.set_default_timeout(30_000)
    page.add_init_script(WATCH_NOTICES)
    console: list[str] = []
    page.on("console", lambda m: console.append(m.text) if m.type == "error" else None)
    statuses: list[tuple[int, str]] = []
    page.on("response", lambda r: statuses.append((r.status, r.url)))
    entry: dict[str, Any] = {"run": name, "message": spec["message"], "browser_host": host}

    page.goto(base + "/")
    if name == "1":
        page.screenshot(path=str(shots / "gate.png"))
    page.fill("#password", PASSWORD)
    page.click("#gate-form button[type=submit]")
    page.wait_for_selector("#workspace[data-session-id]")
    sid = page.get_attribute("#workspace", "data-session-id") or ""
    page.set_input_files(
        "#file",
        {
            "name": "manual_report.xlsx",
            "mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "buffer": original,
        },
    )
    page.click("#upload-form button[type=submit]")
    page.wait_for_selector(".file-list .file-name:text-is('manual_report.xlsx')")
    stored = next(server.data.rglob("manual_report.xlsx"), None)
    entry["original_sha256_local_fixture"] = sha(original)
    entry["original_sha256_stored_before"] = sha(stored.read_bytes()) if stored else None

    started = time.monotonic()
    page.fill("#message", spec["message"])
    page.click("#composer button[type=submit]")

    plan_text = None
    if spec["answer"] is not None:
        page.wait_for_selector("#turn-t0001 .plan", timeout=180_000)
        plan_text = {
            "steps": page.locator("#turn-t0001 .plan ol li").all_inner_texts(),
            "plan_note": (
                page.locator("#turn-t0001 .plan .plan-note").inner_text()
                if page.locator("#turn-t0001 .plan .plan-note").count()
                else None
            ),
            "title": page.title(),
        }
        if name == "2":
            page.screenshot(path=str(shots / "plan-card.png"))
        page.click(f"#turn-t0001 .plan button[value={spec['answer']}]")
    entry["plan_shown"] = plan_text
    entry["answer_sent"] = spec["answer"]

    page.wait_for_selector("#turn-t0001 [data-slot=reply]", timeout=300_000)
    entry["wall_s"] = round(time.monotonic() - started, 1)
    page.wait_for_timeout(400)
    page.screenshot(path=str(shots / f"run-{name}-finished.png"))
    page_reply = page.eval_on_selector("#turn-t0001 [data-slot=reply]", "e => e.textContent")

    api = server.api(f"/api/v1/sessions/{sid}/turns/t0001")
    detail = api.get("detail") or {}
    entry.update(
        {
            "outcome": api["outcome"],
            "reply_source": api["reply_source"],
            "intent": detail.get("intent"),
            "clarification_required": detail.get("clarification_required"),
            "steps": [
                f"{s['capability']}{' (re-check)' if s['recheck'] else ''} [{s['status']}, "
                f"verified={s['verified']}] {s['input_name']} -> "
                f"{', '.join(s['output_names']) or '-'}"
                for s in detail.get("steps", [])
            ],
            "stopped_at": detail.get("stopped_at"),
            "confirmation": detail.get("confirmation"),
            "api_delivered": [f["name"] for f in api["delivered_files"]],
            "api_reply": api["reply"],
            "page_reply": page_reply,
            "page_reply_equals_api_reply": page_reply == api["reply"],
            "page_links": page.locator("#turn-t0001 a.download").count(),
            "sidebar_links": page.locator("#files a[download]").count(),
            "page_notices": page.evaluate("window.__notices"),
            "bad_http_statuses": [(s, u) for s, u in statuses if s >= 400],
            "console_errors": console,
            "web_storage": page.evaluate(
                "({local: Object.keys(localStorage), session: Object.keys(sessionStorage)})"
            ),
        }
    )
    source = pd.read_excel(io.BytesIO(original))
    entry["original_measured"] = measure(source)
    if api["delivered_files"]:
        (delivered,) = api["delivered_files"]
        with page.expect_download() as info:
            page.click("#turn-t0001 a.download")
        saved = SCRATCH / f"download-{name}.xlsx"
        info.value.save_as(saved)
        content = saved.read_bytes()
        entry["download_sha256_matches_record"] = sha(content) == delivered["sha256"]
        written = pd.read_excel(io.BytesIO(content))
        entry["delivered_measured"] = measure(written)
        if spec["expect"] == "dedupe":
            want = source.drop_duplicates()
        elif spec["expect"] == "chain":
            want = source.dropna(how="all").drop_duplicates()
        else:
            want = None
        if want is not None:
            entry["equals_expected_" + spec["expect"]] = _same(written, want)
    stored_after = next(server.data.rglob("manual_report.xlsx"), None)
    entry["original_sha256_stored_after"] = sha(stored_after.read_bytes()) if stored_after else None
    entry["original_unchanged"] = (
        entry["original_sha256_local_fixture"]
        == entry["original_sha256_stored_before"]
        == entry["original_sha256_stored_after"]
    )
    context.close()
    return entry


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", default="1,2,3,4")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--pause", type=float, default=8.0)
    parser.add_argument("--shots", default=str(REPO_ROOT / "docs/reviews/frontend-demo-screens"))
    parser.add_argument("--json", default=None)
    args = parser.parse_args()
    if not (os.environ.get("GROQ_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")):
        raise SystemExit("set GROQ_API_KEY or ANTHROPIC_API_KEY")
    shots = Path(args.shots)
    shots.mkdir(parents=True, exist_ok=True)
    server = Server(args.port or free_port())
    server.start()
    original = default_fixture()
    results: list[dict[str, Any]] = []
    stopped = None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            for number, name in enumerate(args.runs.split(",")):
                host = "localhost" if name == "1" else "127.0.0.1"  # run 1 also covers review L5
                try:
                    entry = run_one(name, RUNS[name], server, browser, original, shots, host)
                except Exception as exc:  # noqa: BLE001 — report the run, then stop
                    entry = {"run": name, "error": f"{type(exc).__name__}: {str(exc)[:300]}"}
                    results.append(entry)
                    print(json.dumps(entry, indent=2, default=str))
                    stopped = f"run {name} errored"
                    break
                results.append(entry)
                print(json.dumps(entry, indent=2, default=str))
                if provider_429(server.log_text()):
                    stopped = f"the LLM provider returned a 429 during run {name}"
                    break
                if number < len(args.runs.split(",")) - 1:
                    time.sleep(args.pause)
            browser.close()
    finally:
        server.stop()
    log = server.log_text()
    print("\nprovider 429 in the server log:", provider_429(log))
    print("password in the server log:", PASSWORD in log)
    print("sessions folder files left:", len(list((server.data / "sessions").rglob("*"))))
    completed = [r["run"] for r in results if "error" not in r]
    print(
        f"runs completed: {len(completed)} of {len(args.runs.split(','))} ({', '.join(completed)})"
    )
    if stopped:
        print("STOPPED:", stopped)
    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=2, default=str) + "\n")


if __name__ == "__main__":
    main()

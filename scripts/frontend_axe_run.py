"""One axe-core run over the page's four states (frontend design 5, "What is checked in the demo").

The gate, the empty workspace, the plan card and a finished turn, each in the light and the dark
colour scheme, on the real app with a fake LLM. axe-core isn't vendored in this repo: pass the path
of its ``axe.min.js`` (for example from the ``axe-core`` package on npm). axe is injected through
the browser's debugging protocol, so the page keeps its Content-Security-Policy.

Usage:  python scripts/frontend_axe_run.py /path/to/axe.min.js [--json out.json]
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import uvicorn
from playwright.sync_api import Page, sync_playwright

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from backend.main import create_app  # noqa: E402
from backend.settings import Settings  # noqa: E402
from tests.backend.factories import (  # noqa: E402
    DEDUPE_THEN_EMPTY,
    PipelineFactory,
    QueuedProvider,
    chain,
)
from tests.e2e.pages import CHAIN, send, sign_in, upload  # noqa: E402

PASSWORD = "axe-run-placeholder"


def audit(page: Page, axe_source: str, label: str) -> dict[str, Any]:
    page.evaluate(axe_source)
    result = page.evaluate("axe.run(document, {resultTypes: ['violations', 'incomplete']})")
    return {
        "state": label,
        "violations": [
            {
                "id": v["id"],
                "impact": v["impact"],
                "help": v["help"],
                "nodes": [n["target"] for n in v["nodes"]][:5],
            }
            for v in result["violations"]
        ],
        "needs_review": [{"id": v["id"], "nodes": len(v["nodes"])} for v in result["incomplete"]],
        "passes": len(result["passes"]),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("axe")
    parser.add_argument("--json", default=None)
    args = parser.parse_args()
    axe_source = Path(args.axe).read_text(encoding="utf-8")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    queue = chain(*DEDUPE_THEN_EMPTY) * 2
    settings = Settings(
        data_dir=Path(tempfile.mkdtemp()), env="", demo_password=PASSWORD, port=port
    )
    app = create_app(settings, PipelineFactory(QueuedProvider(queue)))
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)
    base = f"http://127.0.0.1:{port}"
    report: list[dict[str, Any]] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for scheme in ("light", "dark"):
            context = browser.new_context(
                viewport={"width": 1100, "height": 800}, color_scheme=scheme
            )
            page = context.new_page()
            page.goto(base + "/")
            report.append(audit(page, axe_source, f"gate ({scheme})"))
            sign_in(page, base, PASSWORD)
            report.append(audit(page, axe_source, f"empty workspace ({scheme})"))
            upload(page)
            send(page, CHAIN)
            page.wait_for_selector(".plan")
            report.append(audit(page, axe_source, f"plan card ({scheme})"))
            page.click(".plan button[value=approve]")
            page.wait_for_selector("[data-slot=reply]", timeout=60_000)
            report.append(audit(page, axe_source, f"finished turn ({scheme})"))
            context.close()
        browser.close()
    server.should_exit = True
    for entry in report:
        found = ", ".join(f"{v['id']} ({v['impact']})" for v in entry["violations"]) or "none"
        review = ", ".join(f"{v['id']}x{v['nodes']}" for v in entry["needs_review"]) or "none"
        print(f"{entry['state']}: violations: {found}; needs review: {review}")
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()

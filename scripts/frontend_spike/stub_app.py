"""The FE-1 spike's stub: one turn fragment (poll, plan card, reply, download) and no real backend.

Frontend design Section 3.4: before the rest of FE-1 is built, the htmx behaviours the design relies
on are checked against a stub turn service, under the design's own Content-Security-Policy (11.2).
``run_spike.py`` drives this app in Chromium. ``test_spike_rule1.py`` holds the spike's three
rule-1 tests. Throwaway tooling: the real routes are in ``backend/web/``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape

HERE = Path(__file__).resolve().parent
STATIC = HERE.parent.parent / "backend" / "web" / "static"

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
)

env = Environment(
    loader=FileSystemLoader(HERE / "templates"),
    autoescape=select_autoescape(["html"], default=True),
)


@dataclass
class StubTurn:
    """What the stub 'service' holds for the one turn t0001."""

    status: str = "running"  # running | awaiting | finished
    version: int = 0
    message: str = "remove duplicate rows, then remove empty rows"
    reply: str = "Reply text"
    outcome: str = "delivered"
    delivered: list[dict[str, Any]] = field(default_factory=list)
    steps: list[str] = field(
        default_factory=lambda: [
            "Remove duplicate rows from report.xlsx.",
            "Then remove empty rows from the result of step 1.",
        ]
    )
    expires_at: float = 0.0
    poll_delay_s: float = 0.0  # server-side sleep before a poll answers (spike scenario E/F)
    confirm_delay_s: float = 0.0
    received: list[dict[str, Any]] = field(default_factory=list)
    polls: int = 0


STATE = StubTurn()


def reset() -> None:
    global STATE
    STATE = StubTurn()


def turn_context(turn: StubTurn) -> dict[str, Any]:
    left = max(0, round(turn.expires_at - time.time())) if turn.status == "awaiting" else 0
    return {
        "t": turn,
        "show_downloads": should_show_downloads(turn),
        "left": left,
        "delay": 1,
    }


def should_show_downloads(turn: StubTurn) -> bool:
    """The only function that reads the outcome (design 4.3 item 1)."""
    return turn.outcome == "delivered"


def render_turn(turn: StubTurn) -> str:
    return env.get_template("turn.html").render(**turn_context(turn))


def create_app() -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    app.mount("/spike-static", StaticFiles(directory=HERE / "static"), name="spike-static")

    def html(content: str, status: int = 200, headers: dict[str, str] | None = None) -> HTMLResponse:
        response = HTMLResponse(content, status_code=status, headers=headers)
        response.headers["Content-Security-Policy"] = CSP
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/")
    def page() -> Response:
        response = html(env.get_template("page.html").render(**turn_context(STATE)))
        response.headers.append(
            "Set-Cookie", "__Host-wb_probe=1; Path=/; Secure; HttpOnly; SameSite=Strict"
        )
        return response

    @app.post("/_ctl/{action}")
    async def control(action: str, request: Request) -> Response:
        data = await request.json() if await request.body() else {}
        if action == "reset":
            reset()
        elif action == "set":
            for key, value in data.items():
                setattr(STATE, key, value)
        elif action == "awaiting":
            STATE.status, STATE.version = "awaiting", STATE.version + 1
            STATE.expires_at = time.time() + 120
        elif action == "running":
            STATE.status, STATE.version = "running", STATE.version + 1
        elif action == "finished":
            STATE.status, STATE.version = "finished", STATE.version + 1
        return JSONResponse({"status": STATE.status, "version": STATE.version})

    @app.get("/_ctl/received")
    def received() -> Response:
        return JSONResponse({"received": STATE.received, "polls": STATE.polls})

    @app.get("/ui/poll")
    def poll(v: int = 0, unconfirmed: str | None = None) -> Response:
        STATE.polls += 1
        if STATE.poll_delay_s:
            time.sleep(STATE.poll_delay_s)
        if v != STATE.version or unconfirmed is not None:
            return html(
                render_turn(STATE),
                headers={"HX-Retarget": "#turn-t0001", "HX-Reswap": "outerHTML"},
            )
        return html(env.get_template("poll_only.html").render(**turn_context(STATE)))

    @app.post("/ui/confirm")
    async def confirm(request: Request) -> Response:
        form = await request.form()
        STATE.received.append(dict(form))
        if STATE.confirm_delay_s:
            time.sleep(STATE.confirm_delay_s)
        STATE.status, STATE.version = "running", STATE.version + 1
        return html(render_turn(STATE))

    @app.api_route("/ui/err/{status}", methods=["GET", "POST"])
    def error(status: int) -> Response:
        headers = {"Retry-After": "7"} if status == 429 else None
        return JSONResponse(
            {"error": {"code": "stub", "message": f"Stub error {status}."}},
            status_code=status,
            headers=headers,
        )

    @app.post("/ui/echo")
    async def echo(request: Request) -> Response:
        return JSONResponse(
            {
                "origin": request.headers.get("origin"),
                "cookie_arrived": "__Host-wb_probe=1" in request.headers.get("cookie", ""),
            }
        )

    @app.get("/dl/{name}")
    def download(name: str) -> Response:
        return Response(b"x", headers={"Content-Disposition": f"attachment; filename={name}"})

    return app


app = create_app()

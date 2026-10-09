"""The fake identity provider as a small web server (browser tests, live runs).

    python -m tests.idp.server --port 9100 --issuer http://127.0.0.1:9100 \
        --redirect-uri https://demo.example/auth/google/callback

``/authorize`` shows a list of accounts, as Google's account chooser does; clicking one goes to
``/choose``, which issues the code and sends the browser back. ``/choose?...&email=...`` can be
called directly by a script, and ``&flaw=expired`` (any name in ``provider.FLAWS``) makes the token
wrong on purpose. Not part of the app, and not safe to leave running anywhere public: anyone can
pick any account.
"""

from __future__ import annotations

import argparse
import html
from urllib.parse import parse_qs, urlencode

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from tests.idp.provider import FakeIdp


def make_app(idp: FakeIdp) -> Starlette:
    async def discovery(request: Request) -> Response:
        return JSONResponse(idp.discovery())

    async def jwks(request: Request) -> Response:
        return JSONResponse(idp.jwks())

    async def token(request: Request) -> Response:
        form = {k: v[0] for k, v in parse_qs((await request.body()).decode()).items()}
        status, body = idp.token(form)
        return JSONResponse(body, status_code=status)

    async def authorize(request: Request) -> Response:
        query = dict(request.query_params)
        items = []
        for email in sorted(idp.accounts):
            link = urlencode({**query, "email": email})
            items.append(f'<li><a href="/choose?{html.escape(link)}">{html.escape(email)}</a></li>')
        page = (
            "<!doctype html><title>Fake identity provider</title>"
            "<h1>Choose an account</h1><ul>" + "".join(items) + "</ul>"
        )
        return HTMLResponse(page)

    async def choose(request: Request) -> Response:
        query = dict(request.query_params)
        email = query.pop("email", "")
        flaw = query.pop("flaw", "")
        if email not in idp.accounts:
            return JSONResponse({"error": "unknown account"}, status_code=400)
        url = f"{idp.issuer}/authorize?{urlencode(query)}"
        location = idp.authorize(url, email, *([flaw] if flaw else []))
        return RedirectResponse(location, status_code=302)

    return Starlette(
        routes=[
            Route("/.well-known/openid-configuration", discovery),
            Route("/jwks", jwks),
            Route("/token", token, methods=["POST"]),
            Route("/authorize", authorize),
            Route("/choose", choose),
        ]
    )


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9100)
    parser.add_argument("--issuer", required=True)
    parser.add_argument("--redirect-uri", required=True)
    parser.add_argument("--client-id", default="workbuddy-client.apps.test")
    parser.add_argument("--client-secret", default="fake-client-secret-for-tests")
    parser.add_argument("--account", action="append", default=[], help="email=subject")
    args = parser.parse_args()
    idp = FakeIdp(
        issuer=args.issuer,
        client_id=args.client_id,
        client_secret=args.client_secret,
        redirect_uri=args.redirect_uri,
    )
    from tests.idp.provider import Account

    for item in args.account:
        email, _, subject = item.partition("=")
        idp.accounts[email] = Account(email, subject or f"sub-{email}")
    uvicorn.run(make_app(idp), host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()

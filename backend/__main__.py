"""Serve the API: ``python -m backend [--port 8000] [--host HOST]``.

One worker: sessions and turns live in this process (design Sections 2 and
6.1). It binds to ``127.0.0.1``, except in production (``WORKBUDDY_ENV=production``),
where it binds ``0.0.0.0`` (design "Decisions (2026-09-30)", item 1). ``--host``
may name another address, but a public one is refused outside production.
"""

from __future__ import annotations

import argparse
import ipaddress
import logging
import sys

import uvicorn

from backend.main import create_app
from backend.settings import ENV_PRODUCTION, Settings, SettingsError


def is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def choose_host(settings: Settings, requested: str | None) -> str:
    """The address to bind: ``requested`` if given, else the settings' default.

    Raises:
        SettingsError: A non-loopback address was asked for outside production.
    """
    host = requested or settings.bind_host
    if settings.env != ENV_PRODUCTION and not is_loopback(host):
        raise SettingsError("only WORKBUDDY_ENV=production may bind a non-loopback address")
    return host


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the WorkBuddy API.")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--host", default=None, help="default: 0.0.0.0 in production, else 127.0.0.1"
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        settings = Settings.from_env(port=args.port)
        host = choose_host(settings, args.host)
    except SettingsError as exc:
        # The message names the setting, never a value.
        print(f"WorkBuddy can't start: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
    uvicorn.run(create_app(settings), host=host, port=args.port, workers=1, log_level="info")


if __name__ == "__main__":
    main()

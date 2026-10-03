"""Serve the API: ``python -m backend [--port 8000]``.

Binds to 127.0.0.1 only, with one worker: sessions and turns live in this
process (design Sections 2 and 6.1). Public binding comes with the demo
password (PR-4).
"""

from __future__ import annotations

import argparse
import logging

import uvicorn

from backend.main import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the WorkBuddy API on 127.0.0.1.")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    uvicorn.run(create_app(), host="127.0.0.1", port=args.port, workers=1, log_level="info")


if __name__ == "__main__":
    main()

"""Write (or check) ``backend/openapi.json``: ``python -m backend.export_openapi [--check]``.

The committed file is the API's contract for scripts, reviews and the
frontend (design "Decisions (2026-09-30, part 2)", ask 3). It is
``app.openapi()`` serialized with sorted keys, 2-space indent and a
trailing newline. ``tests/backend/test_openapi_sync.py`` fails when the
committed file and the app disagree; rerun this command and review the diff.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from backend.main import create_app
from backend.settings import Settings

OPENAPI_PATH = Path(__file__).resolve().parent / "openapi.json"
REGENERATE = "python -m backend.export_openapi"


def render() -> str:
    """The schema as the committed file holds it."""
    with tempfile.TemporaryDirectory() as data_dir:
        app = create_app(Settings(data_dir=Path(data_dir)), lambda confirmation_provider: None)  # type: ignore[arg-type,return-value]
        schema = app.openapi()
    return json.dumps(schema, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="exit 1 if the file is out of date")
    args = parser.parse_args()
    rendered = render()
    if args.check:
        current = OPENAPI_PATH.read_text(encoding="utf-8") if OPENAPI_PATH.exists() else ""
        if current != rendered:
            print(f"{OPENAPI_PATH} is out of date; run: {REGENERATE}", file=sys.stderr)
            return 1
        return 0
    OPENAPI_PATH.write_text(rendered, encoding="utf-8")
    print(f"wrote {OPENAPI_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

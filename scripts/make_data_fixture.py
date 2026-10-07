"""Write a saved-data fixture with the current version of the server.

    python scripts/make_data_fixture.py 1        # tests/deploy/fixtures/data-v1/

Run it only when the on-disk format has changed (``CLAUDE.md``, "Working method": the change needs a
migration, the old fixture stays, and a new one is added beside it). It refuses to overwrite a
fixture that exists: those are what an older version wrote, and they're kept as they are.

It runs the real app with only the LLM faked: a session, an upload, a delivered turn and a
rejected turn (``tests/deploy/data_scenario.py``). The result goes in the fixture folder, with
``manifest.json`` (what the API said while writing it, for the test to compare against).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.store import FORMAT_VERSION  # noqa: E402
from tests.deploy import data_scenario as scenario  # noqa: E402


def main(argv: list[str]) -> int:
    if len(argv) != 1 or not argv[0].isdigit():
        print(__doc__)
        return 2
    target = scenario.FIXTURES / f"data-v{int(argv[0])}"
    if target.exists():
        print(f"{target} exists. A fixture is never overwritten: add the next number.")
        return 1
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "data"
        manifest = scenario.write_scenario(work)
        stray = scenario.paths_outside_sessions(work, str(work))
        if stray:
            print(f"absolute paths outside session.json, which relocation doesn't cover: {stray}")
            return 1
        scenario.relocate(work, str(work), scenario.IMAGE_DATA_DIR)
        shutil.copytree(work, target / "data")
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False
    ).stdout.strip()
    manifest = {
        "format_version": FORMAT_VERSION,
        "written_by_commit": commit,
        "data_dir_when_written": scenario.IMAGE_DATA_DIR,
        **manifest,
    }
    (target / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"wrote {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

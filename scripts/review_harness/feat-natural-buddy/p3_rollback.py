"""P3: can the base server (a rollback) open data the branch wrote? Run with the base tree first on sys.path.

    python p3_rollback.py BASE_TREE HEAD_TREE
Copies HEAD's tests/deploy/fixtures/data-v5 and lists the base store's sessions and turns.
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

base, head = map(lambda p: str(Path(p).resolve()), sys.argv[1:3])
sys.path.insert(0, base)
import backend.store as store  # noqa: E402
from tests.deploy import data_scenario as scenario  # noqa: E402

assert store.__file__.startswith(base), store.__file__
fx = Path(head) / "tests/deploy/fixtures/data-v5"
man = json.loads((fx / "manifest.json").read_text())
tmp = Path(tempfile.mkdtemp())
data = tmp / "data"
shutil.copytree(fx / "data", data)
scenario.relocate(data, man["data_dir_when_written"], str(data))
from tests.backend.helpers import production_settings  # noqa: E402

settings = production_settings(tmp, data_dir=data)
st = store.SessionStore(settings)
for folder in sorted((data / "sessions").iterdir()):
    rec = st.load_session(folder.name)
    if rec is None:
        print("session", folder.name, "-> NOT RESTORED by this tree")
    else:
        print("session", folder.name, "-> restored,", len(rec.turns), "turn(s)")

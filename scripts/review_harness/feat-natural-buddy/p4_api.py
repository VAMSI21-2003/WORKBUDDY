"""P4: through the HTTP API (real app, real pipeline, fake models).

    python p4_api.py            # run from this folder; uses the repo at ../../..

1. A slow writer and the turn deadline: the work is done and verified, then the writer is slow.
   What does the turn end as, and is a file handed back?
2. A writer message with markup: is it escaped on the page?
3. A writer that returns 3 messages: API, page bubbles, restart.
"""
import json
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

from fastapi.testclient import TestClient  # noqa: E402

from backend.main import create_app  # noqa: E402
from tests.backend.factories import PipelineFactory, QueuedProvider, single  # noqa: E402
from backend.settings import Settings  # noqa: E402
from tests.backend.helpers import (  # noqa: E402
    RELAXED_LIMITS,
    new_session,
    start_turn,
    upload,
    wait_finished,
    xlsx_bytes,
)
from tests.buddy.writer_fakes import echo_answer  # noqa: E402


class SlowWriter:
    def __init__(self, delay, answer=None):
        self.delay, self.answer = delay, answer

    def complete(self, *, system, messages, max_tokens=1024):
        time.sleep(self.delay)
        return self.answer(messages[0].content) if self.answer else echo_answer(messages[0].content)


def one(writer, deadline=None, msg="remove duplicate rows"):
    tmp = Path(tempfile.mkdtemp())
    kw = {"turn_deadline_s": deadline} if deadline else {}
    settings = Settings(data_dir=tmp / "data", rate_limits=RELAXED_LIMITS, **kw)
    factory = PipelineFactory(QueuedProvider(single("remove_duplicate_rows")), speaker="natural", writer=writer)
    client = TestClient(create_app(settings, factory))
    sid = new_session(client, "friend")
    up = upload(client, sid, xlsx_bytes())
    assert up.status_code == 201, up.text
    started = start_turn(client, sid, msg)
    turn = wait_finished(client, sid, started.json()["turn_id"])
    return client, sid, turn


if __name__ == "__main__":
    try:
        _, _, t = one(SlowWriter(4.0), deadline=3.0)
        print("1. slow writer (4 s) vs deadline 3 s:", t["outcome"], "| files:", len(t.get("delivered_files") or []),
              "| reply:", t["reply"][:200])
    except TypeError as e:
        print("1. couldn't set the deadline:", e)
    hostile = lambda p: json.dumps({"messages": ["<script>alert(1)</script> <b>bold</b> removed 2 duplicate rows from report.xlsx. values only."],  # noqa: E731
                                    "facts_used": [x for x in __import__("re").findall(r"^- \[(F\d+)\]", p, __import__("re").M)]})
    client, sid, t = one(SlowWriter(0, hostile))
    print("2. markup message outcome:", t["outcome"], "| messages:", t["messages"])
    html = ""
    for path in (f"/sessions/{sid}", f"/s/{sid}", f"/chat/{sid}", "/"):
        r = client.get(path)
        if r.status_code == 200 and "alert(1)" in r.text:
            html = r.text
            print("   page", path, "raw <script> present:", "<script>alert(1)</script>" in html,
                  "| escaped:", "&lt;script&gt;" in html)
            break
    else:
        print("   (no page route found that shows the turn; checked by tests/web instead)")

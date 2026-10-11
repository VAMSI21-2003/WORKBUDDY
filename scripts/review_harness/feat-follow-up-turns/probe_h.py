"""A failed turn that Brain read as "start over" records nothing; restart after a turn that worked on a copy. python probe_h.py [--tree DIR]"""
import dataclasses
from fu import *  # noqa
from backend.main import create_app
FAIL = {"on": False}
class Flaky:
    def __init__(self, inner): self.inner = inner
    def __call__(self, st):
        st = self.inner(st)
        if FAIL["on"]: raise RuntimeError("boom")
        return st
def wrap(cycle): return dataclasses.replace(cycle, hands=Flaky(cycle.hands))
s = Sess("focus", wrap=wrap)
s.turn("dd", brain("remove_duplicate_rows", (RD, "dd")))
FAIL["on"] = True
r = s.turn("start over, empty rows (hands fails)", brain("remove_empty_rows", (RE, "er"), file="original"))
print("failed turn:", r["outcome"], "| next file:", (r["next_request_file"] or {}).get("name"))
FAIL["on"] = False
r = s.turn("er", brain("remove_empty_rows", (RE, "er")))
print("then:", r["outcome"], r["reply"][-160:])
# restart after a copy turn
s.app.client.close()
s.app.client = pr3.TestClient(create_app(s.app.settings, s.app.factory), raise_server_exceptions=False)
st = s.state()
print("after restart:", st.get("session_id") and "session ok", [t["outcome"] for t in st.get("turns", [])], (st.get("next_request_file") or {}).get("name"))

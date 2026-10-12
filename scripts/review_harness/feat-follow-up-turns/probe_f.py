"""What the turn detail and page show for a nothing-left turn (step output names, links). python probe_f.py"""
from fu import *  # noqa
import json
s = Sess("friend")
s.turn("dedupe", brain("remove_duplicate_rows", (RD, "dedupe")))
r = s.turn("dedupe again", brain("remove_duplicate_rows", (RD, "dedupe")))
print(r["outcome"], r["detail_steps"], r["delivered"])
body = s.app.client.get(f"/api/v1/sessions/{s.sid}/turns/{r['turn_id']}").json()
print(json.dumps(body.get("detail"), indent=1)[:1500])
st = s.state()
print([ (t.get("turn_id"), t.get("outcome"), t.get("delivered_files")) for t in st["turns"]])
# the page
page = s.app.client.get(f"/").text if False else None

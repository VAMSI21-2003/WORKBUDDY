"""Rule 2 for delivered files: after several follow-ups every earlier delivered file still downloads with the sha it was listed with. python probe_g.py"""
from fu import *  # noqa
bad = 0
for sp in ("template", "focus"):
    s = Sess(sp)
    listed = {}
    for msg, b in (("dd", brain("remove_duplicate_rows", (RD, "dd"))), ("er", brain("remove_empty_rows", (RE, "er"))),
                   ("start", brain("remove_duplicate_rows", (RD, "dd"), file="original")), ("er2", brain("remove_empty_rows", (RE, "er")))):
        r = s.turn(msg, b)
        body = s.app.client.get(f"/api/v1/sessions/{s.sid}/turns/{r['turn_id']}").json()
        for d in body.get("delivered_files") or []:
            listed[d["download_url"]] = d["sha256"]
    for url, sha in listed.items():
        got = h.sha(s.app.client.get(url).content)
        print(sp, url[-30:], got == sha); bad += got != sha
    print(sp, "uploads unchanged all turns:", all(t["uploads_unchanged"] for t in s.log))
print("MISMATCHES", bad)

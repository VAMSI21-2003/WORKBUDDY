"""Round-1 probe D: the HTTP API with the real pipeline (fake LLM only): download names, file lists, greetings,
reports, no-file replies. Imports pr3.py / harness.py from the other branches' folders (not edited).

    python probe_d_api.py [--tree DIR] [--out FILE]        (--tree is consumed by pr3 before the product imports)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "feat-backend-api-confirmation"))
import pr3  # noqa: E402  (handles --tree)
from pr3 import h  # noqa: E402

OUT = HERE / "out" / "d.json"
if "--out" in sys.argv:
    i = sys.argv.index("--out")
    OUT = Path(sys.argv[i + 1])
    del sys.argv[i:i + 2]

DD, ER = "spreadsheet.deduplicate", "spreadsheet.remove_empty_rows"


def brain(intent, *acts):
    return json.dumps({"intent": intent, "confidence": 0.9,
                       "requested_actions": [{"action": a, "source_phrase": p} for a, p in acts]})


def think(clarify=False, **kw):
    d = {"objective": "o", "summary": "s", "clarification_required": clarify, "confidence": 0.9}
    d.update(kw)
    return json.dumps(d)


def plan(*steps):
    return json.dumps({"steps": [{"description": f"do {c}", "capability": c, "depends_on_step_numbers": [],
                                  "source_phrase": p} for c, p in steps]})


class Routed:
    """Fake LLM routed by system prompt; set .b/.t/.p before each turn."""

    def __init__(self):
        self.b, self.t, self.p, self.calls = brain("unclear"), think(), json.dumps({"steps": []}), 0

    def complete(self, *, system, messages, max_tokens=1024):
        if "intent classifier" in system:
            return self.b
        if "reasoning stage" in system:
            return self.t
        if "planning stage" in system:
            return self.p
        self.calls += 1
        return "ZQXVOICE"


def session(speaker, filename="report.xlsx", data=None, upload=True):
    llm = Routed()
    from backend.settings import DEFAULT_RATE_LIMITS, BucketSpec
    big = {k: BucketSpec(burst=100000, per_hour=1e9) for k in DEFAULT_RATE_LIMITS}
    app = pr3.make_app(llm, speaker, rate_limits=big)
    sid = pr3.new_session(app.client, speaker)
    orig = None
    if upload:
        _, orig = pr3.upload(app, sid, data if data is not None else h.xlsx(), filename)
    return app, llm, sid, orig


def turn(app, llm, sid, orig, msg, b, t=None, p=None):
    llm.b, llm.t, llm.p = b, t or think(), p or json.dumps({"steps": []})
    r = pr3.run_turn(app, sid, msg, orig)
    return r


def listing(app, sid):
    files = app.client.get(f"/api/v1/sessions/{sid}/files").json()["files"]
    state = app.client.get(f"/api/v1/sessions/{sid}").json()
    return files, state


def downloads(app, sid, body_files):
    out = []
    for d in body_files:
        r = app.client.get(d["download_url"])
        out.append(dict(listed=d["name"], http=r.status_code, cd=r.headers.get("content-disposition"),
                        media=r.headers.get("content-type"), sha_ok=(h.sha(r.content) == d["sha256"]) if r.status_code == 200 else None))
    return out


res = {}

# A: four deliveries in one session, then every view of them.
for speaker in ("friend", "template"):
    app, llm, sid, orig = session(speaker)
    try:
        turns = []
        turns.append(turn(app, llm, sid, orig, "remove the duplicate rows", brain("remove_duplicate_rows", ("remove_duplicate_rows", "remove the duplicate rows"))))
        turns.append(turn(app, llm, sid, orig, "remove the duplicate rows again", brain("remove_duplicate_rows", ("remove_duplicate_rows", "remove the duplicate rows"))))
        turns.append(turn(app, llm, sid, orig, "remove the empty rows", brain("remove_empty_rows", ("remove_empty_rows", "remove the empty rows"))))
        turns.append(turn(app, llm, sid, orig, "remove the empty rows", brain("remove_empty_rows", ("remove_empty_rows", "remove the empty rows"))))
        turns.append(turn(app, llm, sid, orig, "remove duplicates then remove empty rows",
                          brain("multi_step_request", ("remove_duplicate_rows", "remove duplicates"), ("remove_empty_rows", "remove empty rows")),
                          p=plan((DD, "remove duplicates"), (ER, "remove empty rows"))))
        turns.append(turn(app, llm, sid, orig, "remove duplicates then remove empty rows",
                          brain("multi_step_request", ("remove_duplicate_rows", "remove duplicates"), ("remove_empty_rows", "remove empty rows")),
                          p=plan((DD, "remove duplicates"), (ER, "remove empty rows"))))
        files, state = listing(app, sid)
        on_disk = sorted(p.name for p in app.settings.outputs_dir.rglob("*.xlsx"))
        views = {
            "turn_bodies": [[d["name"] for d in (app.client.get(f"/api/v1/sessions/{sid}/turns/{t['turn_id']}").json().get("delivered_files") or [])] for t in turns],
            "file_list": [(f["kind"], f["name"]) for f in files],
            "state_turns": [[d["name"] for d in (t.get("delivered_files") or [])] for t in state["turns"]],
        }
        dl = []
        for f in files:
            if f["kind"] == "delivered":
                r = app.client.get(f["download_url"] if "download_url" in f else f"/api/v1/sessions/{sid}/files/{f['file_id']}/download")
                dl.append(dict(listed=f["name"], http=r.status_code, cd=r.headers.get("content-disposition"), media=r.headers.get("content-type")))
        names_listed = [n for ns in views["turn_bodies"] for n in ns]
        res[f"A-{speaker}"] = dict(views=views, downloads=dl, on_disk=on_disk,
                                   distinct=len(set(n.casefold() for n in names_listed)) == len(names_listed),
                                   replies=[t.get("reply") for t in turns], orig_unchanged=[t["original_unchanged"] for t in turns],
                                   outcomes=[t.get("outcome") for t in turns])
    finally:
        app.close()

# B: awkward upload names
for fname in ["my_no_empty_rows_data.xlsx", "report (2).xlsx", "Q3 sales — final.xlsx", "x" * 95 + ".xlsx", "report_deduplicated.xlsx",
              "a.b.c.xlsx", "REPORT.XLSX", "con.xlsx"]:
    app, llm, sid, orig = session("friend", fname)
    try:
        t1 = turn(app, llm, sid, orig, "remove the duplicate rows", brain("remove_duplicate_rows", ("remove_duplicate_rows", "remove the duplicate rows")))
        t2 = turn(app, llm, sid, orig, "remove the empty rows", brain("remove_empty_rows", ("remove_empty_rows", "remove the empty rows")))
        files, _ = listing(app, sid)
        res[f"B-{fname[:40]}"] = dict(stored=sorted(p.name for p in app.settings.outputs_dir.rglob("*.xlsx")),
                                      shown=[f["name"] for f in files if f["kind"] == "delivered"],
                                      uploads=[f["name"] for f in files if f["kind"] != "delivered"])
    finally:
        app.close()

# C: greetings, reports, no-file, through the API
for speaker in ("friend", "focus", "template"):
    app, llm, sid, orig = session(speaker, upload=False)
    try:
        r = turn(app, llm, sid, None, "hi", brain("conversation"))
        res[f"C-{speaker}-hi-nofile"] = dict(reply=r.get("reply"), outcome=r.get("outcome"), src=r.get("reply_source"))
        r = turn(app, llm, sid, None, "remove the duplicate rows", brain("remove_duplicate_rows", ("remove_duplicate_rows", "remove the duplicate rows")))
        res[f"C-{speaker}-action-nofile"] = dict(reply=r.get("reply"), outcome=r.get("outcome"), start=r.get("start_http"), body=r.get("start_body"))
    finally:
        app.close()
    app, llm, sid, orig = session(speaker)
    try:
        r = turn(app, llm, sid, orig, "hi", brain("conversation"))
        res[f"C-{speaker}-hi-file"] = dict(reply=r.get("reply"), outcome=r.get("outcome"), orig=r["original_unchanged"], written=r["written_in_turn_folder"])
        r = turn(app, llm, sid, orig, "give me a report", brain("unclear", ("unsupported", "give me a report")))
        res[f"C-{speaker}-report-file"] = dict(reply=r.get("reply"), outcome=r.get("outcome"), orig=r["original_unchanged"], delivered=r["delivered"])
        r = turn(app, llm, sid, orig, "remove duplicates and give me a report", brain("remove_duplicate_rows", ("remove_duplicate_rows", "remove duplicates"), ("unsupported", "give me a report")))
        res[f"C-{speaker}-dedupe-report"] = dict(reply=r.get("reply"), outcome=r.get("outcome"), orig=r["original_unchanged"], delivered=r["delivered"])
    finally:
        app.close()

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(res, indent=1, ensure_ascii=False, default=str))
print(json.dumps(res, indent=1, ensure_ascii=False, default=str))

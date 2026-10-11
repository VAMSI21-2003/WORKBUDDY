"""Round 5, probe 4: the HTTP API with the real pipeline (fake LLM only) for a report Brain read (`report_on_file`):
report alone, dedupe + report, report after a delivery, in the two speakers the backend can select (friend, focus).
Checks the reply, the outcome, the original's SHA-256 (the helper `pr3.run_turn` does it), the download name and
that the turn record holds no free text. Imports pr3.py / harness.py from the other branch's folder (not edited).

    python r5_api.py [--tree DIR] [--out FILE]        (--tree is consumed by pr3 before the product imports)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "feat-backend-api-confirmation"))
import pr3  # noqa: E402  (handles --tree)
from pr3 import h  # noqa: E402

OUT = HERE / "out" / "r5-api.json"
if "--out" in sys.argv:
    i = sys.argv.index("--out")
    OUT = Path(sys.argv[i + 1])
    del sys.argv[i:i + 2]

DD, AN = "spreadsheet.deduplicate", "spreadsheet.analyze"
RPT = "report_on_file"


def brain(intent, *acts):
    return json.dumps({"intent": intent, "confidence": 0.9,
                       "requested_actions": [{"action": a, "source_phrase": p} for a, p in acts]})


def think():
    return json.dumps({"objective": "o", "summary": "s", "clarification_required": False, "confidence": 0.9})


def plan(*steps):
    return json.dumps({"steps": [{"description": f"do {c}", "capability": c, "depends_on_step_numbers": [],
                                  "source_phrase": p} for c, p in steps]})


class Routed:
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


def session(speaker, filename="report.xlsx"):
    llm = Routed()
    from backend.settings import DEFAULT_RATE_LIMITS, BucketSpec
    big = {k: BucketSpec(burst=100000, per_hour=1e9) for k in DEFAULT_RATE_LIMITS}
    app = pr3.make_app(llm, speaker, rate_limits=big)
    sid = pr3.new_session(app.client, speaker)
    _, orig = pr3.upload(app, sid, h.xlsx(), filename)
    return app, llm, sid, orig


def turn(app, llm, sid, orig, msg, b, p=None):
    llm.b, llm.t, llm.p = b, think(), p or json.dumps({"steps": []})
    return pr3.run_turn(app, sid, msg, orig)


res = {}
for speaker in ("friend", "focus"):
    app, llm, sid, orig = session(speaker)
    try:
        out = []
        out.append(("report-alone", turn(app, llm, sid, orig, "give me a report", brain(RPT, (RPT, "give me a report")))))
        out.append(("dedupe+report", turn(app, llm, sid, orig, "remove the duplicate rows and give me a report",
                                           brain("multi_step_request", ("remove_duplicate_rows", "remove the duplicate rows"), (RPT, "give me a report")),
                                           plan((DD, "remove the duplicate rows"), (AN, "give me a report")))))
        out.append(("report-after-delivery", turn(app, llm, sid, orig, "give me a report again", brain(RPT, (RPT, "give me a report again")))))
        out.append(("dedupe-only", turn(app, llm, sid, orig, "remove the duplicate rows", brain("remove_duplicate_rows", ("remove_duplicate_rows", "remove the duplicate rows")))))
        files = app.client.get(f"/api/v1/sessions/{sid}/files").json()["files"]
        rec = {}
        for name, r in out:
            body = r.get("start_body") or {}
            rec[name] = dict(reply=r.get("reply"), outcome=r.get("outcome"), src=r.get("reply_source"),
                             orig_unchanged=r.get("original_unchanged"), written=r.get("written_in_turn_folder"),
                             delivered=[d.get("name") for d in (r.get("delivered") or [])] if isinstance(r.get("delivered"), list) else r.get("delivered"))
        turns = app.client.get(f"/api/v1/sessions/{sid}").json()["turns"]
        rec["turn_records"] = [dict(intent=(t.get("detail") or {}).get("intent"),
                                    steps=[(s["capability"].split(".")[-1], s["status"], s["verified"]) for s in (t.get("detail") or {}).get("steps", [])],
                                    reply_head=(t.get("reply") or "")[:80]) for t in turns]
        rec["files"] = [(f["kind"], f["name"]) for f in files]
        res[speaker] = rec
        res[speaker]["llm_voice_calls"] = llm.calls
    finally:
        app.close()

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(res, indent=1, ensure_ascii=False, default=str))
print(json.dumps(res, indent=1, ensure_ascii=False, default=str))

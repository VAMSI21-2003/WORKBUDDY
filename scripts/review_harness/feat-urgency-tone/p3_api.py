"""Round-1 probe 3: the confirmation card and the whole turn through the HTTP API, per user_state.

    python p3_api.py [--tree DIR] > out/p3.jsonl     (imports ../feat-backend-api-confirmation/pr3.py)

For each of calm/urgent/frustrated/absent x template/friend/focus: a two-step chain approved, a chain
rejected, a one-step plan approved, an answer to a pending question. Records the plan card (steps,
note), the answers, the reply, the independent read of the delivered file and the original's SHA.
Also: HEAD vs GET /healthz.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "feat-backend-api-confirmation"))
import pr3  # noqa: E402
from pr3 import h  # noqa: E402

DD, ER = "spreadsheet.deduplicate", "spreadsheet.remove_empty_rows"


def brain(intent, acts, state):
    d = {"intent": intent, "confidence": 0.9,
         "requested_actions": [{"action": a, "source_phrase": p} for a, p in acts]}
    if state is not None:
        d["user_state"] = state
    return json.dumps(d)


BOTH = [("remove_duplicate_rows", "remove duplicates"), ("remove_empty_rows", "remove empty rows")]
ONE = [("remove_duplicate_rows", "remove duplicates")]


def run(name, speaker, state, decision, acts, steps, msg):
    intent = "multi_step_request" if len(acts) > 1 else "remove_duplicate_rows"
    replies = [brain(intent, acts, state), h.think()] + ([h.plan(*steps)] if len(steps) > 1 else [])
    llm = h.FakeLLM(replies)
    app = pr3.make_app(llm, speaker)
    try:
        sid = pr3.new_session(app.client, speaker)
        _, original = pr3.upload(app, sid, h.xlsx(), "report.xlsx")
        r = pr3.run_turn(app, sid, msg, original, decision)
        r.update(scenario=name, speaker=speaker, state=state)
        return r
    finally:
        app.close()


if __name__ == "__main__":
    for state in ("calm", "urgent", "frustrated", None):
        for speaker in pr3.SPEAKERS:
            for name, decision, acts, steps in (
                ("chain_approve", "approve", BOTH, [(DD, "remove duplicates"), (ER, "remove empty rows")]),
                ("chain_reject", "reject", BOTH, [(DD, "remove duplicates"), (ER, "remove empty rows")]),
                ("chain_rev_approve", "approve", BOTH[::-1], [(ER, "remove empty rows"), (DD, "remove duplicates")]),
                ("single_approve", "approve", ONE, [(DD, "remove duplicates")]),
                ("single_reject", "reject", ONE, [(DD, "remove duplicates")]),
            ):
                msg = " then ".join(p for _, p in (acts if name != "chain_rev_approve" else BOTH[::-1]))
                print(json.dumps(run(name, speaker, state, decision, acts, steps, msg), default=str))
    # HEAD vs GET /healthz
    app = pr3.make_app(h.FakeLLM([]), "template")
    for m in ("get", "head"):
        r = getattr(app.client, m)("/healthz")
        print(json.dumps({"healthz": m, "status": r.status_code, "len": len(r.content), "headers": dict(r.headers)}))

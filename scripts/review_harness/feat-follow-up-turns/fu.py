# ruff: noqa: E501
"""Library for the feat/follow-up-turns review probes (round 1). Reviewer tooling, not product code.

Real app (`backend.main.create_app`) + real pipeline (`build_engine`: real skills, Hands, Inspector,
Voice) with only the LLM faked. `--tree DIR` (consumed here, before any product import) probes another
checkout, e.g. a worktree of origin/main. Imports pr3.py / harness.py from other branches' folders (not edited).
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "feat-backend-api-confirmation"))
import pr3  # noqa: E402  (handles --tree)
from pr3 import h  # noqa: E402

ROOT = pr3.ROOT
DD, ER, AN = "spreadsheet.deduplicate", "spreadsheet.remove_empty_rows", "spreadsheet.analyze"
RPT = "report_on_file"
RD, RE = "remove_duplicate_rows", "remove_empty_rows"

DEF = h.DUP_ROWS  # Alice,Bob,blank,Alice,Carol,Bob : 2 dups, 1 empty


def brain(intent, *acts, file=None, raw=None):
    d = {"intent": intent, "confidence": 0.9}
    d["requested_actions"] = [{"action": a, "source_phrase": p} for a, p in acts]
    if file is not None:
        d["file_to_use"] = file
    if raw:
        d.update(raw)
    return json.dumps(d)


def think(clarify=False):
    return json.dumps({"objective": "o", "summary": "s", "clarification_required": clarify, "confidence": 0.9})


def plan(*steps):
    return json.dumps({"steps": [{"description": f"do {c}", "capability": c, "depends_on_step_numbers": [],
                                  "source_phrase": p} for c, p in steps]})


class Routed:
    """Fake LLM routed by system prompt; the scenario sets .b/.t/.p before each turn."""
    def __init__(self):
        self.b, self.t, self.p, self.voice_calls = brain("unclear"), think(), json.dumps({"steps": []}), 0
        self.brain_prompts = []

    def complete(self, *, system, messages, max_tokens=1024):
        text = "\n".join(getattr(m, "content", str(m)) for m in messages)
        if "intent classifier" in system:
            self.brain_prompts.append(text)
            if callable(self.b):
                return self.b()
            return self.b
        if "reasoning stage" in system:
            return self.t
        if "planning stage" in system:
            return self.p
        self.voice_calls += 1
        return "ZQXVOICE Done - everything is clean, duplicates removed."


class Sess:
    def __init__(self, speaker, rows=None, filename="report.xlsx", data=None, **settings):
        from backend.settings import DEFAULT_RATE_LIMITS, BucketSpec
        self.llm = Routed()
        big = {k: BucketSpec(burst=100000, per_hour=1e9) for k in DEFAULT_RATE_LIMITS}
        self.app = pr3.make_app(self.llm, speaker, rate_limits=big, **settings)
        self.sid = pr3.new_session(self.app.client, speaker)
        self.speaker = speaker
        self.uploads = {}
        self.orig_id, self.orig_path = self.upload(data if data is not None else h.xlsx(rows), filename)
        self.log = []

    def upload(self, data, name):
        fid, path = pr3.upload(self.app, self.sid, data, name)
        self.uploads[fid] = path
        return fid, path

    def state(self):
        return self.app.client.get(f"/api/v1/sessions/{self.sid}").json()

    def turn(self, msg, b, p=None, think_=None, decision="approve", label=None, **extra):
        self.llm.b, self.llm.t, self.llm.p = b, think_ or think(), p or json.dumps({"steps": []})
        shas = {fid: h.sha(path) for fid, path in self.uploads.items()}
        before_calls = self.llm.voice_calls
        r = pr3.run_turn(self.app, self.sid, msg, None, decision, **extra)
        r["uploads_unchanged"] = all(h.sha(path) == shas[fid] for fid, path in self.uploads.items())
        r["voice_calls"] = self.llm.voice_calls - before_calls
        st = self.state()
        r["next_request_file"] = st.get("next_request_file")
        r["active_file_id"] = st.get("active_file_id")
        r["label"] = label or msg
        r["msg"] = msg
        r["final_describe"] = [d.get("independent") for d in r.get("delivered", [])]
        r["delivered_names"] = [d["name"] for d in r.get("delivered", [])]
        self.log.append(r)
        return r

    def turn_dir(self, tid):
        return self.app.settings.outputs_dir / pr3.session_key(self.sid) / tid

    def close(self):
        self.app.close()


def short(r):
    return {k: r.get(k) for k in ("label", "outcome", "reply", "delivered_names", "final_describe",
                                  "next_request_file", "written_in_turn_folder", "uploads_unchanged", "voice_calls")}


def dump(path, obj):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(obj, indent=1, default=str))


def outpath(default):
    if "--out" in sys.argv:
        i = sys.argv.index("--out")
        p = Path(sys.argv[i + 1])
        del sys.argv[i:i + 2]
        return p
    return HERE / "out" / default

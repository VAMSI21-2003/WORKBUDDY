# ruff: noqa: E501
"""Faults on a follow-up turn (a stage fails halfway, a delivered file or the turn's copy is touched). python probe_c.py [--tree DIR] [--out FILE]"""
import dataclasses, os
from pathlib import Path
from fu import *  # noqa
from backend.settings import DEFAULT_RATE_LIMITS, BucketSpec

OUT = outpath("c-head.json")
res = {}
FAIL = {"stage": None, "mode": None, "n": 0}


class Flaky:
    def __init__(self, inner, name, sess_ref):
        self.inner, self.name, self.ref = inner, name, sess_ref

    def __call__(self, state):
        if FAIL["stage"] != self.name:
            return self.inner(state)
        if FAIL["mode"] == "raise_before":
            raise RuntimeError("secret cell 4111 at /srv/data gsk_FAKE")
        state = self.inner(state)
        if FAIL["mode"] == "raise_after":
            raise RuntimeError("secret cell 4111 at /srv/data gsk_FAKE")
        if FAIL["mode"] == "touch_delivered":
            for p in self.ref["s"].app.settings.outputs_dir.rglob("*-task-*.xlsx"):
                if "input" not in p.parts:
                    os.chmod(p, 0o644); p.write_bytes(p.read_bytes() + b"\0")
                    break
        if FAIL["mode"] == "touch_input":
            for p in self.ref["s"].app.settings.outputs_dir.rglob("input/*.xlsx"):
                os.chmod(p, 0o644); p.write_bytes(p.read_bytes() + b"\0")
        return state


ref = {}


def wrap(cycle):
    return dataclasses.replace(cycle, **{n: Flaky(getattr(cycle, n), n, ref) for n in ("brain", "think", "planner", "hands", "inspector", "voice")})


def run(name, stage, mode, speaker):
    s = Sess(speaker, wrap=wrap)
    ref["s"] = s
    FAIL.update(stage=None, mode=None)
    s.turn("remove the duplicate rows", brain("remove_duplicate_rows", (RD, "remove the duplicate rows")))
    FAIL.update(stage=stage, mode=mode)
    s.turn(f"[{stage}:{mode}] now the empty rows", brain("remove_empty_rows", (RE, "remove the empty rows")), label=f"{stage}:{mode}")
    FAIL.update(stage=None, mode=None)
    s.turn("now the empty rows (retry)", brain("remove_empty_rows", (RE, "remove the empty rows")), label="retry")
    s.turn("dedupe (check)", brain("remove_duplicate_rows", (RD, "remove the duplicate rows")), label="check")
    res[f"{name}/{speaker}"] = [short(r) for r in s.log]
    s.close()


for sp in ("template", "focus"):
    for stage in ("brain", "think", "planner", "hands", "inspector", "voice"):
        run(f"c-{stage}-before", stage, "raise_before", sp)
        run(f"c-{stage}-after", stage, "raise_after", sp)
    run("c-touch-delivered", "hands", "touch_delivered", sp)
    run("c-touch-input", "hands", "touch_input", sp)
dump(OUT, res)
print("wrote", OUT, len(res))

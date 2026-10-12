# ruff: noqa: E501
"""Think/Brain retry: fail once, fail twice, raise with a secret + user text in the exception; what is logged.  python probe_e_retry_logs.py [--tree DIR] 2>/dev/null"""
from cr import *  # noqa
import logging, io
SECRET = "gsk_REVIEWSECRET0123456789abcdef"
USERTEXT = "CONFIDENTIAL-ROW-xyz remove duplicate rows"
buf = io.StringIO(); hdl = logging.StreamHandler(buf); hdl.setLevel(logging.DEBUG)
fmt = logging.Formatter("%(levelname)s %(name)s %(message)s"); hdl.setFormatter(fmt)
logging.getLogger().addHandler(hdl); logging.getLogger().setLevel(logging.INFO)
out = {}
def seq(*vals):
    it = iter(vals)
    def f():
        v = next(it)
        if isinstance(v, BaseException): raise v
        return v
    return f
good_b = bj(phrase="remove duplicate rows")
for sp in ("template", "friend", "focus"):
    s = mk(sp, [("messy-data.xlsx", LOOSE)])
    cases = {
      "brain-fail-once-nonjson": (seq("nope", good_b), tj()),
      "brain-fail-once-raise":   (seq(RuntimeError(f"{SECRET} {USERTEXT}"), good_b), tj()),
      "brain-fail-twice-raise":  (seq(RuntimeError(f"{SECRET} {USERTEXT}"), RuntimeError(f"{SECRET} {USERTEXT}")), tj()),
      "brain-wrong-shape-twice": (seq('[1,2]', '"x"'), tj()),
      "brain-null-twice":        (seq("null", "null"), tj()),
      "think-fail-once":         (good_b, None),
      "think-fail-twice-raise":  (good_b, "RAISE"),
    }
    for k, (b, th) in cases.items():
        buf.truncate(0); buf.seek(0)
        if k.startswith("think-fail-once"):
            s.llm.t = None
        # Think handled by the Routed returning self.t; for sequences use callable via attribute swap
        if k == "think-fail-once":
            tseq = seq("garbage", tj())
            orig = Routed.complete
            def complete(self, *, system, messages, max_tokens=1024, _o=orig, _t=tseq):
                if "reasoning stage" in system: return _t()
                return _o(self, system=system, messages=messages, max_tokens=max_tokens)
            Routed.complete = complete
        elif k == "think-fail-twice-raise":
            orig = Routed.complete
            def complete(self, *, system, messages, max_tokens=1024, _o=orig):
                if "reasoning stage" in system: raise RuntimeError(f"{SECRET} {USERTEXT}")
                return _o(self, system=system, messages=messages, max_tokens=max_tokens)
            Routed.complete = complete
        r = s.turn(USERTEXT, b, think_=th or tj())
        if k.startswith("think-fail"): Routed.complete = orig
        logs = buf.getvalue()
        out[f"{k}/{sp}"] = {"outcome": r.get("outcome"), "reply": r.get("reply"), "delivered": r.get("delivered_names"),
                            "log_has_secret": SECRET in logs, "log_has_usertext": "CONFIDENTIAL" in logs,
                            "log_lines": [l[:200] for l in logs.splitlines() if "Turn" in l or "ClaudeThinker" in l or "Interpreter" in l][:6]}
    s.close()
dump(outpath("e-head.json"), out)
for k, v in out.items():
    if not k.endswith("/friend") and "--all" not in sys.argv: continue
    print(k, json.dumps(v)[:900])
print("ANY secret in logs:", any(v["log_has_secret"] for v in out.values()), " ANY user text in logs:", any(v["log_has_usertext"] for v in out.values()))

"""Brain and Think meeting provider errors, through the real pipeline (template speaker).

A 429, a 503, a timeout, a 400, a connection drop and a status given as text, raised on the first
call only (then a good reply), or on every call. Records the reply, the number of Brain calls and
the wall time. Run from the repo root:

    python scripts/review_harness/fix-security-pass/probe_d_retry.py [--tree BASE]
"""
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "feat-reply-quality"))
import r5lib  # noqa: E402

A = r5lib.args()
rq = r5lib.start(A)
import httpx  # noqa: E402
from rq import brain, think  # noqa: E402

RD = ("remove_duplicate_rows", "remove the duplicate rows")
REQ = httpx.Request("POST", "https://api.groq.com/x")


def status_error(code):
    import groq
    resp = httpx.Response(code, request=REQ)
    cls = {429: groq.RateLimitError, 503: groq.InternalServerError, 400: groq.BadRequestError}[code]
    return cls("TOPSECRET provider text", response=resp, body=None)


class Text429(Exception):
    status_code = "429"


ERRORS = {
    "429": lambda: status_error(429),
    "503": lambda: status_error(503),
    "400": lambda: status_error(400),
    "timeout": lambda: __import__("groq").APITimeoutError(request=REQ),
    "connection": lambda: __import__("groq").APIConnectionError(request=REQ),
    "status-as-text": lambda: Text429("TOPSECRET"),
}


def flaky(good, make, times):
    calls = {"n": 0}

    def reply(_messages):
        calls["n"] += 1
        if calls["n"] <= times:
            raise make()
        return good

    return reply, calls


S = {}
for stage in ("brain", "think"):
    for kind, make in ERRORS.items():
        for times in (1, 9):
            S[f"{stage}-{kind}-x{times}"] = (stage, make, times)

if __name__ == "__main__":
    for name, (stage, make, times) in S.items():
        if A.only and not any(name.startswith(o) for o in A.only.split(",")):
            continue
        b, t = brain("remove_duplicate_rows", RD), think()
        fn, calls = flaky(b if stage == "brain" else t, make, times)
        spec = dict(file="plain", turns=[dict(msg="remove the duplicate rows",
                                             b=fn if stage == "brain" else b,
                                             t=fn if stage == "think" else t)])
        start = time.time()
        rec = rq.run(spec, "template")["turns"][0]
        took = time.time() - start
        reply = rec.get("reply") or rec.get("error")
        print(f"== {name:24} calls={calls['n']} seconds={took:4.1f} delivered={rec.get('delivered')} "
              f"orig_ok={rec.get('orig_unchanged')} secret_in_reply={'TOPSECRET' in (reply or '')}")
        print("   " + (reply or "")[:230])

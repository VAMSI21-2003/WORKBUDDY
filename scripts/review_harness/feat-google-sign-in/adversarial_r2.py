# ruff: noqa: E501 — reviewer tooling, not product code
"""Round-2 adversarial cases, aimed at the round-1 fixes (H1, L3, the issuer warning).

    python scripts/review_harness/feat-google-sign-in/adversarial_r2.py [--only rNN] > out/adversarial_r2.jsonl

Imports ``adversarial.py`` (round 1) for its World and helpers. Real app, real pipeline; only the
LLM is faked and Google is the suite's fake provider. Every case that uploads records the
original's SHA-256 before and after, the reply, and what was delivered.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import tempfile
import traceback
from pathlib import Path
from typing import Any

import adversarial as r1  # noqa: E402  (same folder)
from adversarial import ALICE, BOB, PUBLIC_ORIGIN, RELAXED_LIMITS, UNKNOWN, World, read_rows, sha, upload, wait_finished, xlsx_bytes  # noqa: E402

from backend.main import create_app  # noqa: E402
from backend.settings import Settings  # noqa: E402
from tests.backend.factories import PipelineFactory, QueuedProvider, single  # noqa: E402
from tests.backend.helpers import GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, IDP_ISSUER, OPERATOR_TOKEN  # noqa: E402
from tests.backend.signin_helpers import make_idp, sign_in, signed_in  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

CASES: dict[str, Any] = {}
SECRET = "payroll_private.xlsx"


def case(fn):
    CASES[fn.__name__.split("_")[0]] = fn
    return fn


def _app(settings: Settings, replies=None, idp=None):
    kw = {"oidc_http": idp.client()} if idp is not None else {}
    return create_app(settings, PipelineFactory(QueuedProvider(replies if replies is not None else single("remove_duplicate_rows") * 6), speaker="friend"), **kw)


def _client(app, **headers: str) -> TestClient:
    return TestClient(app, base_url=PUBLIC_ORIGIN, headers={"Origin": PUBLIC_ORIGIN, **headers}, raise_server_exceptions=False)


def _demo(app, pw: str) -> TestClient:
    c = _client(app)
    assert c.post("/api/v1/access", data={"password": pw}).status_code == 204
    return c


def _full_turn(app, c: TestClient, data_dir: Path) -> dict[str, Any]:
    """The page's way: POST /ui/sessions, upload, a turn; returns the evidence."""
    c.post("/ui/sessions")
    sid = c.cookies.get("__Host-wb_session")
    c.post(f"/ui/s/{sid}/files", files={"file": (SECRET, xlsx_bytes(), "application/octet-stream")})
    uploads = sorted(p for p in (data_dir / "uploads").rglob("*") if p.is_file())
    orig = uploads[-1]
    before = sha(orig)
    tid = c.post(f"/api/v1/sessions/{sid}/turns", json={"message": "remove duplicate rows"}).json()["turn_id"]
    turn = wait_finished(c, sid, tid)
    delivered = turn.get("delivered_files") or []
    check = None
    if delivered:
        fid = delivered[0]["file_id"]
        got = c.get(f"/api/v1/sessions/{sid}/files/{fid}/download")
        check = read_rows(got.content) if got.status_code == 200 else {"download": got.status_code}
    return {"sid": sid, "reply": turn.get("reply"), "status": turn.get("status"), "delivered": [d.get("name") for d in delivered], "openpyxl": check, "original_sha_same": sha(orig) == before, "original_input": read_rows(orig.read_bytes())}


def _leak(page_text: str, ev: dict[str, Any]) -> dict[str, bool]:
    return {"file_name": SECRET in page_text, "session_id": ev["sid"] in page_text, "reply": bool(ev["reply"]) and ev["reply"][:40] in page_text}


@case
def r01_demo_three_holders_and_restart(_: Any = None) -> dict[str, Any]:
    """H1 again, with a finished turn, three holders, and after a restart of the server on the same data."""
    tmp = tempfile.TemporaryDirectory()
    data = Path(tmp.name) / "data"
    s = Settings(data_dir=data, env="", demo_password="shared-pw", public_origin=PUBLIC_ORIGIN, rate_limits=RELAXED_LIMITS)
    app = _app(s)
    p1 = _demo(app, "shared-pw")
    ev = _full_turn(app, p1, data)
    p2 = _demo(app, "shared-pw")
    g2 = p2.get("/")
    p3 = _demo(app, "shared-pw")
    p3.post("/ui/sessions")  # p3 makes their own, newer session
    p4 = _demo(app, "shared-pw")
    g4 = p4.get("/")
    app2 = _app(s)  # restart: owner.json files say "demo"
    p5 = _demo(app2, "shared-pw")
    g5 = p5.get("/")
    leaks = {"p2": _leak(g2.text, ev), "p4": _leak(g4.text, ev), "p4_sees_p3_session": p3.cookies.get("__Host-wb_session") in g4.text, "p5_after_restart": _leak(g5.text, ev)}
    pointers = {k: "__Host-wb_session" in r.headers.get("set-cookie", "") for k, r in (("p2", g2), ("p4", g4), ("p5", g5))}
    # p1 still reaches their own with their pointer
    own = SECRET in p1.get("/").text
    ok = not any(any(v.values()) if isinstance(v, dict) else v for v in leaks.values()) and not any(pointers.values()) and own and ev["original_sha_same"]
    return {"ok": ok, "p1_turn": ev, "leaks": leaks, "pointer_set": pointers, "p1_still_has_own": own}


@case
def r02_dev_two_browsers_full_turn(_: Any = None) -> dict[str, Any]:
    tmp = tempfile.TemporaryDirectory()
    data = Path(tmp.name) / "data"
    s = Settings(data_dir=data, env="dev", rate_limits=RELAXED_LIMITS)
    app = _app(s)
    one = _client(app)
    ev = _full_turn(app, one, data)
    two = _client(app)
    page = two.get("/")
    leak = _leak(page.text, ev)
    return {"ok": not any(leak.values()) and page.status_code == 200 and "__Host-wb_session" not in page.headers.get("set-cookie", "") and ev["original_sha_same"], "turn": ev, "second_browser": leak, "status": page.status_code}


@case
def r03_mixed_google_and_demo_outside_production(_: Any = None) -> dict[str, Any]:
    """Outside production with Google configured and a demo password: a person lands in theirs only; a password holder lands nowhere."""
    tmp = tempfile.TemporaryDirectory()
    data = Path(tmp.name) / "data"
    idp = make_idp()
    try:
        s = Settings(data_dir=data, env="", demo_password="shared-pw", public_origin=PUBLIC_ORIGIN, rate_limits=RELAXED_LIMITS, google_client_id=GOOGLE_CLIENT_ID, google_client_secret=GOOGLE_CLIENT_SECRET, oidc_issuer=IDP_ISSUER)
    except Exception as exc:  # noqa: BLE001
        return {"ok": True, "mixed_mode_refused_by_settings": repr(exc)}
    app = _app(s, idp=idp)
    alice = _client(app)
    signed_in(alice, idp, ALICE)
    ev = _full_turn(app, alice, data)
    holder = _demo(app, "shared-pw")
    hp = holder.get("/")
    holder.post("/ui/sessions")
    hsid = holder.cookies.get("__Host-wb_session")
    alice2 = _client(app)
    signed_in(alice2, idp, ALICE)
    ap = alice2.get("/")
    bob = _client(app)
    signed_in(bob, idp, BOB)
    bp = bob.get("/")
    res = {"holder_sees_alice": _leak(hp.text, ev), "alice_lands_in_own": ev["sid"] in ap.text, "alice_sees_holder_session": hsid in ap.text, "bob_sees_alice": _leak(bp.text, ev), "bob_sees_holder": hsid in bp.text}
    ok = not any(res["holder_sees_alice"].values()) and res["alice_lands_in_own"] and not res["alice_sees_holder_session"] and not any(res["bob_sees_alice"].values()) and not res["bob_sees_holder"]
    return {"ok": ok, **res, "turn": ev}


@case
def r04_operator_on_the_page(_: Any = None) -> dict[str, Any]:
    """The operator token on GET /: what renders, and no earlier script's session."""
    w = World()
    op = w.operator()
    sid = op.post("/api/v1/sessions", json={"mode": "friend"}).json()["session_id"]
    upload(op, sid, xlsx_bytes(), SECRET)
    page = op.get("/")
    alice = w.browser(ALICE)
    ap = alice.get("/")
    return {"ok": sid not in page.text and SECRET not in page.text and sid not in ap.text, "operator_page_status": page.status_code, "operator_page_is_gate": "Continue with Google" in page.text, "operator_page_is_workspace": 'hx-post="/ui/sessions"' in page.text, "pointer_set": "__Host-wb_session" in page.headers.get("set-cookie", "")}


@case
def r05_stale_and_hostile_callbacks_keep_the_binder(_: Any = None) -> dict[str, Any]:
    """L3: refusals that keep the binder vs those that clear it; an attacker's callback (a state of their own) can't finish or spoil the victim's sign-in."""
    w = World()
    attacker = w.browser()
    a_start = attacker.get("/auth/google/start", follow_redirects=False)
    a_back = w.idp.authorize(a_start.headers["location"], BOB)  # the attacker's own state + code
    victim = w.browser()
    v_start = victim.get("/auth/google/start", follow_redirects=False)
    v_back = w.idp.authorize(v_start.headers["location"], ALICE)
    hostile = victim.get(a_back, follow_redirects=False)  # login CSRF: the attacker's callback in the victim's browser
    kept = "__Host-wb_login" not in hostile.headers.get("set-cookie", "")
    signed_as_bob = "bob@example.test" in victim.get("/").text
    finish = victim.get(v_back, follow_redirects=False)
    alice_home = victim.get("/").text
    # The attacker's own login is still there for the attacker (nothing was used up), and finishes as Bob in the attacker's browser.
    a_finish = attacker.get(a_back, follow_redirects=False)
    # A refusal after the pending login was taken still clears the binder (provider error).
    third = w.browser()
    t_start = third.get("/auth/google/start", follow_redirects=False)
    from urllib.parse import parse_qs, urlsplit
    state = parse_qs(urlsplit(t_start.headers["location"]).query)["state"][0]
    t_err = third.get(f"/auth/google/callback?error=access_denied&state={state}", follow_redirects=False)
    cleared_on_error = "__Host-wb_login" in t_err.headers.get("set-cookie", "")
    ok = hostile.status_code == 400 and kept and not signed_as_bob and finish.status_code == 200 and "Signed in as alice@example.test" in alice_home and a_finish.status_code == 200 and cleared_on_error
    return {"ok": ok, "hostile_status": hostile.status_code, "hostile_reflects_state": parse_qs(urlsplit(a_back).query)["state"][0] in hostile.text, "binder_kept_on_stale": kept, "victim_signed_in_as_attacker": signed_as_bob, "victim_finishes_own": finish.status_code, "attacker_finishes_own": a_finish.status_code, "binder_cleared_on_provider_error": cleared_on_error}


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(self.format(record))


@case
def r06_issuer_warning(_: Any = None) -> dict[str, Any]:
    cap = _Capture()
    root = logging.getLogger()
    root.addHandler(cap)
    old = root.level
    root.setLevel(logging.DEBUG)
    try:
        World()  # production settings, the fake issuer
        fake = [ln for ln in cap.lines if "OIDC_ISSUER" in ln]
        cap.lines.clear()
        tmp = tempfile.TemporaryDirectory()
        from tests.backend.helpers import production_settings
        s = production_settings(Path(tmp.name), oidc_issuer="https://accounts.google.com", rate_limits=RELAXED_LIMITS)
        create_app(s, PipelineFactory(QueuedProvider([])))
        google = [ln for ln in cap.lines if "OIDC_ISSUER" in ln]
        secrets = [ln for ln in fake + google if any(x in ln for x in (OPERATOR_TOKEN, GOOGLE_CLIENT_SECRET))]
    finally:
        root.removeHandler(cap)
        root.setLevel(old)
    return {"ok": len(fake) == 1 and not google and not secrets, "warning_with_fake_issuer": fake, "warning_with_google": google, "secret_in_log": bool(secrets)}


@case
def r07_google_owner_prefix_edges(_: Any = None) -> dict[str, Any]:
    """latest_for's prefix test: owners that look like a person but aren't, straight on the registry."""
    w = World()
    reg = w.app.state.registry
    a = w.browser(ALICE)
    sid = a.post("/api/v1/sessions", json={"mode": "friend"}).json()["session_id"]
    alice_owner = next(o for o in reg._by_owner if o.startswith("google:"))
    probes = {o: reg.latest_for(o) for o in ("google:", "Google:" + alice_owner[7:], alice_owner + " ", " " + alice_owner, "demo", "dev", "operator", "")}
    return {"ok": reg.latest_for(alice_owner) == sid and not any(probes.values()), "alice": reg.latest_for(alice_owner) == sid, "others": probes}


@case
def r08_signed_in_person_then_demo_password_same_browser(_: Any = None) -> dict[str, Any]:
    """Outside production: Alice (Google) is signed in; the same browser then enters the demo password. Does GET / still show Alice's session?"""
    tmp = tempfile.TemporaryDirectory()
    data = Path(tmp.name) / "data"
    idp = make_idp()
    try:
        s = Settings(data_dir=data, env="", demo_password="shared-pw", public_origin=PUBLIC_ORIGIN, rate_limits=RELAXED_LIMITS, google_client_id=GOOGLE_CLIENT_ID, google_client_secret=GOOGLE_CLIENT_SECRET, oidc_issuer=IDP_ISSUER)
    except Exception as exc:  # noqa: BLE001
        return {"ok": True, "mixed_mode_refused_by_settings": repr(exc)}
    app = _app(s, idp=idp)
    c = _client(app)
    signed_in(c, idp, ALICE)
    ev = _full_turn(app, c, data)
    c.post("/api/v1/access", data={"password": "shared-pw"})
    page = c.get("/")
    c.cookies.delete("__Host-wb_session")
    page2 = c.get("/")
    return {"ok": True, "info_only": "same browser, same person: not a cross-person leak either way", "with_pointer_shows_alice": SECRET in page.text, "without_pointer_shows_alice": SECRET in page2.text, "header": "Signed in as" in page2.text}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--only")
    a = p.parse_args()
    for name, fn in CASES.items():
        if a.only and a.only != name:
            continue
        try:
            out = fn()
        except Exception as exc:  # noqa: BLE001
            out = {"ok": None, "PROBE_ERROR": repr(exc), "trace": traceback.format_exc()[-1500:]}
        print(json.dumps({"case": fn.__name__, **out}, default=str), flush=True)


if __name__ == "__main__":
    os.environ.setdefault("WORKBUDDY_PROBE", "1")
    _ = (r1, UNKNOWN, sign_in)
    main()

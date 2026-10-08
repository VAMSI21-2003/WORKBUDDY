# ruff: noqa: E501 — reviewer tooling, not product code
"""Round-1 adversarial cases for sign-in and ownership. One JSON line per case.

    python scripts/review_harness/feat-google-sign-in/adversarial.py [--only cNN] > out/adversarial_head.jsonl

Real app (``create_app``) and real pipeline (``tests.backend.factories.PipelineFactory`` = build_engine
with a queued fake LLM); Google is the suite's fake identity provider (``tests/idp``). Each line has
``ok`` (what a secure server must do held) and the evidence. Imports the suite's helpers; edits none.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))

import jwt  # noqa: E402
import openpyxl  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend.main import create_app  # noqa: E402
from backend.oidc import SignInError  # noqa: E402
from backend.settings import BucketSpec, DEFAULT_RATE_LIMITS, Settings  # noqa: E402
from tests.backend.factories import PipelineFactory, QueuedProvider, single  # noqa: E402
from tests.backend.helpers import (  # noqa: E402
    GOOGLE_CLIENT_ID,
    OPERATOR_TOKEN,
    PUBLIC_ORIGIN,
    RELAXED_LIMITS,
    production_settings,
    upload,
    wait_finished,
    xlsx_bytes,
)
from tests.backend.signin_helpers import ALICE, BOB, make_idp, sign_in, signed_in  # noqa: E402
from tests.idp.provider import Account, _key  # noqa: E402

UNKNOWN = "s_" + "x" * 22
CASES: dict[str, Any] = {}


def case(fn):
    CASES[fn.__name__.split("_")[0]] = fn
    return fn


class World:
    def __init__(self, data: Path | None = None, *, idp=None, replies=None, clock=None, wall_clock=None, **overrides: Any) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.data = data or Path(self.tmp.name) / "data"
        self.idp = idp or make_idp()
        overrides.setdefault("rate_limits", RELAXED_LIMITS)
        overrides["data_dir"] = self.data
        self.settings = production_settings(Path(self.tmp.name), **overrides)
        self.factory = PipelineFactory(QueuedProvider(replies if replies is not None else single("remove_duplicate_rows") * 6), speaker="friend")
        extra: dict[str, Any] = {}
        if clock:
            extra["clock"] = clock
        if wall_clock:
            extra["wall_clock"] = wall_clock
        self.app = create_app(self.settings, self.factory, oidc_http=self.idp.client(), **extra)

    def browser(self, email: str | None = None, ip: str | None = None) -> TestClient:
        headers = {"Origin": PUBLIC_ORIGIN}
        if ip:
            headers["X-Forwarded-For"] = ip
        c = TestClient(self.app, base_url=PUBLIC_ORIGIN, headers=headers, raise_server_exceptions=False)
        if email:
            signed_in(c, self.idp, email)
        return c

    def operator(self) -> TestClient:
        c = TestClient(self.app, base_url=PUBLIC_ORIGIN, raise_server_exceptions=False)
        c.headers["Authorization"] = f"Bearer {OPERATOR_TOKEN}"
        return c


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def tree_hashes(root: Path) -> dict[str, str]:
    return {str(p.relative_to(root)): sha(p) for p in sorted(root.rglob("*")) if p.is_file()} if root.exists() else {}


def delivered_session(w: World, who: TestClient) -> dict[str, Any]:
    sid = who.post("/api/v1/sessions", json={"mode": "friend"}).json()["session_id"]
    up = upload(who, sid, xlsx_bytes()).json()
    orig = next(p for p in w.settings.uploads_dir.rglob("*") if p.is_file())
    before = sha(orig)
    tid = who.post(f"/api/v1/sessions/{sid}/turns", json={"message": "remove duplicate rows"}).json()["turn_id"]
    turn = wait_finished(who, sid, tid)
    return {"sid": sid, "tid": tid, "turn": turn, "upload": up, "orig": orig, "before": before}


def read_rows(content: bytes) -> dict[str, int]:
    ws = openpyxl.load_workbook(io.BytesIO(content), read_only=True).active
    rows = [tuple(r) for r in ws.iter_rows(min_row=2, values_only=True)]
    data = [r for r in rows if any(v is not None for v in r)]
    return {"rows": len(rows), "empty": len(rows) - len(data), "duplicates": len(data) - len(set(data))}


# -- Token checks, in-process against the real OidcClient ---------------------------------------------


def _pem(name: str) -> bytes:
    return _key(name).private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())


def _b64(d: bytes) -> str:
    return base64.urlsafe_b64encode(d).rstrip(b"=").decode()


@case
def c01_token_variants(_: Any = None) -> dict[str, Any]:
    """Tokens the suite's FLAWS don't make, straight into verify_id_token."""
    w = World()
    oidc = w.app.state.oidc
    now = int(time.time())
    base = {"iss": w.idp.issuer, "aud": GOOGLE_CLIENT_ID, "azp": GOOGLE_CLIENT_ID, "sub": "sub-x", "email": "x@example.test", "email_verified": True, "nonce": "N", "iat": now, "exp": now + 600}
    pub_pem = _key("good").public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)

    def rs(claims: dict[str, Any], headers: dict[str, Any] | None = None) -> str:
        return jwt.encode(claims, _pem("good"), algorithm="RS256", headers={"kid": w.idp.kid, **(headers or {})})

    def hs_confusion() -> str:
        head = _b64(json.dumps({"alg": "HS256", "kid": w.idp.kid, "typ": "JWT"}).encode())
        body = _b64(json.dumps(base).encode())
        import hmac as _h
        sig = _h.new(pub_pem, f"{head}.{body}".encode(), hashlib.sha256).digest()
        return f"{head}.{body}.{_b64(sig)}"

    variants = {
        "control_valid": (rs(base), True),
        "alg_confusion_hs256_with_public_key": (hs_confusion(), False),
        "rs512_header": (jwt.encode(base, _pem("good"), algorithm="RS512", headers={"kid": w.idp.kid}), False),
        "aud_list_ours_and_other_azp_ours": (rs({**base, "aud": [GOOGLE_CLIENT_ID, "other"]}), True),
        "aud_list_other_only": (rs({**base, "aud": ["other"]}), False),
        "aud_list_ours_and_other_no_azp": (rs({k: v for k, v in {**base, "aud": [GOOGLE_CLIENT_ID, "other"]}.items() if k != "azp"}), False),
        "no_nonce": (rs({k: v for k, v in base.items() if k != "nonce"}), False),
        "nonce_prefix": (rs({**base, "nonce": "N2"}), False),
        "email_verified_1": (rs({**base, "email_verified": 1}), False),
        "no_iat": (rs({k: v for k, v in base.items() if k != "iat"}), False),
        "no_exp": (rs({k: v for k, v in base.items() if k != "exp"}), False),
        "expired_beyond_leeway": (rs({**base, "exp": now - 120}), False),
        "nbf_future": (rs({**base, "nbf": now + 3600}), False),
        "iss_google_spelling_on_fake_issuer": (rs({**base, "iss": "accounts.google.com"}), False),
        "sub_empty": (rs({**base, "sub": ""}), False),
        "sub_int": (rs({**base, "sub": 12345}), False),
        "email_two_at": (rs({**base, "email": "a@b@example.test"}), False),
        "email_upper_space": (rs({**base, "email": " X@Example.TEST "}), True),
        "kid_path_traversal": (jwt.encode(base, _pem("good"), algorithm="RS256", headers={"kid": "../../etc"}), False),
        "jku_header_ignored": (rs(base, {"jku": "https://evil.test/jwks"}), True),
        "wrong_key_same_kid": (jwt.encode(base, _pem("wrong"), algorithm="RS256", headers={"kid": w.idp.kid}), False),
    }
    results = {}
    ok = True
    for name, (token, should_pass) in variants.items():
        try:
            ident = oidc.verify_id_token(token, "N")
            got = {"accepted": True, "subject": ident.subject, "email": ident.email}
        except SignInError as exc:
            got = {"accepted": False, "reason": exc.reason}
        except Exception as exc:  # noqa: BLE001
            got = {"accepted": False, "EXCEPTION": repr(exc)}
        got["expected_accept"] = should_pass
        if got["accepted"] != should_pass or "EXCEPTION" in got:
            ok = False
        results[name] = got
    return {"ok": ok, "results": results}


# -- The browser flow ---------------------------------------------------------------------------------


@case
def c02_login_csrf(_: Any = None) -> dict[str, Any]:
    """Mallory's own callback URL planted in Alice's browser (with and without Alice's own binder)."""
    w = World()
    w.idp.accounts["mallory@example.test"] = Account("mallory@example.test", "sub-mallory")
    mallory = w.browser()
    started = mallory.get("/auth/google/start", follow_redirects=False)
    planted = w.idp.authorize(started.headers["location"], "mallory@example.test")
    alice = w.browser()
    r1 = alice.get(planted, follow_redirects=False)
    alice.get("/auth/google/start", follow_redirects=False)  # Alice has a binder of her own now
    r2 = alice.get(planted, follow_redirects=False)
    # Mallory can still use her own URL afterwards (a wrong binder takes nothing)
    r3 = mallory.get(planted, follow_redirects=False)
    ok = r1.status_code == 400 and r2.status_code == 400 and "__Host-wb_access" not in alice.cookies and r3.status_code == 200
    return {"ok": ok, "no_binder": r1.status_code, "alice_binder": r2.status_code, "mallory_own": r3.status_code, "alice_cookie": "__Host-wb_access" in alice.cookies, "alice_page_says": "didn't work" in r1.text}


@case
def c03_replay_and_two_tabs(_: Any = None) -> dict[str, Any]:
    w = World()
    a = w.browser()
    started = a.get("/auth/google/start", follow_redirects=False)
    back = w.idp.authorize(started.headers["location"], ALICE)
    first = a.get(back, follow_redirects=False)
    second = a.get(back, follow_redirects=False)
    # Two tabs: start twice, finish the first tab's login.
    b = w.browser()
    s1 = b.get("/auth/google/start", follow_redirects=False)
    s2 = b.get("/auth/google/start", follow_redirects=False)
    back1 = w.idp.authorize(s1.headers["location"], BOB)
    back2 = w.idp.authorize(s2.headers["location"], BOB)
    tab1 = b.get(back1, follow_redirects=False)
    tab2 = b.get(back2, follow_redirects=False)
    ok = first.status_code == 200 and second.status_code == 400 and a.get("/api/v1/sessions/" + UNKNOWN).status_code == 404
    return {"ok": ok, "first": first.status_code, "replay": second.status_code, "still_signed_in_after_replay": a.get("/api/v1/sessions/" + UNKNOWN).status_code, "two_tabs_first_tab": tab1.status_code, "two_tabs_second_tab": tab2.status_code}


@case
def c04_callback_error_and_junk(_: Any = None) -> dict[str, Any]:
    w = World()
    a = w.browser()
    started = a.get("/auth/google/start", follow_redirects=False)
    state = parse_qs(urlsplit(started.headers["location"]).query)["state"][0]
    r_err = a.get(f"/auth/google/callback?state={state}&error=access_denied", follow_redirects=False)
    r_again = a.get(f"/auth/google/callback?state={state}&code=x", follow_redirects=False)
    hostile = a.get("/auth/google/callback?state=%3Cscript%3E&code=%3Cimg%3E&error=%3Cb%3Ehi", follow_redirects=False)
    longp = a.get("/auth/google/callback?state=" + "a" * 5000 + "&code=" + "b" * 5000, follow_redirects=False)
    ok = r_err.status_code == 400 and r_again.status_code == 400 and "<script>" not in hostile.text and "<b>hi" not in hostile.text and longp.status_code in (400, 414, 431)
    return {"ok": ok, "error_param": r_err.status_code, "after_error_same_state": r_again.status_code, "hostile": hostile.status_code, "hostile_reflected": "<script>" in hostile.text or "<b>hi" in hostile.text, "long": longp.status_code}


@case
def c05_allowed_list(_: Any = None) -> dict[str, Any]:
    tmp = tempfile.TemporaryDirectory()
    data = Path(tmp.name) / "data"
    w1 = World(data, allowed_emails=("ALICE@Example.TEST",))
    a = w1.browser(ALICE)
    sid = a.post("/api/v1/sessions", json={"mode": "friend"}).json()["session_id"]
    b = w1.browser()
    rb = sign_in(b, w1.idp, BOB)
    token = a.cookies["__Host-wb_access"]
    # The owner takes Alice off the list and restarts on the same data.
    w2 = World(data, idp=w1.idp, allowed_emails=("bob@example.test",))
    a2 = w2.browser()
    a2.cookies.set("__Host-wb_access", token)
    r_after = a2.get(f"/api/v1/sessions/{sid}")
    page = a2.get("/")
    ok = rb.status_code == 403 and r_after.status_code == 401 and "Continue with Google" in page.text
    return {"ok": ok, "bob_not_on_list": rb.status_code, "bob_page_says": "isn't on the list" in rb.text, "alice_after_removal": r_after.status_code}


@case
def c06_restart_and_disk_paths(_: Any = None) -> dict[str, Any]:
    tmp = tempfile.TemporaryDirectory()
    data = Path(tmp.name) / "data"
    w1 = World(data)
    a = w1.browser(ALICE)
    b = w1.browser(BOB)
    d = delivered_session(w1, a)
    sid = d["sid"]
    ta, tb = a.cookies["__Host-wb_access"], b.cookies["__Host-wb_access"]
    w2 = World(data, idp=w1.idp)  # restart: sessions not loaded, sign-ins restored
    a2, b2 = w2.browser(), w2.browser()
    a2.cookies.set("__Host-wb_access", ta)
    b2.cookies.set("__Host-wb_access", tb)
    bob_first = b2.get(f"/api/v1/sessions/{sid}")  # Bob first, so the record isn't loaded yet
    bob_dl = b2.get(d["turn"]["delivered_files"][0]["download_url"])
    alice = a2.get(f"/api/v1/sessions/{sid}")
    alice_dl = a2.get(d["turn"]["delivered_files"][0]["download_url"])
    bob_after = b2.get(f"/api/v1/sessions/{sid}")
    root = a2.get("/")
    ok = bob_first.status_code == 404 and bob_dl.status_code == 404 and alice.status_code == 200 and alice_dl.status_code == 200 and bob_after.status_code == 404 and sid in root.text
    return {"ok": ok, "bob_before_load": bob_first.status_code, "bob_download": bob_dl.status_code, "alice": alice.status_code, "alice_download": alice_dl.status_code, "alice_lands_in_her_session_after_restart": sid in root.text, "original_unchanged": sha(d["orig"]) == d["before"], "delivered_rows": read_rows(alice_dl.content) if alice_dl.status_code == 200 else None, "reply": d["turn"]["reply"]}


@case
def c07_owner_file_damage(_: Any = None) -> dict[str, Any]:
    from backend.sessions import session_key
    tmp = tempfile.TemporaryDirectory()
    data = Path(tmp.name) / "data"
    w1 = World(data)
    a = w1.browser(ALICE)
    s1 = a.post("/api/v1/sessions", json={"mode": "friend"}).json()["session_id"]
    s2 = a.post("/api/v1/sessions", json={"mode": "friend"}).json()["session_id"]
    s3 = a.post("/api/v1/sessions", json={"mode": "friend"}).json()["session_id"]
    tok = a.cookies["__Host-wb_access"]
    sd = w1.settings.sessions_dir
    (sd / session_key(s1) / "owner.json").unlink()
    o2 = json.loads((sd / session_key(s2) / "owner.json").read_text())
    o2["session_id"] = s3  # an owner file naming another id
    (sd / session_key(s2) / "owner.json").write_text(json.dumps(o2))
    (sd / session_key(s3) / "owner.json").write_text("{not json")
    w2 = World(data, idp=w1.idp)
    a2 = w2.browser()
    a2.cookies.set("__Host-wb_access", tok)
    got = {s: a2.get(f"/api/v1/sessions/{s}").status_code for s in (s1, s2, s3)}
    marked = json.loads((sd / session_key(s1) / "owner.json").read_text())
    ok = all(v == 404 for v in got.values())
    return {"ok": ok, "missing_owner": got[s1], "owner_names_other_id": got[s2], "unreadable_owner": got[s3], "missing_owner_file_after_restart": marked}


@case
def c08_v1_data(_: Any = None) -> dict[str, Any]:
    tmp = tempfile.TemporaryDirectory()
    data = Path(tmp.name) / "data"
    shutil.copytree(ROOT / "tests/deploy/fixtures/data-v1/data", data)
    manifest = json.loads((ROOT / "tests/deploy/fixtures/data-v1/manifest.json").read_text())
    sids = [v for k, v in _walk(manifest) if isinstance(v, str) and v.startswith("s_") and len(v) == 24]
    before = tree_hashes(data / "uploads") | tree_hashes(data / "outputs")
    now = [time.time()]
    w = World(data, wall_clock=lambda: now[0])
    owners = {p.parent.name: json.loads(p.read_text()) for p in w.settings.sessions_dir.glob("*/owner.json")}
    a = w.browser(ALICE)
    op = w.operator()
    got = {s: (a.get(f"/api/v1/sessions/{s}").status_code, op.get(f"/api/v1/sessions/{s}").status_code) for s in sids}
    after_start = tree_hashes(data / "uploads") | tree_hashes(data / "outputs")
    now[0] += 30 * 86400
    swept = w.app.state.maintenance._sweeper.sweep() if hasattr(w.app.state.maintenance, "_sweeper") else None
    left = sorted(p.name for p in w.settings.sessions_dir.iterdir()) if w.settings.sessions_dir.exists() else []
    ok = bool(sids) and all(v == (404, 404) for v in got.values()) and before == after_start and all(o["owner"] is None for o in owners.values())
    return {"ok": ok, "session_ids_in_fixture": len(sids), "alice_operator_status": got, "owner_files": owners, "files_unchanged_by_start": before == after_start, "sweep": repr(swept), "sessions_left_after_sweep": left}


def _walk(obj: Any):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k, v
            yield from _walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield None, v
            yield from _walk(v)


BASE_SCRIPT = r"""
import sys, json
sys.path.insert(0, sys.argv[1])
from pathlib import Path
from fastapi.testclient import TestClient
from backend.main import create_app
from backend.settings import Settings
from tests.backend.factories import PipelineFactory, QueuedProvider
s = Settings(data_dir=Path(sys.argv[2]), env="", demo_password="pw-for-rollback", public_origin="https://demo.example")
app = create_app(s, PipelineFactory(QueuedProvider([])))
c = TestClient(app, base_url="https://demo.example", raise_server_exceptions=False)
c.headers["Authorization"] = "Bearer pw-for-rollback"
out = {}
r = c.get("/api/v1/sessions/" + sys.argv[3])
out["get"] = r.status_code
out["turns"] = len(r.json().get("turns", [])) if r.status_code == 200 else None
new = c.post("/api/v1/sessions", json={"mode": "friend"})
out["new_session"] = new.status_code
out["new_sid"] = new.json().get("session_id")
print(json.dumps(out))
"""


@case
def c09_rollback_and_forward(_: Any = None) -> dict[str, Any]:
    tmp = tempfile.TemporaryDirectory()
    data = Path(tmp.name) / "data"
    w1 = World(data)
    a = w1.browser(ALICE)
    d = delivered_session(w1, a)
    tok = a.cookies["__Host-wb_access"]
    del w1
    p = subprocess.run([sys.executable, "-c", BASE_SCRIPT, "/root/base", str(data), d["sid"]], capture_output=True, text=True, cwd="/root/base", timeout=120)
    base_out = json.loads(p.stdout.strip().splitlines()[-1]) if p.returncode == 0 and p.stdout.strip() else {"stderr": p.stderr[-800:]}
    w2 = World(data)
    a2, b2 = w2.browser(), w2.browser(BOB)
    a2.cookies.set("__Host-wb_access", tok)
    alice = a2.get(f"/api/v1/sessions/{d['sid']}").status_code
    bob = b2.get(f"/api/v1/sessions/{d['sid']}").status_code
    rollback_sid = base_out.get("new_sid")
    rb = (a2.get(f"/api/v1/sessions/{rollback_sid}").status_code, w2.operator().get(f"/api/v1/sessions/{rollback_sid}").status_code) if rollback_sid else None
    ok = base_out.get("get") == 200 and alice == 200 and bob == 404 and (rb is None or rb == (404, 404))
    return {"ok": ok, "base_server_on_head_data": base_out, "forward_alice": alice, "forward_bob": bob, "rollback_made_session_after_forward_alice_operator": rb, "original_unchanged": sha(d["orig"]) == d["before"]}


@case
def c10_operator_and_password(_: Any = None) -> dict[str, Any]:
    w = World(demo_password="a-demo-password-left-in-env")
    op = w.operator()
    a = w.browser(ALICE)
    op_sid = op.post("/api/v1/sessions", json={"mode": "friend"}).json()["session_id"]
    a_sid = a.post("/api/v1/sessions", json={"mode": "friend"}).json()["session_id"]
    pw = TestClient(w.app, base_url=PUBLIC_ORIGIN, headers={"Origin": PUBLIC_ORIGIN}, raise_server_exceptions=False)
    exch = pw.post("/api/v1/access", data={"password": "a-demo-password-left-in-env"})
    pw.headers["Authorization"] = "Bearer a-demo-password-left-in-env"
    bearer_pw = pw.post("/api/v1/sessions", json={"mode": "friend"})
    res = {
        "operator_reads_own": op.get(f"/api/v1/sessions/{op_sid}").status_code,
        "operator_reads_alice": op.get(f"/api/v1/sessions/{a_sid}").status_code,
        "alice_reads_operator": a.get(f"/api/v1/sessions/{op_sid}").status_code,
        "prod_password_exchange": exch.status_code,
        "prod_bearer_password": bearer_pw.status_code,
        "operator_root_page": op.get("/").status_code,
        "gate_has_password_form": "gate-form" in TestClient(w.app, base_url=PUBLIC_ORIGIN).get("/").text,
    }
    ok = res["operator_reads_own"] == 200 and res["operator_reads_alice"] == 404 and res["alice_reads_operator"] == 404 and exch.status_code == 401 and bearer_pw.status_code == 401 and not res["gate_has_password_form"]
    return {"ok": ok, **res}


@case
def c11_person_limits(_: Any = None) -> dict[str, Any]:
    limits = dict(RELAXED_LIMITS)
    limits["user_turns"] = DEFAULT_RATE_LIMITS["user_turns"]
    w = World(rate_limits=limits, trusted_proxy_hops=1, replies=[])
    a1 = w.browser(ALICE, ip="10.0.0.1")
    a2 = TestClient(w.app, base_url=PUBLIC_ORIGIN, headers={"Origin": PUBLIC_ORIGIN, "X-Forwarded-For": "10.0.0.2"}, raise_server_exceptions=False)
    a2.cookies.set("__Host-wb_access", a1.cookies["__Host-wb_access"])
    b = w.browser(BOB, ip="10.0.0.3")
    sid = a1.post("/api/v1/sessions", json={"mode": "friend"}).json()["session_id"]
    bsid = b.post("/api/v1/sessions", json={"mode": "friend"}).json()["session_id"]
    codes = []
    for i in range(12):
        c = a1 if i % 2 == 0 else a2
        r = c.post(f"/api/v1/sessions/{sid}/turns", json={"message": "hello"})
        codes.append(r.status_code)
        if r.status_code in (201, 202):
            wait_finished(c, sid, r.json()["turn_id"], decision=None)
    bob = b.post(f"/api/v1/sessions/{bsid}/turns", json={"message": "hello"}).status_code
    # Bob probing Alice's session spends Bob's tokens, not Alice's
    ok = codes.count(429) >= 1 and bob in (201, 202)
    return {"ok": ok, "alice_codes_two_ips": codes, "bob_first_turn": bob}


@case
def c12_log_redaction(_: Any = None) -> dict[str, Any]:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(name)s %(levelname)s %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    old = root.level
    root.setLevel(logging.DEBUG)
    try:
        w = World()
        a = w.browser()
        started = a.get("/auth/google/start", follow_redirects=False)
        q = parse_qs(urlsplit(started.headers["location"]).query)
        back = w.idp.authorize(started.headers["location"], ALICE)
        code = parse_qs(urlsplit(back).query)["code"][0]
        a.get(back, follow_redirects=False)
        sid = a.post("/api/v1/sessions", json={"mode": "friend"}).json()["session_id"]
        logging.getLogger("uvicorn.access").info('%s - "%s %s HTTP/%s" %d', "1.2.3.4", "GET", back.replace(PUBLIC_ORIGIN, ""), "1.1", 200)
        logging.getLogger("uvicorn.access").info('%s - "%s %s HTTP/%s" %d', "1.2.3.4", "GET", f"/ui/s/{sid}/transcript", "1.1", 200)
        logging.getLogger("probe").info("cookie %s", a.cookies["__Host-wb_access"])
        logging.getLogger("probe").error("an exception text: %s", f"client_secret={w.settings.google_client_secret} GOCSPX-abcdef")
        logging.getLogger("probe").info("token %s", w.idp.id_token(next(iter(w.idp._codes.values()))))
    finally:
        root.removeHandler(handler)
        root.setLevel(old)
    text = stream.getvalue()
    found = {
        "code": code in text,
        "state": q["state"][0] in text,
        "session_id": sid in text,
        "email": ALICE in text,
        "client_secret": w.settings.google_client_secret in text,
        "gocspx": "GOCSPX-abcdef" in text,
        "jwt": "eyJ" in text,
        "raw_access_cookie_value": a.cookies["__Host-wb_access"] in text,
    }
    # Not counted: a bare cookie value with no name around it (no pattern can find it), and the test
    # client secret written as "client_secret=..." by this probe itself with no ? or & before it
    # (no product code logs the secret; a real Google secret is GOCSPX-..., which is masked).
    ok = not any(v for k, v in found.items() if k not in ("raw_access_cookie_value", "client_secret"))
    return {"ok": ok, "found_in_logs": found, "sign_in_log_lines": [line for line in text.splitlines() if "sign" in line.lower()][:8]}


@case
def c13_hostile_email(_: Any = None) -> dict[str, Any]:
    w = World()
    evil = '"<img src=x onerror=alert(1)>"@example.test'
    w.idp.accounts[evil] = Account(evil, "sub-evil")
    c = w.browser()
    r = sign_in(c, w.idp, evil)
    page = c.get("/") if r.status_code == 200 else None
    ok = page is None or ("<img src=x" not in page.text)
    return {"ok": ok, "signin": r.status_code, "raw_tag_in_page": page is not None and "<img src=x" in page.text, "escaped_in_page": page is not None and "&lt;img" in page.text}


@case
def c14_signout(_: Any = None) -> dict[str, Any]:
    w = World()
    a = w.browser(ALICE)
    tok = a.cookies["__Host-wb_access"]
    evil = a.post("/auth/signout", headers={"Origin": "https://evil.test", "HX-Request": "true"})
    none = TestClient(w.app, base_url=PUBLIC_ORIGIN, raise_server_exceptions=False)
    none.cookies.set("__Host-wb_access", tok)
    no_origin = none.post("/auth/signout")
    still = a.get("/api/v1/sessions/" + UNKNOWN).status_code
    good = a.post("/auth/signout", headers={"HX-Request": "true"})
    reuse = TestClient(w.app, base_url=PUBLIC_ORIGIN, raise_server_exceptions=False)
    reuse.cookies.set("__Host-wb_access", tok)
    after = reuse.get("/api/v1/sessions/" + UNKNOWN).status_code
    files_left = len(list((w.settings.data_dir / "auth" / "signins").glob("*.json")))
    ok = evil.status_code == 403 and no_origin.status_code == 403 and still == 404 and good.status_code == 204 and after == 401 and files_left == 0
    return {"ok": ok, "foreign_origin": evil.status_code, "no_origin": no_origin.status_code, "still_signed_in": still, "signout": good.status_code, "old_token_after": after, "signin_files_left": files_left}


@case
def c15_turn_in_flight(_: Any = None) -> dict[str, Any]:
    w = World()
    a, b = w.browser(ALICE), w.browser(BOB)
    sid = a.post("/api/v1/sessions", json={"mode": "friend"}).json()["session_id"]
    upload(a, sid, xlsx_bytes())
    orig = next(p for p in w.settings.uploads_dir.rglob("*") if p.is_file())
    before = sha(orig)
    tid = a.post(f"/api/v1/sessions/{sid}/turns", json={"message": "remove duplicate rows"}).json()["turn_id"]
    for _ in range(300):
        t = a.get(f"/api/v1/sessions/{sid}/turns/{tid}").json()
        if t["status"] == "awaiting_confirmation":
            break
        time.sleep(0.02)
    cid = t["pending_confirmation"]["confirmation_id"]
    bob_api = b.post(f"/api/v1/sessions/{sid}/turns/{tid}/confirmation", json={"confirmation_id": cid, "decision": "reject"}).status_code
    bob_ui = b.post(f"/ui/s/{sid}/turns/{tid}/confirmation", data={"confirmation_id": cid, "decision": "reject"}).status_code
    still = a.get(f"/api/v1/sessions/{sid}/turns/{tid}").json()["status"]
    done = wait_finished(a, sid, tid)
    dl = a.get(done["delivered_files"][0]["download_url"])
    rows = read_rows(dl.content)
    reply = done["reply"]
    ok = bob_api == 404 and bob_ui == 404 and still == "awaiting_confirmation" and done["outcome"] == "delivered" and f"The copy has {rows['rows']} row(s)" in reply and sha(orig) == before
    return {"ok": ok, "bob_api_confirm": bob_api, "bob_ui_confirm": bob_ui, "plan_still_waiting": still, "outcome": done["outcome"], "reply": reply, "independent_read": rows, "original_unchanged": sha(orig) == before}


@case
def c16_file_id_from_other_session(_: Any = None) -> dict[str, Any]:
    w = World()
    a, b = w.browser(ALICE), w.browser(BOB)
    d = delivered_session(w, a)
    bsid = b.post("/api/v1/sessions", json={"mode": "friend"}).json()["session_id"]
    fid = d["turn"]["delivered_files"][0]["file_id"]
    up_fid = d["upload"]["file_id"]
    r1 = b.get(f"/api/v1/sessions/{bsid}/files/{fid}/download")
    r2 = b.get(f"/api/v1/sessions/{bsid}/files/{up_fid}/download")
    r3 = b.post(f"/api/v1/sessions/{bsid}/turns", json={"message": f"remove duplicate rows from {up_fid}"})
    bob_turn = wait_finished(b, bsid, r3.json()["turn_id"]) if r3.status_code == 202 else {}
    leaked = any(x in json.dumps(bob_turn) for x in ("report_deduplicated", d["upload"]["name"]))
    ok = r1.status_code == 404 and r2.status_code == 404 and not bob_turn.get("delivered_files") and not leaked
    return {"ok": ok, "delivered_fid_in_bob_session": r1.status_code, "upload_fid_in_bob_session": r2.status_code, "bob_turn_naming_alice_fid": r3.status_code, "bob_turn_outcome": bob_turn.get("outcome"), "bob_turn_reply": bob_turn.get("reply"), "bob_turn_mentions_alice_file": leaked}


@case
def c17_signin_eviction(_: Any = None) -> dict[str, Any]:
    w = World(access_max_tokens=2)
    w.idp.accounts["carol@example.test"] = Account("carol@example.test", "sub-carol")
    a = w.browser(ALICE)
    w.browser(BOB)
    w.browser("carol@example.test")
    r = a.get("/api/v1/sessions/" + UNKNOWN).status_code
    return {"ok": True, "alice_after_two_more_signins_with_cap_2": r, "note": "eviction is by design (soonest expiry); a DoS lever only"}


@case
def c18_demo_password_mode_latest(_: Any = None) -> dict[str, Any]:
    """Outside production, the demo password is one shared principal; GET / now lands in the latest 'demo' session."""
    tmp = tempfile.TemporaryDirectory()
    s = Settings(data_dir=Path(tmp.name) / "data", env="", demo_password="shared-demo-pw", public_origin=PUBLIC_ORIGIN, rate_limits=RELAXED_LIMITS)
    app = create_app(s, PipelineFactory(QueuedProvider(single("remove_duplicate_rows")), speaker="friend"))

    def person() -> TestClient:
        c = TestClient(app, base_url=PUBLIC_ORIGIN, headers={"Origin": PUBLIC_ORIGIN}, raise_server_exceptions=False)
        assert c.post("/api/v1/access", data={"password": "shared-demo-pw"}).status_code == 204
        return c

    p1 = person()
    r = p1.post("/ui/sessions")
    sid = r.cookies.get("__Host-wb_session") or p1.cookies.get("__Host-wb_session")
    p1.post(f"/ui/s/{sid}/files", files={"file": ("payroll_private.xlsx", xlsx_bytes(), "application/octet-stream")})
    tid = p1.post(f"/api/v1/sessions/{sid}/turns", json={"message": "remove duplicate rows"}).json()["turn_id"]
    turn = wait_finished(p1, sid, tid)
    p2 = person()  # someone else with the password, a fresh browser
    page = p2.get("/")
    sees = {"file_name": "payroll_private.xlsx" in page.text, "reply": (turn.get("reply") or "")[:40] in page.text, "session_id": sid in page.text}
    base_behaviour = "base: GET / without a pointer renders an empty workspace and the page makes a new session"
    return {"ok": not any(sees.values()), "second_person_sees_first_persons": sees, "pointer_set_for_p2": "__Host-wb_session" in page.headers.get("set-cookie", ""), "note": base_behaviour}


@case
def c19_path_tricks(_: Any = None) -> dict[str, Any]:
    w = World()
    a, b = w.browser(ALICE), w.browser(BOB)
    sid = a.post("/api/v1/sessions", json={"mode": "friend"}).json()["session_id"]
    paths = [f"/api/v1/sessions/{sid}/", f"/api/v1/sessions//{sid}", f"/api/v1/sessions/{sid}%2F", f"/api/v1/sessions/{sid}/files/", f"/ui/s/{sid}/transcript/", "/api/v1/sessions/" + sid.replace("s_", "s%5F")]
    res = {}
    ok = True
    for p in paths:
        rb = b.get(p, follow_redirects=True)
        res[p.replace(sid, "<alice>")] = rb.status_code
        if rb.status_code == 200:
            ok = False
    head = b.head(f"/api/v1/sessions/{sid}").status_code
    head_u = b.head(f"/api/v1/sessions/{UNKNOWN}").status_code
    res["HEAD alice vs unknown"] = (head, head_u)
    ok = ok and head == head_u
    return {"ok": ok, "bob": res}


@case
def c20_switch_person_same_browser(_: Any = None) -> dict[str, Any]:
    w = World()
    c = w.browser(ALICE)
    r = c.post("/ui/sessions")
    asid = c.cookies.get("__Host-wb_session")
    c.post(f"/ui/s/{asid}/files", files={"file": ("alice_secret.xlsx", xlsx_bytes(), "application/octet-stream")})
    # Bob signs in on the same browser without signing Alice out.
    sign_in(c, w.idp, BOB)
    page = c.get("/")
    sees = "alice_secret.xlsx" in page.text or asid in page.text
    # And with sign-out between.
    c2 = w.browser(ALICE)
    c2.post("/ui/sessions")
    c2.post("/auth/signout", headers={"HX-Request": "true"})
    signed_in(c2, w.idp, BOB)
    page2 = c2.get("/")
    sees2 = "alice_secret.xlsx" in page2.text
    return {"ok": not sees and not sees2 and r.status_code == 200, "bob_sees_alice_without_signout": sees, "bob_sees_alice_after_signout": sees2, "header": "Signed in as bob@example.test" in page.text}


@case
def c21_recycled_email(_: Any = None) -> dict[str, Any]:
    w = World()
    a = w.browser(ALICE)
    sid = a.post("/api/v1/sessions", json={"mode": "friend"}).json()["session_id"]
    w.idp.accounts[ALICE] = Account(ALICE, "sub-alice-new-account")
    a2 = w.browser(ALICE)
    r = a2.get(f"/api/v1/sessions/{sid}").status_code
    root = a2.get("/")
    return {"ok": r == 404 and sid not in root.text, "same_email_new_subject": r, "lands_in_old_session": sid in root.text}


@case
def c22_expiry(_: Any = None) -> dict[str, Any]:
    now = [time.time()]
    w = World(wall_clock=lambda: now[0])
    a = w.browser(ALICE)
    before = a.get("/api/v1/sessions/" + UNKNOWN).status_code
    now[0] += 12 * 3600 + 1
    after = a.get("/api/v1/sessions/" + UNKNOWN).status_code
    return {"ok": before == 404 and after == 401, "before": before, "after_12h": after}


@case
def c23_latest_after_sweep(_: Any = None) -> dict[str, Any]:
    now = [time.time()]
    w = World(wall_clock=lambda: now[0])
    a = w.browser(ALICE)
    s_old = a.post("/api/v1/sessions", json={"mode": "friend"}).json()["session_id"]
    now[0] += 60
    s_new = a.post("/api/v1/sessions", json={"mode": "friend"}).json()["session_id"]
    fresh = w.browser()
    fresh.cookies.set("__Host-wb_access", a.cookies["__Host-wb_access"])
    lands = fresh.get("/")
    first = "new" if s_new in lands.text else "old" if s_old in lands.text else "none"
    now[0] += 30 * 86400
    try:
        w.app.state.maintenance._sweeper.sweep()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "sweep_error": repr(exc)}
    fresh2 = w.browser()
    fresh2.cookies.set("__Host-wb_access", a.cookies["__Host-wb_access"])
    lands2 = fresh2.get("/")
    return {"ok": first == "new", "lands_first": first, "after_sweep_status": lands2.status_code, "after_sweep_mentions_any": s_new in lands2.text or s_old in lands2.text}


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
    main()

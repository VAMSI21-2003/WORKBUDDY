"""Two people on the compose stack: each signs in (a fake Google behind Caddy), runs a real turn,
and is refused the other's session, files, turns, plan answers and downloads.

    python scripts/live_stack/people_flow.py [--stack-dir /root/vm] [--skip-turns]

Needs the stack up (``deploy/release.sh``), ``tests.idp.server`` running on the host (the stack's
Caddy proxies ``https://idp.test`` to it) and ``127.0.0.1 idp.test`` in /etc/hosts. Talks HTTPS to
https://$WORKBUDDY_DOMAIN with Caddy's own certificate authority. Prints, for every turn: the intent,
``clarification_required``, the steps, whether a file was delivered, a pandas check of the delivered
file against the original, the reply, and the original's SHA-256 before and after. Never prints a
cookie, the operator token or a secret.
"""

from __future__ import annotations

import argparse
import io
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import stack_flow as sf  # noqa: E402  (this folder; stack_flow.ROOT and FIXTURE are pointed at the stack)

ALICE, BOB = "alice@example.test", "bob@example.test"
NOTHING = "s_" + "x" * 22


def say(text: str = "") -> None:
    print(text, flush=True)


class Stack:
    def __init__(self, root: Path) -> None:
        sf.ROOT = root
        sf.FIXTURE = root / "deploy" / "smoke.xlsx"
        self.root = root
        self.domain = sf.env_value("WORKBUDDY_DOMAIN")
        self.operator_token = sf.env_value("WORKBUDDY_OPERATOR_TOKEN")
        cid = subprocess.run(
            [str(root / "deploy" / "compose.sh"), "ps", "-q", "caddy"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        self.ca = Path(tempfile.mkdtemp()) / "root.crt"
        subprocess.run(
            ["docker", "cp", f"{cid}:/data/caddy/pki/authorities/local/root.crt", str(self.ca)],
            check=True, capture_output=True,
        )

    def browser(self) -> httpx.Client:
        return httpx.Client(
            base_url=f"https://{self.domain}", verify=str(self.ca), trust_env=False, timeout=90,
            headers={"Origin": f"https://{self.domain}"}, follow_redirects=False,
        )

    def sign_in(self, email: str) -> httpx.Client:
        """What a browser does: start, the fake Google's account chooser, the callback."""
        http = self.browser()
        start = http.get("/auth/google/start")
        assert start.status_code == 302, start.text
        query = urlsplit(start.headers["location"]).query
        with httpx.Client(verify=str(self.ca), trust_env=False, follow_redirects=False) as idp:
            chosen = idp.get(f"https://idp.test/choose?{query}&email={email}")
        assert chosen.status_code == 302, chosen.text
        back = http.get(chosen.headers["location"])
        assert back.status_code == 200 and "__Host-wb_access" in http.cookies, back.text[:200]
        return http

    def operator(self) -> httpx.Client:
        http = self.browser()
        http.headers["Authorization"] = f"Bearer {self.operator_token}"
        http.headers.pop("Origin")
        return http


def run_turn(http: httpx.Client, who: str, message: str, mode: str, expect: str) -> dict[str, Any]:
    """One real turn, checked independently. Returns what the cross-access checks need."""
    original_bytes = sf.FIXTURE.read_bytes()
    before = sf.sha(original_bytes)
    session = http.post("/api/v1/sessions", json={"mode": mode}).json()["session_id"]
    up = http.post(
        f"/api/v1/sessions/{session}/files",
        files={"file": ("report.xlsx", original_bytes, "application/octet-stream")},
    )
    up.raise_for_status()
    upload = up.json()
    say(f"[{who}] session made, uploaded report.xlsx {upload['sha256'][:16]}…, mode {mode}")
    started = http.post(f"/api/v1/sessions/{session}/turns", json={"message": message})
    started.raise_for_status()
    turn_id = started.json()["turn_id"]
    turn = sf.wait_turn(http, session, turn_id)
    detail = turn["detail"] or {}
    say(f"  message: {message!r}")
    say(f"  intent: {detail.get('intent')}   clarification_required: "
        f"{detail.get('clarification_required')}   stopped_at: {detail.get('stopped_at')}")
    for step in detail.get("steps", []):
        say(f"  step: {step}")
    say(f"  outcome: {turn['outcome']}   files delivered: {len(turn['delivered_files'])}")
    problems: list[str] = []
    file_id = None
    if turn["delivered_files"]:
        item = turn["delivered_files"][0]
        file_id = item["file_id"]
        got = http.get(f"/api/v1/sessions/{session}/files/{file_id}/download")
        got.raise_for_status()
        delivered = pd.read_excel(io.BytesIO(got.content))
        original = pd.read_excel(io.BytesIO(original_bytes))
        want = sf.expected_frame(original, expect.split(",") if expect else [])
        same = sf.same_values(delivered, want)
        say(f"  independent check (pandas): original {len(original)} rows -> delivered "
            f"{len(delivered)} rows; expected {len(want)}; contents equal: {same}; "
            f"duplicates left: {int(delivered.duplicated().sum())}")
        say(f"  download hash {sf.sha(got.content)[:16]}… == recorded {item['sha256'][:16]}…: "
            f"{sf.sha(got.content) == item['sha256']}")
        if not same:
            problems.append("delivered file differs from the independent result")
        if sf.sha(got.content) != item["sha256"]:
            problems.append("downloaded hash differs from the recorded one")
    else:
        problems.append("no file was delivered")
    say(f"  reply: {turn['reply']}")
    server_copy = sf.stored_copy_hash(upload["file_id"])
    after = sf.sha(sf.FIXTURE.read_bytes())
    say(f"  original SHA-256: local before {before[:16]}…, local after {after[:16]}…, server "
        f"recorded {upload['sha256'][:16]}…, server's stored copy after "
        f"{(server_copy or 'UNREADABLE')[:16]}…")
    if not (before == after == upload["sha256"] == server_copy):
        problems.append("the original's hash changed or disagrees")
    say("  RESULT: " + ("; ".join(problems) if problems else "checks passed (reply judged by hand)"))
    return {"session": session, "turn": turn_id, "file": file_id,
            "upload": upload["file_id"], "problems": problems}


def requests_for(session: str, turn: str, file_id: str, upload_id: str) -> list[tuple[str, str, dict[str, Any]]]:
    """Every route that names a session, as (method, path, kwargs) for one session."""
    xlsx = ("b.xlsx", sf.FIXTURE.read_bytes(), "application/octet-stream")
    return [
        ("GET", f"/api/v1/sessions/{session}", {}),
        ("GET", f"/api/v1/sessions/{session}/files", {}),
        ("POST", f"/api/v1/sessions/{session}/files", {"files": {"file": xlsx}}),
        ("POST", f"/api/v1/sessions/{session}/turns", {"json": {"message": "remove duplicate rows"}}),
        ("GET", f"/api/v1/sessions/{session}/turns/{turn}", {}),
        ("POST", f"/api/v1/sessions/{session}/turns/{turn}/confirmation",
         {"json": {"confirmation_id": "c_x", "decision": "approve"}}),
        ("GET", f"/api/v1/sessions/{session}/files/{file_id}/download", {}),
        ("GET", f"/api/v1/sessions/{session}/files/{upload_id}/download", {}),
        ("GET", f"/ui/s/{session}/transcript", {}),
        ("POST", f"/ui/s/{session}/files", {"files": {"file": xlsx}}),
        ("POST", f"/ui/s/{session}/turns", {"data": {"message": "hi", "mode": "friend"}}),
        ("GET", f"/ui/s/{session}/turns/{turn}/poll", {}),
        ("POST", f"/ui/s/{session}/turns/{turn}/confirmation",
         {"data": {"confirmation_id": "c_x", "decision": "approve"}}),
    ]


def cross_check(actor: str, http: httpx.Client, victim: dict[str, Any], expect_status: int) -> int:
    bad = 0
    for method, path, kwargs in requests_for(
        victim["session"], victim["turn"], victim["file"] or "d_none", victim["upload"]
    ):
        got = http.request(method, path, **kwargs)
        nothing_path = path.replace(victim["session"], NOTHING)
        nothing = http.request(method, nothing_path, **kwargs)
        same = got.status_code == nothing.status_code and got.content == nothing.content
        ok = got.status_code == expect_status and (expect_status == 401 or same)
        bad += 0 if ok else 1
        say(f"  {actor:9} {method:4} {re.sub(r's_[A-Za-z0-9_-]{22}', 's_…', path):70} "
            f"{got.status_code}  same as a missing session: {same}  {'ok' if ok else 'WRONG'}")
    return bad


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stack-dir", default="/root/vm")
    parser.add_argument("--skip-turns", action="store_true", help="no real turn (no LLM)")
    args = parser.parse_args()
    stack = Stack(Path(args.stack_dir))
    failures = 0

    say("== Two people sign in (a fake Google, behind Caddy, over HTTPS)")
    alice, bob = stack.sign_in(ALICE), stack.sign_in(BOB)
    for name, http in (("alice", alice), ("bob", bob)):
        page = http.get("/")
        say(f"  {name}: GET / {page.status_code}, header says "
            f"{re.search(r'Signed in as [^<]+', page.text).group(0)!r}")  # type: ignore[union-attr]
    anonymous = stack.browser()
    say(f"  anonymous: GET / shows Continue with Google: {'Continue with Google' in anonymous.get('/').text}")

    people: dict[str, dict[str, Any]] = {}
    if args.skip_turns:
        for name, http in (("alice", alice), ("bob", bob)):
            sid = http.post("/api/v1/sessions", json={"mode": "friend"}).json()["session_id"]
            up = http.post(f"/api/v1/sessions/{sid}/files",
                           files={"file": ("report.xlsx", sf.FIXTURE.read_bytes(), "application/octet-stream")}).json()
            people[name] = {"session": sid, "turn": "t0001", "file": None, "upload": up["file_id"]}
    else:
        say("\n== Alice runs a real turn")
        people["alice"] = run_turn(alice, "alice", "remove duplicate rows", "focus", "dedupe")
        say("\n== Bob runs a real turn")
        people["bob"] = run_turn(bob, "bob", "remove duplicate rows, then remove empty rows", "friend", "dedupe,empty")
        failures += len(people["alice"]["problems"]) + len(people["bob"]["problems"])

    say("\n== Each is refused the other's session, files, turns, plan answers and downloads")
    operator = stack.operator()
    for actor, http, victim in (("bob", bob, "alice"), ("alice", alice, "bob")):
        failures += cross_check(actor, http, people[victim], 404)
    say("  -- the operator token (reaches only its own sessions):")
    failures += cross_check("operator", operator, people["alice"], 404)
    say("  -- no credential:")
    failures += cross_check("anonymous", anonymous, people["alice"], 401)
    own = alice.get(f"/api/v1/sessions/{people['alice']['session']}")
    say(f"  control: Alice reads her own session: {own.status_code}")
    failures += own.status_code != 200
    if people["alice"]["file"]:
        mine = alice.get(f"/api/v1/sessions/{people['alice']['session']}/files/{people['alice']['file']}/download")
        say(f"  control: Alice downloads her own file: {mine.status_code}, {sf.sha(mine.content)[:16]}…")
        failures += mine.status_code != 200

    say("\n== Bob answering Alice's waiting plan")
    if not args.skip_turns:
        sid = people["alice"]["session"]
        # (a second real turn would need the LLM; the plan check uses a turn that is waiting)
        started = alice.post(f"/api/v1/sessions/{sid}/turns", json={"message": "remove empty rows"})
        tid = started.json()["turn_id"]
        plan = None
        for _ in range(150):
            body = alice.get(f"/api/v1/sessions/{sid}/turns/{tid}").json()
            if body["status"] == "awaiting_confirmation":
                plan = body["pending_confirmation"]
                break
            if body["status"] == "finished":
                break
            time.sleep(2)
        if plan is None:
            say("  no plan appeared (the turn finished first): not checked")
        else:
            for decision in ("approve", "reject"):
                r = bob.post(f"/api/v1/sessions/{sid}/turns/{tid}/confirmation",
                             json={"confirmation_id": plan["confirmation_id"], "decision": decision})
                say(f"  bob {decision}: {r.status_code} {r.json()['error']['code']}")
                failures += r.status_code != 404
            still = alice.get(f"/api/v1/sessions/{sid}/turns/{tid}").json()
            say(f"  Alice's turn is still {still['status']} with her plan: "
                f"{still['pending_confirmation'] is not None}")
            failures += still["status"] != "awaiting_confirmation"
            r = alice.post(f"/api/v1/sessions/{sid}/turns/{tid}/confirmation",
                           json={"confirmation_id": plan["confirmation_id"], "decision": "reject"})
            say(f"  Alice rejects her own plan: {r.status_code}")

    say("\n== Landing, sign-out, and a restart of the server")
    fresh = stack.sign_in(ALICE)
    landed = fresh.get("/")
    say(f"  Alice on a new browser lands in a session of hers: "
        f"{people['alice']['session'] in landed.text}")
    bobs = bob.get("/")
    say(f"  Bob's page does not contain Alice's session: {people['alice']['session'] not in bobs.text}")
    failures += people["alice"]["session"] in bobs.text
    out = fresh.post("/auth/signout", headers={"HX-Request": "true"})
    say(f"  sign-out (the page's request): {out.status_code} HX-Redirect {out.headers.get('HX-Redirect')}")
    say(f"  the same browser afterwards: {fresh.get('/api/v1/sessions/' + NOTHING).status_code} (401 = signed out)")
    subprocess.run([str(stack.root / "deploy" / "compose.sh"), "restart", "app"], check=True,
                   capture_output=True)
    for _ in range(60):
        if httpx.get(f"https://{stack.domain}/healthz", verify=str(stack.ca), trust_env=False, timeout=5).status_code == 200:
            break
        time.sleep(2)
    after = alice.get(f"/api/v1/sessions/{people['alice']['session']}")
    say(f"  after restarting the app container, Alice (same cookie) reads her session: {after.status_code}")
    failures += after.status_code != 200
    say(f"  after the restart, Bob reads Alice's: "
        f"{bob.get('/api/v1/sessions/' + people['alice']['session']).status_code}")

    say("\n== The app's log")
    log = subprocess.run([str(stack.root / "deploy" / "compose.sh"), "logs", "--no-log-prefix", "app"],
                         capture_output=True, text=True).stdout
    for name, needle in (("operator token", stack.operator_token),
                         ("client secret", sf.env_value("WORKBUDDY_GOOGLE_CLIENT_SECRET")),
                         ("a token (eyJ…)", "eyJ"), ("Alice's email", ALICE), ("Bob's email", BOB),
                         ("a session id", people["alice"]["session"]),
                         ("an access cookie value", alice.cookies.get("__Host-wb_access") or "?")):
        count = log.count(needle)
        say(f"  {name} in the log: {count}")
        failures += count
    say(f"\nRESULT: {'all checks passed' if not failures else str(failures) + ' check(s) FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

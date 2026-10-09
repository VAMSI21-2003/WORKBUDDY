"""Drive the compose stack over HTTPS the way the page does, and check the result independently.

    python scripts/live_stack/stack_flow.py flow "remove duplicate rows" --expect dedupe
    python scripts/live_stack/stack_flow.py flow "remove duplicate rows, then remove empty rows" \
        --expect dedupe,empty --mode friend
    python scripts/live_stack/stack_flow.py show SESSION_ID DELIVERED_FILE_ID

Talks to https://$WORKBUDDY_DOMAIN (from deploy/.env; the stack's own name "localhost" in these
runs) as the operator (the token from deploy/.env; for a stack from before sign-in with Google, the
password), trusting Caddy's own certificate authority, which it
copies out of the Caddy container. Prints, for a turn: the intent, ``clarification_required``, the
steps, whether a file was delivered, a pandas check of the delivered file against the original, the
reply, and the original's SHA-256 (the local fixture, the server's recorded one, and the server's
stored copy read from the data volume) before and after. Never prints the token or the password.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "deploy" / "smoke.xlsx"  # 6 rows: 2 exact duplicates, 1 empty row
VOLUME = "workbuddy-data"


def env_value(name: str) -> str:
    for line in (ROOT / "deploy" / ".env").read_text().splitlines():
        if line.startswith(name + "="):
            return line.split("=", 1)[1]
    raise SystemExit(f"{name} is not set in deploy/.env")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def stored_copy_hash(file_id: str) -> str | None:
    """SHA-256 of the server's copy of an upload, read from the data volume."""
    done = subprocess.run(
        ["docker", "run", "--rm", "-v", f"{VOLUME}:/data:ro", "--entrypoint", "sh",
         "python:3.11-slim", "-c", f"sha256sum /data/uploads/*/{file_id}/* | cut -d' ' -f1"],
        capture_output=True, text=True, check=False,
    )
    return done.stdout.strip() or None


def client() -> httpx.Client:
    domain = env_value("WORKBUDDY_DOMAIN")
    cid = subprocess.run(
        [str(ROOT / "deploy" / "compose.sh"), "ps", "-q", "caddy"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    ca = Path(tempfile.mkdtemp()) / "root.crt"
    subprocess.run(
        ["docker", "cp", f"{cid}:/data/caddy/pki/authorities/local/root.crt", str(ca)],
        check=True, capture_output=True,
    )
    http = httpx.Client(
        base_url=f"https://{domain}", verify=str(ca), trust_env=False, timeout=60,
        headers={"Origin": f"https://{domain}"},
    )
    try:
        token = env_value("WORKBUDDY_OPERATOR_TOKEN")
    except SystemExit:
        token = ""
    if token:  # a stack with Google sign-in: scripts are the operator (Bearer, no cookie)
        http.headers["Authorization"] = f"Bearer {token}"
        http.headers.pop("Origin")
        return http
    response = http.post("/api/v1/access", data={"password": env_value("WORKBUDDY_DEMO_PASSWORD")})
    if response.status_code != 204:
        raise SystemExit(f"sign-in failed: HTTP {response.status_code}")
    return http


def wait_turn(http: httpx.Client, session: str, turn: str, approve: bool = True) -> dict[str, Any]:
    deadline = time.monotonic() + 300
    answered = False
    while time.monotonic() < deadline:
        body = http.get(f"/api/v1/sessions/{session}/turns/{turn}").json()
        if body["status"] == "awaiting_confirmation" and not answered:
            plan = body["pending_confirmation"]
            print("  plan shown:", [(s["capability"], s["prompt"]) for s in plan["steps"]])
            http.post(
                f"/api/v1/sessions/{session}/turns/{turn}/confirmation",
                json={"confirmation_id": plan["confirmation_id"],
                      "decision": "approve" if approve else "reject"},
            )
            answered = True
        if body["status"] == "finished":
            return dict(body)
        time.sleep(2)
    raise SystemExit("the turn didn't finish in 300 s")


def expected_frame(original: pd.DataFrame, actions: list[str]) -> pd.DataFrame:
    frame = original
    for action in actions:
        frame = frame.drop_duplicates() if action == "dedupe" else frame.dropna(how="all")
    return frame.reset_index(drop=True)


def same_values(delivered: pd.DataFrame, want: pd.DataFrame) -> bool:
    """Equal shape and cell values; empty cells equal each other, dtypes don't matter (the
    original's empty row makes its numbers floats; a delivered file may hold integers)."""

    def cells(frame: pd.DataFrame) -> list[list[Any]]:
        return [list(row) for row in frame.reset_index(drop=True).astype(object)
                .where(frame.reset_index(drop=True).notna(), None).values.tolist()]

    return delivered.shape == want.shape and cells(delivered) == cells(want)


def flow(args: argparse.Namespace) -> int:
    http = client()
    original_bytes = FIXTURE.read_bytes()
    before = sha(original_bytes)
    session = http.post("/api/v1/sessions", json={"mode": args.mode}).json()["session_id"]
    up = http.post(f"/api/v1/sessions/{session}/files",
                   files={"file": ("report.xlsx", original_bytes, "application/octet-stream")})
    up.raise_for_status()
    upload = up.json()
    print(f"session {session} (mode {args.mode}), uploaded report.xlsx {upload['sha256']}")
    started = http.post(f"/api/v1/sessions/{session}/turns", json={"message": args.message})
    started.raise_for_status()
    turn = wait_turn(http, session, started.json()["turn_id"])
    detail = turn["detail"] or {}
    print(f"  message: {args.message!r}")
    print(f"  intent: {detail.get('intent')}   clarification_required: "
          f"{detail.get('clarification_required')}   stopped_at: {detail.get('stopped_at')}")
    for step in detail.get("steps", []):
        print(f"  step: {step}")
    print(f"  outcome: {turn['outcome']}   files delivered: {len(turn['delivered_files'])}")
    problems: list[str] = []
    if turn["delivered_files"]:
        item = turn["delivered_files"][0]
        got = http.get(f"/api/v1/sessions/{session}/files/{item['file_id']}/download")
        got.raise_for_status()
        if sha(got.content) != item["sha256"]:
            problems.append("downloaded hash differs from the recorded one")
        delivered = pd.read_excel(io.BytesIO(got.content))
        original = pd.read_excel(io.BytesIO(original_bytes))
        want = expected_frame(original, args.expect.split(",") if args.expect else [])
        same = same_values(delivered, want)
        print(f"  independent check (pandas): original {len(original)} rows -> delivered "
              f"{len(delivered)} rows; expected {len(want)}; contents equal: {same}; "
              f"duplicates left: {int(delivered.duplicated().sum())}")
        if not same:
            problems.append("delivered file differs from the independently computed result")
        print(f"  download hash {sha(got.content)} == recorded {item['sha256']}: "
              f"{sha(got.content) == item['sha256']}")
        print(f"  delivered file id (for 'show'): {item['file_id']}")
    print(f"  reply: {turn['reply']}")
    server_copy = stored_copy_hash(upload["file_id"])
    after = sha(FIXTURE.read_bytes())
    print(f"  original SHA-256: local before {before[:16]}…, local after {after[:16]}…, "
          f"server recorded {upload['sha256'][:16]}…, server's stored copy after "
          f"{(server_copy or 'UNREADABLE')[:16]}…")
    if not (before == after == upload["sha256"] == server_copy):
        problems.append("the original's hash changed or disagrees")
    verdict = "; ".join(problems) if problems else "checks passed (reply wording judged by hand)"
    print("  RESULT:", verdict)
    return 1 if problems else 0


def show(args: argparse.Namespace) -> int:
    http = client()
    state = http.get(f"/api/v1/sessions/{args.session}")
    print("GET session:", state.status_code)
    if state.status_code != 200:
        return 1
    body = state.json()
    print("  files:", [(f["file_id"], f["name"], f["sha256"][:16]) for f in body["files"]])
    print("  turns:", [(t["turn_id"], t["status"], t["outcome"]) for t in body["turns"]])
    got = http.get(f"/api/v1/sessions/{args.session}/files/{args.file}/download")
    digest = sha(got.content) if got.status_code == 200 else "-"
    print("download:", got.status_code, "sha256", digest)
    if got.status_code == 200 and args.expect is not None:
        want = expected_frame(pd.read_excel(FIXTURE), args.expect.split(",") if args.expect else [])
        delivered = pd.read_excel(io.BytesIO(got.content))
        print(f"independent check (pandas): {len(delivered)} rows; expected {len(want)}; "
              f"contents equal: {same_values(delivered, want)}")
    return 0 if got.status_code == 200 else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    one = sub.add_parser("flow")
    one.add_argument("message")
    one.add_argument("--expect", default="")
    one.add_argument("--mode", default="focus", choices=["friend", "focus"])
    two = sub.add_parser("show")
    two.add_argument("session")
    two.add_argument("file")
    two.add_argument("--expect", default=None)
    args = parser.parse_args()
    return flow(args) if args.command == "flow" else show(args)


if __name__ == "__main__":
    sys.exit(main())

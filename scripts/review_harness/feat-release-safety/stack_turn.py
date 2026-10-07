"""One turn through the running deploy stack (Caddy -> app), checked independently.

Signs in, makes a session in MODE, uploads FILE, sends MESSAGE, approves the plan, downloads what
was delivered and reads it with openpyxl. Hashes the original inside the data volume before and
after (rule 2). Prints one JSON line: outcome, reply, delivered rows, the original's hashes.

    python stack_turn.py --stack DIR --mode friend --file deploy/smoke.xlsx \
        --message "remove duplicate rows" [--decision approve|reject]

DIR is the scratch clone whose deploy/ runs the stack (deploy/.env names the domain and password;
neither is printed).
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import openpyxl


def env_value(env: Path, name: str) -> str:
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.startswith(name + "="):
            return line[len(name) + 1 :]
    return ""


def volume_sha(session_dir_glob: str) -> dict[str, str]:
    """sha256 of every upload in the data volume, read through a throwaway container."""
    done = subprocess.run(
        ["docker", "run", "--rm", "-v", "workbuddy-data:/data:ro", "python:3.11-slim", "sh", "-c",
         f"cd /data/uploads && find . -type f -path '{session_dir_glob}' -exec sha256sum {{}} +"],
        capture_output=True, text=True, check=False,
    )
    out = {}
    for line in done.stdout.splitlines():
        digest, path = line.split(maxsplit=1)
        out[path] = digest
    return out


def rows_of(data: bytes) -> list[list[object]]:
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True)
    return [list(r) for r in wb.worksheets[0].iter_rows(values_only=True)]


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--stack", required=True, type=Path)
    p.add_argument("--mode", default="friend")
    p.add_argument("--file", required=True, type=Path)
    p.add_argument("--message", default="remove duplicate rows")
    p.add_argument("--decision", default="approve")
    p.add_argument("--label", default="")
    a = p.parse_args()
    env = a.stack / "deploy" / ".env"
    domain = env_value(env, "WORKBUDDY_DOMAIN")
    password = env_value(env, "WORKBUDDY_DEMO_PASSWORD")
    caddy = subprocess.run(
        [str(a.stack / "deploy" / "compose.sh"), "ps", "-q", "caddy"],
        capture_output=True, text=True, check=True
    ).stdout.strip()
    ca = Path(tempfile.mkdtemp()) / "root.crt"
    subprocess.run(["docker", "cp", f"{caddy}:/data/caddy/pki/authorities/local/root.crt", str(ca)],
                   check=True, capture_output=True)
    origin = f"https://{domain}"
    c = httpx.Client(base_url=origin, verify=str(ca), headers={"Origin": origin}, timeout=60)
    record: dict[str, object] = {"label": a.label, "mode": a.mode, "message": a.message,
                                 "file": a.file.name}
    r = c.post("/api/v1/access", data={"password": password})
    record["access"] = r.status_code
    if r.status_code >= 300:
        print(json.dumps(record))
        return 1
    sid = c.post("/api/v1/sessions", json={"mode": a.mode}).json()["session_id"]
    local_before = hashlib.sha256(a.file.read_bytes()).hexdigest()
    seen = volume_sha(f"*/{a.file.name}")
    up = c.post(f"/api/v1/sessions/{sid}/files",
                files={"file": (a.file.name, a.file.read_bytes(), "application/octet-stream")})
    record["upload_status"] = up.status_code
    if up.status_code != 201:
        record["upload_body"] = up.text[:300]
        print(json.dumps(record))
        return 0
    # Upload folders are named by a digest of the session id, so take every upload with this
    # file's name and keep the ones that weren't there before the upload.
    vol_before = {k: v for k, v in volume_sha(f"*/{a.file.name}").items() if k not in seen}
    turn = c.post(f"/api/v1/sessions/{sid}/turns", json={"message": a.message}).json()["turn_id"]
    deadline = time.time() + 240
    decided = False
    while True:
        st = c.get(f"/api/v1/sessions/{sid}/turns/{turn}").json()
        if st["status"] == "awaiting_confirmation" and not decided:
            pc = st["pending_confirmation"]
            record["plan"] = [s["capability"] for s in pc["steps"]]
            c.post(f"/api/v1/sessions/{sid}/turns/{turn}/confirmation",
                   json={"confirmation_id": pc["confirmation_id"], "decision": a.decision})
            decided = True
        if st["status"] == "finished" or time.time() > deadline:
            break
        time.sleep(1)
    record["outcome"] = st.get("outcome")
    record["reply"] = st.get("reply")
    record["delivered"] = []
    for f in st.get("delivered_files", []):
        d = c.get(f"/api/v1/sessions/{sid}/files/{f['file_id']}/download")
        entry = {"name": f["name"], "status": d.status_code,
                 "hash_ok": hashlib.sha256(d.content).hexdigest() == f["sha256"]}
        if d.status_code == 200:
            entry["rows"] = rows_of(d.content)
        record["delivered"].append(entry)
    record["original_rows"] = rows_of(a.file.read_bytes())
    vol_after = {k: v for k, v in volume_sha(f"*/{a.file.name}").items() if k in vol_before}
    record["volume_upload_sha_before"] = vol_before
    record["volume_upload_sha_after"] = vol_after
    record["rule2_ok"] = bool(vol_before) and vol_before == vol_after and all(
        v == local_before for v in vol_after.values()
    )
    print(json.dumps(record, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())

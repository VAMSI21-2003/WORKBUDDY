# ruff: noqa: E501 — reviewer tooling, not product code
"""FE-2 image probes. Needs a running Docker daemon and an image built from this branch:

    docker build --secret id=pip_ca,src=/root/.ccr/ca-bundle.crt -t workbuddy:review .
    python scripts/review_harness/feat-frontend-deploy/docker_probe.py [--image workbuddy:review] > out/docker_probe.json

No LLM key is given to the container (``GROQ_API_KEY`` is not passed), so turns end with the
fallback reply; the password and the origin are placeholders. Containers run with
``--network host`` (as in the implementer's runs) on free ports, and are removed afterwards.

- k01 startup refusals: no password; an http origin; a bad number; an origin with a path. Exit
  status, and whether the message names the variable and never the value.
- k02 the image: user, what's in /app, write access, no tests/docs/.git/.env, ``docker history``
  free of the CA bundle and of any secret-looking value.
- k03 a bind-mounted /data owned by root (the deploy doc's warning): what happens at start and on
  the first upload.
- k04 the whole flow over HTTP in production mode (Bearer password, Origin header): session,
  upload, a turn with no LLM, the reply, the original's SHA-256 on the mounted volume before and
  after, the headers of the page, a static file and the download route; then the logs, searched
  for the password and the placeholder key sentinel.
- k05 health: the HEALTHCHECK reaches "healthy"; ``docker stop`` time (SIGTERM to PID 1).
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx
import openpyxl

PASSWORD = "review-sentinel-pw-7f3c"
KEY = "review-sentinel-key-91ab"  # never a real key: proves a key value isn't logged
ORIGIN = "https://demo.example"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def docker(*args: str, check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=check)


def fixture() -> bytes:
    book = openpyxl.Workbook()
    sheet = book.active
    for row in (["name", "amount"], ["Alice", 10], ["Bob", 20], [None, None], ["Alice", 10], ["Carol", 30], ["Bob", 20]):
        sheet.append(row)
    buf = io.BytesIO()
    book.save(buf)
    return buf.getvalue()


def run_container(image: str, name: str, env: dict[str, str], *, volume: Path | None = None, detach: bool = True) -> subprocess.CompletedProcess[str]:
    docker("rm", "-f", name)
    args = ["run", "--name", name, "--network", "host"]
    if detach:
        args.append("-d")
    for k, v in env.items():
        args += ["-e", f"{k}={v}"]
    if volume is not None:
        args += ["-v", f"{volume}:/data"]
    args.append(image)
    return docker(*args)


def wait_exit(name: str, timeout: float = 60) -> tuple[int | None, str]:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        state = docker("inspect", "-f", "{{.State.Running}} {{.State.ExitCode}}", name).stdout.split()
        if state and state[0] == "false":
            return int(state[1]), docker("logs", name).stdout + docker("logs", name).stderr
        time.sleep(0.5)
    return None, docker("logs", name).stderr


def k01(image: str) -> dict[str, Any]:
    port = str(free_port())
    cases = {
        "no password": {"PORT": port, "WORKBUDDY_PUBLIC_ORIGIN": ORIGIN},
        "http origin": {"PORT": port, "WORKBUDDY_DEMO_PASSWORD": PASSWORD, "WORKBUDDY_PUBLIC_ORIGIN": "http://demo.example"},
        "bad number": {"PORT": port, "WORKBUDDY_DEMO_PASSWORD": PASSWORD, "WORKBUDDY_PUBLIC_ORIGIN": ORIGIN, "WORKBUDDY_MAX_UPLOAD_BYTES": "lots-SENTINEL"},
        "zero number": {"PORT": port, "WORKBUDDY_DEMO_PASSWORD": PASSWORD, "WORKBUDDY_PUBLIC_ORIGIN": ORIGIN, "WORKBUDDY_MAX_UPLOAD_BYTES": "0"},
        "origin with a path": {"PORT": port, "WORKBUDDY_DEMO_PASSWORD": PASSWORD, "WORKBUDDY_PUBLIC_ORIGIN": "https://demo.example/SENTINELPATH"},
        "port not a number": {"PORT": "eighty", "WORKBUDDY_DEMO_PASSWORD": PASSWORD, "WORKBUDDY_PUBLIC_ORIGIN": ORIGIN},
    }
    out = {}
    for label, env in cases.items():
        name = "wb-review-k01"
        run_container(image, name, env)
        code, log = wait_exit(name, 60)
        docker("rm", "-f", name)
        out[label] = {
            "exit": code,
            "message": log.strip().splitlines()[-1] if log.strip() else "",
            "leaks_password": PASSWORD in log,
            "leaks_value": "SENTINEL" in log,
        }
    return out


def k02(image: str) -> dict[str, Any]:
    script = (
        "id -u; id -un; ls -A /app; "
        "for p in /app /app/backend /data /tmp /home/app; do if [ -w $p ]; then echo W:$p; else echo R:$p; fi; done; "
        "find / -xdev \\( -name '.git' -o -name '.env' -o -name 'tests' -o -name 'scratch' -o -name 'workspace' -o -name 'docs' -o -name 'pyproject.toml' \\) -not -path '/usr/*' -not -path '/proc/*' 2>/dev/null | head; "
        "ls /run/secrets 2>&1 | head -1; "
        "python -c 'import pytest' 2>&1 | tail -1; python -c 'import playwright' 2>&1 | tail -1; python -c 'import lxml' 2>&1 | tail -1"
    )
    r = docker("run", "--rm", "--entrypoint", "sh", image, "-c", script)
    history = docker("history", "--no-trunc", image).stdout
    return {
        "inside": r.stdout.strip().splitlines(),
        "history_mentions_ca_or_secret": [w for w in ("BEGIN CERTIFICATE", "GROQ_API_KEY=", "ANTHROPIC_API_KEY=", "WORKBUDDY_DEMO_PASSWORD=") if w in history],
        "image_size": docker("image", "inspect", "-f", "{{.Size}}", image).stdout.strip(),
    }


def k03(image: str) -> dict[str, Any]:
    folder = Path(tempfile.mkdtemp(prefix="wb-k03-"))
    os.chmod(folder, 0o755)  # owned by root, as a bind mount is unless chowned
    port = free_port()
    name = "wb-review-k03"
    run_container(image, name, {"PORT": str(port), "WORKBUDDY_DEMO_PASSWORD": PASSWORD, "WORKBUDDY_PUBLIC_ORIGIN": ORIGIN}, volume=folder)
    time.sleep(8)
    running = docker("inspect", "-f", "{{.State.Running}} {{.State.ExitCode}}", name).stdout.strip()
    log = docker("logs", name).stderr + docker("logs", name).stdout
    result: dict[str, Any] = {"state_after_8s": running, "log_tail": log.strip().splitlines()[-3:]}
    if running.startswith("true"):
        c = httpx.Client(base_url=f"http://127.0.0.1:{port}", headers={"Authorization": f"Bearer {PASSWORD}", "Origin": ORIGIN}, timeout=20)
        r = c.post("/api/v1/sessions", json={})
        result["new_session"] = (r.status_code, r.text[:200])
        if r.status_code in (200, 201):
            sid = r.json()["session_id"]
            u = c.post(f"/api/v1/sessions/{sid}/files", files={"file": ("r.xlsx", fixture(), "application/octet-stream")})
            result["upload"] = (u.status_code, u.text[:200])
        result["healthz"] = httpx.get(f"http://127.0.0.1:{port}/healthz").json()
    docker("rm", "-f", name)
    shutil.rmtree(folder, ignore_errors=True)
    return result


def k04(image: str) -> dict[str, Any]:
    folder = Path(tempfile.mkdtemp(prefix="wb-k04-"))
    os.chown(folder, 10001, 10001)
    port = free_port()
    name = "wb-review-k04"
    env = {"PORT": str(port), "WORKBUDDY_DEMO_PASSWORD": PASSWORD, "WORKBUDDY_PUBLIC_ORIGIN": ORIGIN, "GROQ_API_KEY": KEY}
    run_container(image, name, env, volume=folder)
    base = f"http://127.0.0.1:{port}"
    for _ in range(120):
        try:
            if httpx.get(base + "/healthz", timeout=2).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.5)
    out: dict[str, Any] = {}
    c = httpx.Client(base_url=base, headers={"Authorization": f"Bearer {PASSWORD}", "Origin": ORIGIN}, timeout=30)
    try:
        page = httpx.get(base + "/")
        out["page_headers"] = {k: page.headers.get(k) for k in ("cache-control", "referrer-policy", "strict-transport-security", "content-security-policy", "x-content-type-options")}
        wrong = httpx.post(base + "/api/v1/access", data={"password": "wrong-" + PASSWORD}, headers={"Origin": ORIGIN})
        right = httpx.post(base + "/api/v1/access", data={"password": PASSWORD}, headers={"Origin": ORIGIN})
        out["gate"] = {"wrong": wrong.status_code, "right": right.status_code, "cookie_flags": right.headers.get("set-cookie", "").split(";")[1:]}
        r = c.post("/api/v1/sessions", json={})
        sid = r.json()["session_id"]
        u = c.post(f"/api/v1/sessions/{sid}/files", files={"file": ("report.xlsx", fixture(), "application/octet-stream")})
        out["upload"] = u.status_code
        originals = sorted(p for p in (folder / "uploads").rglob("*") if p.is_file())
        before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in originals}
        t = c.post(f"/api/v1/sessions/{sid}/turns", json={"message": "remove duplicate rows", "mode": "focus"})
        tid = t.json()["turn_id"]
        for _ in range(240):
            turn = c.get(f"/api/v1/sessions/{sid}/turns/{tid}").json()
            if turn["status"] == "finished":
                break
            time.sleep(0.5)
        after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in originals}
        out["turn"] = {k: turn.get(k) for k in ("status", "outcome", "reply", "delivered_files")}
        out["original_sha_before"] = before
        out["original_sha_after"] = after
        out["outputs_on_volume"] = sorted(str(p.relative_to(folder)) for p in (folder / "outputs").rglob("*") if p.is_file()) if (folder / "outputs").exists() else []
        out["volume_owner"] = sorted({(p.stat().st_uid, p.stat().st_gid) for p in folder.rglob("*")})
    finally:
        log = docker("logs", name).stdout + docker("logs", name).stderr
        out["log_lines"] = len(log.splitlines())
        out["log_leaks"] = [s for s in (PASSWORD, KEY) if s in log]
        start = time.monotonic()
        docker("stop", name)
        out["docker_stop_s"] = round(time.monotonic() - start, 1)
        out["exit_after_stop"] = docker("inspect", "-f", "{{.State.ExitCode}}", name).stdout.strip()
        docker("rm", "-f", name)
        shutil.rmtree(folder, ignore_errors=True)
    return out


def k05(image: str) -> dict[str, Any]:
    port = free_port()
    name = "wb-review-k05"
    run_container(image, name, {"PORT": str(port), "WORKBUDDY_DEMO_PASSWORD": PASSWORD, "WORKBUDDY_PUBLIC_ORIGIN": ORIGIN})
    status = ""
    start = time.monotonic()
    while time.monotonic() - start < 120:
        status = docker("inspect", "-f", "{{.State.Health.Status}}", name).stdout.strip()
        if status in ("healthy", "unhealthy"):
            break
        time.sleep(2)
    took = round(time.monotonic() - start, 1)
    docker("rm", "-f", name)
    return {"health": status, "seconds": took}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--image", default="workbuddy:review")
    p.add_argument("--only")
    a = p.parse_args()
    result = {}
    for name, fn in (("k01", k01), ("k02", k02), ("k03", k03), ("k04", k04), ("k05", k05)):
        if a.only and a.only not in name:
            continue
        try:
            result[name] = fn(a.image)
        except Exception as exc:  # noqa: BLE001
            result[name] = {"PROBE_ERROR": repr(exc)[:500]}
    print(json.dumps(result, indent=1, default=str))


if __name__ == "__main__":
    main()

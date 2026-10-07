# ruff: noqa: E501 — reviewer tooling, not product code
"""Round 2 image probes for the L7 fix (the startup message for a data folder the app can't
write). Reuses round 1's ``docker_probe.py`` (imported; its k01-k05 can be rerun with it).

    docker build --secret id=pip_ca,src=/root/.ccr/ca-bundle.crt -t workbuddy:r2 .
    python scripts/review_harness/feat-frontend-deploy/docker_probe_r2.py --image workbuddy:r2 > out/docker_probe_r2.json

- k06 /data bind-mounted read-only (``:ro``), owned by uid 10001: EROFS, not EACCES.
- k07 /data is a regular file, not a folder.
- k08 /data writable, but ``/data/uploads`` left behind root-owned (a volume first used by a
  container that ran as root): create_app's ``mkdir(exist_ok=True)`` passes, so the start
  succeeds; what does the first upload do?
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

import docker_probe as k
import httpx


def _start(image: str, name: str, mount: str, port: int) -> None:
    k.docker("rm", "-f", name)
    env = {"PORT": str(port), "WORKBUDDY_DEMO_PASSWORD": k.PASSWORD, "WORKBUDDY_PUBLIC_ORIGIN": k.ORIGIN}
    args = ["run", "-d", "--name", name, "--network", "host", "-v", mount]
    for key, value in env.items():
        args += ["-e", f"{key}={value}"]
    k.docker(*args, image)


def _exit_case(image: str, name: str, mount: str) -> dict[str, Any]:
    _start(image, name, mount, k.free_port())
    code, log = k.wait_exit(name, 30)
    k.docker("rm", "-f", name)
    lines = log.strip().splitlines()
    return {"exit": code, "last_line": lines[-1] if lines else "", "traceback": "Traceback" in log, "leaks_password": k.PASSWORD in log}


def k06(image: str) -> dict[str, Any]:
    folder = Path(tempfile.mkdtemp(prefix="wb-k06-"))
    os.chown(folder, 10001, 10001)
    try:
        return _exit_case(image, "wb-review-k06", f"{folder}:/data:ro")
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def k07(image: str) -> dict[str, Any]:
    folder = Path(tempfile.mkdtemp(prefix="wb-k07-"))
    target = folder / "data"
    target.write_text("not a folder")
    os.chown(target, 10001, 10001)
    try:
        return _exit_case(image, "wb-review-k07", f"{target}:/data")
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def k08(image: str) -> dict[str, Any]:
    folder = Path(tempfile.mkdtemp(prefix="wb-k08-"))
    os.chown(folder, 10001, 10001)
    for sub in ("uploads", "outputs", "sessions"):
        (folder / sub).mkdir()  # root-owned, 0755
    port = k.free_port()
    name = "wb-review-k08"
    _start(image, name, f"{folder}:/data", port)
    try:
        time.sleep(8)
        running = k.docker("inspect", "-f", "{{.State.Running}} {{.State.ExitCode}}", name).stdout.strip()
        out: dict[str, Any] = {"state_after_8s": running}
        if running.startswith("true"):
            c = httpx.Client(base_url=f"http://127.0.0.1:{port}", headers={"Authorization": f"Bearer {k.PASSWORD}", "Origin": k.ORIGIN}, timeout=20)
            r = c.post("/api/v1/sessions", json={})
            out["new_session"] = (r.status_code, r.text[:200])
        log = k.docker("logs", name).stderr + k.docker("logs", name).stdout
        out["log_tail"] = log.strip().splitlines()[-3:]
        return out
    finally:
        k.docker("rm", "-f", name)
        shutil.rmtree(folder, ignore_errors=True)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--image", default="workbuddy:r2")
    p.add_argument("--only")
    a = p.parse_args()
    result: dict[str, Any] = {}
    for name, fn in (("k03", k.k03), ("k06", k06), ("k07", k07), ("k08", k08)):
        if a.only and a.only not in name:
            continue
        try:
            result[name] = fn(a.image)
        except Exception as exc:  # noqa: BLE001
            result[name] = {"PROBE_ERROR": repr(exc)[:500]}
    print(json.dumps(result, indent=1, default=str))


if __name__ == "__main__":
    main()

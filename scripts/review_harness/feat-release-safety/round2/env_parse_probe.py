"""Compare what `docker compose` reads from deploy/.env with what the scripts read (env_value),
and whether lib.sh's check_env_syntax refuses the value. Needs docker compose; touches no volume.

    python scripts/review_harness/feat-release-safety/round2/env_parse_probe.py [--deploy DIR]

A row is a problem when compose and the scripts disagree and the guard accepts the value.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
VALUES = [
    "plain-Password_123", "a\\b", "ends\\", "a\\nb", "#starts", "a#b", "a #b", "{x}", "a=b",
    "x%y!z", "café", "nb sp", "vt\x0bx", "ff\x0cx", "a$b", "a'b", 'a"b', "a`b",
    "trail ", " lead", "with\ttab", "${X}", "a\\$b",
]
LINES = {  # whole lines, for the key side
    "export": "export WORKBUDDY_DEMO_PASSWORD=exported-pass",
    "yaml_colon": "WORKBUDDY_DEMO_PASSWORD: colon-pass",
    "space_eq": "WORKBUDDY_DEMO_PASSWORD = spaced-pass",
    "indented": "  WORKBUDDY_DEMO_PASSWORD=indented-pass",
    "no_equals_secret": "WORKBUDDY_DEMO_PASSWORD=\nsecret$tail-on-its-own-line",
    "indented_comment": "WORKBUDDY_DEMO_PASSWORD=ok-pass\n  # note: old pass was hunter$2",
}
COMPOSE = "services:\n  app:\n    image: busybox\n    env_file: .env\n"


def run(deploy_src: Path, env_text: str) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp) / "deploy"
        d.mkdir()
        (d / "lib.sh").write_text((deploy_src / "lib.sh").read_text())
        (d / ".env").write_bytes(env_text.encode())
        (d / "docker-compose.yml").write_text(COMPOSE)
        guard = subprocess.run(
            ["bash", "-c", f". {d}/lib.sh; check_env_syntax && echo ACCEPTED"],
            capture_output=True, text=True)
        literal = subprocess.run(
            ["bash", "-c", f". {d}/lib.sh; env_value {d}/.env WORKBUDDY_DEMO_PASSWORD"],
            capture_output=True, text=True).stdout.rstrip("\n")
        cfg = subprocess.run(
            ["docker", "compose", "--project-directory", str(d), "--env-file", str(d / ".env"),
             "config", "--format", "json"], capture_output=True, text=True)
        try:
            env = json.loads(cfg.stdout)["services"]["app"].get("environment", {})
            composed = env.get("WORKBUDDY_DEMO_PASSWORD")
        except Exception:  # noqa: BLE001
            composed = None
        return {
            "guard": "accepted" if "ACCEPTED" in guard.stdout else "refused",
            "guard_msg": guard.stderr.strip()[:300],
            "scripts_read": literal, "compose_read": composed,
            "compose_err": cfg.stderr.strip()[:200],
        }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--deploy", default=str(ROOT / "deploy"))
    deploy = Path(ap.parse_args().deploy)
    base = "WORKBUDDY_DOMAIN=example.test\nGROQ_API_KEY=gsk_placeholder\n"
    cases = [(repr(v), base + f"WORKBUDDY_DEMO_PASSWORD={v}\n") for v in VALUES]
    cases += [(k, base + line + "\n") for k, line in LINES.items()]
    for label, text in cases:
        r = run(deploy, text)
        differ = r["scripts_read"] != r["compose_read"]
        r["problem"] = differ and r["guard"] == "accepted"
        print(json.dumps({"case": label, **r}, ensure_ascii=False))


if __name__ == "__main__":
    main()

"""After an upgrade from the demo-password version: a session it saved belongs to nobody.

    python scripts/live_stack/legacy_check.py LEGACY.json [--stack-dir /root/vm] [--expect-swept]

LEGACY.json is {"session", "file", "sha", "upload", "upload_sha"} of a session made before the
upgrade. Checks that two signed-in people and the operator get the unknown-session answer for it,
that its marker says "nobody", and that its files are byte for byte what they were (read from the
data volume). With --expect-swept, that the folders are gone.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import people_flow as pf  # noqa: E402
import stack_flow as sf  # noqa: E402


def volume(script: str) -> str:
    return subprocess.run(
        ["docker", "run", "--rm", "-v", f"{sf.VOLUME}:/data:ro", "--entrypoint", "sh",
         "python:3.11-slim", "-c", script], capture_output=True, text=True, check=False,
    ).stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("legacy")
    parser.add_argument("--stack-dir", default="/root/vm")
    parser.add_argument("--expect-swept", action="store_true")
    args = parser.parse_args()
    legacy = json.loads(Path(args.legacy).read_text())
    stack = pf.Stack(Path(args.stack_dir))
    sid, bad = legacy["session"], 0
    if not args.expect_swept:
        people = {"alice": stack.sign_in(pf.ALICE), "bob": stack.sign_in(pf.BOB), "operator": stack.operator()}
        for name, http in people.items():
            for path in (f"/api/v1/sessions/{sid}", f"/api/v1/sessions/{sid}/files",
                         f"/api/v1/sessions/{sid}/files/{legacy['file']}/download",
                         f"/ui/s/{sid}/transcript"):
                got = http.get(path)
                missing = http.get(path.replace(sid, pf.NOTHING))
                ok = got.status_code == 404 and got.content == missing.content
                bad += not ok
                print(f"  {name:8} GET {path.replace(sid, 's_…')[:60]:60} {got.status_code}  same as a missing session: {got.content == missing.content}")
    key = volume(f"ls /data/sessions | head -50")
    print("  session folders on the volume:", len(key.split()) if key else 0)
    marker = volume("cat /data/sessions/*/owner.json | tr -d ' \\n' | sed 's/}{/}\\n{/g'")
    print("  owner markers:", sorted(set(marker.splitlines())) if marker else "none")
    up = volume(f"sha256sum /data/uploads/*/{legacy['upload']}/* 2>/dev/null | cut -d' ' -f1")
    out = volume("sha256sum /data/outputs/*/t0001/* 2>/dev/null | cut -d' ' -f1")
    if args.expect_swept:
        gone = not up and legacy["sha"] not in out
        print(f"  the legacy upload and delivered file are gone from the volume: {gone}")
        bad += not gone
    else:
        same = up == legacy["upload_sha"] and legacy["sha"] in out
        print(f"  the legacy upload on the volume has its old SHA-256 ({up[:16]}…): {up == legacy['upload_sha']}; "
              f"the delivered file still there with its old SHA-256: {legacy['sha'] in out}")
        bad += not same
    print("RESULT:", "ok" if not bad else f"{bad} check(s) FAILED")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

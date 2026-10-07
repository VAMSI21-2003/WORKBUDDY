# ruff: noqa: E501 — reviewer tooling, not product code
"""Round 2: adversarial restore.sh / backup.sh cases on real Docker, isolated from the real stack.

    python scripts/review_harness/feat-release-safety/round2/restore_probes.py [> out/restore_probes.txt]

Reuses the suite's Site/Volume helpers (tests/deploy/test_release_scripts.py, imported, not edited):
a temporary git repo holding a copy of deploy/, a stub compose.sh that records calls, a unique
data volume read through python:3.11-slim. Prints, per case: exit code, the script's output tail,
what /data holds afterwards, and whether the asked-for archive still exists.
"""

from __future__ import annotations

import io
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
from tests.deploy.test_release_scripts import Site, Volume, _age  # noqa: E402


def data(vol: Volume) -> str:
    return vol.sh("cd /data && find . -mindepth 1 | sort | head -20; echo; cat sessions/marker 2>/dev/null; true", readonly=True).replace("\n", " ")


def report(name: str, done: subprocess.CompletedProcess[str], vol: Volume, extra: str = "") -> None:
    text = (done.stdout + done.stderr).strip().splitlines()
    print(f"\n### {name}\nexit={done.returncode}\n--- output (last 12 lines)")
    print("\n".join(text[-12:]))
    print(f"--- /data now: {data(vol)}")
    calls = vol.site.deploy / "compose.calls"
    print(f"--- compose calls: {calls.read_text().split(chr(10)) if calls.exists() else []}")
    if extra:
        print(extra)


def tar_gz(path: Path, members: dict[str, bytes]) -> None:
    with tarfile.open(path, "w:gz") as tar:
        for name, body in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(body)
            tar.addfile(info, io.BytesIO(body))


def fresh(tmp: Path) -> tuple[Site, Volume]:
    site = Site(tmp)
    return site, Volume(site)


def cleanup(vol: Volume) -> None:
    subprocess.run(["docker", "volume", "rm", "-f", vol.name], capture_output=True, check=False)


def case_relative_path_with_space_and_quote() -> None:
    with tempfile.TemporaryDirectory() as t:
        site, vol = fresh(Path(t))
        try:
            vol.set_marker("one")
            a = vol.backup(); _age(a, 300)
            vol.set_marker("two")
            b = vol.backup(); _age(b, 200)
            vol.set_marker("three")
            odd = vol.backups / "it's a backup"
            odd.mkdir()
            moved = odd / a.name
            a.rename(moved)
            rel = Path("..") / moved.relative_to(site.root)
            done = site.run([str(site.deploy / "restore.sh"), str(rel)], real_docker=True, **vol.env)
            undo = [line for line in (done.stdout + done.stderr).splitlines() if "The data from before is in" in line]
            report("R1 oldest archive, relative path, folder name with a space and a quote, KEEP=2", done, vol,
                   f"--- asked-for archive still exists: {moved.is_file()}; backups now: {sorted(p.name for p in vol.backups.rglob('*.tar.gz'))}; {undo}")
        finally:
            cleanup(vol)


def case_traversal_member() -> None:
    with tempfile.TemporaryDirectory() as t:
        site, vol = fresh(Path(t))
        try:
            vol.set_marker("keep")
            bad = Path(t) / "workbuddy-data-evil.tar.gz"
            tar_gz(bad, {"./sessions/marker": b"evil\n", "../../escape": b"x"})
            done = vol.restore(bad)
            esc = vol.sh("ls -la / | grep escape || echo no-escape-in-volume-root", readonly=True)
            report("R2 hostile archive with a ../ member", done, vol, f"--- escape: {esc.strip()}")
        finally:
            cleanup(vol)


def case_replace_fails_halfway_then_undo() -> None:
    with tempfile.TemporaryDirectory() as t:
        site, vol = fresh(Path(t))
        try:
            vol.set_marker("before-restore")
            odd = Path(t) / "workbuddy-data-odd.tar.gz"
            tar_gz(odd, {"./sessions/marker": b"from-archive\n", "./my dir/file": b"x"})
            done = vol.restore(odd)
            report("R3 archive with a top-level name holding a space (replace step fails?)", done, vol)
            out = done.stdout + done.stderr
            undo = [line.strip() for line in out.splitlines() if line.strip().startswith("./deploy/restore.sh")]
            if undo:
                # the printed command assumes the repo root as cwd: run it exactly so, from site.repo
                cmd = ["bash", "-c", undo[0]]
                d2 = site.run(cmd, real_docker=True, **vol.env)
                report("R3b the printed undo command, run from the repository root", d2, vol)
        finally:
            cleanup(vol)


def case_disk_full_during_unpack() -> None:
    mounts: list[Path] = []
    with tempfile.TemporaryDirectory() as t:
        site, vol = fresh(Path(t))
        try:
            subprocess.run(["docker", "volume", "rm", "-f", vol.name], capture_output=True)
            # A tmpfs-type volume is a fresh tmpfs per container, so bind a host tmpfs instead (needs root).
            small = Path(t) / "small"
            small.mkdir()
            subprocess.run(["mount", "-t", "tmpfs", "-o", "size=4m", "tmpfs", str(small)], check=True)
            mounts.append(small)
            subprocess.run(["docker", "volume", "create", "--driver", "local", "--opt", "type=none",
                            "--opt", "o=bind", "--opt", f"device={small}", vol.name], capture_output=True, check=True)
            vol.sh("mkdir -p /data/sessions && head -c 2500000 /dev/urandom > /data/sessions/big && echo keep > /data/sessions/marker && chown -R 10001:10001 /data")
            big = Path(t) / "workbuddy-data-big.tar.gz"
            import os
            tar_gz(big, {"./sessions/marker": b"archive\n", "./sessions/big2": os.urandom(2_500_000)})
            done = vol.restore(big)
            report("R4 the volume can't hold a second copy (4 MB tmpfs, 2.5 MB now, 2.5 MB archive)", done, vol)
        finally:
            cleanup(vol)
            for m in mounts:
                subprocess.run(["umount", str(m)], check=False)


def case_leftover_staging() -> None:
    with tempfile.TemporaryDirectory() as t:
        site, vol = fresh(Path(t))
        try:
            vol.set_marker("one")
            a = vol.backup()
            vol.set_marker("two")
            vol.sh("mkdir -p /data/.restore-staging/sessions && echo junk > /data/.restore-staging/sessions/junk")
            done = vol.restore(a)
            report("R5 a .restore-staging left over from a crashed restore", done, vol)
        finally:
            cleanup(vol)


def case_restore_while_backup_dir_is_default_and_full() -> None:
    """H1 as round 1 ran it, but KEEP=1: the folder holds exactly the archive being restored."""
    with tempfile.TemporaryDirectory() as t:
        site, vol = fresh(Path(t))
        try:
            vol.set_marker("one")
            a = vol.backup(WORKBUDDY_BACKUP_KEEP="1")
            time.sleep(1)
            vol.set_marker("two")
            done = vol.restore(a, WORKBUDDY_BACKUP_KEEP="1")
            report("R6 KEEP=1, restore the only archive", done, vol,
                   f"--- asked-for archive still exists: {a.is_file()}; backups: {sorted(p.name for p in vol.backups.glob('*.tar.gz'))}")
            # the next nightly backup prunes back to 1: which one survives?
            vol.backup(WORKBUDDY_BACKUP_KEEP="1")
            print(f"--- after the next nightly backup (KEEP=1): {sorted(p.name for p in vol.backups.glob('*.tar.gz'))}")
        finally:
            cleanup(vol)


if __name__ == "__main__":
    for case in (case_relative_path_with_space_and_quote, case_traversal_member,
                 case_replace_fails_halfway_then_undo, case_disk_full_during_unpack,
                 case_leftover_staging, case_restore_while_backup_dir_is_default_and_full):
        try:
            case()
        except Exception as exc:  # noqa: BLE001
            print(f"\n### {case.__name__} PROBE ERROR: {type(exc).__name__}: {exc}")
        sys.stdout.flush()

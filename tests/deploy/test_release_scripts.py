"""The deploy scripts' behaviour: release, backup and restore (deploy/).

Two kinds of test. The release and settings tests run the real scripts against a stand-in
``docker`` (a small shell script on ``PATH`` that answers the few commands the scripts ask), in a
temporary copy of ``deploy/`` inside a temporary git repository, so they need nothing installed.
The backup and restore tests run against real Docker and a real volume, and are skipped where
Docker or the ``python:3.11-slim`` image isn't there (the CI job pulls it).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"
HELPER_IMAGE = "python:3.11-slim"
PASSWORD = "plain-Password_123"

# Answers the docker commands that release.sh, lib.sh and compose.sh ask. State is in $SHIM_DIR:
# images/<tag> (images that exist), volume (a data volume exists), appstate (one line per
# `docker inspect`; the last repeats), calls (the compose commands run).
DOCKER_SHIM = r"""#!/usr/bin/env bash
state="${SHIM_DIR:?}"
case "$1" in
  compose)
    shift
    while [ "$1" = "--project-directory" ] || [ "$1" = "--env-file" ]; do shift 2; done
    case "$1" in
      version) exit 0 ;;
      ps) echo cid-1 ;;
      *) echo "compose $*" >> "$state/calls" ;;
    esac ;;
  image) [ -e "$state/images/${3#workbuddy:}" ] ;;
  volume) [ -e "$state/volume" ] ;;
  build)
    cat > /dev/null
    echo "build" >> "$state/calls"
    for arg in "$@"; do case "$arg" in workbuddy:*) tag="${arg#workbuddy:}" ;; esac; done
    [ "${SHIM_BUILD_FAILS:-0}" = 1 ] && exit 1
    mkdir -p "$state/images"; : > "$state/images/$tag" ;;
  inspect)
    if [ "$(wc -l < "$state/appstate")" -gt 1 ]; then
      head -n 1 "$state/appstate"; sed -i 1d "$state/appstate"
    else cat "$state/appstate"; fi ;;
  images) ls "$state/images" 2>/dev/null || true ;;
  rmi) rm -f "$state/images/${2#workbuddy:}" ;;
  *) echo "unexpected docker $*" >> "$state/calls"; exit 1 ;;
esac
"""


def git(repo: Path, *args: str) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example"}
    done = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          env=env, check=True)
    return done.stdout.strip()


class Site:
    """A temporary repository holding a copy of ``deploy/``, with a stand-in docker."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.repo = root / "repo"
        self.deploy = self.repo / "deploy"
        self.state = root / "state"
        (self.state / "images").mkdir(parents=True)
        (self.state / "appstate").write_text("running healthy\n")
        shim_dir = root / "bin"
        shim_dir.mkdir()
        shim = shim_dir / "docker"
        shim.write_text(DOCKER_SHIM)
        shim.chmod(0o755)
        self.bin = shim_dir
        self.deploy.mkdir(parents=True)
        for item in DEPLOY.iterdir():
            if item.is_file() and item.name not in (".env", "current.env", "previous.env",
                                                    "releases.log", "docker-compose.override.yml"):
                shutil.copy2(item, self.deploy / item.name)
        (self.repo / "Dockerfile").write_text("FROM scratch\n")
        git(self.repo, "init", "-q")
        self.write_env(f"WORKBUDDY_DOMAIN=example.test\nWORKBUDDY_DEMO_PASSWORD={PASSWORD}\n"
                       "GROQ_API_KEY=gsk_placeholder\n")
        self.commit("first")

    def write_env(self, text: str) -> None:
        (self.deploy / ".env").write_bytes(text.encode())

    def commit(self, message: str) -> str:
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", message)
        return git(self.repo, "rev-parse", "HEAD")

    def stub(self, script: str, body: str) -> None:
        path = self.deploy / script
        path.write_text(f"#!/usr/bin/env bash\n{body}\n")
        path.chmod(0o755)
        self.commit(f"stub {script}")  # a release refuses a checkout that differs from the commit

    def run(
        self, command: list[str], *, real_docker: bool = False, **env: str
    ) -> subprocess.CompletedProcess[str]:
        path = os.environ["PATH"] if real_docker else f"{self.bin}:{os.environ['PATH']}"
        full_env = {**os.environ, "PATH": path, "SHIM_DIR": str(self.state), **env}
        return subprocess.run(command, cwd=self.repo, capture_output=True, text=True,
                              env=full_env, check=False, timeout=120)

    def release(self, commit: str = "HEAD", **env: str) -> subprocess.CompletedProcess[str]:
        return self.run([str(self.deploy / "release.sh"), commit], **env)

    def calls(self) -> list[str]:
        path = self.state / "calls"
        return path.read_text().splitlines() if path.exists() else []

    @property
    def current(self) -> Path:
        return self.deploy / "current.env"


@pytest.fixture
def site(tmp_path: Path) -> Site:
    return Site(tmp_path)


def out(done: subprocess.CompletedProcess[str]) -> str:
    return done.stdout + done.stderr


# -- release.sh -------------------------------------------------------------------------------


def test_a_release_that_goes_well_is_recorded(site: Site) -> None:
    site.stub("smoke.sh", "exit 0")

    done = site.release()

    assert done.returncode == 0, out(done)
    assert "RELEASED" in out(done)
    assert site.current.read_text().startswith("WORKBUDDY_TAG=")


def test_after_a_failed_first_release_a_rerun_does_the_whole_release(site: Site) -> None:
    """Review round 1, M2: it used to say "already the live release" with the app stopped."""
    site.stub("smoke.sh", "exit 1")
    failed = site.release()
    assert failed.returncode == 1 and "no earlier release" in out(failed)
    assert not site.current.exists(), "a failed first release left itself recorded as live"

    site.stub("smoke.sh", "exit 0")
    again = site.release()

    assert "already the live release" not in out(again)
    assert again.returncode == 0 and "RELEASED" in out(again), out(again)


def test_the_same_commit_is_skipped_only_while_it_is_running_and_healthy(site: Site) -> None:
    site.stub("smoke.sh", "exit 0")
    assert site.release().returncode == 0
    builds = site.calls().count("build")

    healthy = site.release()
    assert healthy.returncode == 0 and "already the live release" in out(healthy)
    assert site.calls().count("build") == builds, "a healthy live release was rebuilt"

    (site.state / "appstate").write_text("exited 1\nrunning healthy\n")  # first look: stopped
    stopped = site.release()
    assert "already the live release" not in out(stopped)
    assert stopped.returncode == 0 and "RELEASED" in out(stopped), out(stopped)


def test_a_failed_build_changes_nothing_and_takes_no_backup(site: Site) -> None:
    (site.state / "volume").write_text("")
    site.stub("backup.sh", 'echo "backup was run" >> "$(dirname "$0")/../../state/calls"')

    done = site.release(SHIM_BUILD_FAILS="1")

    assert done.returncode == 1 and "build failed" in out(done)
    assert "backup was run" not in site.calls()
    assert not site.current.exists()


def test_a_release_refuses_when_deploy_differs_from_the_commit(site: Site) -> None:
    """Review round 1, M5: the image comes from the commit, the compose file from the checkout."""
    site.stub("smoke.sh", "exit 0")
    first = git(site.repo, "rev-parse", "HEAD")
    (site.deploy / "Caddyfile").write_text("# changed\n")
    second = site.commit("change Caddyfile")
    git(site.repo, "checkout", "-q", first)

    refused = site.release(second)

    assert refused.returncode != 0
    assert "deploy/" in out(refused) and "git checkout" in out(refused)
    assert "build" not in site.calls() and not site.current.exists()

    git(site.repo, "checkout", "-q", second)
    assert site.release(second).returncode == 0


# -- the .env syntax guard --------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    ["Correct$horse-battery", 'has"quote', "has'quote", "trailing ", " leading", "a #comment",
     "with\ttab", "back`tick"],
)
def test_a_value_that_compose_would_read_differently_is_refused_by_name_only(
    site: Site, value: str
) -> None:
    """Review round 1, M3: ``$horse`` was dropped from the app's password and then printed."""
    site.write_env(f"WORKBUDDY_DOMAIN=example.test\nWORKBUDDY_DEMO_PASSWORD={value}\n"
                   "GROQ_API_KEY=gsk_placeholder\n")

    for command in ([str(site.deploy / "release.sh"), "HEAD"],
                    [str(site.deploy / "compose.sh"), "ps"]):
        done = site.run(command)

        assert done.returncode != 0
        assert "WORKBUDDY_DEMO_PASSWORD" in out(done)
        assert value.strip() not in out(done) and "horse" not in out(done)
        assert "unexpected docker" not in "\n".join(site.calls())


def test_a_carriage_return_in_env_is_refused(site: Site) -> None:
    site.write_env("WORKBUDDY_DOMAIN=example.test\r\nWORKBUDDY_DEMO_PASSWORD=x\r\n"
                   "GROQ_API_KEY=y\r\n")

    done = site.run([str(site.deploy / "release.sh"), "HEAD"])

    assert done.returncode != 0 and "carriage return" in out(done)


def test_an_ordinary_env_is_accepted(site: Site) -> None:
    site.write_env("# a comment with $ and 'quotes' is fine\n\nWORKBUDDY_DOMAIN=a.b.c\n"
                   "WORKBUDDY_DEMO_PASSWORD=Abc-123_.:/=+@%^\nGROQ_API_KEY=gsk_x\n")

    done = site.run(["bash", "-c", f". {site.deploy}/lib.sh; require_settings; echo fine"])

    assert done.returncode == 0 and "fine" in done.stdout, out(done)


# -- the Caddyfile ----------------------------------------------------------------------------


def test_caddy_writes_no_access_log() -> None:
    """Review round 1, M4: the request line holds the session address, which the app masks."""
    import re

    caddyfile = (DEPLOY / "Caddyfile").read_text(encoding="utf-8")
    code = "\n".join(line for line in caddyfile.splitlines() if not line.lstrip().startswith("#"))
    assert not re.search(r"^\s*log\b", code, re.M)


# -- backup and restore, on a real volume -----------------------------------------------------


def _docker_ready() -> bool:
    if shutil.which("docker") is None:
        return False
    return subprocess.run(["docker", "image", "inspect", HELPER_IMAGE], capture_output=True,
                          check=False).returncode == 0


needs_docker = pytest.mark.skipif(
    not _docker_ready(), reason=f"needs Docker and the {HELPER_IMAGE} image on this machine"
)


class Volume:
    def __init__(self, site: Site) -> None:
        self.site = site
        self.name = f"wbtest-{uuid.uuid4().hex[:10]}"
        site.stub("compose.sh", 'echo "$*" >> "$(dirname "$0")/compose.calls"')
        lib = site.deploy / "lib.sh"
        lib.write_text(lib.read_text() + "\nwait_healthy() { return 0; }\n")
        site.commit("test copy of lib.sh")
        self.backups = site.root / "backups"
        self.env = {
            "WORKBUDDY_DATA_VOLUME": self.name,
            "WORKBUDDY_BACKUP_DIR": str(self.backups),
            "WORKBUDDY_BACKUP_IMAGE": HELPER_IMAGE,
            "WORKBUDDY_BACKUP_KEEP": "2",
            "WORKBUDDY_YES": "1",
        }
        self.sh("mkdir -p /data/sessions && chown -R 10001:10001 /data")

    def sh(self, script: str, *, readonly: bool = False) -> str:
        mount = f"{self.name}:/data" + (":ro" if readonly else "")
        done = subprocess.run(
            ["docker", "run", "--rm", "--user", "0", "-v", mount, "--entrypoint", "sh",
             HELPER_IMAGE, "-c", script], capture_output=True, text=True, check=True)
        return done.stdout

    def marker(self) -> str:
        return self.sh("cat /data/sessions/marker 2>/dev/null || echo MISSING", readonly=True)

    def set_marker(self, value: str) -> None:
        self.sh(f"echo {value} > /data/sessions/marker && chown 10001:10001 /data/sessions/marker")

    def backup(self, **env: str) -> Path:
        done = self.site.run(
            [str(self.site.deploy / "backup.sh")], real_docker=True, **{**self.env, **env}
        )
        assert done.returncode == 0, out(done)
        newest = max(self.backups.glob("workbuddy-data-*.tar.gz"), key=lambda p: p.stat().st_mtime)
        return newest

    def restore(self, archive: Path, **env: str) -> subprocess.CompletedProcess[str]:
        return self.site.run(
            [str(self.site.deploy / "restore.sh"), str(archive)],
            real_docker=True, **{**self.env, **env},
        )


@pytest.fixture
def volume(site: Site) -> Iterator[Volume]:
    vol = Volume(site)
    yield vol
    subprocess.run(["docker", "volume", "rm", "-f", vol.name], capture_output=True, check=False)


def _age(path: Path, seconds_ago: int) -> None:
    stamp = time.time() - seconds_ago
    os.utime(path, (stamp, stamp))


@needs_docker
def test_backups_keep_only_the_newest_few_unless_told_not_to(volume: Volume) -> None:
    first = volume.backup()
    _age(first, 300)
    second = volume.backup()
    _age(second, 200)
    third = volume.backup()
    assert not first.exists(), "the oldest of three should have been pruned (keep 2)"
    assert second.exists() and third.exists()

    fourth = volume.backup(WORKBUDDY_BACKUP_PRUNE="0")
    assert second.exists() and third.exists() and fourth.exists()


@needs_docker
def test_restoring_the_oldest_backup_works_when_the_folder_is_full(volume: Volume) -> None:
    """Review round 1, H1: the safety backup pruned the archive being restored, then /data was
    emptied and the unpack failed."""
    volume.set_marker("one")
    oldest = volume.backup()
    _age(oldest, 300)
    volume.set_marker("two")
    newer = volume.backup()
    _age(newer, 200)
    volume.set_marker("three")
    assert len(list(volume.backups.glob("*.tar.gz"))) == 2  # the folder is at its limit

    done = volume.restore(oldest)

    assert done.returncode == 0, out(done)
    assert volume.marker().strip() == "one"
    assert oldest.is_file(), "the archive that was restored was deleted"
    assert volume.sh("ls -A /data", readonly=True).split() == ["sessions"]  # no staging left
    assert volume.sh("stat -c %u /data/sessions/marker", readonly=True).strip() == "10001"
    assert len(list(volume.backups.glob("*.tar.gz"))) == 3  # the safety backup was added
    safety = [p for p in volume.backups.glob("*.tar.gz") if p not in (oldest, newer)]
    assert len(safety) == 1
    kept = subprocess.run(["tar", "-xzOf", str(safety[0]), "./sessions/marker"],
                          capture_output=True, text=True, check=True).stdout
    assert kept.strip() == "three", "the data that was replaced is in the safety backup"


@needs_docker
def test_the_pre_restore_backup_holds_what_was_there(volume: Volume) -> None:
    volume.set_marker("one")
    archive = volume.backup()
    volume.set_marker("current")

    done = volume.restore(archive)

    assert done.returncode == 0, out(done)
    safety = [p for p in volume.backups.glob("*.tar.gz") if p != archive]
    assert len(safety) == 1
    listing = subprocess.run(["tar", "-xzOf", str(safety[0]), "./sessions/marker"],
                             capture_output=True, text=True, check=True).stdout
    assert listing.strip() == "current"
    assert str(safety[0]) in out(done), "the restore should say where the old data went"


@needs_docker
def test_a_file_that_is_not_an_archive_is_refused_and_nothing_changes(
    volume: Volume, tmp_path: Path
) -> None:
    volume.set_marker("keep")
    bogus = tmp_path / "not-an-archive.tar.gz"
    bogus.write_text("this is not a tar file")

    done = volume.restore(bogus)

    assert done.returncode != 0 and "nothing was changed" in out(done)
    assert volume.marker().strip() == "keep"
    assert not (volume.site.deploy / "compose.calls").exists(), "the app was stopped for nothing"


@needs_docker
def test_a_failed_unpack_leaves_data_alone_and_starts_the_app_again(
    volume: Volume, tmp_path: Path
) -> None:
    """A readable list but an unpack that fails: /data must be as it was."""
    import io
    import tarfile

    volume.set_marker("keep")
    archive = tmp_path / "workbuddy-data-bad.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        info = tarfile.TarInfo("sessions/" + "x" * 300 + "/file")  # a name too long to create
        info.size = 3
        tar.addfile(info, io.BytesIO(b"abc"))

    done = volume.restore(archive)

    assert done.returncode != 0, out(done)
    assert volume.marker().strip() == "keep"
    assert volume.sh("ls -A /data", readonly=True).split() == ["sessions"]
    calls = (volume.site.deploy / "compose.calls").read_text()
    assert "stop app" in calls and "up -d" in calls, calls

# ruff: noqa: E501 — reviewer tooling, not product code
"""Round 2: release.sh edge cases against the suite's stand-in docker (no real Docker needed).

    python scripts/review_harness/feat-release-safety/round2/release_probes.py [> out/release_probes.txt]

Reuses Site from tests/deploy/test_release_scripts.py (imported, not edited).
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
from tests.deploy.test_release_scripts import Site, git, out  # noqa: E402


IGNORED = ["deploy/.env", "deploy/current.env", "deploy/previous.env", "deploy/releases.log",
           "deploy/backups/", "deploy/docker-compose.override.yml"]


def new_site(t: str) -> Site:
    """Site, plus the real repository's .gitignore entries for deploy/ (the suite's Site has none,
    so a commit made after a release would otherwise commit current.env)."""
    site = Site(Path(t))
    (site.repo / ".gitignore").write_text("\n".join(IGNORED) + "\n")
    git(site.repo, "rm", "-q", "--cached", "--ignore-unmatch", "deploy/.env")
    site.commit("gitignore as in the real repository")
    return site


def show(name: str, site: Site, done) -> None:  # noqa: ANN001
    print(f"\n### {name}\nexit={done.returncode}")
    print("\n".join(out(done).strip().splitlines()[-8:]))
    images = sorted(p.name for p in (site.state / "images").iterdir())
    cur = site.current.read_text().strip() if site.current.exists() else "(none)"
    prev = (site.deploy / "previous.env")
    print(f"--- images: {images}; current.env: {cur}; previous.env: {prev.read_text().strip() if prev.exists() else '(none)'}")
    print(f"--- calls: {site.calls()[-6:]}")


def p1_unhealthy_live_release_rereleased_older_image_pruned() -> None:
    with tempfile.TemporaryDirectory() as t:
        site = new_site(t)
        site.stub("smoke.sh", "exit 0")
        older = git(site.repo, "rev-parse", "HEAD")
        site.release()  # W live
        (site.repo / "README").write_text("x")
        site.commit("next")
        site.release()  # X live, W kept as the rollback image
        print(f"\n(P1 setup) older W={older[:12]} images before: {sorted(p.name for p in (site.state / 'images').iterdir())}")
        (site.state / "appstate").write_text("running unhealthy\nrunning healthy\n")
        done = site.release()  # X again while it's unhealthy
        show("P1 live release is unhealthy, the owner reruns it: does the older rollback image survive?", site, done)


def p2_unhealthy_rerelease_fails() -> None:
    with tempfile.TemporaryDirectory() as t:
        site = new_site(t)
        site.stub("smoke.sh", '[ "${SMOKE_FAIL:-0}" = 1 ] && exit 1; exit 0')
        site.release()
        (site.repo / "README").write_text("x")
        site.commit("next")
        site.release()
        (site.state / "appstate").write_text("running starting\nrunning healthy\n")
        done = site.release(SMOKE_FAIL="1")  # e.g. the LLM provider is down for a minute
        show("P2 live release still 'starting' (e.g. just after a reboot), rerun of the same commit, smoke fails", site, done)


def p3_local_edit_in_deploy() -> None:
    with tempfile.TemporaryDirectory() as t:
        site = new_site(t)
        site.stub("smoke.sh", "exit 0")
        with (site.deploy / "Caddyfile").open("a") as f:
            f.write("# a local tweak\n")
        done = site.release()
        show("P3 an uncommitted local edit under deploy/, release HEAD", site, done)


def p4_untracked_override_is_ignored() -> None:
    with tempfile.TemporaryDirectory() as t:
        site = new_site(t)
        site.stub("smoke.sh", "exit 0")
        (site.deploy / "docker-compose.override.yml").write_text("services: {}\n")
        (site.repo / ".gitignore").write_text("deploy/docker-compose.override.yml\ndeploy/.env\ndeploy/current.env\ndeploy/previous.env\ndeploy/releases.log\n")
        site.commit("ignore")
        done = site.release()
        show("P4 a git-ignored override file under deploy/", site, done)


def p5_release_older_commit_without_checkout_when_deploy_unchanged() -> None:
    with tempfile.TemporaryDirectory() as t:
        site = new_site(t)
        site.stub("smoke.sh", "exit 0")
        older = git(site.repo, "rev-parse", "HEAD")
        (site.repo / "README").write_text("x")
        site.commit("next")
        site.release()
        done = site.release(older)
        show("P5 by-hand rollback to an older commit whose deploy/ is the same, no checkout", site, done)


if __name__ == "__main__":
    for p in (p1_unhealthy_live_release_rereleased_older_image_pruned, p2_unhealthy_rerelease_fails,
              p3_local_edit_in_deploy, p4_untracked_override_is_ignored,
              p5_release_older_commit_without_checkout_when_deploy_unchanged):
        try:
            p()
        except Exception as exc:  # noqa: BLE001
            print(f"\n### {p.__name__} PROBE ERROR: {type(exc).__name__}: {exc}")

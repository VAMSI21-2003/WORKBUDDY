"""The deployment files say what the checklist needs them to say (deploy/, docs/deploy.md).

Static checks, no Docker: they catch an edit that drops the proxy setting, publishes the app's port,
puts a value in ``.env.example``, or leaves a script that can't run. What the stack does is checked
by ``deploy/check-proxy.sh`` and ``deploy/smoke.sh`` on a running stack.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"
SCRIPTS = [
    "install.sh", "backup.sh", "restore.sh", "smoke.sh", "release.sh", "check-proxy.sh",
    "compose.sh",
]


@pytest.fixture(scope="module")
def compose() -> dict[str, dict[str, object]]:
    return yaml.safe_load((DEPLOY / "docker-compose.yml").read_text(encoding="utf-8"))


def test_the_app_is_not_published_and_caddy_is(compose: dict[str, dict[str, object]]) -> None:
    app, caddy = compose["services"]["app"], compose["services"]["caddy"]  # type: ignore[index]
    assert "ports" not in app, "the app must be reachable only through Caddy"
    assert app["expose"] == ["8000"]
    assert {"80:80", "443:443"} <= set(caddy["ports"])  # type: ignore[arg-type]


def test_the_app_is_told_there_is_one_proxy(compose: dict[str, dict[str, object]]) -> None:
    environment = compose["services"]["app"]["environment"]  # type: ignore[index]
    assert environment["WORKBUDDY_TRUSTED_PROXY_HOPS"] == "1"  # type: ignore[index]
    assert environment["WORKBUDDY_PUBLIC_ORIGIN"].startswith("https://")  # type: ignore[index]


def test_the_app_is_given_the_sign_in_settings_and_none_may_be_missing(
    compose: dict[str, dict[str, object]],
) -> None:
    environment = compose["services"]["app"]["environment"]  # type: ignore[index]
    for name in (
        "WORKBUDDY_GOOGLE_CLIENT_ID",
        "WORKBUDDY_GOOGLE_CLIENT_SECRET",
        "WORKBUDDY_OPERATOR_TOKEN",
    ):
        assert environment[name] == f"${{{name}:?set {name} in .env}}", name  # type: ignore[index]
    assert environment["WORKBUDDY_ALLOWED_EMAILS"] == "${WORKBUDDY_ALLOWED_EMAILS:-}"  # type: ignore[index]
    assert "WORKBUDDY_DEMO_PASSWORD" not in environment  # type: ignore[operator]


def test_caddy_replaces_x_forwarded_for_with_the_address_it_saw() -> None:
    """With one trusted hop the app reads the last entry; this is what makes it the real client."""
    caddyfile = (DEPLOY / "Caddyfile").read_text(encoding="utf-8")
    assert re.search(r"header_up\s+X-Forwarded-For\s+\{remote_host\}", caddyfile)
    assert "reverse_proxy app:8000" in caddyfile


def test_restart_memory_logs_and_data_volume(compose: dict[str, dict[str, object]]) -> None:
    for name, service in compose["services"].items():  # type: ignore[attr-defined]
        assert service["restart"] == "unless-stopped", name
        assert service["mem_limit"], name
        options = service["logging"]["options"]  # *log-rotation anchor
        assert options["max-size"] and options["max-file"], name
    app = compose["services"]["app"]  # type: ignore[index]
    assert app["volumes"] == ["workbuddy-data:/data"]
    assert compose["volumes"]["workbuddy-data"]["name"] == "workbuddy-data"  # type: ignore[index]


def test_the_image_installs_the_lock_with_pip_retries_and_a_timeout() -> None:
    """A download that hiccups must not fail a build; the lock's hashes still decide."""
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    pattern = r"^\s*&& pip install (?P<flags>[^\\\n]*)requirements-runtime\.lock"
    install = re.search(pattern, dockerfile, re.M)
    assert install is not None, "the lock install line moved"
    flags = install.group("flags")
    assert "--retries 10" in flags and "--timeout 60" in flags
    assert "--require-hashes" in flags and "--no-deps" in flags


def test_caddy_waits_for_a_healthy_app(compose: dict[str, dict[str, object]]) -> None:
    caddy = compose["services"]["caddy"]  # type: ignore[index]
    assert caddy["depends_on"]["app"]["condition"] == "service_healthy"  # type: ignore[index]


def test_env_example_holds_names_and_no_values() -> None:
    required = {
        "WORKBUDDY_DOMAIN",
        "WORKBUDDY_GOOGLE_CLIENT_ID",
        "WORKBUDDY_GOOGLE_CLIENT_SECRET",
        "WORKBUDDY_OPERATOR_TOKEN",
        "GROQ_API_KEY",
        "GEMINI_API_KEY",
    }
    seen: set[str] = set()
    for line in (DEPLOY / ".env.example").read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        name, _, value = text.partition("=")
        assert re.fullmatch(r"[A-Z][A-Z0-9_]*", name), line
        assert value == "", f"{name} has a value in .env.example"
        seen.add(name)
    assert seen == required


def test_secrets_and_release_state_are_not_committed() -> None:
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    for path in ("deploy/.env", "deploy/current.env", "deploy/backups/", "deploy/releases.log"):
        assert path in ignored, f"{path} isn't git-ignored"


@pytest.mark.parametrize("name", SCRIPTS)
def test_every_script_parses_and_is_executable_in_git(name: str) -> None:
    path = DEPLOY / name
    assert path.is_file()
    assert subprocess.run(["bash", "-n", str(path)], check=False).returncode == 0
    mode = subprocess.run(
        ["git", "ls-files", "--stage", str(path)],
        cwd=ROOT, capture_output=True, text=True, check=False,
    ).stdout.split()
    if mode:  # untracked (a fresh checkout of a test run) can't be asked
        assert mode[0] == "100755", f"{name} isn't executable in git (git update-index --chmod=+x)"


def test_release_script_rolls_back_and_says_so() -> None:
    text = (DEPLOY / "release.sh").read_text(encoding="utf-8")
    for needed in ("rollback", "ROLLED BACK", "backup.sh", "smoke.sh", "wait_healthy"):
        assert needed in text
    # The build comes first (a failed build changes nothing and takes no backup), then the backup,
    # both ahead of the switch.
    assert text.index("1/5 build") < text.index("2/5 backup") < text.index("3/5 start")


def test_the_smoke_fixture_is_a_workbook() -> None:
    assert (DEPLOY / "smoke.xlsx").read_bytes()[:2] == b"PK"

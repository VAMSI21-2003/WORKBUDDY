"""The lock files and the requirements files agree, and the image installs from the lock only.

``requirements-runtime.txt`` and ``requirements.txt`` say what the project needs, in ranges. The two
lock files (``requirements-runtime.lock`` for the image, ``requirements-dev.lock`` for CI and
development) say exactly which versions, with hashes. These tests catch the two ways they drift: a
range edited so that the locked version no longer satisfies it (or a new requirement with no pin),
and a lock edited so that it no longer matches its sibling. They don't resolve anything, so they
run offline. Refresh the locks with ``python scripts/refresh_locks.py`` (``docs/deploy.md``).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[2]
RUNTIME_TXT = ROOT / "requirements-runtime.txt"
DEV_TXT = ROOT / "requirements.txt"
RUNTIME_LOCK = ROOT / "requirements-runtime.lock"
DEV_LOCK = ROOT / "requirements-dev.lock"

_PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;\\]+)")
_HASH = re.compile(r"--hash=sha256:([0-9a-f]{64})")


def read_lock(path: Path) -> dict[str, tuple[str, frozenset[str]]]:
    """name -> (version, hashes). Fails on any line that isn't a pin, a hash or a comment."""
    entries: dict[str, tuple[str, set[str]]] = {}
    current: str | None = None
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        hashed = _HASH.match(line)
        if hashed:
            assert current is not None, f"{path.name}:{number}: a hash before any package"
            entries[current][1].add(hashed.group(1))
            continue
        pinned = _PIN.match(line)
        assert pinned, f"{path.name}:{number}: not an exact pin: {line[:60]!r}"
        current = canonicalize_name(pinned.group(1))
        assert current not in entries, f"{path.name}:{number}: {current} is pinned twice"
        entries[current] = (pinned.group(2), set())
    return {name: (version, frozenset(hashes)) for name, (version, hashes) in entries.items()}


def read_requirements(path: Path) -> list[Requirement]:
    """The requirements in a requirements file, following ``-r`` includes."""
    found: list[Requirement] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split(" #")[0].strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("-r "):
            found += read_requirements(path.parent / line[3:].strip())
            continue
        assert not line.startswith("-"), f"{path.name}: option not understood: {line!r}"
        found.append(Requirement(line))
    return found


@pytest.fixture(scope="module")
def runtime_lock() -> dict[str, tuple[str, frozenset[str]]]:
    return read_lock(RUNTIME_LOCK)


@pytest.fixture(scope="module")
def dev_lock() -> dict[str, tuple[str, frozenset[str]]]:
    return read_lock(DEV_LOCK)


@pytest.mark.parametrize(
    ("requirements", "lock_name"),
    [(RUNTIME_TXT, "runtime_lock"), (DEV_TXT, "dev_lock")],
    ids=["runtime", "dev"],
)
def test_every_requirement_is_locked_at_a_version_that_satisfies_it(
    requirements: Path, lock_name: str, request: pytest.FixtureRequest
) -> None:
    lock = request.getfixturevalue(lock_name)
    for requirement in read_requirements(requirements):
        name = canonicalize_name(requirement.name)
        assert name in lock, f"{requirement.name} is in {requirements.name} but not in the lock"
        version = lock[name][0]
        assert requirement.specifier.contains(version, prereleases=True), (
            f"{requirements.name} wants {requirement.name}{requirement.specifier}, "
            f"the lock has {version}"
        )


@pytest.mark.parametrize("lock_name", ["runtime_lock", "dev_lock"])
def test_every_pin_has_hashes(lock_name: str, request: pytest.FixtureRequest) -> None:
    lock = request.getfixturevalue(lock_name)
    assert len(lock) > 50
    for name, (version, hashes) in lock.items():
        assert hashes, f"{name}=={version} has no hash, so pip can't verify the download"


def test_the_dev_lock_holds_the_runtime_lock_exactly(
    runtime_lock: dict[str, tuple[str, frozenset[str]]],
    dev_lock: dict[str, tuple[str, frozenset[str]]],
) -> None:
    """What CI tests is what the image ships: same version, same files, for every shared package."""
    for name, (version, hashes) in runtime_lock.items():
        assert name in dev_lock, f"{name} is in the image's lock but not in the dev lock"
        assert dev_lock[name][0] == version, (
            f"{name}: the image has {version}, the dev lock {dev_lock[name][0]}"
        )
        assert dev_lock[name][1] == hashes, f"{name}=={version}: the two locks' hashes differ"


def test_the_dev_lock_adds_the_dev_tools(
    runtime_lock: dict[str, tuple[str, frozenset[str]]],
    dev_lock: dict[str, tuple[str, frozenset[str]]],
) -> None:
    """The image stays free of test and lint tools; the dev lock has them."""
    for tool in ("pytest", "ruff", "black", "mypy", "playwright"):
        assert tool in dev_lock
        assert tool not in runtime_lock, f"{tool} is in the image's lock"


def test_the_runtime_requirements_file_names_no_dev_tool() -> None:
    names = {canonicalize_name(r.name) for r in read_requirements(RUNTIME_TXT)}
    assert not names & {"pytest", "ruff", "black", "mypy", "playwright", "pytest-cov"}


def test_the_dockerfile_installs_from_the_lock_only() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    code = "\n".join(
        line for line in dockerfile.splitlines() if not line.lstrip().startswith("#")
    )
    assert "COPY requirements-runtime.lock" in code
    installs = [part for part in code.split("pip install")[1:]]
    assert len(installs) == 1, "the image should have exactly one pip install"
    install = installs[0].split("\n\n")[0]
    for flag in ("--require-hashes", "--no-deps", "-r requirements-runtime.lock"):
        assert flag in install, f"the image's pip install lacks {flag}"
    assert "requirements-runtime.txt" not in code
    assert "requirements.txt" not in code


def test_the_dockerfiles_pip_install_retries_a_failed_download() -> None:
    """One build on the VM failed once on a download hiccup (exit code 2) and passed on retry."""
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    (install,) = [line for line in dockerfile.splitlines() if "pip install" in line]
    assert "--retries 10" in install and "--timeout 60" in install


def test_the_image_job_requires_the_pdf_sandbox_to_hold_inside_the_image() -> None:
    workflow = (ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
    image_job = workflow.split("\n  image:\n", 1)[1]
    assert "docker run --rm workbuddy:ci python -m skills.pdf" in image_job
    assert 'grep --fixed-strings --line-regexp "The PDF sandbox holds."' in image_job


def test_the_build_context_lets_the_lock_in() -> None:
    lines = [
        line.strip()
        for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    ]
    assert "!requirements-runtime.lock" in lines


def test_the_refresh_command_the_locks_name_exists() -> None:
    script = ROOT / "scripts" / "refresh_locks.py"
    assert script.is_file()
    for lock in (RUNTIME_LOCK, DEV_LOCK):
        head = lock.read_text(encoding="utf-8").split("\n\n")[0]
        assert "python scripts/refresh_locks.py" in head

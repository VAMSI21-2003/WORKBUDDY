"""``python -m skills.pdf``: the check the image's CI job and the release run, and what it requires.

It prints "The PDF sandbox holds." and exits 0 only when the worker ran, reported checks, and every
check is true, the filesystem confinement (Landlock) included: without it the server refuses PDFs,
so a check that passed without it would say "holds" about a server that reads none.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from skills.pdf import __main__ as release_check
from skills.pdf import sandbox

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="the sandbox is Linux-only (it fails closed elsewhere)",
)

HOLDS = "The PDF sandbox holds."
DOES_NOT_HOLD = "The PDF sandbox does NOT hold"


def _all_true() -> dict[str, bool]:
    outcome = sandbox.run("selftest", b"")
    assert outcome.ok, outcome.reason
    return dict(outcome.data)


def _check_with(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    outcome: sandbox.WorkerOutcome,
) -> tuple[int, str]:
    monkeypatch.setattr(release_check.sandbox, "run", lambda mode, pdf: outcome)
    code = release_check.main()
    return code, capsys.readouterr().out


def test_the_real_check_holds_on_this_machine() -> None:
    """Fails, and says why, on a kernel without Landlock or a container that blocks the filter."""
    ran = subprocess.run(  # noqa: S603 - our own module
        [sys.executable, "-m", "skills.pdf"], capture_output=True, text=True, timeout=120
    )
    assert HOLDS in ran.stdout.splitlines(), ran.stdout[-1500:]
    assert ran.returncode == 0


def test_every_check_true_holds(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out = _check_with(monkeypatch, capsys, sandbox.WorkerOutcome(ok=True, data=_all_true()))
    assert code == 0 and HOLDS in out.splitlines()


def test_a_missing_landlock_does_not_hold(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Every other check true, the filesystem not confined: the server would refuse PDFs."""
    data = _all_true() | {"filesystem_confined": False}
    code, out = _check_with(monkeypatch, capsys, sandbox.WorkerOutcome(ok=True, data=data))
    assert code == 1
    assert DOES_NOT_HOLD in out and HOLDS not in out.splitlines()
    assert "not confined" in out  # and it says which check it was


def test_any_single_failed_check_does_not_hold(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for name in _all_true():
        data = _all_true() | {name: False}
        code, out = _check_with(monkeypatch, capsys, sandbox.WorkerOutcome(ok=True, data=data))
        assert code == 1 and HOLDS not in out.splitlines(), name


def test_a_worker_that_did_not_run_or_reported_nothing_does_not_hold(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for outcome in (
        sandbox.WorkerOutcome(ok=False, reason="sandbox_unavailable"),
        sandbox.WorkerOutcome(ok=True, data={}),
    ):
        code, out = _check_with(monkeypatch, capsys, outcome)
        assert code == 1 and HOLDS not in out.splitlines()

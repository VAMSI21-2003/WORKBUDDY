"""The dependency audit: a vulnerability in a lock file fails CI, and an ignore needs its reason.

``scripts/run_pip_audit.py`` runs pip-audit over both lock files. These tests don't call the
advisory databases (CI does); they check what the script does with its ignore list and that the
workflow runs it.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from datetime import date
from pathlib import Path
from types import ModuleType

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "run_pip_audit", ROOT / "scripts" / "run_pip_audit.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


audit = _load()
TODAY = date(2026, 10, 18)
GOOD = """\
[[ignore]]
id = "PYSEC-1"
aliases = ["CVE-1"]
reason = "not reachable here"
review_by = 2026-12-18
"""


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "ignores.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_a_complete_ignore_is_read(tmp_path: Path) -> None:
    (ignore,) = audit.read_ignores(_write(tmp_path, GOOD), TODAY)
    assert ignore.id == "PYSEC-1" and ignore.aliases == ("CVE-1",)
    assert ignore.reason == "not reachable here" and ignore.review_by == date(2026, 12, 18)


def test_no_ignores_is_fine(tmp_path: Path) -> None:
    assert audit.read_ignores(_write(tmp_path, ""), TODAY) == []


@pytest.mark.parametrize(
    ("text", "complaint"),
    [
        (GOOD.replace('reason = "not reachable here"\n', ""), "needs a reason"),
        (GOOD.replace('reason = "not reachable here"', 'reason = "  "'), "needs a reason"),
        (GOOD.replace("review_by = 2026-12-18\n", ""), "review_by"),
        (GOOD.replace("2026-12-18", '"soon"'), "review_by"),
        (GOOD.replace('id = "PYSEC-1"\n', ""), "needs an id"),
        (GOOD.replace("2026-12-18", "2026-10-17"), "has passed"),
    ],
    ids=["no reason", "blank reason", "no date", "not a date", "no id", "overdue"],
)
def test_an_incomplete_or_overdue_ignore_fails_the_run(
    tmp_path: Path, text: str, complaint: str
) -> None:
    with pytest.raises(SystemExit) as raised:
        audit.read_ignores(_write(tmp_path, text), TODAY)
    assert complaint in str(raised.value)


def test_an_ignore_is_still_good_on_its_review_day(tmp_path: Path) -> None:
    assert audit.read_ignores(_write(tmp_path, GOOD), date(2026, 12, 18))


def test_every_ignore_is_passed_to_pip_audit_under_its_id_and_aliases(tmp_path: Path) -> None:
    ignores = audit.read_ignores(_write(tmp_path, GOOD), TODAY)
    args = audit.command("requirements-runtime.lock", ignores)
    assert args[args.index("--requirement") + 1] == "requirements-runtime.lock"
    assert "--require-hashes" in args and "--disable-pip" in args
    passed = [args[i + 1] for i, arg in enumerate(args) if arg == "--ignore-vuln"]
    assert passed == ["PYSEC-1", "CVE-1"]


def test_the_real_ignore_list_is_complete() -> None:
    """Dated far back so the check is about the shape, not about today's date."""
    assert audit.read_ignores(audit.IGNORES, date(2000, 1, 1))


def test_both_lock_files_are_audited_and_a_failure_in_either_fails(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: list[str] = []

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        lock = args[args.index("--requirement") + 1]
        seen.append(lock)
        return subprocess.CompletedProcess(args, 1 if lock == "requirements-dev.lock" else 0)

    monkeypatch.setattr(audit.subprocess, "run", fake_run)
    monkeypatch.setattr(audit, "read_ignores", lambda: [])

    assert audit.main() == 1
    assert seen == ["requirements-runtime.lock", "requirements-dev.lock"]  # both ran
    monkeypatch.setattr(
        audit.subprocess, "run", lambda args, **kw: subprocess.CompletedProcess(args, 0)
    )
    assert audit.main() == 0
    capsys.readouterr()


def test_ci_runs_the_audit_in_its_own_job() -> None:
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "tests.yml").read_text())
    job = workflow["jobs"]["audit"]
    steps = " ".join(str(step.get("run", "")) for step in job["steps"])
    assert "python scripts/run_pip_audit.py" in steps
    assert "pip-audit==" in steps, "the audit tool's own version is pinned"

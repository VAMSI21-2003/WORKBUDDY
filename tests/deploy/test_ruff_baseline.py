"""The ruff baseline check: new errors fail; known ones, and fewer, don't.

``scripts/check_ruff_baseline.py`` is what CI runs after the suite.
"""

from __future__ import annotations

import importlib.util
import sys
from collections import Counter
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "check_ruff_baseline", ROOT / "scripts" / "check_ruff_baseline.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


check = _load()


BASE = Counter({("buddy/a.py", "E501"): 2, ("tests/b.py", "F401"): 1})


def test_the_same_errors_are_not_new() -> None:
    assert check.compare(BASE, Counter(BASE)) == ([], [])


def test_one_more_of_a_known_rule_in_a_known_file_is_new() -> None:
    current = Counter(BASE)
    current[("buddy/a.py", "E501")] += 1

    new, better = check.compare(BASE, current)

    assert len(new) == 1 and "buddy/a.py" in new[0] and "E501" in new[0] and better == []


def test_a_known_rule_in_a_new_file_is_new() -> None:
    current = Counter(BASE)
    current[("buddy/new.py", "E501")] = 1

    new, _ = check.compare(BASE, current)

    assert len(new) == 1 and "buddy/new.py" in new[0]


def test_a_new_rule_in_a_known_file_is_new() -> None:
    current = Counter(BASE)
    current[("buddy/a.py", "B008")] = 1

    new, _ = check.compare(BASE, current)

    assert len(new) == 1 and "B008" in new[0]


def test_fewer_errors_than_the_baseline_pass_and_are_reported_as_improved() -> None:
    current = Counter({("buddy/a.py", "E501"): 1})

    new, better = check.compare(BASE, current)

    assert new == []
    assert len(better) == 2


def test_moving_an_error_to_another_line_is_not_new() -> None:
    """The baseline counts per file and rule, not by line, so an edit above it changes nothing."""
    findings = [
        {"filename": str(ROOT / "buddy" / "a.py"), "code": "E501", "location": {"row": 10}},
        {"filename": str(ROOT / "buddy" / "a.py"), "code": "E501", "location": {"row": 90}},
    ]

    assert check.count(findings) == Counter({("buddy/a.py", "E501"): 2})


def test_the_baseline_file_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "ruff-baseline.txt"

    check.write_baseline(BASE, path)

    assert check.read_baseline(path) == BASE
    assert path.read_text(encoding="utf-8").startswith("#")


def test_a_malformed_baseline_line_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "ruff-baseline.txt"
    path.write_text("buddy/a.py E501\n", encoding="utf-8")

    with pytest.raises(SystemExit):
        check.read_baseline(path)


def test_the_committed_baseline_parses_and_names_files_that_exist() -> None:
    baseline = check.read_baseline()
    assert baseline, "the baseline is empty: the known errors are missing"
    for (file, rule), n in baseline.items():
        assert (ROOT / file).is_file(), f"{file} is in the baseline but isn't in the tree"
        assert rule[0].isalpha() and n > 0

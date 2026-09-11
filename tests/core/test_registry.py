"""Unit tests for skills.registry."""

from __future__ import annotations

import pytest

from skills.base import PermissionTier, Skill, SkillResult, SkillStatus, SkillTask
from skills.registry import SkillAlreadyRegisteredError, SkillNotFoundError, SkillRegistry


class _FakeSkill(Skill):
    """A minimal, configurable fake Skill for exercising the Registry."""

    def __init__(self, name: str, *, capabilities: tuple[str, ...] = ("cap.default",)) -> None:
        self._name = name
        self._capabilities = capabilities

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"fake skill {self._name}"

    @property
    def supported_inputs(self) -> tuple[str, ...]:
        return self._capabilities

    @property
    def permission_tier(self) -> PermissionTier:
        return PermissionTier.READ

    def can_handle(self, task: SkillTask) -> bool:
        return True

    def execute(self, task: SkillTask) -> SkillResult:
        return SkillResult(status=SkillStatus.SUCCESS)

    def verify(self, task: SkillTask, result: SkillResult) -> bool:
        return True


# -- register -------------------------------------------------------------------


def test_register_makes_a_skill_discoverable() -> None:
    registry = SkillRegistry()
    skill = _FakeSkill("skill.a")

    registry.register(skill)

    assert registry.is_registered("skill.a") is True
    assert registry.discover("skill.a") is skill
    assert len(registry) == 1


def test_register_rejects_duplicate_name() -> None:
    """Confirms the Registry's documented, intended behavior: a duplicate
    registration under the same name is a hard error, not a silent
    overwrite or a no-op.
    """
    registry = SkillRegistry()
    registry.register(_FakeSkill("skill.a"))

    with pytest.raises(SkillAlreadyRegisteredError):
        registry.register(_FakeSkill("skill.a"))

    # The original registration is untouched by the rejected attempt.
    assert len(registry) == 1


def test_register_allows_two_distinct_skills_with_the_same_capability() -> None:
    """Registering two different skills that both declare the same
    capability is not itself an error — resolving *which one* to use for
    a given task is the Dispatcher's job (tests/core/test_dispatcher.py),
    not the Registry's.
    """
    registry = SkillRegistry()
    registry.register(_FakeSkill("skill.a", capabilities=("spreadsheet.deduplicate",)))
    registry.register(_FakeSkill("skill.b", capabilities=("spreadsheet.deduplicate",)))

    assert len(registry) == 2
    assert len(registry.find_by_capability("spreadsheet.deduplicate")) == 2


# -- discover ---------------------------------------------------------------------


def test_discover_raises_for_unknown_skill() -> None:
    registry = SkillRegistry()

    with pytest.raises(SkillNotFoundError):
        registry.discover("does-not-exist")


# -- remove -------------------------------------------------------------------------


def test_remove_unregisters_a_skill() -> None:
    registry = SkillRegistry()
    registry.register(_FakeSkill("skill.a"))

    registry.remove("skill.a")

    assert registry.is_registered("skill.a") is False
    assert len(registry) == 0


def test_remove_raises_for_unknown_skill() -> None:
    registry = SkillRegistry()

    with pytest.raises(SkillNotFoundError):
        registry.remove("does-not-exist")


# -- find_by_capability ---------------------------------------------------------------


def test_find_by_capability_returns_empty_tuple_when_nothing_matches() -> None:
    registry = SkillRegistry()
    registry.register(_FakeSkill("skill.a", capabilities=("other.capability",)))

    assert registry.find_by_capability("spreadsheet.deduplicate") == ()


def test_find_by_capability_returns_matches_in_registration_order() -> None:
    registry = SkillRegistry()
    skill_a = _FakeSkill("skill.a", capabilities=("spreadsheet.deduplicate",))
    skill_b = _FakeSkill("skill.b", capabilities=("spreadsheet.deduplicate",))
    registry.register(skill_a)
    registry.register(skill_b)

    matches = registry.find_by_capability("spreadsheet.deduplicate")

    assert matches == (skill_a, skill_b)


# -- available_skills -----------------------------------------------------------------


def test_available_skills_returns_everything_registered() -> None:
    registry = SkillRegistry()
    skill_a = _FakeSkill("skill.a")
    skill_b = _FakeSkill("skill.b")
    registry.register(skill_a)
    registry.register(skill_b)

    assert registry.available_skills() == (skill_a, skill_b)


def test_available_skills_is_empty_for_a_fresh_registry() -> None:
    assert SkillRegistry().available_skills() == ()

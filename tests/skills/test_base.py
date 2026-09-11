"""Unit tests for skills.base."""

from __future__ import annotations

import pytest

from skills.base import PermissionTier, Skill, SkillResult, SkillStatus, SkillTask


# -- PermissionTier / SkillStatus ------------------------------------------------


def test_permission_tier_values() -> None:
    assert PermissionTier.READ.value == "read"
    assert PermissionTier.WRITE.value == "write"
    assert PermissionTier.EXTERNAL_EFFECT.value == "external_effect"


def test_skill_status_values() -> None:
    assert SkillStatus.SUCCESS.value == "success"
    assert SkillStatus.PARTIAL.value == "partial"
    assert SkillStatus.FAILURE.value == "failure"


# -- SkillTask --------------------------------------------------------------------


def test_skill_task_requires_only_the_mandatory_fields() -> None:
    task = SkillTask(
        task_id="task-1",
        session_id="session-1",
        workspace_id="workspace-1",
        description="do the thing",
    )

    assert task.input_references == []
    assert task.parameters == {}
    assert task.permission is PermissionTier.READ


def test_skill_task_default_lists_are_independent_per_instance() -> None:
    """A bare mutable default (instead of ``field(default_factory=list)``)
    would make every instance share the same list. Confirm that isn't
    happening.
    """
    task_a = SkillTask(task_id="a", session_id="s", workspace_id="w", description="d")
    task_b = SkillTask(task_id="b", session_id="s", workspace_id="w", description="d")

    task_a.input_references.append("uploads/report.xlsx")

    assert task_a.input_references == ["uploads/report.xlsx"]
    assert task_b.input_references == []


def test_skill_task_is_mutable() -> None:
    """``SkillTask`` is a plain (non-frozen) dataclass — Execute and tests
    rely on being able to construct one and then mutate it freely (e.g.
    appending to ``input_references``).
    """
    task = SkillTask(task_id="a", session_id="s", workspace_id="w", description="d")

    task.permission = PermissionTier.WRITE

    assert task.permission is PermissionTier.WRITE


# -- SkillResult ------------------------------------------------------------------


def test_skill_result_requires_only_status() -> None:
    result = SkillResult(status=SkillStatus.SUCCESS)

    assert result.outputs == []
    assert result.summary == ""
    assert result.warnings == []
    assert result.errors == []
    assert result.metadata == {}


def test_skill_result_default_lists_and_dicts_are_independent_per_instance() -> None:
    result_a = SkillResult(status=SkillStatus.SUCCESS)
    result_b = SkillResult(status=SkillStatus.SUCCESS)

    result_a.outputs.append("out.xlsx")
    result_a.metadata["removed_count"] = 2

    assert result_a.outputs == ["out.xlsx"]
    assert result_b.outputs == []
    assert result_a.metadata == {"removed_count": 2}
    assert result_b.metadata == {}


# -- Skill ABC ----------------------------------------------------------------------


def test_skill_cannot_be_instantiated_directly() -> None:
    with pytest.raises(TypeError):
        Skill()  # type: ignore[abstract]


class _MinimalSkill(Skill):
    """The smallest possible concrete Skill, for exercising the contract."""

    @property
    def name(self) -> str:
        return "minimal"

    @property
    def description(self) -> str:
        return "does nothing"

    @property
    def supported_inputs(self) -> tuple[str, ...]:
        return ("noop",)

    @property
    def permission_tier(self) -> PermissionTier:
        return PermissionTier.READ

    def can_handle(self, task: SkillTask) -> bool:
        return True

    def execute(self, task: SkillTask) -> SkillResult:
        return SkillResult(status=SkillStatus.SUCCESS)

    def verify(self, task: SkillTask, result: SkillResult) -> bool:
        return True


def test_a_concrete_skill_implementing_every_abstract_member_can_be_instantiated() -> None:
    skill = _MinimalSkill()

    assert skill.name == "minimal"
    assert skill.supported_inputs == ("noop",)
    assert skill.permission_tier is PermissionTier.READ


def test_skill_subclass_missing_an_abstract_method_cannot_be_instantiated() -> None:
    class _IncompleteSkill(Skill):
        @property
        def name(self) -> str:
            return "incomplete"

        @property
        def description(self) -> str:
            return "missing most of the contract"

        @property
        def supported_inputs(self) -> tuple[str, ...]:
            return ()

        @property
        def permission_tier(self) -> PermissionTier:
            return PermissionTier.READ

        def can_handle(self, task: SkillTask) -> bool:
            return False

        # execute() and verify() are deliberately not implemented.

    with pytest.raises(TypeError):
        _IncompleteSkill()  # type: ignore[abstract]

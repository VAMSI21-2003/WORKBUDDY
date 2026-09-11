"""Dispatcher — routes delegated work to the correct Specialist (Skill)."""

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from skills.base import Skill, SkillTask
from skills.registry import SkillNotFoundError, SkillRegistry

logger = logging.getLogger(__name__)


class DispatchError(Exception):
    """Base class for errors raised while routing work to a skill."""


class NoCapableSkillError(DispatchError):
    """Raised when no registered skill can handle the requested work."""


class SkillUnavailableError(DispatchError):
    """Raised when an explicitly requested skill cannot be dispatched to."""


@dataclass(frozen=True)
class ExecutionRequest:
    """A prepared pairing of a task with the skill selected to run it.

    An ``ExecutionRequest`` is the Dispatcher's only output. It is handed
    to Buddy's Execute stage, which is responsible for actually invoking
    ``skill.execute(task)``. The Dispatcher itself never calls it.

    Attributes:
        task: The task to be executed.
        skill: The skill selected to execute it.
    """

    task: SkillTask
    skill: Skill


SelectionStrategy = Callable[[Sequence[Skill], SkillTask], Skill]


def select_first_available(candidates: Sequence[Skill], task: SkillTask) -> Skill:
    """Default selection strategy: choose the first capable skill found.

    This is the only selection policy implemented in Sprint 1. It exists as
    a pluggable strategy specifically so that future selection policies —
    priority ordering among multiple Specialists, load balancing, or
    routing to cloud workers — can be introduced by supplying a different
    strategy to the ``Dispatcher`` without changing its public interface.

    Args:
        candidates: Skills confirmed capable of handling ``task``, in
            registration order.
        task: The task being dispatched.

    Returns:
        The first skill in ``candidates``.
    """
    return candidates[0]


class Dispatcher:
    """Routes delegated work to the correct Specialist.

    The Dispatcher never performs work itself. Given a task that Buddy's
    Delegate stage has already produced, it determines which registered
    skill (or skills) declare the capability to handle it, validates that
    at least one is actually available and capable, selects the correct
    one, and prepares an ``ExecutionRequest`` for Buddy's Execute stage to
    carry out. It does not execute skills, retry failed selections, verify
    results, or reason about user intent — see ``docs/BUDDY_SKILLS.md`` for
    where those responsibilities live instead.
    """

    def __init__(
        self,
        registry: SkillRegistry,
        selection_strategy: SelectionStrategy = select_first_available,
    ) -> None:
        """Initialize the dispatcher.

        Args:
            registry: The skill registry to route work through.
            selection_strategy: The policy used to pick one skill among
                multiple capable candidates. Defaults to selecting the
                first capable skill found; future policies (priority,
                load balancing, cloud workers) can be supplied here.
        """
        self._registry = registry
        self._selection_strategy = selection_strategy

    def locate_by_capability(self, input_type: str) -> tuple[Skill, ...]:
        """Locate registered skills that declare support for a capability.

        Args:
            input_type: The capability to look for, expressed as an input
                type (e.g. a file extension) that a skill declares in its
                ``supported_inputs``.

        Returns:
            A tuple of matching skills, in registration order. Empty if no
            registered skill declares that capability.
        """
        candidates = self._registry.find_by_capability(input_type)
        logger.debug(
            "Located %d skill(s) declaring capability %r", len(candidates), input_type
        )
        return candidates

    def select(self, task: SkillTask, input_type: str) -> Skill:
        """Select the correct skill to handle a task.

        Locates every skill that declares support for ``input_type``,
        validates that at least one of them is actually capable of the
        specific ``task``, and applies the configured selection strategy
        to pick one.

        Args:
            task: The task to be handled.
            input_type: The capability the task requires.

        Returns:
            The selected skill.

        Raises:
            NoCapableSkillError: If no registered skill declares the
                requested capability, or none of the skills that declare
                it can actually handle this specific task.
        """
        candidates = self.locate_by_capability(input_type)
        if not candidates:
            logger.error("No registered skill declares capability %r", input_type)
            raise NoCapableSkillError(
                f"No registered skill declares support for {input_type!r}."
            )

        capable = tuple(skill for skill in candidates if skill.can_handle(task))
        if not capable:
            logger.error(
                "No skill declaring capability %r can handle task %r",
                input_type,
                task.task_id,
            )
            raise NoCapableSkillError(
                f"No skill supporting {input_type!r} can handle task {task.task_id!r}."
            )

        selected = self._selection_strategy(capable, task)
        logger.info(
            "Selected skill %r for task %r (%d candidate(s))",
            selected.name,
            task.task_id,
            len(capable),
        )
        return selected

    def dispatch(self, task: SkillTask, input_type: str) -> ExecutionRequest:
        """Select a skill for a task and prepare it for execution.

        Args:
            task: The task to be handled.
            input_type: The capability the task requires.

        Returns:
            An ``ExecutionRequest`` pairing the task with the selected
            skill, ready for Buddy's Execute stage.

        Raises:
            NoCapableSkillError: If no suitable skill can be selected.
        """
        skill = self.select(task, input_type)
        logger.info("Prepared execution request: task=%r skill=%r", task.task_id, skill.name)
        return ExecutionRequest(task=task, skill=skill)

    def dispatch_to(self, skill_name: str, task: SkillTask) -> ExecutionRequest:
        """Prepare an execution request for an explicitly named skill.

        Used when Delegate has already decided which skill should handle a
        task and only needs the Dispatcher to validate availability and
        prepare the request.

        Args:
            skill_name: The name of the skill to dispatch to.
            task: The task to be handled.

        Returns:
            An ``ExecutionRequest`` pairing the task with the named skill.

        Raises:
            SkillUnavailableError: If no skill is registered under
                ``skill_name``, or the skill is registered but cannot
                handle this specific task.
        """
        try:
            skill = self._registry.discover(skill_name)
        except SkillNotFoundError as exc:
            logger.error("Requested skill %r is not registered", skill_name)
            raise SkillUnavailableError(
                f"Skill {skill_name!r} is not registered."
            ) from exc

        if not skill.can_handle(task):
            logger.error("Skill %r cannot handle task %r", skill_name, task.task_id)
            raise SkillUnavailableError(
                f"Skill {skill_name!r} cannot handle task {task.task_id!r}."
            )

        logger.info(
            "Prepared execution request: task=%r skill=%r (explicit selection)",
            task.task_id,
            skill_name,
        )
        return ExecutionRequest(task=task, skill=skill)

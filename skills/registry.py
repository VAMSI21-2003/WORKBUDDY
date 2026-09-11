"""Registry — discovers and looks up available skills."""

import logging

from .base import Skill

logger = logging.getLogger(__name__)


class SkillAlreadyRegisteredError(Exception):
    """Raised when attempting to register a skill under a name already in use."""


class SkillNotFoundError(Exception):
    """Raised when a lookup fails to find a matching registered skill."""


class SkillRegistry:
    """Central registry of skills available to Buddy.

    The registry is where skills declare themselves and the place Buddy's
    Router consults to find which skill can satisfy a given task (see
    ``docs/BUDDY_SKILLS.md``, Section 10). Registration is explicit:
    whatever assembles WorkBuddy at startup registers each skill instance
    directly. Filesystem discovery and plugin loading are deliberately out
    of scope for this registry.
    """

    def __init__(self) -> None:
        """Initialize an empty registry."""
        self._skills: dict[str, Skill] = {}

    def register(self, skill: Skill) -> None:
        """Register a skill, making it available to Buddy.

        Args:
            skill: The skill instance to register.

        Raises:
            SkillAlreadyRegisteredError: If a skill with the same name is
                already registered.
        """
        if self.is_registered(skill.name):
            logger.error("Duplicate skill registration rejected: %s", skill.name)
            raise SkillAlreadyRegisteredError(
                f"A skill named {skill.name!r} is already registered."
            )
        self._skills[skill.name] = skill
        logger.info("Registered skill: %s", skill.name)

    def remove(self, name: str) -> None:
        """Remove a registered skill.

        Args:
            name: The name of the skill to remove.

        Raises:
            SkillNotFoundError: If no skill with that name is registered.
        """
        if not self.is_registered(name):
            logger.error("Attempted to remove unknown skill: %s", name)
            raise SkillNotFoundError(f"No skill named {name!r} is registered.")
        del self._skills[name]
        logger.info("Removed skill: %s", name)

    def discover(self, name: str) -> Skill:
        """Look up a registered skill by its exact name.

        Args:
            name: The name of the skill to retrieve.

        Returns:
            The registered skill.

        Raises:
            SkillNotFoundError: If no skill with that name is registered.
        """
        try:
            return self._skills[name]
        except KeyError as exc:
            logger.error("Attempted to discover unknown skill: %s", name)
            raise SkillNotFoundError(f"No skill named {name!r} is registered.") from exc

    def find_by_capability(self, input_type: str) -> tuple[Skill, ...]:
        """Find registered skills that declare support for an input type.

        Args:
            input_type: The input type to match against each skill's
                declared ``supported_inputs``.

        Returns:
            A tuple of matching skills, in registration order. Empty if no
            registered skill declares support for the input type.
        """
        matches = tuple(
            skill for skill in self._skills.values() if input_type in skill.supported_inputs
        )
        logger.debug(
            "Found %d skill(s) supporting input type %r", len(matches), input_type
        )
        return matches

    def available_skills(self) -> tuple[Skill, ...]:
        """Return every currently registered skill.

        Returns:
            A tuple of all registered skills, in registration order.
        """
        return tuple(self._skills.values())

    def is_registered(self, name: str) -> bool:
        """Check whether a skill with the given name is registered.

        Args:
            name: The skill name to check.

        Returns:
            True if a skill with that name is registered, False otherwise.
        """
        return name in self._skills

    def __len__(self) -> int:
        """Return the number of currently registered skills."""
        return len(self._skills)

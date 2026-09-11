"""Buddy's engine factory — wires the Eyes -> Brain -> Think -> Planner ->
Delegate -> Hands -> Inspector -> Voice cycle into a runnable Engine.
"""

import logging

from buddy.brain import Brain, ClaudeInterpreter
from buddy.delegate import Delegate, MechanicalDelegator
from buddy.eyes import Eyes
from buddy.hands import Hands, NullConfirmationProvider
from buddy.inspector import Inspector, SpreadsheetDedupeVerifier
from buddy.llm import ClaudeProvider, LLMProvider
from buddy.planner import ClaudePlanner, CompositePlanner, Planner, RuleBasedPlanner
from buddy.think import ClaudeThinker, Think
from buddy.voice import Speaker, TemplateSpeaker, Voice
from core.dispatcher import Dispatcher
from core.engine import CognitiveCycle, Engine
from skills.excel import ExcelDeduplicateSkill
from skills.registry import SkillRegistry

logger = logging.getLogger(__name__)


def _capability_snapshot(registry: SkillRegistry) -> tuple[tuple[str, str], ...]:
    """Build a static ``(capability, description)`` snapshot from the registry.

    Read once, here, at engine-construction time — never held as a live
    reference anywhere downstream. Per
    ``docs/WORKBUDDY_ARCHITECTURE.md`` Section 10, nothing but the
    Dispatcher queries the Registry directly; this snapshot is what
    ``ClaudePlanner`` gets instead of a live handle. See
    ``docs/design/multi-step-planner-design.md`` Section 3.

    Args:
        registry: The registry to snapshot.

    Returns:
        One ``(capability, description)`` pair per capability declared by
        every currently-registered Skill.
    """
    return tuple(
        (capability, skill.description)
        for skill in registry.available_skills()
        for capability in skill.supported_inputs
    )


def build_engine(
    *, llm_provider: LLMProvider | None = None, speaker: Speaker | None = None
) -> Engine:
    """Build the real, end-to-end Engine for the "remove duplicate rows" slice.

    Wires the real Claude-backed Brain/Think stages, a Planner that tries
    the rule-based single-step strategy first and falls back to an
    LLM-backed multi-step strategy (see
    ``docs/design/multi-step-planner-design.md``), the Delegate stage, a
    Dispatcher backed by a registry containing ``ExcelDeduplicateSkill``,
    Hands wired to ``NullConfirmationProvider`` (auto-approves every
    Write/External-Effect task — there is no user-facing surface to ask
    through yet; see ``buddy.hands.NullConfirmationProvider``), and the
    independent ``SpreadsheetDedupeVerifier`` — the concrete
    implementations this vertical slice needs (see
    ``docs/design/slice-1-dedupe-design.md``). All wiring happens here,
    once; every stage above continues to depend only on the protocol it
    was already built against, and none of them import this module.

    Args:
        llm_provider: The ``LLMProvider`` to back Brain/Think with.
            Defaults to a real ``ClaudeProvider`` (which reads
            ``ANTHROPIC_API_KEY`` from the environment). Tests should pass
            a fake provider here instead of relying on the default, so
            they never make a real network call or need a real key.
        speaker: The ``Speaker`` to back Voice with. Defaults to
            ``TemplateSpeaker`` — the deterministic, non-LLM speaker this
            slice was originally built with — so every existing caller's
            behavior is unchanged. Pass a ``PersonalityVoice`` (see
            ``buddy.voice``) to exercise the friend/focus personality
            modes instead.

    Returns:
        An ``Engine`` ready to run the full, real cognitive cycle.
    """
    provider = llm_provider if llm_provider is not None else ClaudeProvider()

    registry = SkillRegistry()
    registry.register(ExcelDeduplicateSkill())
    dispatcher = Dispatcher(registry)
    known_capabilities = _capability_snapshot(registry)

    cycle = CognitiveCycle(
        eyes=Eyes(),
        brain=Brain(interpreter=ClaudeInterpreter(provider)),
        think=Think(thinker=ClaudeThinker(provider)),
        planner=Planner(
            planner=CompositePlanner(
                RuleBasedPlanner(),
                ClaudePlanner(provider, known_capabilities=known_capabilities),
            )
        ),
        delegate=Delegate(delegator=MechanicalDelegator()),
        hands=Hands(dispatcher=dispatcher, confirmation_provider=NullConfirmationProvider()),
        inspector=Inspector(verifier=SpreadsheetDedupeVerifier()),
        voice=Voice(speaker=speaker if speaker is not None else TemplateSpeaker()),
    )
    logger.debug("build_engine: assembled the CognitiveCycle for the dedupe slice")
    return Engine(cycle)

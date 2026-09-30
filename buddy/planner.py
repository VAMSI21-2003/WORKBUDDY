"""Planner — turns a reasoned understanding into a concrete sequence of steps."""

import json
import logging
import re
from dataclasses import dataclass, replace
from typing import Protocol

from buddy.actions import INTENT_CAPABILITIES
from buddy.llm import LLMMessage, LLMProvider
from buddy.models import PlanningStep, Understanding
from buddy.state import BuddyState
from buddy.think import Reasoning

logger = logging.getLogger(__name__)

_SUPPORTED_ANALYSIS_EXTENSIONS = (".xlsx", ".xls")


@dataclass(frozen=True)
class PlanningRequest:
    """The inputs available for turning a reasoned understanding into steps.

    A plain, self-contained bundle of everything a ``PlanningStrategy``
    needs to work from — assembled mechanically from ``BuddyState`` by the
    Planner stage, so a strategy never has to depend on ``BuddyState``
    directly.

    Attributes:
        understanding: The structured understanding produced by Brain.
        reasoning: The structured reasoning produced by Think.
        uploaded_files: Files currently available in the workspace.
        active_file: Reference to the file the user was last focused on,
            if any, from the working context.
        user_message: The current turn's raw message from the user. A
            classified ``understanding.intent`` alone cannot carry
            compound detail (e.g. "this column") — strategies that need
            to decompose a request read this field directly, the same
            way ``UnderstandingRequest`` already does for Brain.
        unanalyzed_spreadsheet_files: This turn's resolved target file
            (``active_file`` or the most recent upload — the same
            resolution ``RuleBasedPlanner``/``ClaudePlanner``/
            ``MechanicalDelegator`` already use), as a 0-or-1-element
            tuple, if that file is a spreadsheet and hasn't already been
            proactively analyzed this session. Deliberately scoped to at
            most the one file every step this turn will actually target —
            not every unanalyzed upload in the workspace — because
            ``MechanicalDelegator`` resolves one shared target for the
            whole plan (see ``docs/design/multi-step-planner-design.md``),
            so a step queued against a *different* file would silently
            run against this same resolved target instead. Read by
            ``ProactiveAnalysisPlanner``; every other strategy ignores it.
            See ``docs/design/proactive-analysis-design.md`` Section 1.2.
    """

    understanding: Understanding
    reasoning: Reasoning
    uploaded_files: tuple[str, ...]
    active_file: str | None
    user_message: str
    unanalyzed_spreadsheet_files: tuple[str, ...] = ()


class PlanningStrategy(Protocol):
    """Produces an ordered sequence of ``PlanningStep``s from a ``PlanningRequest``.

    This is the seam where real planning is plugged in — a rule-based
    strategy, or one backed by an LLM. The Planner stage itself never
    decides what to do; it only calls whichever ``PlanningStrategy`` it
    was given.
    """

    def __call__(self, request: PlanningRequest) -> tuple[PlanningStep, ...]:
        """Plan ``request`` and return the resulting steps."""
        ...


class NullPlanner:
    """A ``PlanningStrategy`` that produces no steps.

    Never invents a plan. Serves as a safe default so ``Planner`` can be
    constructed and exercised — in tests, or wired into the Engine —
    before a real strategy is plugged in.
    """

    def __call__(self, request: PlanningRequest) -> tuple[PlanningStep, ...]:
        """Return no steps for any request.

        Args:
            request: The request that would otherwise be planned.

        Returns:
            An empty tuple — no strategy has been configured.
        """
        return ()


class RuleBasedPlanner:
    """A ``PlanningStrategy`` that mechanically maps known intents to one step.

    Turns a small, closed set of known intents into a single
    ``PlanningStep`` requesting the matching capability. This is
    deliberately not an LLM-backed strategy: translating one already-known
    intent into one step is mechanical, not a judgment call, so no
    additional reasoning is needed here. As more intents are added, a
    richer ``PlanningStrategy`` can be substituted behind this same
    protocol without changing the Planner stage.

    Produces no steps when: the intent isn't in the known mapping, Think
    flagged ``clarification_required``, or no file is available to act on
    — in all three cases, proceeding would mean guessing.
    """

    def __call__(self, request: PlanningRequest) -> tuple[PlanningStep, ...]:
        """Plan ``request`` and return the resulting steps.

        Args:
            request: The request to plan for.

        Returns:
            A single ``PlanningStep`` if the intent is known, the request
            isn't flagged as needing clarification, and a target file is
            available; otherwise an empty tuple.
        """
        intent = request.understanding.intent
        capability = INTENT_CAPABILITIES.get(intent) if intent else None
        target_file = request.active_file or (
            request.uploaded_files[-1] if request.uploaded_files else None
        )

        if capability is None or request.reasoning.clarification_required or target_file is None:
            return ()

        return (
            PlanningStep(
                id="step-1",
                description=f"Apply {capability} to {target_file}",
                capability=capability,
                priority=0,
            ),
        )


class CompositePlanner:
    """A ``PlanningStrategy`` that tries one strategy, falling back to another.

    Tries ``primary`` first; if it produces no steps, tries ``fallback``.
    Composition happens entirely at this layer — the ``Planner`` stage
    itself never changes to support this, the same extensibility model
    ``docs/WORKBUDDY_ARCHITECTURE.md`` Section 18 documents for the
    Dispatcher's selection strategy. Used to wire a cheap, deterministic
    strategy (e.g. ``RuleBasedPlanner``) ahead of a more expensive,
    LLM-backed one (e.g. ``ClaudePlanner``), so the LLM is only ever
    consulted when the cheap strategy genuinely has nothing to offer. See
    ``docs/design/multi-step-planner-design.md`` Section 7 for the design.
    """

    def __init__(self, primary: PlanningStrategy, fallback: PlanningStrategy) -> None:
        """Initialize the composed strategy.

        Args:
            primary: The strategy tried first.
            fallback: The strategy tried only if ``primary`` returns no
                steps.
        """
        self._primary = primary
        self._fallback = fallback

    def __call__(self, request: PlanningRequest) -> tuple[PlanningStep, ...]:
        """Plan ``request`` using ``primary``, falling back to ``fallback``.

        Args:
            request: The request to plan for.

        Returns:
            ``primary``'s steps if it produced any; otherwise
            ``fallback``'s steps.
        """
        steps = self._primary(request)
        if steps:
            return steps
        return self._fallback(request)


class ProactiveAnalysisPlanner:
    """A ``PlanningStrategy`` that unconditionally prepends analysis.

    Wraps another ``PlanningStrategy`` rather than replacing it: for every
    not-yet-analyzed spreadsheet upload this turn resolves as its target
    (``request.unanalyzed_spreadsheet_files`` — see ``PlanningRequest``),
    prepends a ``spreadsheet.analyze`` step ahead of whatever ``inner``
    decides for the user's own stated request. This runs regardless of
    what the user asked, or whether they asked for anything at all — the
    entire point, per
    ``docs/design/proactive-analysis-design.md`` Section 1.2, is that
    proactive analysis is independent of intent classification.

    Because ``Hands`` executes ``state.delegation`` strictly in list
    order (``MechanicalDelegator`` drops ``PlanningStep.priority``/
    ``.dependencies`` when it flattens steps into ``CapabilityRequest``s),
    prepending here is sufficient on its own to guarantee the analysis
    step runs first — no dependency-tracking machinery is needed.
    """

    def __init__(self, inner: PlanningStrategy) -> None:
        """Initialize the wrapping strategy.

        Args:
            inner: The strategy to defer to for the user's own stated
                request, after any analysis step has been prepended.
        """
        self._inner = inner

    def __call__(self, request: PlanningRequest) -> tuple[PlanningStep, ...]:
        """Prepend analysis steps to whatever ``inner`` produces.

        Args:
            request: The request to plan for.

        Returns:
            One ``spreadsheet.analyze`` step per entry in
            ``request.unanalyzed_spreadsheet_files`` (today, at most one —
            see that field's docstring), followed by ``inner``'s own
            steps. When an analysis step was prepended, any
            ``spreadsheet.analyze`` step ``inner`` also produced is dropped
            (see ``_drop_redundant_analysis``).
        """
        analysis_steps = tuple(
            PlanningStep(
                id=f"analyze-{index}",
                description=f"Look over {file_reference} before doing anything else",
                capability=_ANALYZE_CAPABILITY,
                priority=0,
            )
            for index, file_reference in enumerate(request.unanalyzed_spreadsheet_files)
        )
        inner_steps = self._inner(request)
        if analysis_steps:
            inner_steps = _drop_redundant_analysis(inner_steps, analysis_steps[0].id)
        return analysis_steps + _analysis_first(inner_steps)


_ANALYZE_CAPABILITY = "spreadsheet.analyze"


def _drop_redundant_analysis(
    steps: tuple[PlanningStep, ...], prepended_analysis_id: str
) -> tuple[PlanningStep, ...]:
    """Remove ``spreadsheet.analyze`` steps an inner strategy planned itself.

    Only called when ``ProactiveAnalysisPlanner`` already prepended an
    analysis step this turn: an LLM-backed inner strategy
    (``ClaudePlanner``) sometimes echoes Think's mention of analysis back
    as a step of its own, which would run the same read-only analysis
    twice. When nothing was prepended — the file was already analyzed
    earlier this session — an inner analysis step is the user explicitly
    asking for a re-analysis, so it's kept (the caller doesn't call this).

    A dependency on a dropped step is remapped to
    ``prepended_analysis_id`` rather than deleted: the step still depends
    on analysis having run, just the copy that actually remains in the
    plan.

    Args:
        steps: The inner strategy's steps, in order.
        prepended_analysis_id: The id of the analysis step
            ``ProactiveAnalysisPlanner`` prepended this turn.

    Returns:
        ``steps`` without any analysis steps, otherwise in the same order
        with the same ids, and with dependencies on a dropped step
        pointing at ``prepended_analysis_id`` instead (never listed twice).
    """
    dropped_ids = {step.id for step in steps if step.capability == _ANALYZE_CAPABILITY}
    if not dropped_ids:
        return steps

    def remap(dependencies: tuple[str, ...]) -> tuple[str, ...]:
        remapped = (prepended_analysis_id if d in dropped_ids else d for d in dependencies)
        return tuple(dict.fromkeys(remapped))  # de-duplicate, keeping order

    return tuple(
        replace(step, dependencies=remap(step.dependencies))
        for step in steps
        if step.id not in dropped_ids
    )


def _analysis_first(steps: tuple[PlanningStep, ...]) -> tuple[PlanningStep, ...]:
    """Move any ``spreadsheet.analyze`` step ahead of every other step.

    Analysis always reads the original upload and sits outside the chain
    of file-changing steps (see ``buddy.hands.Hands``), so it never runs
    mid-chain — a requested re-analysis the inner strategy placed later
    is moved to the front. A moved analysis step's own dependencies are
    cleared, since it no longer runs after anything. Other steps keep
    their order.

    Args:
        steps: The steps to reorder.

    Returns:
        The analysis steps, then every other step, each group in its
        original order.
    """
    analysis = tuple(
        replace(step, dependencies=()) for step in steps if step.capability == _ANALYZE_CAPABILITY
    )
    if not analysis:
        return steps
    return analysis + tuple(step for step in steps if step.capability != _ANALYZE_CAPABILITY)


# The only wording that may join two consecutive steps for their order to
# be read from where they're mentioned. Compared after lowercasing, turning
# punctuation into spaces and collapsing whitespace, so ", then" and
# "; then" both count as "then" (and "once that's done" as "once that s
# done"). Anything else between two steps — "and", "last, and", ", but do",
# "after you" — isn't understood, and the model's order stands.
_FORWARD_CONNECTORS = frozenset(
    {
        "then",
        "and then",
        "after that",
        "and after that",
        "afterwards",
        "once that s done",
        "followed by",
        "next",
    }
)
# Connectors made only of punctuation, which normalizing would erase.
# Compared with surrounding whitespace removed.
_FORWARD_SYMBOLS = frozenset({"->"})
# Order-related wording. Outside the connectors above, any of it — before
# the first step, after the last, or inside a quoted phrase — means the
# message may state the order some other way ("… last", "… second",
# "… ahead of that"), so the model's order stands. The one exception is a
# lone "first" before the first step ("first X, then Y").
_ORDER_WORDS = re.compile(
    r"\b(first|second|third|fourth|last|lastly|finally|then|next|after|afterwards|"
    r"before|beforehand|prior|following|followed|preceding|once|until|ahead|earlier|"
    r"later|start|begin|end|meanwhile|previously|subsequently)\b"
)
_PUNCTUATION = re.compile(r"[^\w\s]")


def _normalize_connector(text: str) -> str:
    """``text`` lowercased, punctuation removed, whitespace collapsed."""
    return " ".join(_PUNCTUATION.sub(" ", text.lower()).split())


def _quotes_nothing(raw_step: object) -> bool:
    """Whether a model step's ``source_phrase`` is present but empty.

    A missing phrase isn't treated this way: it says nothing about what
    the user asked for, only that the model left the field out.
    """
    if not isinstance(raw_step, dict):
        return False
    phrase = raw_step.get("source_phrase")
    return isinstance(phrase, str) and not phrase.strip()


def _order_stated_by_user(raw_steps: list[dict], user_message: str) -> list[int] | None:
    """Return the order the user stated the steps in, if it can be checked.

    Each step's ``source_phrase`` (the model's verbatim quote of the words
    asking for it) is located in ``user_message``. The steps are put in
    the order their phrases appear **only** when that order is stated in
    wording this check recognises (an allowlist, not a list of exceptions):

    - every phrase appears exactly once (case-insensitive), with no two
      phrases overlapping;
    - consecutive phrases are joined by nothing but a forward connector
      (``_FORWARD_CONNECTORS``: "then", "and then", ", then", "after
      that", "and after that", "afterwards", "once that's done",
      "followed by", "next"; or ``_FORWARD_SYMBOLS``: "->");
    - no other order-related word (``_ORDER_WORDS``) appears anywhere
      else — before the first phrase (except a lone "first", for "first X,
      then Y"), after the last, or inside a phrase.

    Otherwise it returns ``None``, meaning "keep the model's order", and
    logs why.

    Args:
        raw_steps: The model's steps, in the model's order.
        user_message: The user's own message.

    Returns:
        Indices into ``raw_steps`` in the user's stated order, or ``None``.
    """
    message = user_message.lower()
    if not message or len(raw_steps) < 2:
        return None

    spans: list[tuple[int, int]] = []
    for raw_step in raw_steps:
        phrase = raw_step.get("source_phrase")
        if not isinstance(phrase, str) or not phrase.strip():
            logger.warning("ClaudePlanner: a step had no source_phrase; keeping model's order")
            return None
        needle = phrase.strip().lower()
        occurrences = message.count(needle)
        if occurrences != 1:
            logger.warning(
                "ClaudePlanner: source_phrase %r appears %d times in the message, not exactly "
                "once; keeping model's order",
                phrase,
                occurrences,
            )
            return None
        if _ORDER_WORDS.search(needle):
            logger.info(
                "ClaudePlanner: source_phrase %r contains order wording; keeping model's order",
                phrase,
            )
            return None
        start = message.find(needle)
        spans.append((start, start + len(needle)))

    order = sorted(range(len(spans)), key=lambda i: spans[i][0])
    if any(spans[b][0] < spans[a][1] for a, b in zip(order, order[1:], strict=False)):
        logger.warning("ClaudePlanner: source phrases overlap; keeping model's order")
        return None

    gaps = [message[spans[a][1] : spans[b][0]] for a, b in zip(order, order[1:], strict=False)]
    unrecognised = [
        _normalize_connector(gap)
        for gap in gaps
        if _normalize_connector(gap) not in _FORWARD_CONNECTORS
        and gap.strip() not in _FORWARD_SYMBOLS
    ]
    if unrecognised:
        logger.info(
            "ClaudePlanner: steps joined by unrecognised wording %r; keeping model's order",
            unrecognised,
        )
        return None
    lead = _normalize_connector(message[: spans[order[0]][0]])
    tail = message[spans[order[-1]][1] :]
    if (_ORDER_WORDS.search(lead) and lead != "first") or _ORDER_WORDS.search(tail):
        logger.info(
            "ClaudePlanner: order wording outside the connectors (%r / %r); "
            "keeping model's order",
            lead,
            _normalize_connector(tail),
        )
        return None
    return order


class Planner:
    """The Planner stage of Buddy's cognitive cycle.

    Planner converts Think's reasoning into a concrete, ordered sequence
    of steps — what Delegate will turn into capability requests for the
    Dispatcher to route (see ``docs/WORKBUDDY_ARCHITECTURE.md`` Section
    4). It is not responsible for resolving a step to a specific Skill,
    for executing anything, or for verifying results — those belong to
    Delegate, Hands, and Inspector respectively.

    Planner contains no planning logic of its own. All of the actual
    decision-making — which capability a step requires, how many steps,
    their ordering — is delegated to an injected ``PlanningStrategy``.
    Planner's own responsibility is purely mechanical: assemble the
    strategy's input from ``BuddyState``, call the strategy, and record
    what it returns. This keeps Planner a stable, swappable framework — a
    rule-based strategy, an LLM-backed strategy, or anything else
    conforming to ``PlanningStrategy`` can be substituted without ever
    changing this stage.
    """

    def __init__(self, planner: PlanningStrategy | None = None) -> None:
        """Initialize the stage.

        Args:
            planner: The strategy to delegate planning to. Defaults to
                ``NullPlanner``, which produces no steps rather than
                guessing.
        """
        if planner is None:
            logger.debug("No planning strategy provided; falling back to NullPlanner")
        self._planner = planner if planner is not None else NullPlanner()

    def __call__(self, state: BuddyState) -> BuddyState:
        """Plan for ``state`` and record the resulting steps.

        Args:
            state: The current ``BuddyState`` for this turn.

        Returns:
            The same state, with the full sequence of ``PlanningStep``s
            recorded in ``state.planning`` and mirrored as plain-language
            descriptions in the legacy ``state.plan``.
        """
        request = self._build_request(state)
        steps = self._planner(request)
        self._apply(state, steps)
        logger.debug("Planner recorded %d step(s) for session %r", len(steps), state.session_id)
        return state

    @staticmethod
    def _build_request(state: BuddyState) -> PlanningRequest:
        """Assemble the strategy's input from the current state.

        This step is purely mechanical: it gathers what is already
        present on ``state`` and ``state.context`` into a plain,
        self-contained bundle. It performs no planning of its own. If
        Brain or Think have not run yet, empty defaults are used
        rather than guessing at what they might have contained.

        Args:
            state: The current ``BuddyState`` for this turn.

        Returns:
            The request to hand to the configured strategy.
        """
        return PlanningRequest(
            understanding=state.understanding or Understanding(),
            reasoning=state.reasoning or Reasoning(),
            uploaded_files=tuple(state.uploaded_files),
            active_file=state.context.active_file,
            user_message=state.user_message,
            unanalyzed_spreadsheet_files=Planner._resolve_unanalyzed_spreadsheet_files(state),
        )

    @staticmethod
    def _resolve_unanalyzed_spreadsheet_files(state: BuddyState) -> tuple[str, ...]:
        """Resolve this turn's target file, if it needs proactive analysis.

        Mirrors the exact ``active_file or uploaded_files[-1]`` resolution
        ``RuleBasedPlanner``/``ClaudePlanner``/``MechanicalDelegator``
        already use — see ``PlanningRequest.unanalyzed_spreadsheet_files``
        for why this must be the *same* file every other step this turn
        will target, not just any unanalyzed upload.

        Args:
            state: The current ``BuddyState`` for this turn.

        Returns:
            A 0-or-1-element tuple containing the resolved target file, if
            it's a spreadsheet and hasn't already been analyzed this
            session; otherwise an empty tuple.
        """
        target_file = state.context.active_file or (
            state.uploaded_files[-1] if state.uploaded_files else None
        )
        if target_file is None:
            return ()
        if not target_file.lower().endswith(_SUPPORTED_ANALYSIS_EXTENSIONS):
            return ()
        if target_file in state.context.analyzed_files:
            return ()
        return (target_file,)

    @staticmethod
    def _apply(state: BuddyState, steps: tuple[PlanningStep, ...]) -> None:
        """Record the strategy's output onto ``state``.

        ``state.planning`` is the source of truth. Plain-language
        descriptions are additionally mirrored into the legacy
        ``state.plan`` list, consistent with how Brain/Think keep
        their legacy fields populated alongside the typed ones.

        Args:
            state: The state to update.
            steps: The planned steps to record.
        """
        state.planning = steps
        state.plan = [step.description for step in steps]


_SYSTEM_PROMPT = """You are the planning stage for WorkBuddy, an AI coworker.

The user's request has already been classified as describing more than
one distinct action ("multi_step_request"), and Think has already judged
it clear enough to proceed. Your job is to decompose it into an ordered
sequence of concrete steps, each requesting one capability.

You are never shown the contents of any uploaded file — only its name,
the user's own words, and what Brain/Think already concluded — so never
assume anything about what data a file contains.

You will be given a list of capabilities that currently have a real
skill able to perform them. Prefer those exact capability strings when a
step matches one. If a requested action does not match any capability in
that list, still include the step — name the most sensible capability
string for what's being asked, following the same dot-separated
domain.action pattern already in use (e.g. "spreadsheet.report",
"spreadsheet.dashboard"). Never omit a requested action just because no
matching capability is listed; the system already handles telling the
user a capability isn't supported yet.

Respond with a single JSON object and nothing else, matching this shape:
{
  "steps": [
    {
      "description": "<plain-language description of this step>",
      "capability": "<dot-separated capability string>",
      "depends_on_step_numbers": [<1-based numbers of steps in THIS list that must complete first, or empty>],
      "source_phrase": "<the exact words from the user's message that ask for this step, copied verbatim>"
    }
  ]
}

List the steps in the order the user asked for them. Steps run as a chain —
each works on the previous step's result — so the order matters.
"source_phrase" must be copied character-for-character from the user's
message, not paraphrased.
"""


class ClaudePlanner:
    """A ``PlanningStrategy`` backed by a Claude model, for compound requests.

    Decomposes a request classified as ``"multi_step_request"`` into an
    ordered sequence of ``PlanningStep``s, using the raw
    ``request.user_message`` — a classified intent alone cannot carry
    compound detail. Named ``ClaudePlanner`` to match the
    ``Claude<domain-noun>`` convention already used by
    ``ClaudeInterpreter``/``ClaudeThinker``, even though — like those two
    — it works with any ``LLMProvider``, not only Claude specifically.

    Only engages for ``"multi_step_request"``: for any other intent
    (including the single-step ``"remove_duplicate_rows"`` and
    ``"remove_empty_rows"``, and ``"unclear"``), it returns ``()``
    immediately with no LLM call, the same early-return discipline
    ``RuleBasedPlanner`` already follows. Also respects
    ``reasoning.clarification_required`` and requires a resolvable target
    file — the same two gates ``RuleBasedPlanner`` uses, since per
    ``docs/design/multi-step-planner-design.md`` Step 1, every step this
    strategy produces will end up targeting that one file regardless of
    what it intended (``MechanicalDelegator`` resolves one shared target
    for the whole plan, not per step).

    Never invents a capability that isn't real without saying so, and
    never silently drops a requested action just because no skill exists
    for it yet — see ``docs/design/multi-step-planner-design.md`` Section
    3. Never raises: on an LLM failure or a malformed/unexpected reply,
    logs the failure (never the prompt or reply content — the same
    logging discipline every other LLM-backed piece in this codebase
    follows) and returns ``()``, the same "produce nothing rather than
    guess" contract ``NullPlanner``/``RuleBasedPlanner``'s own refusal
    paths already use.
    """

    def __init__(
        self, provider: LLMProvider, *, known_capabilities: tuple[tuple[str, str], ...]
    ) -> None:
        """Initialize the strategy.

        Args:
            provider: The ``LLMProvider`` used to decompose the request.
            known_capabilities: ``(capability, description)`` pairs for
                every capability a currently-registered Skill declares.
                A static snapshot taken once at wiring time — never a
                live ``SkillRegistry`` reference (see
                ``docs/design/multi-step-planner-design.md`` Section 3;
                ``docs/WORKBUDDY_ARCHITECTURE.md`` Section 10: nothing
                but the Dispatcher queries the Registry directly).
        """
        self._provider = provider
        self._known_capabilities = known_capabilities

    def __call__(self, request: PlanningRequest) -> tuple[PlanningStep, ...]:
        """Decompose ``request`` into an ordered sequence of steps.

        Args:
            request: The request to plan for.

        Returns:
            The decomposed steps, in intended execution order
            (``priority`` set to list position), or ``()`` if this
            strategy doesn't apply, Think flagged clarification is
            required, no target file is available, or the model call
            fails or returns something unparseable.
        """
        if request.understanding.intent != "multi_step_request":
            return ()
        if request.reasoning.clarification_required:
            return ()
        target_file = request.active_file or (
            request.uploaded_files[-1] if request.uploaded_files else None
        )
        if target_file is None:
            return ()

        user_prompt = self._build_user_prompt(request, target_file)
        try:
            raw_reply = self._provider.complete(
                system=_SYSTEM_PROMPT,
                messages=(LLMMessage(role="user", content=user_prompt),),
            )
        except Exception:
            logger.exception("ClaudePlanner: LLM call failed")
            return ()
        return self._parse_reply(raw_reply, request.user_message)

    def _build_user_prompt(self, request: PlanningRequest, target_file: str) -> str:
        """Build the user-turn prompt from ``request``.

        Only the raw message, classified intent, Think's own conclusion,
        the target file, and the known-capabilities snapshot are
        included — never file content.

        Args:
            request: The request to build a prompt from.
            target_file: The already-resolved target file.

        Returns:
            The plain-text user prompt to send to the model.
        """
        lines = [
            f'User message: "{request.user_message}"',
            f"Classified intent: {request.understanding.intent!r}",
        ]
        if request.reasoning.objective:
            lines.append(f"Objective: {request.reasoning.objective}")
        if request.reasoning.recommended_strategy:
            lines.append(f"Recommended strategy: {request.reasoning.recommended_strategy}")
        lines.append(f"Target file: {target_file}")

        if self._known_capabilities:
            lines.append("Capabilities with a real skill today:")
            for capability, description in self._known_capabilities:
                lines.append(f"  {capability}: {description}")
        else:
            lines.append("No capabilities currently have a real skill.")

        return "\n".join(lines)

    @staticmethod
    def _parse_reply(raw_reply: str, user_message: str) -> tuple[PlanningStep, ...]:
        """Parse the model's raw JSON reply into an ordered step sequence.

        Steps are put in the order the user stated them whenever that can
        be checked deterministically (see ``_order_stated_by_user``);
        otherwise the model's own order stands.

        Args:
            raw_reply: The model's raw text reply.
            user_message: The user's own message, to check the order
                against.

        Returns:
            The parsed steps, or ``()`` if ``raw_reply`` is not valid
            JSON or doesn't match the expected shape.
        """
        try:
            data = json.loads(raw_reply)
        except json.JSONDecodeError:
            logger.warning("ClaudePlanner: reply was not valid JSON: %r", raw_reply)
            return ()

        try:
            raw_steps = data.get("steps") or []
            if not isinstance(raw_steps, list):
                raise TypeError("'steps' was not a list")

            # A step quoting an empty source_phrase quotes nothing the user
            # said: the user never asked for it, so it doesn't run. (A
            # non-empty phrase that can't be found is kept: it may be a
            # paraphrase of a real request.)
            asked_for = [i for i, raw_step in enumerate(raw_steps) if not _quotes_nothing(raw_step)]
            for i in range(len(raw_steps)):
                if i not in asked_for:
                    logger.warning(
                        "ClaudePlanner: dropped step %d (%r): its source_phrase is empty, so the "
                        "user never asked for it",
                        i + 1,
                        raw_steps[i].get("capability"),
                    )
            kept_steps = [raw_steps[i] for i in asked_for]

            # Enforce the order the user stated, not just the model's order:
            # steps run as a chain, so order changes the result.
            order = _order_stated_by_user(kept_steps, user_message)
            # With two or more steps, an order that couldn't be checked is
            # flagged, so the reply can say which order was used.
            order_confirmed = order is not None or len(kept_steps) < 2
            if order is None:
                order = list(range(len(kept_steps)))
            elif order != sorted(order):
                logger.info("ClaudePlanner: reordered steps to match the user's stated order")
            raw_steps = [kept_steps[i] for i in order]
            # The model's 1-based step numbers -> positions after dropping
            # and reordering. A dropped step has no position, so a
            # dependency on it is dropped too.
            position_of = {asked_for[old] + 1: new for new, old in enumerate(order)}

            step_ids = [f"step-{index + 1}" for index in range(len(raw_steps))]
            steps: list[PlanningStep] = []
            for index, raw_step in enumerate(raw_steps):
                depends_on_numbers = raw_step.get("depends_on_step_numbers") or []
                # Only earlier steps: a step can't depend on one that runs
                # after it (possible once the user's order is enforced).
                dependencies = tuple(
                    step_ids[position_of[number]]
                    for number in depends_on_numbers
                    if isinstance(number, int)
                    and number in position_of
                    and position_of[number] < index
                )
                steps.append(
                    PlanningStep(
                        id=step_ids[index],
                        description=str(raw_step["description"]),
                        capability=str(raw_step["capability"]),
                        priority=index,
                        dependencies=dependencies,
                        order_confirmed=order_confirmed,
                    )
                )
            return tuple(steps)
        except (TypeError, KeyError, AttributeError):
            logger.warning("ClaudePlanner: reply JSON had an unexpected shape: %r", data)
            return ()

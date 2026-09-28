"""Voice — communicates results and status back to the user."""

import dataclasses
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from buddy.llm import LLMMessage, LLMProvider
from buddy.models import (
    SKIPPED_STATUS,
    ExecutionResult,
    StepResult,
    Understanding,
    VerificationResult,
)
from buddy.planner import _INTENT_CAPABILITIES
from buddy.state import BuddyState

logger = logging.getLogger(__name__)

_ANALYZE_CAPABILITY = "spreadsheet.analyze"
_ANALYSIS_FINDING_PATTERN = re.compile(
    r"^\[" + re.escape(_ANALYZE_CAPABILITY) + r"\] \[(?P<category>\w+)\] (?P<text>.*)$"
)

# No spreadsheet.analyze sub-check has a capability that actually resolves
# it yet -- see docs/design/proactive-analysis-design.md Section 5.3. A
# finding's category is only ever folded into "already handled" when a
# real, matching capability is added here AND a step for it ran and passed
# in the same turn. Deliberately empty today: near-duplicate findings, for
# instance, are never resolved by spreadsheet.deduplicate (exact-match
# only) even when it ran and passed in the same turn.
_ANALYSIS_FINDING_FIX_CAPABILITY: dict[str, str] = {}

# Inspector prefixes each finding with its capability (and, for analysis
# findings, a category) -- "[spreadsheet.deduplicate] ..." or
# "[spreadsheet.analyze] [outliers] ...". Those tags are internal routing
# labels and must never reach the user, so they're stripped before a
# finding is rendered into a reply or handed to the model.
_FINDING_TAG_PREFIX = re.compile(r"^(?:\[[\w.]+\]\s*)+")


def _strip_finding_tags(finding: str) -> str:
    """Return ``finding`` without its leading ``[capability]``/``[category]`` tags."""
    return _FINDING_TAG_PREFIX.sub("", finding)


def _is_analysis_finding(finding: str) -> bool:
    """Return whether ``finding`` came from the proactive ``spreadsheet.analyze`` step."""
    return finding.startswith(f"[{_ANALYZE_CAPABILITY}]")


def _describe_capability(capability: str) -> str:
    """Turn a capability id into plain words, e.g. ``"spreadsheet.deduplicate"`` -> ``"deduplicate"``."""
    return capability.rsplit(".", 1)[-1].replace("_", " ")


def _find_step(execution_result: ExecutionResult | None, capability: str) -> StepResult | None:
    """Return the first step in ``execution_result.steps`` for ``capability``.

    Args:
        execution_result: The execution result to search, if any.
        capability: The capability to look for.

    Returns:
        The matching ``StepResult``, or ``None`` if there isn't one.
    """
    if execution_result is None:
        return None
    for step in execution_result.steps:
        if step.capability == capability:
            return step
    return None


def _step_passed(
    step: StepResult, verification_result: VerificationResult | None
) -> bool:
    """Return whether ``step`` actually ran successfully this turn.

    A step counts as passed only if its Skill reported ``"success"`` and
    Inspector didn't independently fail that same capability (when
    ``verification_result.steps`` carries a per-step verdict for it).

    Args:
        step: The step to check.
        verification_result: Inspector's verdict, if it ran.

    Returns:
        ``True`` if the step both ran successfully and wasn't failed by
        verification.
    """
    if step.status != "success":
        return False
    if verification_result is not None:
        for step_verification in verification_result.steps:
            if step_verification.capability == step.capability and not step_verification.passed:
                return False
    return True


_ACTION_TIERS = ("write", "external_effect")

NO_CHANGES_SENTENCE = "No changes were made to any file this turn."

# Wording that claims work was carried out. When nothing was delivered this
# turn, a model reply containing any of it is discarded for the factual
# TemplateSpeaker reply. Deliberately broad: an honest reply that happens to
# match ("nothing was removed") is replaced by an equally honest one, never
# by a false claim.
_COMPLETION_CLAIM = re.compile(
    r"\b(done|complet(e|ed|ely|ion)|finish(ed)?|succeed(ed)?|success(ful|fully)?|"
    r"removed|deleted|clean(ed)?|cleared|dedup(ed|licated)?|saved|fixed|ready|"
    r"handled|taken care)\b",
    re.IGNORECASE,
)


def _is_action_step(step: StepResult) -> bool:
    """Whether ``step`` ran a Skill that changes files (tier above READ).

    Results recorded before steps carried their tier count as actions only
    if they produced a file.
    """
    if step.status == SKIPPED_STATUS:
        return False
    if step.permission_tier:
        return step.permission_tier in _ACTION_TIERS
    return bool(step.outputs)


def _action_passed_verification(
    execution_result: ExecutionResult | None, verification_result: VerificationResult | None
) -> bool:
    """Whether any action step succeeded *and* Inspector passed it this turn."""
    if execution_result is None or verification_result is None:
        return False
    if not execution_result.steps:
        # A result with no per-step breakdown: judge it by its output alone.
        return bool(execution_result.outputs) and verification_result.passed
    for step in execution_result.steps:
        if not _is_action_step(step) or step.status != "success":
            continue
        if not verification_result.steps:
            if verification_result.passed:
                return True
        elif any(
            v.capability == step.capability and v.passed for v in verification_result.steps
        ):
            return True
    return False


def _no_changes_made(
    execution_result: ExecutionResult | None, verification_result: VerificationResult | None
) -> bool:
    """Whether to tell the user no file was changed this turn.

    True when no action step passed verification — e.g. Think asked for
    clarification, so only read-only analysis ran. When Inspector didn't
    run at all but an action step did, this is ``False``: the file may have
    changed, just unverified, and the reply says that instead.
    """
    if verification_result is None:
        steps = execution_result.steps if execution_result is not None else ()
        return not any(_is_action_step(s) and s.status == "success" for s in steps) and not (
            execution_result is not None and execution_result.outputs
        )
    return not _action_passed_verification(execution_result, verification_result)


def _nothing_delivered(request: "SpeakingRequest") -> bool:
    """Whether this turn handed the user no verified result at all."""
    execution_result = request.execution_result
    return (
        _no_changes_made(execution_result, request.verification_result)
        or execution_result is None
        or not execution_result.outputs
        or execution_result.stopped_at is not None
    )


def _delivered_file_name(request: "SpeakingRequest") -> str | None:
    """The name of the user's original file, if a changed copy was handed back.

    The generated output name is never shown to the user (see the mode
    prompts); the reply refers to it as a cleaned copy of the original.
    """
    if _nothing_delivered(request):
        return None
    assert request.execution_result is not None
    for step in request.execution_result.steps:
        if _is_action_step(step) and step.input_reference:
            return Path(step.input_reference).name
    return Path(request.execution_result.outputs[0]).name


def _mentions_file(reply: str, file_name: str) -> bool:
    stem = Path(file_name).stem.lower()
    text = reply.lower()
    return stem in text or stem.replace("_", " ") in text


def _findings_for_reply(verification_result: VerificationResult) -> tuple[str, ...]:
    """The findings a reply should report, with analysis of the right file.

    When analysis ran more than once this turn — on the original, then
    re-run on the final file after the chain changed it — only the latest
    run describes the file the user receives, so the original's analysis
    findings are replaced by it.
    """
    analyses = [s for s in verification_result.steps if s.capability == _ANALYZE_CAPABILITY]
    if len(analyses) < 2:
        return verification_result.findings
    others = tuple(f for f in verification_result.findings if not _is_analysis_finding(f))
    return others + tuple(f"[{_ANALYZE_CAPABILITY}] {f}" for f in analyses[-1].findings)


def _analysis_describes_final_file(verification_result: VerificationResult | None) -> bool:
    if verification_result is None:
        return False
    return sum(s.capability == _ANALYZE_CAPABILITY for s in verification_result.steps) >= 2


def _chain_stop_sentence(execution_result: ExecutionResult | None) -> str:
    """Say, in plain words, which step stopped the chain and what didn't run.

    Returns:
        ``""`` if the chain didn't stop; otherwise a sentence naming the
        failed step (never its raw capability id) and every skipped step.
    """
    if execution_result is None or execution_result.stopped_at is None:
        return ""
    skipped = [
        _describe_capability(step.capability)
        for step in execution_result.steps
        if step.status == SKIPPED_STATUS
    ]
    sentence = f"I stopped at the {_describe_capability(execution_result.stopped_at)} step"
    if skipped:
        sentence += f", so {', '.join(skipped)} never ran"
    return sentence + " and no file was handed back; your original file is unchanged."


@dataclass(frozen=True)
class SpeakingRequest:
    """The inputs available for composing Buddy's reply to the user.

    A plain, self-contained bundle of everything a ``Speaker`` needs to
    work from — assembled mechanically from ``BuddyState`` by the Voice
    stage, so a speaker never has to depend on ``BuddyState`` directly.

    Attributes:
        understanding: The structured understanding produced by
            Brain.
        execution_result: The structured result produced by Hands, if
            it has run yet.
        verification_result: The structured result produced by Inspector,
            if it has run yet.
        mode: Buddy's conversational personality mode for this session —
            ``"friend"`` or ``"focus"`` — sourced from
            ``Context.mode``. ``NullSpeaker``/``TemplateSpeaker`` ignore
            it; ``PersonalityVoice`` uses it to select a system prompt.
    """

    understanding: Understanding
    execution_result: ExecutionResult | None
    verification_result: VerificationResult | None
    mode: str


class Speaker(Protocol):
    """Produces the reply text from a ``SpeakingRequest``.

    This is the seam where real reply composition is plugged in — a
    templated speaker, or one backed by an LLM. The Voice stage itself
    never composes a reply; it only calls whichever ``Speaker`` it was
    given.
    """

    def __call__(self, request: SpeakingRequest) -> str:
        """Compose a reply for ``request`` and return the reply text."""
        ...


class NullSpeaker:
    """A ``Speaker`` that never actually composes a reply.

    Honestly reports that no reply could be produced, rather than
    guessing. Serves as a safe default so ``Voice`` can be constructed and
    exercised — in tests, or wired into the Engine — before a real speaker
    is plugged in.
    """

    def __call__(self, request: SpeakingRequest) -> str:
        """Return a fixed, honest fallback reply for any request.

        Args:
            request: The request that would otherwise be spoken for.

        Returns:
            A fixed message noting that no speaker has been configured.
        """
        return "I wasn't able to generate a response — no speaker has been configured."


class TemplateSpeaker:
    """A ``Speaker`` that deterministically templates the reply.

    Builds the reply entirely from ``execution_result`` and
    ``verification_result`` — in particular, from Inspector's own
    independently-computed ``findings``, never from a Skill's
    self-reported summary or metadata (``ExecutionResult`` doesn't even
    carry those). No LLM call is made here: templating a known-shape
    success/fail message from already-structured data is mechanical, not
    a judgment call — the same reasoning ``RuleBasedPlanner`` follows for
    this slice.
    """

    def __call__(self, request: SpeakingRequest) -> str:
        """Compose a reply for ``request``.

        Args:
            request: The request to compose a reply for.

        Returns:
            The reply text.
        """
        execution_result = request.execution_result
        verification_result = request.verification_result
        stop_sentence = _chain_stop_sentence(execution_result)
        if verification_result is None:
            reply = self._describe_without_verification(execution_result)
        elif _no_changes_made(execution_result, verification_result):
            reply = self._no_changes_message(execution_result, verification_result, stop_sentence)
        elif _find_step(execution_result, _ANALYZE_CAPABILITY) is not None:
            reply = self._compose_with_analysis_preface(execution_result, verification_result)
        elif verification_result.passed:
            reply = self._success_message(execution_result, verification_result)
        else:
            reply = self._failure_message(verification_result)
        return f"{reply} {stop_sentence}" if stop_sentence else reply

    @staticmethod
    def _no_changes_message(
        execution_result: ExecutionResult | None,
        verification_result: VerificationResult,
        stop_sentence: str,
    ) -> str:
        """Compose the reply for a turn in which no file was changed.

        Never says "Done": nothing the user asked for was carried out (e.g.
        Think asked for clarification, so only read-only analysis ran).
        Still reports what was noticed, and why things failed if they did.

        Args:
            execution_result: The execution result, if any.
            verification_result: Inspector's verdict for this turn.
            stop_sentence: The chain-stop sentence, if the chain stopped —
                which already says the original is unchanged.

        Returns:
            The reply text, without the stop sentence itself.
        """
        open_findings, other_findings = TemplateSpeaker._partition_analysis_findings(
            verification_result
        )
        parts = []
        if open_findings:
            parts.append(
                "Before anything else, here's what I noticed: "
                + "; ".join(open_findings)
                + ". Want me to tackle any of these?"
            )
        remaining = dataclasses.replace(verification_result, findings=other_findings)
        if not remaining.passed:
            parts.append(TemplateSpeaker._failure_message(remaining))
        elif other_findings:
            joined = "; ".join(_strip_finding_tags(f) for f in other_findings)
            parts.append(joined[0].upper() + joined[1:] + ".")
        if not stop_sentence:
            parts.append(NO_CHANGES_SENTENCE)
        return " ".join(parts)

    @staticmethod
    def _compose_with_analysis_preface(
        execution_result: ExecutionResult | None, verification_result: VerificationResult
    ) -> str:
        """Compose a reply that leads with proactive-analysis findings.

        Per ``docs/design/proactive-analysis-design.md`` Section 5.3: this
        is an unprompted observation, not a pass/fail outcome, so it's
        never folded into the ordinary success/failure framing. Findings
        already resolved by another capability's step that ran and passed
        in this same turn (per ``_ANALYSIS_FINDING_FIX_CAPABILITY`` and
        ``verification_result.steps``) are silently dropped, not asked
        about again.

        Args:
            execution_result: The execution result, if any, to pull
                output file names from for the trailing message.
            verification_result: Inspector's own verdict, covering every
                step this turn ran, including ``spreadsheet.analyze``.

        Returns:
            The reply text: a leading sentence about open findings (if
            any), followed by the ordinary success/failure message built
            from every other step's own findings.
        """
        open_findings, other_findings = TemplateSpeaker._partition_analysis_findings(
            verification_result
        )
        parts = []
        if open_findings:
            parts.append(
                "Before anything else, here's what I noticed: "
                + "; ".join(open_findings)
                + ". Want me to tackle any of these?"
            )
        remaining_result = dataclasses.replace(verification_result, findings=other_findings)
        if remaining_result.passed:
            parts.append(TemplateSpeaker._success_message(execution_result, remaining_result))
        else:
            parts.append(TemplateSpeaker._failure_message(remaining_result))
        return " ".join(parts)

    @staticmethod
    def _partition_analysis_findings(
        verification_result: VerificationResult,
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """Split ``verification_result.findings`` into open vs. everything else.

        Args:
            verification_result: The verification result to partition.

        Returns:
            ``(open_findings, other_findings)`` — ``open_findings`` are
            this turn's ``spreadsheet.analyze`` findings not already
            resolved by another passed step this turn, with their
            ``[spreadsheet.analyze] [category]`` prefixes stripped for
            display; ``other_findings`` are every finding that isn't a
            ``spreadsheet.analyze`` finding at all, unchanged.
        """
        resolved_capabilities = {
            step.capability for step in verification_result.steps if step.passed
        }
        open_findings: list[str] = []
        other_findings: list[str] = []
        for finding in _findings_for_reply(verification_result):
            match = _ANALYSIS_FINDING_PATTERN.match(finding)
            if match is None:
                other_findings.append(finding)
                continue
            fix_capability = _ANALYSIS_FINDING_FIX_CAPABILITY.get(match.group("category"))
            if fix_capability is not None and fix_capability in resolved_capabilities:
                continue  # already handled this turn -- don't ask about it again
            open_findings.append(match.group("text"))
        return tuple(open_findings), tuple(other_findings)

    @staticmethod
    def _success_message(
        execution_result: ExecutionResult | None, verification_result: VerificationResult
    ) -> str:
        """Compose the reply for a passed verification.

        Covers every step's outcome, not just the first: every finding
        Inspector recorded (one turn may cover several delegated steps,
        each independently verified — see
        ``buddy.inspector.CompositeVerifier``) and every output file
        produced are all included, not only index ``0`` of each.

        Args:
            execution_result: The execution result, if any, to pull
                output file names from.
            verification_result: Inspector's own verdict, covering every
                step this turn ran.

        Returns:
            The reply text.
        """
        parts = ["Done"]
        if verification_result.findings:
            findings = "; ".join(_strip_finding_tags(f) for f in verification_result.findings)
            parts.append(f"— {findings}.")
        else:
            parts[0] = "Done."
        output_names = TemplateSpeaker._output_file_names(execution_result)
        if output_names:
            saved = ", ".join(f"`{name}`" for name in output_names)
            parts.append(f"Saved as {saved}.")
        return " ".join(parts)

    @staticmethod
    def _failure_message(verification_result: VerificationResult) -> str:
        """Compose the reply for a failed (or inconclusive) verification.

        Covers every step's findings, not just the first — see
        ``_success_message``'s docstring for why.

        Args:
            verification_result: The verification result, covering every
                step this turn ran.

        Returns:
            The reply text.
        """
        parts = ["I wasn't able to confirm that worked."]
        if verification_result.findings:
            joined = "; ".join(_strip_finding_tags(f) for f in verification_result.findings)
            parts.append(joined[0].upper() + joined[1:] + ".")
        if verification_result.recommendations:
            parts.append(" ".join(verification_result.recommendations))
        return " ".join(parts)

    @staticmethod
    def _describe_without_verification(execution_result: ExecutionResult | None) -> str:
        """Compose a reply when Inspector hasn't run at all.

        Args:
            execution_result: The execution result, if any.

        Returns:
            The reply text.
        """
        if execution_result is None:
            return "I wasn't able to find anything to do with that request."
        if not execution_result.success:
            errors = "; ".join(execution_result.errors) or "an unknown error occurred"
            return f"I ran into a problem: {errors}"
        return "I did the work, but wasn't able to confirm the result."

    @staticmethod
    def _output_file_names(execution_result: ExecutionResult | None) -> tuple[str, ...]:
        """Extract every output file's name, across every step.

        Args:
            execution_result: The execution result, if any.

        Returns:
            Each output path's base name, in the order they were
            produced, or ``()`` if there aren't any.
        """
        if execution_result is None or not execution_result.outputs:
            return ()
        return tuple(Path(output_path).name for output_path in execution_result.outputs)


class Voice:
    """The Voice stage of Buddy's cognitive cycle.

    Voice converts the verified outcome into the message the user
    actually sees, written to ``BuddyState.reply`` (see
    ``docs/WORKBUDDY_ARCHITECTURE.md`` Section 4). Voice is the only stage
    that produces user-facing language.

    Voice contains no reply-composition logic of its own. All of the
    actual wording is delegated to an injected ``Speaker``. Voice's own
    responsibility is purely mechanical: assemble the speaker's input from
    ``BuddyState``, call the speaker, and record what it returns. This
    keeps Voice a stable, swappable framework — a templated speaker, an
    LLM-backed speaker, or anything else conforming to ``Speaker`` can be
    substituted without ever changing this stage.
    """

    def __init__(self, speaker: Speaker | None = None) -> None:
        """Initialize the stage.

        Args:
            speaker: The speaker to delegate reply composition to.
                Defaults to ``NullSpeaker``, which honestly reports that
                no reply could be produced rather than guessing.
        """
        if speaker is None:
            logger.debug("No speaker provided; falling back to NullSpeaker")
        self._speaker = speaker if speaker is not None else NullSpeaker()

    def __call__(self, state: BuddyState) -> BuddyState:
        """Compose a reply for ``state`` and record it.

        Args:
            state: The current ``BuddyState`` for this turn.

        Returns:
            The same state, with the reply text recorded in
            ``state.reply``.
        """
        request = self._build_request(state)
        reply = self._speaker(request)
        self._apply(state, reply)
        logger.debug("Voice recorded a reply for session %r", state.session_id)
        return state

    @staticmethod
    def _build_request(state: BuddyState) -> SpeakingRequest:
        """Assemble the speaker's input from the current state.

        This step is purely mechanical: it gathers what is already
        present on ``state`` into a plain, self-contained bundle. It
        performs no reply composition of its own.

        Args:
            state: The current ``BuddyState`` for this turn.

        Returns:
            The request to hand to the configured speaker.
        """
        return SpeakingRequest(
            understanding=state.understanding or Understanding(),
            execution_result=state.execution_result,
            verification_result=state.verification_result,
            mode=state.context.mode,
        )

    @staticmethod
    def _apply(state: BuddyState, reply: str) -> None:
        """Record the speaker's output onto ``state``.

        Args:
            state: The state to update.
            reply: The reply text to record.
        """
        state.reply = reply


FRIEND_MODE_PROMPT = """You are the voice of WorkBuddy, an AI coworker, speaking directly to the user in Friend mode.

Your job is to report, in your own words, an outcome that has already
happened — not to decide what happened. You will be given the plain facts
(what was attempted, whether it succeeded, whether it was independently
verified, and any findings, warnings, or errors). Your only job is to
phrase those facts warmly and honestly.

Tone: warm, honest, occasionally funny — like a trusted work friend who
doesn't take things too seriously but always tells the truth. Light,
PG-rated ribbing is welcome when it naturally fits (e.g. gently ribbing a
spreadsheet that turned out to have a lot of duplicates), but never mean
and never forced. A playful pop-culture-adjacent line (e.g. comparing
yourself to Jarvis or another AI assistant) is fine sparingly — not in
every reply, only when it genuinely fits.

Avoid generic AI humor. No reflexive puns, no worn-out phrases like
"superpowers" or "checked out," and no joke that's just the first idea
that comes to mind rather than something genuinely specific to what
actually happened. If nothing about this particular situation is
genuinely funny, skip the joke entirely — flat honesty beats a forced or
generic one, every time.

For calibration, here is the target voice — specific to the situation at
hand, not generic filler:
- "Third time's the charm, or third time we both pretend I didn't
  notice? Let's just fix it properly this time."
- "Deleting it now — but three formulas are about to lose their best
  friend, and they'll break the second it's gone."
These are not templates to reuse verbatim — they're examples of the level
of specificity and callback humor to aim for, not lines to recycle.

If any of the facts you're given are listed under "Noticed on your own,
unprompted", treat them differently from an ordinary outcome: they're
something you noticed on your own, before being asked to — not a request
you're reporting pass/fail on. Lead with them, phrase them warmly, and close by asking which one (if
any) the user wants you to look at first. Don't ask about a finding that's
already been resolved by something else you did this same turn — only ask
about the ones still open.

Hard rules:
- Never invent, omit, or soften a fact you were given. If something
  failed, say it failed. The personality is in HOW you say it, never in
  WHAT you say.
- If you're told no changes were made to any file this turn, say so
  plainly, and never say or imply that anything was done, completed,
  removed, cleaned, or saved.
- If you're told a file was handed back, mention it — by its plain
  original name, as a cleaned copy.
- Never recite a full file path or a generated filename verbatim — no
  folder separators, no system-generated suffixes like
  "_manual-session-task-0". Refer to the file the way a person would:
  "the file," "your spreadsheet," or its plain original name with the
  generated suffix dropped.
- Never repeat internal labels: no bracketed tags like "[something]", no
  dotted system names like "spreadsheet.deduplicate", and none of the
  field labels from the facts you were given. Describe what happened in
  plain words.
- Keep it short: 1-3 sentences. This is a quick status update, not an
  essay.
- Respond with the reply text only — no preamble, no quotation marks, no
  markdown headers.
"""

FOCUS_MODE_PROMPT = """You are the voice of WorkBuddy, an AI coworker, speaking directly to the user in Focus mode.

Your job is to report, in your own words, an outcome that has already
happened — not to decide what happened. You will be given the plain facts
(what was attempted, whether it succeeded, whether it was independently
verified, and any findings, warnings, or errors). Your only job is to
phrase those facts as briefly and directly as possible.

Tone: short, direct, zero humor, zero small talk. State what happened and
what needs attention, in the fewest words possible. No warmth, no jokes,
no filler.

Sound like a clipped human colleague giving a quick heads-up, not a
system log or a formal report. Prefer "Done — 3 rows left, duplicates
gone" over passive, formal phrasing like "resulting in a 3-row output."
Say it the way a coworker would say it out loud in five words, not the
way a status page would print it.

Facts listed under "Noticed on your own, unprompted" are an unprompted
observation, not a pass/fail outcome. Lead with them tersely, then ask which one to prioritize —
skip any already resolved by something else this turn.

Hard rules:
- Never invent, omit, or soften a fact you were given. If something
  failed, say it failed.
- If you're told no changes were made to any file this turn, say so, and
  never say or imply that anything was done, completed, removed, cleaned,
  or saved.
- If you're told a file was handed back, mention it by its plain original
  name.
- Never repeat internal labels: no bracketed tags like "[something]", no
  dotted system names like "spreadsheet.deduplicate", and none of the
  field labels from the facts you were given.
- Prefer a single short sentence. Two only if truly necessary.
- Respond with the reply text only — no preamble, no quotation marks, no
  markdown headers.
"""


class PersonalityVoice:
    """A ``Speaker`` backed by a Claude model, with a selectable personality.

    Reads exactly the same ``SpeakingRequest`` fields ``TemplateSpeaker``
    does — ``understanding``, ``execution_result``, ``verification_result``
    — and asks the model to phrase them, using one of two system prompts
    selected by ``request.mode``: ``FRIEND_MODE_PROMPT`` (warm, honest,
    occasionally funny) or ``FOCUS_MODE_PROMPT`` (short, direct, no
    humor). Both prompts instruct the model that the facts it's given are
    fixed — the personality only changes HOW the outcome is described,
    never WHAT is reported. No new data is gathered beyond what
    ``TemplateSpeaker`` already has access to.

    Never raises: on an LLM failure or an empty reply, falls back to
    ``TemplateSpeaker``'s deterministic, factual reply — the same honesty
    contract ``ClaudeInterpreter``/``ClaudeThinker`` follow, reusing
    ``TemplateSpeaker`` rather than duplicating its logic. Never logs
    prompt or reply content — only that a call failed or was empty.
    """

    def __init__(self, provider: LLMProvider) -> None:
        """Initialize the speaker.

        Args:
            provider: The ``LLMProvider`` used to compose the reply.
        """
        self._provider = provider

    def __call__(self, request: SpeakingRequest) -> str:
        """Compose a reply for ``request`` in the requested personality mode.

        Args:
            request: The request to compose a reply for.

        Returns:
            The composed reply text, or a factual fallback if the model
            call fails or returns an empty reply.
        """
        system_prompt = FOCUS_MODE_PROMPT if request.mode == "focus" else FRIEND_MODE_PROMPT
        user_prompt = self._build_user_prompt(request)
        try:
            raw_reply = self._provider.complete(
                system=system_prompt,
                messages=(LLMMessage(role="user", content=user_prompt),),
            )
        except Exception:
            logger.exception("PersonalityVoice: LLM call failed")
            return self._fallback_reply(request)

        reply = raw_reply.strip()
        if not reply:
            logger.warning("PersonalityVoice: LLM reply was empty")
            return self._fallback_reply(request)

        # The prompt tells the model what happened, but a model can still
        # say "all done" for a turn that changed nothing — so check the
        # reply itself. When nothing was delivered, any completion wording
        # means the factual TemplateSpeaker reply is used instead.
        if _nothing_delivered(request) and _COMPLETION_CLAIM.search(reply):
            logger.warning(
                "PersonalityVoice: reply claimed completion but nothing was delivered; "
                "using the factual fallback"
            )
            return self._fallback_reply(request)

        # A file was handed back: the reply must say so.
        file_name = _delivered_file_name(request)
        if file_name is not None and not _mentions_file(reply, file_name):
            reply = f"{reply} Your cleaned copy of {file_name} is ready."
        return reply

    @staticmethod
    def _build_user_prompt(request: SpeakingRequest) -> str:
        """Build the user-turn prompt from ``request``.

        Only the same facts ``TemplateSpeaker`` already reads —
        ``understanding.intent``, ``execution_result``, and
        ``verification_result`` — are included.

        ``understanding.intent`` is what the user *asked for*, not what
        happened: Think can flag ``clarification_required`` (or the
        planner can otherwise produce no step), leaving the intent's
        capability unrun while other steps — e.g. proactive
        ``spreadsheet.analyze`` — still succeed. So the intent is labelled
        as a request, every step that actually ran is listed with its
        status, and when the intent's capability has no passing step this
        turn the prompt says explicitly that the action was not
        performed. Otherwise "classified as remove_duplicate_rows" plus
        "Execution succeeded: True" reads to the model as "duplicates
        removed".

        Args:
            request: The request to build a prompt from.

        Returns:
            The plain-text user prompt to send to the model.
        """
        lines: list[str] = []
        execution_result = request.execution_result
        verification_result = request.verification_result
        steps = execution_result.steps if execution_result is not None else ()

        # Stated first and unconditionally when true, from the steps alone —
        # not from which intent the request was classified as.
        if _no_changes_made(execution_result, verification_result):
            lines.append(
                f"{NO_CHANGES_SENTENCE} Nothing the user asked for was carried out; "
                "do not say or imply that anything was done, completed, removed, or saved."
            )

        intent = request.understanding.intent
        if intent:
            lines.append(
                "What the user asked for (a request, not an outcome): " + intent.replace("_", " ")
            )
            capability = _INTENT_CAPABILITIES.get(intent)
            if capability is not None and not any(
                step.capability == capability and _step_passed(step, verification_result)
                for step in steps
            ):
                lines.append(
                    f"The requested action ({_describe_capability(capability)}) was NOT "
                    "performed this turn. Do not say or imply that it was done."
                )

        if execution_result is None:
            lines.append("Execution: nothing was executed.")
        else:
            ran = [step for step in steps if step.status != SKIPPED_STATUS]
            skipped = [step for step in steps if step.status == SKIPPED_STATUS]
            if ran:
                ran_text = ", ".join(
                    f"{_describe_capability(step.capability)} "
                    f"({'passed' if _step_passed(step, verification_result) else 'did not pass'})"
                    for step in ran
                )
                lines.append(f"Steps that actually ran this turn: {ran_text}")
            else:
                lines.append("Steps that actually ran this turn: none")
            if execution_result.stopped_at is not None:
                lines.append(
                    "Work stopped at this failed step, so no file was handed back and the "
                    f"original is unchanged: {_describe_capability(execution_result.stopped_at)}"
                )
            if skipped:
                lines.append(
                    "Steps that never ran, because an earlier step failed: "
                    + ", ".join(_describe_capability(step.capability) for step in skipped)
                )
            lines.append(f"Execution succeeded: {execution_result.success}")
            file_name = _delivered_file_name(request)
            if file_name is not None:
                lines.append(
                    f"File handed back to the user (mention it in your reply): a cleaned copy "
                    f"of {file_name}, with every change above applied"
                )
            if execution_result.warnings:
                lines.append("Execution warnings: " + "; ".join(execution_result.warnings))
            if execution_result.errors:
                lines.append("Execution errors: " + "; ".join(execution_result.errors))

        if verification_result is None:
            lines.append("Verification: not performed.")
        else:
            lines.append(f"Verification passed: {verification_result.passed}")
            reported = _findings_for_reply(verification_result)
            noticed = [_strip_finding_tags(f) for f in reported if _is_analysis_finding(f)]
            findings = [_strip_finding_tags(f) for f in reported if not _is_analysis_finding(f)]
            if noticed:
                about = (
                    " (in the cleaned file handed back, after the changes)"
                    if _analysis_describes_final_file(verification_result)
                    else ""
                )
                lines.append(f"Noticed on your own, unprompted{about}: " + "; ".join(noticed))
            if findings:
                lines.append("Verification findings: " + "; ".join(findings))
            if verification_result.recommendations:
                lines.append(
                    "Verification recommendations: "
                    + "; ".join(verification_result.recommendations)
                )

        return "\n".join(lines)

    @staticmethod
    def _fallback_reply(request: SpeakingRequest) -> str:
        """Fall back to a plain, factual reply when the LLM call fails.

        ``TemplateSpeaker`` already builds a deterministic, factual
        sentence directly from ``execution_result``/``verification_result``
        with no LLM involved — reusing it here satisfies the same
        honest-fallback contract ``ClaudeInterpreter``/``ClaudeThinker``
        follow, without duplicating that logic.

        Args:
            request: The request that would otherwise be spoken for.

        Returns:
            The fallback reply text.
        """
        return TemplateSpeaker()(request)

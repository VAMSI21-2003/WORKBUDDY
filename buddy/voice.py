"""Voice — communicates results and status back to the user."""

import dataclasses
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from buddy.inspector import FINAL_OUTPUT_CAPABILITY, SKIPPED_FINDING
from buddy.llm import LLMProvider
from buddy.models import (
    DECLINED_STATUS,
    SKIPPED_STATUS,
    ExecutionResult,
    StepResult,
    Understanding,
    VerificationResult,
)
from buddy.outcome import (
    FILE_STYLE_GENERATED,
    FILE_STYLE_PLAIN,
    _describe_capability,
    _is_action_step,
    _step_passed,
    build_outcome_report,
    render_outcome,
)
from buddy.planner import _INTENT_CAPABILITIES
from buddy.state import BuddyState
from buddy.voice_catalog import select_phrases

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
# finding is rendered into a reply.
_FINDING_TAG_PREFIX = re.compile(r"^(?:\[[\w.]+\]\s*)+")

# A raw capability id anywhere in a text, e.g. the "spreadsheet.report" in
# "No registered skill declares support for 'spreadsheet.report'." Matches
# any id in a known capability domain, so an id with no registered skill is
# caught too — but not a file name such as "spreadsheet.xlsx".
_CAPABILITY_DOMAINS = sorted(
    {c.split(".", 1)[0] for c in (*_INTENT_CAPABILITIES.values(), "spreadsheet.analyze")}
)
_CAPABILITY_ID = re.compile(
    r"\b(?:" + "|".join(map(re.escape, _CAPABILITY_DOMAINS)) + r")\.(?!(?:xlsx|xlsm|xls|csv)\b)"
    r"[a-z_]+\b"
)


def _plain_words(text: str) -> str:
    """Replace every raw capability id in ``text`` with plain words."""
    return _CAPABILITY_ID.sub(lambda match: _describe_capability(match.group(0)), text)


def _strip_finding_tags(finding: str) -> str:
    """Return ``finding`` without its leading ``[capability]``/``[category]``
    tags, and with any capability id inside it in plain words."""
    return _plain_words(_FINDING_TAG_PREFIX.sub("", finding))


def _is_analysis_finding(finding: str) -> bool:
    """Return whether ``finding`` came from the proactive ``spreadsheet.analyze`` step."""
    return finding.startswith(f"[{_ANALYZE_CAPABILITY}]")


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


NO_CHANGES_SENTENCE = "No changes were made to any file this turn."

# Wording that claims work was carried out. No reply for a turn that
# delivered nothing may contain any of it, and no phrase-catalog entry may
# either (``buddy.voice_catalog``): on a delivered turn the outcome block's
# lead word is the only "Done". Tests check replies against it; it is a
# lint, not the safeguard. Deliberately broad.
_COMPLETION_CLAIM = re.compile(
    r"\b(done|complet(e|ed|ely|ion)|finish(ed)?|succeed(ed)?|success(ful|fully)?|"
    r"removed|deleted|clean(ed)?|cleared|dedup(ed|licated)?|saved|fixed|ready|"
    r"handled|taken care)\b",
    re.IGNORECASE,
)


def _action_passed_verification(
    execution_result: ExecutionResult | None, verification_result: VerificationResult | None
) -> bool:
    """Whether any action step passed this turn, by ``_step_passed``'s rule."""
    if execution_result is None or verification_result is None:
        return False
    if not execution_result.steps:
        # A result with no per-step breakdown: judge it by its output alone.
        return bool(execution_result.outputs) and verification_result.passed
    return any(
        _is_action_step(step) and _step_passed(step, verification_result)
        for step in execution_result.steps
    )


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

    ``PersonalityVoice`` never shows the generated output name; its reply
    refers to the file as a cleaned copy of the original.
    """
    if _nothing_delivered(request):
        return None
    assert request.execution_result is not None
    for step in request.execution_result.steps:
        if _is_action_step(step) and step.input_reference:
            return Path(step.input_reference).name
    return Path(request.execution_result.outputs[0]).name


def _findings_for_reply(verification_result: VerificationResult) -> tuple[str, ...]:
    """The findings a reply should report, with analysis of the right file.

    When analysis was re-run on the final file after the chain changed it
    (a ``recheck`` verdict), the original's analysis findings no longer
    describe the file the user receives, so they're dropped. The re-check's
    own findings replace them if it passed; if it failed there's nothing
    current to report, and the reply says the file couldn't be re-checked
    instead (``_recheck_failed``).
    """
    rechecks = [
        s
        for s in verification_result.steps
        if s.capability == _ANALYZE_CAPABILITY and s.recheck
    ]
    if not rechecks:
        return verification_result.findings
    others = tuple(f for f in verification_result.findings if not _is_analysis_finding(f))
    return others + tuple(
        f"[{_ANALYZE_CAPABILITY}] {f}" for s in rechecks if s.passed for f in s.findings
    )


def _analysis_describes_final_file(verification_result: VerificationResult | None) -> bool:
    if verification_result is None:
        return False
    return any(
        s.capability == _ANALYZE_CAPABILITY and s.recheck and s.passed
        for s in verification_result.steps
    )


RECHECK_FAILED_SENTENCE = (
    "I couldn't re-check the final file for anything else worth flagging after the changes."
)


def _verified_delivery(request: "SpeakingRequest") -> bool:
    """Whether this turn delivered a verified file.

    Something was handed back and Inspector ran and passed the turn. Only
    such a turn gets the rendered outcome (``_verified_delivery_reply``),
    and, from ``PersonalityVoice``, catalog phrases around it. Every other
    turn keeps ``TemplateSpeaker``'s factual reply.

    This deliberately doesn't depend on the classified intent. Brain has
    no conversational intent, so a turn classified ``unclear`` or with no
    intent (an LLM failure, a non-JSON reply) may well be an action
    request that nothing carried out.
    """
    verification_result = request.verification_result
    return (
        not _nothing_delivered(request)
        and verification_result is not None
        and verification_result.passed
    )


INITIAL_ANALYSIS_FAILED_SENTENCE = "The initial analysis of your original file couldn't run."


def _order_used_sentence(request: "SpeakingRequest") -> str | None:
    """Say the order the steps ran in, when it couldn't be checked.

    Only when the planner couldn't confirm the order the user stated and
    two or more file-changing steps ran: the user can then see whether it
    matches what they meant.
    """
    if request.order_confirmed or request.execution_result is None:
        return None
    ran = [
        _describe_capability(step.capability)
        for step in request.execution_result.steps
        if _is_action_step(step) and step.status != DECLINED_STATUS
    ]
    if len(ran) < 2:
        return None
    return (
        "I couldn't tell for sure from your message what order you wanted, so the steps ran "
        "in this order: " + ", then ".join(ran) + "."
    )


def _initial_analysis_failed(verification_result: VerificationResult | None) -> bool:
    """Whether a read-only step on the original (not a re-check) failed.

    Non-blocking, like a failed re-check: it never fails the turn, but the
    reply says so in plain words.
    """
    if verification_result is None:
        return False
    return any(
        s.read_only and not s.recheck and not s.passed for s in verification_result.steps
    )


def _recheck_failed(verification_result: VerificationResult | None) -> bool:
    """Whether a re-check of the delivered file failed (non-blocking)."""
    if verification_result is None:
        return False
    return any(s.recheck and not s.passed for s in verification_result.steps)


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
            it; ``PersonalityVoice`` uses it to select catalog phrases.
        order_confirmed: ``False`` when the plan's order couldn't be
            checked against the order the user stated (see
            ``PlanningStep.order_confirmed``).
        session_id: The session this turn belongs to.
        turn_index: How many replies Buddy had already given in this
            session. With ``session_id``, it seeds the deterministic
            choice of catalog phrases (``buddy.voice_catalog``).
    """

    understanding: Understanding
    execution_result: ExecutionResult | None
    verification_result: VerificationResult | None
    mode: str
    order_confirmed: bool = True
    session_id: str = ""
    turn_index: int = 0


# Leads the analysis findings on a delivered turn, after the outcome.
DELIVERED_ANALYSIS_LEAD = "I also noticed: "
ANALYSIS_QUESTION = "Want me to tackle any of these?"


def _verified_delivery_reply(
    request: SpeakingRequest, *, file_style: str, mode: str | None = None
) -> str:
    """The reply for a turn that delivered a verified file, rendered by code.

    In order (design decision D3): the opener, the outcome block, the
    analysis findings, the closer. The outcome block comes from
    ``buddy.outcome`` (execution and verifier data only); the opener and
    closer from ``buddy.voice_catalog``, chosen by code, and only when a
    ``mode`` is given. No model writes any of it.

    Args:
        request: The turn; ``_verified_delivery(request)`` must be true.
        file_style: How the outcome block names the file.
        mode: The personality mode for the catalog phrases, or ``None`` for
            none (``TemplateSpeaker``).

    Returns:
        The reply text.
    """
    execution_result = request.execution_result
    verification_result = request.verification_result
    assert verification_result is not None
    open_findings: tuple[str, ...] = ()
    analysis_notes: list[str] = []
    if _find_step(execution_result, _ANALYZE_CAPABILITY) is not None:
        open_findings, other_findings = TemplateSpeaker._partition_analysis_findings(
            verification_result
        )
        # An analysis finding with no category (e.g. "none of the issues I
        # check for were found") is a note, not a question. A failed
        # analysis is said by INITIAL_ANALYSIS_FAILED_SENTENCE instead. Every
        # other finding is a step verifier's own wording, replaced by the
        # measured counts.
        failed = {
            finding
            for verdict in verification_result.steps
            if verdict.capability == _ANALYZE_CAPABILITY and not verdict.passed
            for finding in verdict.findings
        }
        analysis_notes = [
            _strip_finding_tags(f)
            for f in other_findings
            if _is_analysis_finding(f) and _FINDING_TAG_PREFIX.sub("", f) not in failed
        ]
    report = build_outcome_report(
        execution_result,
        verification_result,
        request.understanding.intent,
        order_used=_order_used_sentence(request),
        delivered_file=_delivered_file_name(request),
        open_findings=len(open_findings),
    )
    opener = closer = ""
    if mode is not None:
        opener_phrase, closer_phrase = select_phrases(
            report, mode, seed=f"{request.session_id}:{request.turn_index}"
        )
        opener, closer = opener_phrase.text, closer_phrase.text

    parts = [opener, render_outcome(report, file_style)]
    if open_findings:
        parts.append(DELIVERED_ANALYSIS_LEAD + "; ".join(open_findings) + ". " + ANALYSIS_QUESTION)
    if analysis_notes:
        joined = "; ".join(analysis_notes)
        parts.append(joined[0].upper() + joined[1:] + ".")
    if _initial_analysis_failed(verification_result):
        parts.append(INITIAL_ANALYSIS_FAILED_SENTENCE)
    if _recheck_failed(verification_result):
        parts.append(RECHECK_FAILED_SENTENCE)
    parts.append(closer)
    return _plain_words(" ".join(part for part in parts if part))


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
        # A verified delivery: the outcome is rendered from execution and
        # verifier data, with the generated file name and no phrases.
        if _verified_delivery(request):
            return _verified_delivery_reply(request, file_style=FILE_STYLE_GENERATED)
        # Routed on "was anything delivered?" first: when the chain stopped
        # or no verified file came back, no later branch may run — they
        # can say "Done" because an earlier step passed.
        if _nothing_delivered(request):
            if execution_result is not None and execution_result.stopped_at is not None:
                reply = self._stopped_message(execution_result, verification_result)
            elif verification_result is None:
                reply = self._nothing_confirmed_message(execution_result)
            else:
                reply = self._no_changes_message(execution_result, verification_result)
        elif verification_result is None:
            reply = "I made the changes, but wasn't able to confirm the result."
        else:
            # Delivered, but Inspector didn't pass it (a verified delivery
            # returned above).
            if _find_step(execution_result, _ANALYZE_CAPABILITY) is not None:
                reply = self._compose_with_analysis_preface(verification_result)
            else:
                reply = self._failure_message(verification_result)
            if _initial_analysis_failed(verification_result):
                reply = f"{reply} {INITIAL_ANALYSIS_FAILED_SENTENCE}"
            if _recheck_failed(verification_result):
                reply = f"{reply} {RECHECK_FAILED_SENTENCE}"
        order_sentence = _order_used_sentence(request)
        if order_sentence is not None:
            reply = f"{reply} {order_sentence}"
        return _plain_words(reply)

    @staticmethod
    def _stopped_message(
        execution_result: ExecutionResult, verification_result: VerificationResult | None
    ) -> str:
        """Compose the reply for a chain that stopped: nothing was delivered.

        Never says "Done" or reports an earlier step's success as if it
        counted: the file that step produced was never handed back. Says
        what was noticed on the (unchanged) original, why the work stopped,
        and which step stopped it.
        """
        parts = []
        reasons: list[str] = []
        if verification_result is not None:
            open_findings, _ = TemplateSpeaker._partition_analysis_findings(verification_result)
            if open_findings:
                parts.append(
                    "Before anything else, here's what I noticed: "
                    + "; ".join(open_findings)
                    + ". Want me to tackle any of these?"
                )
            for verdict in verification_result.steps:
                findings = [f for f in verdict.findings if f != SKIPPED_FINDING]
                if (
                    verdict.passed
                    or verdict.capability in (_ANALYZE_CAPABILITY, FINAL_OUTPUT_CAPABILITY)
                    or not findings
                ):
                    continue
                reasons.append(
                    f"The {_describe_capability(verdict.capability)} step: "
                    + "; ".join(_strip_finding_tags(f) for f in findings)
                )
        if not reasons:
            reasons = [
                _plain_words(error).rstrip(".")
                for error in execution_result.errors
                if not error.startswith("stopped:")
            ]
        sentence = "I ran into a problem partway through."
        if reasons:
            sentence += " " + ". ".join(reasons) + "."
        parts.append(sentence)
        parts.append(_chain_stop_sentence(execution_result))
        return " ".join(parts)

    @staticmethod
    def _nothing_confirmed_message(execution_result: ExecutionResult | None) -> str:
        """Compose the reply when nothing was delivered and Inspector didn't run.

        Never says the work was done: with no verification and no file
        handed back — e.g. only read-only steps ran — nothing the user
        asked for can be claimed.
        """
        if execution_result is None:
            return "I wasn't able to find anything to do with that request."
        if execution_result.errors:
            errors = "; ".join(_plain_words(e).rstrip(".") for e in execution_result.errors)
            return f"I ran into a problem: {errors}. {NO_CHANGES_SENTENCE}"
        return f"I wasn't able to confirm any result. {NO_CHANGES_SENTENCE}"

    @staticmethod
    def _no_changes_message(
        execution_result: ExecutionResult | None,
        verification_result: VerificationResult,
    ) -> str:
        """Compose the reply for a turn in which no file was changed.

        Never says "Done": nothing the user asked for was carried out (e.g.
        Think asked for clarification, so only read-only analysis ran).
        Still reports what was noticed, and why things failed if they did.
        A chain that stopped is ``_stopped_message``'s job, not this one's.

        Args:
            execution_result: The execution result, if any.
            verification_result: Inspector's verdict for this turn.

        Returns:
            The reply text.
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
        parts.append(NO_CHANGES_SENTENCE)
        return " ".join(parts)

    @staticmethod
    def _compose_with_analysis_preface(verification_result: VerificationResult) -> str:
        """Compose a reply that leads with proactive-analysis findings.

        Per ``docs/design/proactive-analysis-design.md`` Section 5.3: this
        is an unprompted observation, not a pass/fail outcome, so it's
        never folded into the ordinary success/failure framing. Findings
        already resolved by another capability's step that ran and passed
        in this same turn (per ``_ANALYSIS_FINDING_FIX_CAPABILITY`` and
        ``verification_result.steps``) are silently dropped, not asked
        about again.

        Only used for a delivered turn Inspector didn't pass: a verified
        delivery is ``_verified_delivery_reply``'s job.

        Args:
            verification_result: Inspector's own verdict, covering every
                step this turn ran, including ``spreadsheet.analyze``.

        Returns:
            The reply text: a leading sentence about open findings (if
            any), followed by the failure message built from every other
            step's own findings.
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
    def _failure_message(verification_result: VerificationResult) -> str:
        """Compose the reply for a failed (or inconclusive) verification.

        Covers every step's findings, not just the first: one turn may
        cover several delegated steps, each independently verified (see
        ``buddy.inspector.CompositeVerifier``).

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
            order_confirmed=all(step.order_confirmed for step in state.planning or ()),
            session_id=state.session_id,
            turn_index=state.context.buddy_reply_count(),
        )

    @staticmethod
    def _apply(state: BuddyState, reply: str) -> None:
        """Record the speaker's output onto ``state``.

        Args:
            state: The state to update.
            reply: The reply text to record.
        """
        state.reply = reply


class PersonalityVoice:
    """A ``Speaker`` with a selectable personality: Friend or Focus mode.

    The Voice model never writes reply text (``CLAUDE.md``, "Decided
    behaviour"; ``docs/design/verified-outcome-design.md``, decision D1):

    - On a turn that delivered a verified file, the reply is rendered by
      code: the outcome block from execution and verifier data, with the
      file named as a cleaned copy of the original, and an opener and
      closer for ``request.mode`` chosen by code from the reviewed phrase
      catalog (``buddy.voice_catalog``).
    - Every other turn gets ``TemplateSpeaker``'s factual reply.

    No turn calls the model today. The provider is kept for explicit
    conversation turns (backlog item 2), the only place model-written
    wording is planned to return.
    """

    def __init__(self, provider: LLMProvider | None = None) -> None:
        """Initialize the speaker.

        Args:
            provider: Kept for explicit conversation turns (backlog item
                2); not called by any turn today.
        """
        self._provider = provider

    def __call__(self, request: SpeakingRequest) -> str:
        """Compose a reply for ``request`` in the requested personality mode.

        Args:
            request: The request to compose a reply for.

        Returns:
            The rendered reply for a verified delivery; otherwise
            ``TemplateSpeaker``'s factual reply.
        """
        if not _verified_delivery(request):
            logger.info(
                "PersonalityVoice: no verified file was delivered this turn; "
                "using the factual reply"
            )
            return TemplateSpeaker()(request)
        return _verified_delivery_reply(request, file_style=FILE_STYLE_PLAIN, mode=request.mode)

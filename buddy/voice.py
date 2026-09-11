"""Voice — communicates results and status back to the user."""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from buddy.llm import LLMMessage, LLMProvider
from buddy.models import ExecutionResult, Understanding, VerificationResult
from buddy.state import BuddyState

logger = logging.getLogger(__name__)


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
        if request.verification_result is None:
            return self._describe_without_verification(request.execution_result)

        verification_result = request.verification_result
        finding = verification_result.findings[0] if verification_result.findings else ""

        if verification_result.passed:
            return self._success_message(request.execution_result, finding)
        return self._failure_message(verification_result, finding)

    @staticmethod
    def _success_message(execution_result: ExecutionResult | None, finding: str) -> str:
        """Compose the reply for a passed verification.

        Args:
            execution_result: The execution result, if any, to pull the
                output file name from.
            finding: Inspector's own plain-language finding, if any.

        Returns:
            The reply text.
        """
        parts = ["Done"]
        if finding:
            parts.append(f"— {finding}.")
        else:
            parts[0] = "Done."
        output_name = TemplateSpeaker._output_file_name(execution_result)
        if output_name:
            parts.append(f"Saved as `{output_name}`.")
        return " ".join(parts)

    @staticmethod
    def _failure_message(verification_result: VerificationResult, finding: str) -> str:
        """Compose the reply for a failed (or inconclusive) verification.

        Args:
            verification_result: The verification result.
            finding: Inspector's own plain-language finding, if any.

        Returns:
            The reply text.
        """
        parts = ["I wasn't able to confirm that worked."]
        if finding:
            parts.append(finding[0].upper() + finding[1:] + ".")
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
    def _output_file_name(execution_result: ExecutionResult | None) -> str | None:
        """Extract the first output file's name, if any.

        Args:
            execution_result: The execution result, if any.

        Returns:
            The output file's base name, or ``None`` if there isn't one.
        """
        if execution_result is None or not execution_result.outputs:
            return None
        return Path(execution_result.outputs[0]).name


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

Hard rules:
- Never invent, omit, or soften a fact you were given. If something
  failed, say it failed. The personality is in HOW you say it, never in
  WHAT you say.
- Never recite a full file path or a generated filename verbatim — no
  folder separators, no system-generated suffixes like
  "_manual-session-task-0". Refer to the file the way a person would:
  "the file," "your spreadsheet," or its plain original name with the
  generated suffix dropped.
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

Hard rules:
- Never invent, omit, or soften a fact you were given. If something
  failed, say it failed.
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
        return reply

    @staticmethod
    def _build_user_prompt(request: SpeakingRequest) -> str:
        """Build the user-turn prompt from ``request``.

        Only the same facts ``TemplateSpeaker`` already reads —
        ``understanding.intent``, ``execution_result``, and
        ``verification_result`` — are included.

        Args:
            request: The request to build a prompt from.

        Returns:
            The plain-text user prompt to send to the model.
        """
        lines: list[str] = []
        if request.understanding.intent:
            lines.append(f"User's request was classified as: {request.understanding.intent}")

        execution_result = request.execution_result
        if execution_result is None:
            lines.append("Execution: nothing was executed.")
        else:
            lines.append(f"Execution succeeded: {execution_result.success}")
            if execution_result.outputs:
                output_names = ", ".join(Path(o).name for o in execution_result.outputs)
                lines.append(f"Output file(s): {output_names}")
            if execution_result.warnings:
                lines.append("Execution warnings: " + "; ".join(execution_result.warnings))
            if execution_result.errors:
                lines.append("Execution errors: " + "; ".join(execution_result.errors))

        verification_result = request.verification_result
        if verification_result is None:
            lines.append("Verification: not performed.")
        else:
            lines.append(f"Verification passed: {verification_result.passed}")
            if verification_result.findings:
                lines.append(
                    "Verification findings: " + "; ".join(verification_result.findings)
                )
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

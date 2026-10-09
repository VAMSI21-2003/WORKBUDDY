"""Outcome — the facts of a delivered turn, rendered by code.

On a turn that delivered a verified file, the reply's statement of what
happened comes from here: an ``OutcomeReport`` built only from
``ExecutionResult``, ``VerificationResult`` (the verifiers' own
``StepMeasurement`` counts, and the residual counts measured on the
delivered file) and the requested-actions ledger
(``buddy.actions.requested_ledger``), and a deterministic renderer whose
wording is fixed in code. No model writes any of it, and nothing here reads
a Skill's self-reported summary, findings or metadata. See
``docs/design/verified-outcome-design.md`` Sections 2 and 3.

Each action in the report is one of:

- ``DONE``: a file-changing step for it ran and passed (``_step_passed``);
- ``NOT_DONE``: in the ledger, with no passing step, with a reason code
  that's true by how it's derived;
- ``DONE_UNREQUESTED``: a step ran and passed, but Brain's reading of the
  request doesn't have it and neither does the mention detector.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from buddy.actions import (
    ACTIONS_BY_CAPABILITY,
    ACTIONS_BY_NAME,
    ACTIONS_BY_TARGET,
    BRAIN_SOURCES,
    UNSUPPORTED,
    is_only_a_report_request,
    requested_ledger,
    strip_report_request,
)
from buddy.inspector import FINAL_OUTPUT_CAPABILITY
from buddy.models import (
    LEDGER_FROM_MENTION,
    SKIPPED_STATUS,
    ExecutionResult,
    OriginalFacts,
    RequestedAction,
    StepMeasurement,
    StepResult,
    StepVerification,
    Understanding,
    VerificationResult,
)
from buddy.text import count_noun

# -- Which steps count, and which passed ----------------------------------------------
# Shared with buddy.voice, which imports these; they live here so this module
# needs nothing from Voice.

_ACTION_TIERS = ("write", "external_effect")


def _describe_capability(capability: str) -> str:
    """Turn a capability id into plain words: ``"spreadsheet.deduplicate"`` -> ``"deduplicate"``."""
    return capability.rsplit(".", 1)[-1].replace("_", " ")


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


def _step_passed(
    step: StepResult, verification_result: VerificationResult | None
) -> bool:
    """Return whether ``step`` ran *and Inspector confirmed it* this turn.

    The one rule for "passed" everywhere in Voice — whether an action is
    reported as done, and whether any change was delivered. It's the
    strict reading: a step passes only when its Skill reported
    ``"success"`` *and* Inspector has a passing verdict for that same step.
    A step Inspector never checked (no verdict for it, or no Inspector at
    all) does not pass — never "passed unless contradicted", since that
    reading is what let an unchecked step count as done.

    When Inspector gave no per-step breakdown (``NullVerifier``, or a
    single non-composite ``Verifier``), its overall ``passed`` is the only
    verdict there is, so it's used for every step.

    Args:
        step: The step to check.
        verification_result: Inspector's verdict, if it ran.

    Returns:
        ``True`` if the step ran successfully and verification confirmed it.
    """
    if step.status != "success" or verification_result is None:
        return False
    if not verification_result.steps:
        return verification_result.passed
    return any(
        _verdict_is_for(verdict, step) and verdict.passed for verdict in verification_result.steps
    )


def _verdict_is_for(verdict: StepVerification, step: StepResult) -> bool:
    """Whether Inspector's ``verdict`` is about ``step``.

    Matched on capability, on whether it's a re-check of the final file,
    and — when both sides recorded one — on the file the step read, so the
    analysis of the original and its re-check on the final file are never
    mixed up.
    """
    if verdict.capability != step.capability or verdict.recheck != step.recheck:
        return False
    return (
        not verdict.input_reference
        or not step.input_reference
        or verdict.input_reference == step.input_reference
    )


# -- The report ------------------------------------------------------------------------

# An action's status (design Section 2.2).
DONE = "done"
NOT_DONE = "not_done"
DONE_UNREQUESTED = "done_unrequested"

# Why a requested action is NOT_DONE. Each is true by how it's derived.
NOT_PLANNED = "not_planned"  # Brain's reading has it; no step for its capability ran
NOT_READ_AS_REQUEST = "not_read_as_request"  # only the mention detector has it; no step ran
NO_SKILL = "no_skill"  # Brain listed it as "unsupported": no capability exists
# A step for it ran but didn't pass. Not reachable through a verified delivery
# from build_engine (a failing file-changing step fails the turn); kept so the
# sentence is never "didn't run" for a step that did run.
NOT_CONFIRMED = "not_confirmed"

# Lead words (design Section 2.3 and decision D2).
LEAD_DONE = "Done"
LEAD_PARTLY_DONE = "Partly done"

FILE_STYLE_GENERATED = "generated"  # TemplateSpeaker: "Saved as `name`."
FILE_STYLE_PLAIN = "plain"  # PersonalityVoice: "Your cleaned copy of report.xlsx is ready."

# The longest stretch of the user's own words a NO_SKILL line quotes.
_MAX_QUOTED_PHRASE = 80


@dataclass(frozen=True)
class ActionOutcome:
    """One action the reply states.

    Attributes:
        capability: The capability of the step or of the requested action,
            e.g. ``"spreadsheet.deduplicate"``; ``""`` for ``NO_SKILL``.
        status: ``DONE``, ``NOT_DONE`` or ``DONE_UNREQUESTED``.
        measurement: What the passing step's verifier measured, or ``None``
            if it measures nothing (the reply then states no count). Always
            ``None`` for ``NOT_DONE``.
        after_earlier_step: The step read an earlier step's output, not the
            user's file, so its counts are about what was left by then.
        reason: The ``NOT_DONE`` reason code; ``None`` otherwise.
        source: The ledger entry's source (``LEDGER_FROM_BRAIN``,
            ``LEDGER_FROM_INTENT`` or ``LEDGER_FROM_MENTION``), or ``""``
            when the action isn't in the ledger: a ``DONE_UNREQUESTED``
            step, or a ``DONE`` step on a turn where Brain gave no reading
            to compare it with (then it's neither confirmed as requested
            nor stated as unrequested, and the lead word is withheld).
        user_phrase: ``NO_SKILL`` only: the user's own words for it,
            sanitised, or ``""`` when Brain's phrase isn't in the message
            (then the reply quotes nothing).
    """

    capability: str
    status: str = DONE
    measurement: StepMeasurement | None = None
    after_earlier_step: bool = False
    reason: str | None = None
    source: str = ""
    user_phrase: str = ""


@dataclass(frozen=True)
class OutcomeReport:
    """Everything the outcome block states, from execution, verifier and ledger data.

    Attributes:
        actions: In the order the reply states them: ``DONE`` actions in
            the order their steps ran, then ``DONE_UNREQUESTED`` ones in the
            order they ran, then ``NOT_DONE`` ones in ledger order (the
            user's order, Brain's entries before mention-only ones).
        brain_reading: How Brain read the request, as plain requests
            ("remove empty rows"), for the ``NOT_READ_AS_REQUEST`` wording.
            Supported actions only, in ledger order.
        final_state: The verifier's counts for the delivered file: the
            final-file check's measurements for a chain, or the last step's
            own measurement when that step's output is the delivered file.
        residuals: Counts measured on the delivered file for the targets of
            ``NOT_DONE`` actions (``VerificationResult.residuals``), in
            ledger order.
        order_used: The sentence saying which order the steps ran in, when
            the order the user stated couldn't be confirmed.
        delivered_file: The name of the user's original file.
        output_names: The generated names of the files handed back.
        open_findings: How many analysis findings the reply asks about.
            Catalog predicates read it so a closer never asks a second
            question.
        original_facts: What the Inspector read in the user's original that
            the values-only copy can't keep (``VerificationResult
            .original_facts``), or ``None`` if nothing was measured. The
            formulas sentence is built from it and from nothing else.
    """

    actions: tuple[ActionOutcome, ...]
    brain_reading: tuple[str, ...] = ()
    final_state: tuple[StepMeasurement, ...] = ()
    residuals: tuple[StepMeasurement, ...] = ()
    order_used: str | None = None
    delivered_file: str | None = None
    output_names: tuple[str, ...] = ()
    open_findings: int = 0
    original_facts: OriginalFacts | None = None

    @property
    def request_complete(self) -> bool:
        """Every action is ``DONE`` and confirmed as requested by Brain's reading.

        False for an empty ledger, for any ``NOT_DONE`` or
        ``DONE_UNREQUESTED`` action, and for a ``DONE`` action Brain's
        reading doesn't confirm (no ledger entry, or only a mention-detector
        entry: the detector matches "don't remove duplicates" as well as
        "remove duplicates", so it can never confirm a request). Catalog
        entries that imply the request is finished require this.
        """
        return bool(self.actions) and all(
            action.status == DONE and action.source in BRAIN_SOURCES for action in self.actions
        )

    @property
    def lead_word(self) -> str | None:
        """``"Done"``, ``"Partly done"`` or ``None`` (design 2.3 and decision D2).

        - ``"Done"`` only when ``request_complete``.
        - ``"Partly done"`` when at least one action Brain's reading has is
          ``DONE`` and at least one is ``NOT_DONE``. An action only the
          mention detector found never produces it (D2), and never counts as
          done for it: it only keeps "Done" away.
        - Otherwise no lead word.
        """
        if self.request_complete:
            return LEAD_DONE
        requested_done = any(a.status == DONE and a.source in BRAIN_SOURCES for a in self.actions)
        brain_not_done = any(
            a.status == NOT_DONE and a.source in BRAIN_SOURCES for a in self.actions
        )
        if requested_done and brain_not_done:
            return LEAD_PARTLY_DONE
        return None


def build_outcome_report(
    execution_result: ExecutionResult | None,
    verification_result: VerificationResult | None,
    understanding: Understanding | None,
    *,
    order_used: str | None = None,
    delivered_file: str | None = None,
    open_findings: int = 0,
    report_stated: bool = False,
) -> OutcomeReport:
    """Build the report for a turn that delivered a verified file.

    Only call it for such a turn (``buddy.voice._verified_delivery``): every
    passing step it lists is stated as having run.

    Derivation (design Section 2.2), with the ledger from
    ``buddy.actions.requested_ledger``:

    - A step that passed is ``DONE`` if Brain's reading has its capability;
      ``DONE_UNREQUESTED`` if it doesn't and Brain gave a reading of the
      request (a mention-detector entry doesn't count as Brain's reading);
      otherwise ``DONE`` with a non-Brain source (nothing to compare it
      with), which never leads to "Done".
    - A ledger action with no passing step is ``NOT_DONE``: ``NO_SKILL`` if
      Brain listed it as ``"unsupported"`` (unless its phrase only names an
      action that has a capability; see ``_no_skill_outcome``),
      ``NOT_CONFIRMED`` if a step for it ran but didn't pass,
      ``NOT_READ_AS_REQUEST`` if only the mention detector has it, and
      ``NOT_PLANNED`` if Brain's reading has it.

    Args:
        execution_result: What Hands ran.
        verification_result: Inspector's verdict, with its measurements and
            the residual counts.
        understanding: Brain's understanding, carrying the ledger.
        order_used: The order-used sentence, if the order wasn't confirmed.
        delivered_file: The original file's name.
        open_findings: How many analysis findings the reply asks about.
        report_stated: The reply states a requested report itself, delivered or
            not available (``buddy.voice``), so the part of an ``unsupported``
            entry that only asks for a report is not also stated as "can't do".

    Returns:
        The ``OutcomeReport``.
    """
    ledger = requested_ledger(understanding or Understanding())
    # "Brain's reading" is what a step is compared with; "unsupported" entries are
    # not actions a step could be, so they alone give nothing to compare with.
    has_reading = any(
        entry.source in BRAIN_SOURCES and entry.action in ACTIONS_BY_NAME for entry in ledger
    )
    ledger_by_capability = {
        ACTIONS_BY_NAME[entry.action].capability: entry
        for entry in reversed(ledger)  # the first entry for a capability wins
        if entry.action in ACTIONS_BY_NAME
    }

    steps = execution_result.steps if execution_result is not None else ()
    action_steps = [s for s in steps if _is_action_step(s)]
    # Hands chains action steps: each after the first reads the previous
    # step's output.
    passed: list[tuple[StepResult, ActionOutcome]] = []
    for index, step in enumerate(action_steps):
        if not _step_passed(step, verification_result):
            continue
        entry = ledger_by_capability.get(step.capability)
        if entry is not None and entry.source in BRAIN_SOURCES:
            status, source = DONE, entry.source
        elif has_reading:
            # Brain's reading doesn't have it. A mention entry doesn't change
            # that: the detector also matches a negation ("don't remove
            # duplicates"), so it can only add to the not-done side.
            status, source = DONE_UNREQUESTED, ""
        else:
            # No reading to compare with: neither confirmed nor called
            # unrequested, and never "Done".
            status, source = DONE, entry.source if entry is not None else ""
        passed.append(
            (
                step,
                ActionOutcome(
                    capability=step.capability,
                    status=status,
                    measurement=_measurement_for(step, verification_result, len(action_steps)),
                    after_earlier_step=index > 0,
                    source=source,
                ),
            )
        )

    passed_capabilities = {step.capability for step, _ in passed}
    ran_capabilities = {step.capability for step in action_steps}
    not_done: list[ActionOutcome] = []
    said_no_skill: set[str] = set()
    for entry in ledger:
        if entry.action == UNSUPPORTED:
            outcome = _no_skill_outcome(entry, ledger, passed_capabilities, report_stated)
            if outcome is None or outcome.user_phrase.casefold() in said_no_skill:
                continue
            said_no_skill.add(outcome.user_phrase.casefold())
            not_done.append(outcome)
            continue
        capability = ACTIONS_BY_NAME[entry.action].capability
        if capability in passed_capabilities:
            continue
        if capability in ran_capabilities:
            reason = NOT_CONFIRMED
        elif entry.source == LEDGER_FROM_MENTION:
            reason = NOT_READ_AS_REQUEST
        else:
            reason = NOT_PLANNED
        not_done.append(ActionOutcome(capability, NOT_DONE, reason=reason, source=entry.source))

    outcomes = [outcome for _, outcome in passed]
    actions = (
        *(o for o in outcomes if o.status == DONE),
        *(o for o in outcomes if o.status == DONE_UNREQUESTED),
        *not_done,
    )
    outputs = execution_result.outputs if execution_result is not None else ()
    return OutcomeReport(
        actions=actions,
        brain_reading=tuple(
            ACTIONS_BY_NAME[entry.action].request
            for entry in ledger
            if entry.source in BRAIN_SOURCES and entry.action in ACTIONS_BY_NAME
        ),
        final_state=_final_state(execution_result, verification_result, passed),
        residuals=_residuals(verification_result, not_done),
        order_used=order_used,
        delivered_file=delivered_file,
        output_names=tuple(Path(output).name for output in outputs),
        open_findings=open_findings,
        original_facts=(
            verification_result.original_facts if verification_result is not None else None
        ),
    )


# RULE for this set: an "unsupported" entry is dropped only when, once the action's
# mention patterns (with a removal verb and determiners in front) and the words Brain
# quoted for a done action are removed, nothing is left or only these remain. They are
# function words: articles, politeness, connectives, pronouns, auxiliaries and negation.
# NEVER add a content word here (no verb like "remove", "delete" or "drop", no noun like
# "file", "sheet" or "data"): anything else left over is a part of the request we may not
# have done, and the reply says so. A verb belongs in the removal-verb list below,
# attached to an action's object. When unsure, keep the not-done sentence.
_FILLER_WORDS = frozenset(
    "a an the please pls and then also just only so but can could would will you i we to do "
    "dont don't not no never keep leave".split()
)
# Words that say "don't do it" or "leave it". With a done action they may be the very
# request that was not carried out (an inverted reading), so the entry is kept then.
_NEGATION_WORDS = frozenset("dont don't not no never keep leave".split())
# A removal verb and determiners in front of an action's object belong to that action.
_REMOVAL_VERBS = r"remove|delete|drop|clear|erase|eliminate|get\s+rid\s+of|take\s+out"
_DETERMINERS = r"all|any|every|each|the|these|those|this|that|my|our|your|of"
_ACTION_WITH_VERB = {
    action.name: re.compile(
        rf"(?:\b(?:{_REMOVAL_VERBS})\s+(?:(?:{_DETERMINERS})\s+)*)?(?:{action.mention.pattern})",
        re.IGNORECASE,
    )
    for action in ACTIONS_BY_NAME.values()
}
# The location strip must never take a phrase that names more than the one sheet the
# skills read: no "across", no "workbook", no "every", no "all". Brain flagging
# "remove duplicates across the workbook" as unsupported is a correct flag, because the
# skills touch only the first sheet; stripping it would turn "Partly done" into "Done".
_LOCATION_PHRASE = re.compile(
    r"\b(?:from|in|on)\s+(?:(?:the|this|that|my|our|your)\s+)?"
    r"(?:sheet|file|spreadsheet|table|data)\b"
)
_TWO_REQUESTS = re.compile(r"\b(?:and|then|also|plus)\b|[,;&+]")


def _no_skill_outcome(
    entry: RequestedAction,
    ledger: tuple[RequestedAction, ...],
    passed_capabilities: set[str],
    report_stated: bool = False,
) -> ActionOutcome | None:
    """The ``NO_SKILL`` outcome for an ``unsupported`` entry, or ``None`` to say nothing.

    Brain's "unsupported" label and phrase boundaries are a model's, so a
    slip (a whole-message phrase, a near-miss action name) can hang it on
    words that name an action Buddy does have. "I can't do this part yet"
    would then sit next to a sentence saying it was done. So when the phrase
    names an action (the mention detector's pattern, with a removal verb and
    determiners in front, or the words Brain quoted for an action that passed):

    - the sentence is the generic, unquoted one (a quote would include the
      named action) if a word is left over once those words and the function
      words in ``_FILLER_WORDS`` are removed, or if a negation/keep word is
      present next to an action that ran (the request may be the opposite of
      what was done), or if a named action didn't pass and has no ledger
      entry of its own (nothing else will say it wasn't done), unless a
      negation word is present;
    - otherwise the entry is dropped: the action has its own sentence (or
      none, when it ran and was said).

    Residual risks (both need a gross misread by Brain; closing them needs the
    ledger-recall measurement, backlog item 1, design 6.8, not more words):

    - A done action's phrase with no joiner that contains an unsupported verb
      (Brain gives "delete the file" as the dedupe phrase) is still dropped.
    - A double negation in a paraphrase ("don't keep the empty rows") counts
      as a negation.
    """
    phrase = entry.source_phrase
    if report_stated and is_only_a_report_request(phrase):
        # The reply states the report itself (delivered or why not), so a phrase that only asks
        # for one is not also "something I can't do". The same predicate the planner uses.
        return None
    lowered = phrase.casefold().replace("\u2019", "'")
    names_action = named_passed = named_silent = False
    for action in ACTIONS_BY_NAME.values():
        remaining = _ACTION_WITH_VERB[action.name].sub(" ", lowered)
        if remaining == lowered:
            continue
        names_action, lowered = True, remaining
        if action.capability in passed_capabilities:
            named_passed = True
        elif all(other.action != action.name for other in ledger):
            named_silent = True
    if report_stated:
        # A report request is stated by the reply itself (delivered or not available), so it
        # is "named" and "done" like an action that passed: what's left decides the sentence.
        lowered, had_report = strip_report_request(lowered)
        if had_report:
            names_action = named_passed = True
    lowered = _LOCATION_PHRASE.sub(" ", lowered, count=1)
    for other in ledger:
        other_action = ACTIONS_BY_NAME.get(other.action)
        if other_action is None or other_action.capability not in passed_capabilities:
            continue
        said = other.source_phrase.casefold().replace("\u2019", "'").strip()
        if not said or said not in lowered:
            continue
        names_action = True
        if not _TWO_REQUESTS.search(said):
            named_passed, lowered = True, lowered.replace(said, " ")
    if not names_action:
        quoted = _quoted_phrase(phrase) if entry.phrase_found else ""
        return ActionOutcome("", NOT_DONE, reason=NO_SKILL, source=entry.source, user_phrase=quoted)
    words = re.findall(r"[\w']+", lowered)
    negated = any(w in _NEGATION_WORDS for w in words)
    if (
        any(w not in _FILLER_WORDS for w in words)
        or (negated and named_passed)
        or (named_silent and not negated)
    ):
        return ActionOutcome("", NOT_DONE, reason=NO_SKILL, source=entry.source)
    return None


def _quoted_phrase(phrase: str) -> str:
    """The user's words, safe to quote: one line, no backticks or quotes, at most 80 characters."""
    cleaned = " ".join(re.sub(r"[`\"“”]", " ", phrase).split())
    if len(cleaned) > _MAX_QUOTED_PHRASE:
        cleaned = cleaned[: _MAX_QUOTED_PHRASE - 1].rstrip() + "…"
    return cleaned


def _residuals(
    verification_result: VerificationResult | None, not_done: list[ActionOutcome]
) -> tuple[StepMeasurement, ...]:
    """The residual counts for the targets of ``NOT_DONE`` actions, in ledger order."""
    if verification_result is None:
        return ()
    residuals: list[StepMeasurement] = []
    for outcome in not_done:
        action = ACTIONS_BY_CAPABILITY.get(outcome.capability)
        if action is None:
            continue
        found = _first_for_target(verification_result.residuals, action.target)
        if found is not None and found not in residuals:
            residuals.append(found)
    return tuple(residuals)


def _measurement_for(
    step: StepResult, verification_result: VerificationResult | None, action_count: int
) -> StepMeasurement | None:
    """The measurement from the passing verdict for ``step``, if any."""
    action = ACTIONS_BY_CAPABILITY.get(step.capability)
    if action is None or verification_result is None:
        return None
    if not verification_result.steps:
        # A single verifier used directly: its measurements can only be
        # attributed to a step when there's exactly one action step.
        if action_count != 1:
            return None
        return _first_for_target(verification_result.measurements, action.target)
    for verdict in verification_result.steps:
        if _verdict_is_for(verdict, step) and verdict.passed:
            found = _first_for_target(verdict.measurements, action.target)
            if found is not None:
                return found
    return None


def _first_for_target(
    measurements: tuple[StepMeasurement, ...], target: str
) -> StepMeasurement | None:
    return next((m for m in measurements if m.target == target), None)


def _final_state(
    execution_result: ExecutionResult | None,
    verification_result: VerificationResult | None,
    passed: list[tuple[StepResult, ActionOutcome]],
) -> tuple[StepMeasurement, ...]:
    """The verifier's counts for the delivered file, or ``()`` if there are none.

    ``passed`` is every step that passed, with its outcome, in the order they ran.
    """
    if execution_result is None or not execution_result.outputs or verification_result is None:
        return ()
    for verdict in verification_result.steps:
        if verdict.capability == FINAL_OUTPUT_CAPABILITY:
            return verdict.measurements if verdict.passed else ()
    # No final-file check: only the last step's own measurement can describe
    # the delivered file, and only if its output is that file.
    if not passed:
        return ()
    last_step, last_action = passed[-1]
    if last_action.measurement is None or last_step.outputs[:1] != execution_result.outputs[:1]:
        return ()
    return (last_action.measurement,)


# -- Rendering ---------------------------------------------------------------------------


def render_outcome(report: OutcomeReport, file_style: str) -> str:
    """Render the outcome block. Deterministic; every sentence comes from one field.

    In order: what was done (the lead word, if any, on the first sentence),
    what was done that the ledger doesn't have, what wasn't done and why,
    the delivered file's counts, what's still in it for actions that weren't
    done, the order used (when it wasn't confirmed), and the file.

    Args:
        report: The report to render.
        file_style: ``FILE_STYLE_GENERATED`` (name the generated file) or
            ``FILE_STYLE_PLAIN`` (a cleaned copy of the original).

    Returns:
        The outcome block.
    """
    done = [a for a in report.actions if a.status == DONE]
    sentences = [_action_sentence(action) for action in done]
    lead = report.lead_word
    if lead is not None and sentences:
        first = sentences[0]
        sentences[0] = f"{lead} — {first[0].lower()}{first[1:]}"
    unrequested = [a for a in report.actions if a.status == DONE_UNREQUESTED]
    for index, action in enumerate(unrequested):
        sentences.append(_unrequested_sentence(action, also=bool(done) or index > 0))
    sentences.extend(not_done_sentences(report))
    final_state = _final_state_sentence(report.final_state)
    if final_state:
        sentences.append(final_state)
    sentences.extend(_residual_sentences(report.residuals, after_final_state=bool(final_state)))
    if report.order_used:
        sentences.append(report.order_used)
    file_sentence = _file_sentence(report, file_style)
    if file_sentence:
        sentences.append(file_sentence)
    # Said whenever a file was handed back (this block is only rendered for a
    # verified delivery), right after the file sentence: the copy is values only,
    # whatever the original held.
    sentences.extend(_values_only_sentences(report.original_facts))
    return " ".join(sentences)


def not_done_sentences(report: OutcomeReport, *, nothing_done: bool = False) -> list[str]:
    """The sentences for the report's ``NOT_DONE`` actions, with how Brain read the request.

    Also used by a turn that changed nothing, where every action in the ledger is one of
    these. ``nothing_done`` then says "that" and not "this part" for what Buddy can't do.
    """
    not_done = [a for a in report.actions if a.status == NOT_DONE]
    last_unread = max(
        (i for i, a in enumerate(not_done) if a.reason == NOT_READ_AS_REQUEST), default=None
    )
    sentences = []
    for index, action in enumerate(not_done):
        sentences.append(_not_done_sentence(action, nothing_done=nothing_done))
        if index == last_unread and report.brain_reading:
            sentences.append(f"I read your request as: {' and '.join(report.brain_reading)}.")
    return sentences


def _removed_clause(measurement: StepMeasurement, row_noun: str) -> str:
    """ "removed 2 duplicate rows, including 1 repeated blank row" """
    clause = f"removed {count_noun(measurement.removed or 0, row_noun)}"
    blank = measurement.removed_blank
    if blank:
        clause += f", including {count_noun(blank, 'repeated blank row')}"
    return clause


def _action_sentence(action: ActionOutcome) -> str:
    """The sentence for a ``DONE`` action."""
    wording = ACTIONS_BY_CAPABILITY.get(action.capability)
    measurement = action.measurement
    if wording is None:
        return f"The {_describe_capability(action.capability)} step ran and passed its check."
    if measurement is None or measurement.removed is None:
        return f"The {wording.step} ran and passed its check."
    if measurement.removed == 0:
        # A later step's input is an earlier step's output: say "left", so
        # the sentence can't be read as being about the user's file.
        left = " left" if action.after_earlier_step else ""
        return f"There were no {wording.rows}{left} to remove."
    clause = _removed_clause(measurement, wording.row_noun)
    return clause[0].upper() + clause[1:] + "."


_NOT_IN_READING = "though I didn't read that as part of your request"


def _unrequested_sentence(action: ActionOutcome, *, also: bool) -> str:
    """The sentence for a ``DONE_UNREQUESTED`` action.

    It says how Buddy read the request, never what the user wanted.
    """
    subject = "I also" if also else "I"
    wording = ACTIONS_BY_CAPABILITY.get(action.capability)
    measurement = action.measurement
    step = wording.step if wording else f"{_describe_capability(action.capability)} step"
    if wording is None or measurement is None or measurement.removed is None:
        return f"{subject} ran the {step}, {_NOT_IN_READING}; it passed its check."
    if measurement.removed == 0:
        left = " left" if action.after_earlier_step else ""
        return (
            f"{subject} ran the {step}, {_NOT_IN_READING}; "
            f"there were no {wording.rows}{left} to remove."
        )
    return f"{subject} {_removed_clause(measurement, wording.row_noun)}, {_NOT_IN_READING}."


def _not_done_sentence(action: ActionOutcome, *, nothing_done: bool = False) -> str:
    """The sentence for a ``NOT_DONE`` action, from its reason code.

    Each one says what Buddy didn't run, never that rows weren't removed:
    another step may have removed some (dedupe removes repeated blank rows).
    """
    if action.reason == NO_SKILL:
        if action.user_phrase:
            what = "that" if nothing_done else "this part"
            return f"I can't do {what} yet: “{action.user_phrase}”."
        if nothing_done:
            return "I read your request as something I can't do yet."
        return "I read part of your request as something I can't do yet."
    wording = ACTIONS_BY_CAPABILITY[action.capability]
    if action.reason == NOT_READ_AS_REQUEST:
        return f"Your message mentions {wording.object}, but I didn't run the {wording.step}."
    if action.reason == NOT_CONFIRMED:
        return f"I couldn't confirm the {wording.step}: its check didn't pass."
    return f"I didn't run the {wording.step}, though I read it as part of your request."


def _residual_sentences(
    residuals: tuple[StepMeasurement, ...], *, after_final_state: bool
) -> list[str]:
    """What's still in the delivered file for requested actions that weren't done."""
    sentences = []
    for measurement in residuals:
        wording = ACTIONS_BY_TARGET.get(measurement.target)
        if wording is None:
            continue
        subject = "It" if after_final_state or sentences else "The copy"
        if measurement.remaining:
            sentences.append(
                f"{subject} still has {count_noun(measurement.remaining, wording.row_noun)}."
            )
        else:
            sentences.append(f"{subject} has no {wording.rows}.")
    return sentences


def _final_state_sentence(final_state: tuple[StepMeasurement, ...]) -> str:
    measured = [m for m in final_state if m.target in ACTIONS_BY_TARGET]
    if not measured or len({m.output_rows for m in measured}) != 1:
        return ""
    remaining = [count_noun(m.remaining, ACTIONS_BY_TARGET[m.target].row_noun) for m in measured]
    listed = remaining[-1]
    if len(remaining) > 1:
        listed = ", ".join(remaining[:-1]) + " and " + listed
    return f"The copy has {count_noun(measured[0].output_rows, 'row')}, with {listed} remaining."


# What a delivered copy doesn't keep (``docs/design/values-only-copy.md``, Section 2).
# The skills write values only, so the first is true of every copy they deliver:
# whether the original had formatting can't be measured reliably, so it isn't asked.
VALUES_ONLY_SENTENCE = (
    "The copy has the values only; formatting, column widths and other sheet settings "
    "aren't carried over."
)
FORMULAS_UNCHECKED_SENTENCE = (
    "I couldn't check whether your file has formulas; if it does, the copy has their "
    "last saved values, not the formulas."
)


def _values_only_sentences(original_facts: OriginalFacts | None) -> list[str]:
    """What the copy doesn't keep: always the values-only sentence, the formulas one if measured.

    The formulas sentences come only from the Inspector's own read of the
    original (``OriginalFacts``): none when it has no formulas or nothing was
    measured, and a plain "couldn't check" when the read failed.
    """
    sentences = [VALUES_ONLY_SENTENCE]
    if original_facts is None:
        return sentences
    formulas = original_facts.formula_cells
    if formulas is None:
        sentences.append(FORMULAS_UNCHECKED_SENTENCE)
    elif formulas:
        unsaved = original_facts.formula_cells_without_value
        if formulas == 1:
            sentences.append(
                "Your file has 1 formula cell. The copy has its last saved value, not the formula."
            )
            if unsaved:
                sentences.append(
                    "That cell had no saved value, so it's empty in the copy, and rows were "
                    "judged with it empty."
                )
        else:
            sentences.append(
                f"Your file has {formulas} formula cells. The copy has their last saved values, "
                "not the formulas."
            )
            if unsaved == 1:
                sentences.append(
                    "1 of them had no saved value, so it's empty in the copy, and rows were "
                    "judged with that cell empty."
                )
            elif unsaved:
                sentences.append(
                    f"{unsaved} of them had no saved value, so they're empty in the copy, and "
                    "rows were judged with those cells empty."
                )
    return sentences


def _file_sentence(report: OutcomeReport, file_style: str) -> str:
    if file_style == FILE_STYLE_PLAIN:
        if report.delivered_file is None:
            return ""
        return f"Your cleaned copy of {report.delivered_file} is ready."
    if not report.output_names:
        return ""
    return "Saved as " + ", ".join(f"`{name}`" for name in report.output_names) + "."

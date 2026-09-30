"""Outcome — the facts of a delivered turn, rendered by code.

On a turn that delivered a verified file, the reply's statement of what
happened comes from here: an ``OutcomeReport`` built only from
``ExecutionResult`` and ``VerificationResult`` (the verifiers' own
``StepMeasurement`` counts), and a deterministic renderer whose wording is
fixed in code. No model writes any of it, and nothing here reads a
Skill's self-reported summary, findings or metadata. See
``docs/design/verified-outcome-design.md`` Section 2.

This is PR-A of that design: there is no ledger of requested actions yet,
so every action in the report is ``DONE`` (a file-changing step that ran
and passed), and the "Done —" lead word is decided from Brain's single
classified intent (see ``OutcomeReport.lead_word``).
"""

from dataclasses import dataclass
from pathlib import Path

from buddy.inspector import FINAL_OUTPUT_CAPABILITY
from buddy.models import (
    DUPLICATE_ROWS_TARGET,
    EMPTY_ROWS_TARGET,
    SKIPPED_STATUS,
    ExecutionResult,
    StepMeasurement,
    StepResult,
    StepVerification,
    VerificationResult,
)
from buddy.planner import _INTENT_CAPABILITIES

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


# -- Plain wording for each action ----------------------------------------------------


@dataclass(frozen=True)
class _Wording:
    target: str  # the StepMeasurement.target this action's verifier measures
    rows: str  # "duplicate rows"
    counted: str  # "duplicate row(s)"
    step: str  # "duplicate-row removal"


_DUPLICATE_ROWS = _Wording(
    DUPLICATE_ROWS_TARGET, "duplicate rows", "duplicate row(s)", "duplicate-row removal"
)
_EMPTY_ROWS = _Wording(
    EMPTY_ROWS_TARGET, "fully-empty rows", "fully-empty row(s)", "empty-row removal"
)

_WORDING_BY_CAPABILITY: dict[str, _Wording] = {
    "spreadsheet.deduplicate": _DUPLICATE_ROWS,
    "spreadsheet.remove_empty_rows": _EMPTY_ROWS,
}
_WORDING_BY_TARGET: dict[str, _Wording] = {w.target: w for w in _WORDING_BY_CAPABILITY.values()}


# -- The report ------------------------------------------------------------------------

# An action's status. PR-A has no ledger, so only DONE exists; NOT_DONE and
# DONE_UNREQUESTED arrive with it (design Section 2.2).
DONE = "done"

FILE_STYLE_GENERATED = "generated"  # TemplateSpeaker: "Saved as `name`."
FILE_STYLE_PLAIN = "plain"  # PersonalityVoice: "Your cleaned copy of report.xlsx is ready."


@dataclass(frozen=True)
class ActionOutcome:
    """One action the reply states.

    Attributes:
        capability: The capability of the step, e.g. ``"spreadsheet.deduplicate"``.
        status: ``DONE``: a file-changing step for it ran and passed
            (``_step_passed``) on a turn that delivered a verified file.
        measurement: What that step's verifier measured, or ``None`` if it
            measures nothing (the reply then states no count).
        after_earlier_step: The step read an earlier step's output, not the
            user's file, so its counts are about what was left by then.
    """

    capability: str
    status: str = DONE
    measurement: StepMeasurement | None = None
    after_earlier_step: bool = False


@dataclass(frozen=True)
class OutcomeReport:
    """Everything the outcome block states, from execution and verifier data.

    Attributes:
        actions: Every file-changing step that ran and passed, in the order
            it ran.
        requested: The capabilities Brain's classified intent asked for:
            one for a single-action intent, none for ``multi_step_request``,
            ``unclear`` or no intent. Used only for the lead word.
        final_state: The verifier's counts for the delivered file: the
            final-file check's measurements for a chain, or the last step's
            own measurement when that step's output is the delivered file.
        order_used: The sentence saying which order the steps ran in, when
            the order the user stated couldn't be confirmed.
        delivered_file: The name of the user's original file.
        output_names: The generated names of the files handed back.
        open_findings: How many analysis findings the reply asks about.
            Catalog predicates read it so a closer never asks a second
            question.
    """

    actions: tuple[ActionOutcome, ...]
    requested: tuple[str, ...] = ()
    final_state: tuple[StepMeasurement, ...] = ()
    order_used: str | None = None
    delivered_file: str | None = None
    output_names: tuple[str, ...] = ()
    open_findings: int = 0

    @property
    def lead_word(self) -> str | None:
        """``"Done"`` when every requested action is done and nothing else was.

        With no ledger yet (PR-A), "requested" is Brain's single
        classified intent. A ``multi_step_request`` turn therefore never
        gets "Done" until the ledger can say every requested part ran.
        """
        if not self.requested or not self.actions:
            return None
        if any(action.status != DONE for action in self.actions):
            return None
        if {action.capability for action in self.actions} != set(self.requested):
            return None
        return "Done"


def build_outcome_report(
    execution_result: ExecutionResult | None,
    verification_result: VerificationResult | None,
    intent: str | None,
    *,
    order_used: str | None = None,
    delivered_file: str | None = None,
    open_findings: int = 0,
) -> OutcomeReport:
    """Build the report for a turn that delivered a verified file.

    Only call it for such a turn (``buddy.voice._verified_delivery``):
    every action it lists is stated as done.

    Args:
        execution_result: What Hands ran.
        verification_result: Inspector's verdict, with its measurements.
        intent: Brain's classified intent.
        order_used: The order-used sentence, if the order wasn't confirmed.
        delivered_file: The original file's name.
        open_findings: How many analysis findings the reply asks about.

    Returns:
        The ``OutcomeReport``.
    """
    steps = execution_result.steps if execution_result is not None else ()
    action_steps = [s for s in steps if _is_action_step(s)]
    # Hands chains action steps: each after the first reads the previous
    # step's output.
    actions = tuple(
        ActionOutcome(
            capability=step.capability,
            status=DONE,
            measurement=_measurement_for(step, verification_result, len(action_steps)),
            after_earlier_step=index > 0,
        )
        for index, step in enumerate(action_steps)
        if _step_passed(step, verification_result)
    )
    capability = _INTENT_CAPABILITIES.get(intent) if intent else None
    outputs = execution_result.outputs if execution_result is not None else ()
    return OutcomeReport(
        actions=actions,
        requested=(capability,) if capability is not None else (),
        final_state=_final_state(execution_result, verification_result, actions),
        order_used=order_used,
        delivered_file=delivered_file,
        output_names=tuple(Path(output).name for output in outputs),
        open_findings=open_findings,
    )


def _measurement_for(
    step: StepResult, verification_result: VerificationResult | None, action_count: int
) -> StepMeasurement | None:
    """The measurement from the passing verdict for ``step``, if any."""
    wording = _WORDING_BY_CAPABILITY.get(step.capability)
    if wording is None or verification_result is None:
        return None
    if not verification_result.steps:
        # A single verifier used directly: its measurements can only be
        # attributed to a step when there's exactly one action step.
        if action_count != 1:
            return None
        return _first_for_target(verification_result.measurements, wording.target)
    for verdict in verification_result.steps:
        if _verdict_is_for(verdict, step) and verdict.passed:
            found = _first_for_target(verdict.measurements, wording.target)
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
    actions: tuple[ActionOutcome, ...],
) -> tuple[StepMeasurement, ...]:
    """The verifier's counts for the delivered file, or ``()`` if there are none."""
    if execution_result is None or not execution_result.outputs or verification_result is None:
        return ()
    for verdict in verification_result.steps:
        if verdict.capability == FINAL_OUTPUT_CAPABILITY:
            return verdict.measurements if verdict.passed else ()
    # No final-file check: only the last step's own measurement can describe
    # the delivered file, and only if its output is that file.
    delivered = execution_result.outputs[0]
    passed_steps = [
        s
        for s in execution_result.steps
        if _is_action_step(s) and _step_passed(s, verification_result)
    ]
    if not passed_steps or not actions:
        return ()
    last_step, last_action = passed_steps[-1], actions[-1]
    if last_action.measurement is None or last_step.outputs[:1] != (delivered,):
        return ()
    return (last_action.measurement,)


# -- Rendering ---------------------------------------------------------------------------


def render_outcome(report: OutcomeReport, file_style: str) -> str:
    """Render the outcome block. Deterministic; every sentence comes from one field.

    Args:
        report: The report to render.
        file_style: ``FILE_STYLE_GENERATED`` (name the generated file) or
            ``FILE_STYLE_PLAIN`` (a cleaned copy of the original).

    Returns:
        The outcome block: the lead word and one sentence per action, the
        delivered file's counts, the order used (when it wasn't confirmed),
        and the file.
    """
    sentences = [_action_sentence(action) for action in report.actions]
    lead = report.lead_word
    if lead is not None and sentences:
        first = sentences[0]
        sentences[0] = f"{lead} — {first[0].lower()}{first[1:]}"
    final_state = _final_state_sentence(report.final_state)
    if final_state:
        sentences.append(final_state)
    if report.order_used:
        sentences.append(report.order_used)
    file_sentence = _file_sentence(report, file_style)
    if file_sentence:
        sentences.append(file_sentence)
    return " ".join(sentences)


def _action_sentence(action: ActionOutcome) -> str:
    wording = _WORDING_BY_CAPABILITY.get(action.capability)
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
    sentence = f"Removed {measurement.removed} {wording.counted}"
    blank = measurement.removed_blank
    if blank:
        sentence += f", including {blank} repeated blank row{'' if blank == 1 else 's'}"
    return sentence + "."


def _final_state_sentence(final_state: tuple[StepMeasurement, ...]) -> str:
    measured = [m for m in final_state if m.target in _WORDING_BY_TARGET]
    if not measured or len({m.output_rows for m in measured}) != 1:
        return ""
    remaining = [f"{m.remaining} {_WORDING_BY_TARGET[m.target].counted}" for m in measured]
    listed = remaining[-1]
    if len(remaining) > 1:
        listed = ", ".join(remaining[:-1]) + " and " + listed
    return f"The copy has {measured[0].output_rows} row(s), {listed} remaining."


def _file_sentence(report: OutcomeReport, file_style: str) -> str:
    if file_style == FILE_STYLE_PLAIN:
        if report.delivered_file is None:
            return ""
        return f"Your cleaned copy of {report.delivered_file} is ready."
    if not report.output_names:
        return ""
    return "Saved as " + ", ".join(f"`{name}`" for name in report.output_names) + "."

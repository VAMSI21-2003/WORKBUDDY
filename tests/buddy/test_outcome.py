"""Unit tests for buddy.outcome: the report and the renderer."""

from __future__ import annotations

import itertools
import random
import re
from pathlib import Path

import pandas as pd
import pytest

from buddy.actions import UNSUPPORTED
from buddy.inspector import (
    CompositeVerifier,
    SpreadsheetDedupeVerifier,
    SpreadsheetEmptyRowsVerifier,
    VerificationRequest,
)
from buddy.models import (
    DUPLICATE_ROWS_TARGET,
    EMPTY_ROWS_TARGET,
    LEDGER_FROM_BRAIN,
    LEDGER_FROM_INTENT,
    LEDGER_FROM_MENTION,
    ExecutionResult,
    RequestedAction,
    StepMeasurement,
    StepResult,
    StepVerification,
    Understanding,
    VerificationResult,
)
from buddy.outcome import (
    _FILLER_WORDS,
    DONE,
    DONE_UNREQUESTED,
    FILE_STYLE_GENERATED,
    FILE_STYLE_PLAIN,
    NO_SKILL,
    NOT_CONFIRMED,
    NOT_DONE,
    NOT_PLANNED,
    NOT_READ_AS_REQUEST,
    ActionOutcome,
    OutcomeReport,
    build_outcome_report,
    render_outcome,
)
from buddy.voice import SpeakingRequest, TemplateSpeaker
from buddy.voice_catalog import CATALOG, MODES, eligible_phrases

_DEDUPE = "spreadsheet.deduplicate"
_EMPTY = "spreadsheet.remove_empty_rows"
_BRAIN, _INTENT, _MENTION = LEDGER_FROM_BRAIN, LEDGER_FROM_INTENT, LEDGER_FROM_MENTION
_ORDER = (
    "I couldn't tell for sure from your message what order you wanted, so the steps ran "
    "in this order: remove empty rows, then deduplicate."
)


def _m(
    target: str, removed: int | None, output_rows: int = 4, remaining: int = 0
) -> StepMeasurement:
    return StepMeasurement(
        target=target,
        output_rows=output_rows,
        remaining=remaining,
        input_rows=None if removed is None else output_rows + removed,
        removed=removed,
    )


# -- The renderer, row by row (design Section 2.3, DONE rows) --------------------------


@pytest.mark.parametrize(
    ("report", "file_style", "expected"),
    [
        (
            OutcomeReport(
                actions=(
                    ActionOutcome(_DEDUPE, DONE, _m(DUPLICATE_ROWS_TARGET, 2), source=_BRAIN),
                ),
                final_state=(_m(DUPLICATE_ROWS_TARGET, 2),),
                output_names=("report_deduplicated_S-task-1.xlsx",),
                delivered_file="report.xlsx",
            ),
            FILE_STYLE_GENERATED,
            "Done — removed 2 duplicate row(s). The copy has 4 row(s), 0 duplicate row(s) "
            "remaining. Saved as `report_deduplicated_S-task-1.xlsx`.",
        ),
        (  # H1: identical blank rows are removed too, and the reply says so
            OutcomeReport(
                actions=(
                    ActionOutcome(
                        _DEDUPE,
                        DONE,
                        StepMeasurement(
                            DUPLICATE_ROWS_TARGET,
                            output_rows=4,
                            remaining=0,
                            input_rows=6,
                            removed=2,
                            removed_blank=1,
                        ),
                        source=_BRAIN,
                    ),
                ),
                final_state=(_m(DUPLICATE_ROWS_TARGET, 2),),
                delivered_file="report.xlsx",
            ),
            FILE_STYLE_PLAIN,
            "Done — removed 2 duplicate row(s), including 1 repeated blank row. "
            "The copy has 4 row(s), 0 duplicate row(s) remaining. "
            "Your cleaned copy of report.xlsx is ready.",
        ),
        (  # H1: only repeated blank rows were removed, several of them
            OutcomeReport(
                actions=(
                    ActionOutcome(
                        _DEDUPE,
                        DONE,
                        StepMeasurement(
                            DUPLICATE_ROWS_TARGET,
                            output_rows=3,
                            remaining=0,
                            input_rows=5,
                            removed=2,
                            removed_blank=2,
                        ),
                        source=_BRAIN,
                    ),
                ),
                output_names=("out.xlsx",),
            ),
            FILE_STYLE_GENERATED,
            "Done — removed 2 duplicate row(s), including 2 repeated blank rows. "
            "Saved as `out.xlsx`.",
        ),
        (  # N2: nothing to remove is said as such, with no invented count
            OutcomeReport(
                actions=(
                    ActionOutcome(_DEDUPE, DONE, _m(DUPLICATE_ROWS_TARGET, 0, 3), source=_BRAIN),
                ),
                final_state=(_m(DUPLICATE_ROWS_TARGET, 0, 3),),
                delivered_file="report.xlsx",
            ),
            FILE_STYLE_PLAIN,
            "Done — there were no duplicate rows to remove. The copy has 3 row(s), "
            "0 duplicate row(s) remaining. Your cleaned copy of report.xlsx is ready.",
        ),
        (  # no measurement: no count at all
            OutcomeReport(
                actions=(ActionOutcome(_EMPTY, DONE, None, source=_INTENT),),
                output_names=("out.xlsx",),
            ),
            FILE_STYLE_GENERATED,
            "Done — the empty-row removal ran and passed its check. Saved as `out.xlsx`.",
        ),
        (  # a capability with no wording yet
            OutcomeReport(actions=(ActionOutcome("spreadsheet.fix_dates", DONE),)),
            FILE_STYLE_GENERATED,
            "The fix dates step ran and passed its check.",
        ),
        (  # a chain with no ledger to compare it with: no lead word; "left" for the later step
            OutcomeReport(
                actions=(
                    ActionOutcome(_EMPTY, DONE, _m(EMPTY_ROWS_TARGET, 1, 5)),
                    ActionOutcome(
                        _DEDUPE, DONE, _m(DUPLICATE_ROWS_TARGET, 0, 5), after_earlier_step=True
                    ),
                ),
                final_state=(
                    StepMeasurement(EMPTY_ROWS_TARGET, 5, 0),
                    StepMeasurement(DUPLICATE_ROWS_TARGET, 5, 0),
                ),
                order_used=_ORDER,
                delivered_file="report.xlsx",
            ),
            FILE_STYLE_PLAIN,
            "Removed 1 fully-empty row(s). There were no duplicate rows left to remove. "
            f"The copy has 5 row(s), 0 fully-empty row(s) and 0 duplicate row(s) remaining. "
            f"{_ORDER} Your cleaned copy of report.xlsx is ready.",
        ),
        (  # final-file counts that disagree on the row count are not stated
            OutcomeReport(
                actions=(ActionOutcome(_DEDUPE, DONE, None),),
                final_state=(
                    StepMeasurement(EMPTY_ROWS_TARGET, 5, 0),
                    StepMeasurement(DUPLICATE_ROWS_TARGET, 4, 0),
                ),
            ),
            FILE_STYLE_GENERATED,
            "The duplicate-row removal ran and passed its check.",
        ),
    ],
    ids=[
        "removed",
        "removed-with-repeated-blank",
        "removed-only-blanks",
        "n2-none-to-remove",
        "no-measurement",
        "unknown-capability",
        "chain",
        "inconsistent-final-state",
    ],
)
def test_render_outcome(report: OutcomeReport, file_style: str, expected: str) -> None:
    assert render_outcome(report, file_style) == expected


# -- The lead word (design 2.3 and decision D2) ------------------------------------------


def _a(capability: str, status: str = DONE, source: str = _BRAIN, reason: str | None = None):
    return ActionOutcome(capability, status, reason=reason, source=source)


@pytest.mark.parametrize(
    ("actions", "expected"),
    [
        ((_a(_DEDUPE),), "Done"),
        ((_a(_EMPTY, source=_INTENT),), "Done"),
        ((_a(_DEDUPE), _a(_EMPTY, source=_MENTION)), None),  # H1: a mention never confirms
        ((_a(_DEDUPE, source=_MENTION),), None),
        ((), None),  # nothing ran
        ((_a(_DEDUPE, source=""),), None),  # no reading of the request to compare with
        ((_a(_DEDUPE), _a(_EMPTY, source="")), None),  # something not in the ledger ran
        ((_a(_EMPTY, DONE_UNREQUESTED, source=""),), None),  # N5-like: only unrequested work
        (  # C1: part of Brain's reading wasn't planned
            (_a(_DEDUPE), _a(_EMPTY, NOT_DONE, reason=NOT_PLANNED)),
            "Partly done",
        ),
        (  # Brain listed an action with no skill
            (_a(_DEDUPE), _a("", NOT_DONE, reason=NO_SKILL)),
            "Partly done",
        ),
        (  # D2: only the mention detector has the unrun action: no "Partly", no "Done"
            (_a(_DEDUPE), _a(_EMPTY, NOT_DONE, _MENTION, NOT_READ_AS_REQUEST)),
            None,
        ),
        (  # N5: nothing requested was done
            (
                _a(_DEDUPE, DONE_UNREQUESTED, source=""),
                _a(_EMPTY, NOT_DONE, reason=NOT_PLANNED),
            ),
            None,
        ),
        (  # H1: a mention-only action that was done never counts as requested
            (_a(_DEDUPE, source=_MENTION), _a("", NOT_DONE, reason=NO_SKILL)),
            None,
        ),
    ],
)
def test_lead_word(actions: tuple[ActionOutcome, ...], expected: str | None) -> None:
    report = OutcomeReport(actions=actions)

    assert report.lead_word == expected
    assert report.request_complete is (expected == "Done")


@pytest.mark.parametrize(
    ("intent", "expected"),
    [
        ("remove_duplicate_rows", ((_DEDUPE, _INTENT),)),
        ("remove_empty_rows", ((_EMPTY, _INTENT),)),
        ("multi_step_request", ()),
        ("unclear", ()),
        (None, ()),
    ],
)
def test_with_no_requested_actions_the_ledger_is_brains_single_action_intent(
    intent: str | None, expected: tuple[tuple[str, str], ...]
) -> None:
    """Older replies and test fakes have no requested_actions (design 3.1)."""
    output = "out.xlsx"
    steps = (_step(_DEDUPE, output, "in.xlsx"), _step(_EMPTY, "out2.xlsx", output))
    report = build_outcome_report(
        ExecutionResult(success=True, outputs=("out2.xlsx",), steps=steps),
        VerificationResult(passed=True),
        Understanding(intent=intent),
    )

    ledger = tuple((a.capability, a.source) for a in report.actions if a.source)
    assert ledger == expected


# -- Building the report ---------------------------------------------------------------


def _step(capability: str, output: str, input_reference: str, **overrides: object) -> StepResult:
    fields: dict[str, object] = {
        "capability": capability,
        "skill": "some.skill",
        "status": "success",
        "outputs": (output,),
        "input_reference": input_reference,
        "permission_tier": "write",
    }
    fields.update(overrides)
    return StepResult(**fields)  # type: ignore[arg-type]


def test_report_takes_each_steps_measurement_from_its_own_passing_verdict() -> None:
    steps = (
        _step(_EMPTY, "a.xlsx", "in.xlsx"),
        _step(_DEDUPE, "b.xlsx", "a.xlsx"),
    )
    empty_m, dedupe_m = _m(EMPTY_ROWS_TARGET, 1, 5), _m(DUPLICATE_ROWS_TARGET, 2, 3)
    final_m = (
        StepMeasurement(EMPTY_ROWS_TARGET, 3, 0),
        StepMeasurement(DUPLICATE_ROWS_TARGET, 3, 0),
    )
    verification = VerificationResult(
        passed=True,
        steps=(
            StepVerification(_EMPTY, True, input_reference="in.xlsx", measurements=(empty_m,)),
            StepVerification(_DEDUPE, True, input_reference="a.xlsx", measurements=(dedupe_m,)),
            StepVerification("final_output", True, measurements=final_m),
        ),
    )

    report = build_outcome_report(
        ExecutionResult(success=True, outputs=("b.xlsx",), steps=steps),
        verification,
        Understanding(intent="multi_step_request"),
    )

    assert report.actions == (
        ActionOutcome(_EMPTY, DONE, empty_m),
        ActionOutcome(_DEDUPE, DONE, dedupe_m, after_earlier_step=True),
    )
    assert report.final_state == final_m
    assert report.output_names == ("b.xlsx",)


def test_report_lists_no_action_whose_step_did_not_pass() -> None:
    steps = (_step(_EMPTY, "a.xlsx", "in.xlsx"), _step(_DEDUPE, "b.xlsx", "a.xlsx"))
    verification = VerificationResult(
        passed=True,
        steps=(
            StepVerification(_EMPTY, True, input_reference="in.xlsx"),
            StepVerification(_DEDUPE, False, input_reference="a.xlsx"),
        ),
    )

    report = build_outcome_report(
        ExecutionResult(success=True, outputs=("b.xlsx",), steps=steps), verification, None
    )

    assert [a.capability for a in report.actions] == [_EMPTY]
    # The last passing step's output isn't the delivered file: no final state.
    assert report.final_state == ()


def test_single_step_final_state_is_its_own_measurement_when_it_made_the_delivered_file() -> None:
    m = _m(DUPLICATE_ROWS_TARGET, 2)
    verification = VerificationResult(
        passed=True,
        steps=(StepVerification(_DEDUPE, True, input_reference="in.xlsx", measurements=(m,)),),
    )

    report = build_outcome_report(
        ExecutionResult(
            success=True, outputs=("out.xlsx",), steps=(_step(_DEDUPE, "out.xlsx", "in.xlsx"),)
        ),
        verification,
        Understanding(intent="remove_duplicate_rows"),
    )

    assert report.final_state == (m,)
    assert report.lead_word == "Done"


def test_a_measurement_for_another_target_is_never_attributed_to_a_step() -> None:
    wrong = _m(EMPTY_ROWS_TARGET, 7)
    verification = VerificationResult(
        passed=True, steps=(StepVerification(_DEDUPE, True, measurements=(wrong,)),)
    )

    report = build_outcome_report(
        ExecutionResult(
            success=True, outputs=("out.xlsx",), steps=(_step(_DEDUPE, "out.xlsx", "in.xlsx"),)
        ),
        verification,
        Understanding(intent="remove_duplicate_rows"),
    )

    assert report.actions == (ActionOutcome(_DEDUPE, DONE, None, source=_INTENT),)
    assert "7" not in render_outcome(report, FILE_STYLE_GENERATED)


def test_the_reply_never_uses_a_skills_self_reported_count(tmp_path: Path) -> None:
    """A Skill that says it removed 99 rows (summary, self-reported
    findings) still gets the verifier's own number in the reply."""
    source = tmp_path / "in.xlsx"
    pd.DataFrame(
        {"name": ["Alice", "Bob", "Alice", "Carol", "Bob"], "amount": [10, 20, 10, 30, 20]}
    ).to_excel(source, index=False)
    output = tmp_path / "out.xlsx"
    pd.read_excel(source).drop_duplicates().to_excel(output, index=False)
    step = StepResult(
        capability=_DEDUPE,
        skill="excel.deduplicate",
        status="success",
        outputs=(str(output),),
        summary="Removed 99 duplicate rows",
        self_reported_findings=("removed_count=99",),
        input_reference=str(source),
        permission_tier="write",
    )
    execution = ExecutionResult(success=True, outputs=(str(output),), steps=(step,))
    verification = CompositeVerifier(
        {_DEDUPE: SpreadsheetDedupeVerifier(), _EMPTY: SpreadsheetEmptyRowsVerifier()}
    )(VerificationRequest(understanding=Understanding(), execution_result=execution))

    reply = TemplateSpeaker()(
        SpeakingRequest(
            understanding=Understanding(intent="remove_duplicate_rows"),
            execution_result=execution,
            verification_result=verification,
            mode="focus",
        )
    )

    assert "99" not in reply
    original = pd.read_excel(source)
    assert f"Done — removed {int(original.duplicated().sum())} duplicate row(s)." in reply
    assert f"The copy has {len(pd.read_excel(output))} row(s)" in reply


# -- Every number in a reply is a measured field (property test) -------------------------

_TARGET_BY_CAPABILITY = {_DEDUPE: DUPLICATE_ROWS_TARGET, _EMPTY: EMPTY_ROWS_TARGET}


_PHRASES = ("sort by amount", "make a chart of Q3 2024", "give me a report")


def _random_report(rng: random.Random) -> OutcomeReport:
    capabilities = rng.choice([(_DEDUPE,), (_EMPTY,), (_DEDUPE, _EMPTY), (_EMPTY, _DEDUPE)])
    actions = []
    rows = rng.randint(0, 5000)
    for index, capability in enumerate(capabilities):
        measurement = None
        if rng.random() < 0.8:
            removed = rng.choice([0, 0, rng.randint(1, 900), None])
            measurement = StepMeasurement(
                target=_TARGET_BY_CAPABILITY[capability],
                output_rows=rng.randint(0, 5000),
                remaining=0,
                input_rows=rng.choice([None, rng.randint(0, 9000)]),
                removed=removed,
                removed_blank=(
                    rng.randint(0, removed)
                    if removed and capability == _DEDUPE and rng.random() < 0.5
                    else 0
                ),
            )
        status = rng.choice([DONE, DONE, DONE_UNREQUESTED])
        source = rng.choice([_BRAIN, _INTENT, _MENTION, ""]) if status == DONE else ""
        actions.append(
            ActionOutcome(
                capability, status, measurement, after_earlier_step=index > 0, source=source
            )
        )
    not_run = [c for c in (_DEDUPE, _EMPTY) if c not in capabilities]
    residuals = []
    for capability in not_run:
        if rng.random() < 0.6:
            reason = rng.choice([NOT_PLANNED, NOT_READ_AS_REQUEST])
            source = _MENTION if reason == NOT_READ_AS_REQUEST else _BRAIN
            actions.append(ActionOutcome(capability, NOT_DONE, reason=reason, source=source))
            if rng.random() < 0.7:
                residuals.append(
                    StepMeasurement(
                        _TARGET_BY_CAPABILITY[capability], rows, rng.choice([0, rng.randint(1, 60)])
                    )
                )
    if rng.random() < 0.4:
        phrase = rng.choice(("", *_PHRASES))
        actions.append(
            ActionOutcome("", NOT_DONE, reason=NO_SKILL, source=_BRAIN, user_phrase=phrase)
        )
    final_state = tuple(
        StepMeasurement(_TARGET_BY_CAPABILITY[c], rows, rng.choice([0, 0, rng.randint(1, 50)]))
        for c in capabilities
        if rng.random() < 0.7
    )
    return OutcomeReport(
        actions=tuple(actions),
        brain_reading=rng.choice([(), ("remove empty rows",), ("remove duplicate rows",)]),
        final_state=final_state,
        residuals=tuple(residuals),
        order_used=rng.choice([None, _ORDER]),
        delivered_file=rng.choice(["report.xlsx", "q3_2024_sales_v12.xlsx"]),
        output_names=(
            rng.choice(
                ["report_deduplicated_S-task-1.xlsx", "q3_2024_v12_no_empty_rows_7-task-2.xlsx"]
            ),
        ),
        open_findings=rng.choice([0, 0, rng.randint(1, 9)]),
    )


def _measured_numbers(report: OutcomeReport) -> set[int]:
    numbers: set[int] = set()
    measurements = (
        *(a.measurement for a in report.actions if a.measurement),
        *report.final_state,
        *report.residuals,
    )
    for m in measurements:
        numbers.update(
            n
            for n in (m.output_rows, m.remaining, m.input_rows, m.removed, m.removed_blank)
            if n is not None
        )
    return numbers


def _without_file_sentence(reply: str, report: OutcomeReport) -> str:
    """The generated/original file names, and the user's own quoted words,
    carry their own digits; drop them."""
    for name in (*report.output_names, report.delivered_file or ""):
        if name:
            reply = reply.replace(name, "")
    return re.sub(r"“[^”]*”", "", reply)


def test_every_number_in_a_rendered_reply_is_a_measured_field() -> None:
    rng = random.Random(20260930)
    reports = [_random_report(rng) for _ in range(200)]
    checked = 0
    for report, style, mode in itertools.product(
        reports, (FILE_STYLE_GENERATED, FILE_STYLE_PLAIN), MODES
    ):
        block = render_outcome(report, style)
        for opener, closer in itertools.product(
            eligible_phrases(report, mode, "opener"), eligible_phrases(report, mode, "closer")
        ):
            reply = " ".join(p for p in (opener.text, block, closer.text) if p)
            numbers = {int(n) for n in re.findall(r"\d+", _without_file_sentence(reply, report))}
            assert numbers <= _measured_numbers(report), (reply, report)
            checked += 1
    assert checked > 1000


def test_no_catalog_entry_carries_a_number() -> None:
    for phrase in CATALOG:
        assert re.search(r"\d", phrase.text) is None, phrase.id


# -- PR-B: NOT_DONE and DONE_UNREQUESTED rows (design Sections 2.3 and 2.5) ---------------

_C1_BLOCK_FINAL = StepMeasurement(DUPLICATE_ROWS_TARGET, output_rows=4, remaining=0)


@pytest.mark.parametrize(
    ("report", "expected"),
    [
        (  # C1: Brain read both; the planner dropped empty-row removal
            OutcomeReport(
                actions=(
                    ActionOutcome(_DEDUPE, DONE, _m(DUPLICATE_ROWS_TARGET, 2), source=_BRAIN),
                    ActionOutcome(_EMPTY, NOT_DONE, reason=NOT_PLANNED, source=_BRAIN),
                ),
                brain_reading=("remove duplicate rows", "remove empty rows"),
                final_state=(_C1_BLOCK_FINAL,),
                residuals=(StepMeasurement(EMPTY_ROWS_TARGET, 4, 1),),
                output_names=("out.xlsx",),
            ),
            "Partly done — removed 2 duplicate row(s). I didn't run the empty-row removal, "
            "though I read it as part of your request. The copy has 4 row(s), 0 duplicate "
            "row(s) remaining. It still has 1 fully-empty row(s). Saved as `out.xlsx`.",
        ),
        (  # N1 / C3: only the mention detector has the unrun action (D2: no lead word)
            OutcomeReport(
                actions=(
                    ActionOutcome(_EMPTY, DONE, _m(EMPTY_ROWS_TARGET, 1, 5), source=_INTENT),
                    ActionOutcome(
                        _DEDUPE, NOT_DONE, reason=NOT_READ_AS_REQUEST, source=_MENTION
                    ),
                ),
                brain_reading=("remove empty rows",),
                final_state=(StepMeasurement(EMPTY_ROWS_TARGET, 5, 0),),
                residuals=(StepMeasurement(DUPLICATE_ROWS_TARGET, 5, 2),),
                delivered_file="report.xlsx",
            ),
            "Removed 1 fully-empty row(s). Your message mentions duplicate rows, but I "
            "didn't run the duplicate-row removal. I read your request as: remove empty rows. "
            "The copy has 5 row(s), 0 fully-empty row(s) remaining. It still has 2 duplicate "
            "row(s). Your cleaned copy of report.xlsx is ready.",
        ),
        (  # N5: unrequested work ran, the requested action didn't
            OutcomeReport(
                actions=(
                    ActionOutcome(_DEDUPE, DONE_UNREQUESTED, _m(DUPLICATE_ROWS_TARGET, 2)),
                    ActionOutcome(_EMPTY, NOT_DONE, reason=NOT_PLANNED, source=_BRAIN),
                ),
                brain_reading=("remove empty rows",),
                final_state=(_C1_BLOCK_FINAL,),
                residuals=(StepMeasurement(EMPTY_ROWS_TARGET, 4, 1),),
                output_names=("out.xlsx",),
            ),
            "I removed 2 duplicate row(s), though I didn't read that as part of your request. "
            "I didn't run the empty-row removal, though I read it as part of your request. "
            "The copy has 4 row(s), 0 duplicate row(s) remaining. It still has 1 fully-empty "
            "row(s). Saved as `out.xlsx`.",
        ),
        (  # NO_SKILL with the user's own words; an unrequested step after a done one
            OutcomeReport(
                actions=(
                    ActionOutcome(_EMPTY, DONE, _m(EMPTY_ROWS_TARGET, 0, 5), source=_BRAIN),
                    ActionOutcome(
                        _DEDUPE,
                        DONE_UNREQUESTED,
                        _m(DUPLICATE_ROWS_TARGET, 0, 5),
                        after_earlier_step=True,
                    ),
                    ActionOutcome(
                        "", NOT_DONE, reason=NO_SKILL, source=_BRAIN, user_phrase="sort by amount"
                    ),
                ),
            ),
            "Partly done — there were no fully-empty rows to remove. I also ran the "
            "duplicate-row removal, though I didn't read that as part of your request; there "
            "were no duplicate rows left to remove. I can't do this part yet: “sort by amount”.",
        ),
        (  # NO_SKILL whose phrase isn't the user's: nothing is quoted
            OutcomeReport(
                actions=(
                    ActionOutcome(_DEDUPE, DONE, None, source=_BRAIN),
                    ActionOutcome("", NOT_DONE, reason=NO_SKILL, source=_BRAIN),
                ),
            ),
            "Partly done — the duplicate-row removal ran and passed its check. I read part of "
            "your request as something I can't do yet.",
        ),
        (  # an unrequested step with no count; NOT_CONFIRMED; a zero residual, no final state
            OutcomeReport(
                actions=(
                    ActionOutcome(_DEDUPE, DONE_UNREQUESTED, None),
                    ActionOutcome(_EMPTY, NOT_DONE, reason=NOT_CONFIRMED, source=_BRAIN),
                ),
                residuals=(StepMeasurement(EMPTY_ROWS_TARGET, 4, 0),),
            ),
            "I ran the duplicate-row removal, though I didn't read that as part of your "
            "request; it passed its check. I couldn't confirm the empty-row removal: its check "
            "didn't pass. The copy has no fully-empty rows.",
        ),
        (  # a mention-only NOT_DONE with no reading from Brain: no "I read your request as"
            OutcomeReport(
                actions=(
                    ActionOutcome(_DEDUPE, DONE, _m(DUPLICATE_ROWS_TARGET, 0, 5), source=_MENTION),
                    ActionOutcome(_EMPTY, NOT_DONE, reason=NOT_READ_AS_REQUEST, source=_MENTION),
                ),
                residuals=(StepMeasurement(EMPTY_ROWS_TARGET, 5, 1),),
            ),
            "There were no duplicate rows to remove. Your message mentions empty rows, but "
            "I didn't run the empty-row removal. The copy still has 1 fully-empty row(s).",
        ),
    ],
    ids=["C1", "N1-C3", "N5", "no-skill-quoted", "no-skill-unquoted", "not-confirmed", "no-read"],
)
def test_render_ledger_rows(report: OutcomeReport, expected: str) -> None:
    style = FILE_STYLE_PLAIN if report.delivered_file else FILE_STYLE_GENERATED
    assert render_outcome(report, style) == expected


# -- PR-B: the derivation table (design Section 2.2) -------------------------------------


def _brain(action: str, phrase: str = "", found: bool = False) -> RequestedAction:
    return RequestedAction(action, phrase, _BRAIN, found)


def _mention(action: str, phrase: str) -> RequestedAction:
    return RequestedAction(action, phrase, _MENTION, True)


def _single_dedupe_turn(
    *ledger: RequestedAction, intent: str | None = "multi_step_request", **verification: object
) -> OutcomeReport:
    m = _m(DUPLICATE_ROWS_TARGET, 2)
    return build_outcome_report(
        ExecutionResult(
            success=True, outputs=("out.xlsx",), steps=(_step(_DEDUPE, "out.xlsx", "in.xlsx"),)
        ),
        VerificationResult(
            passed=True,
            steps=(StepVerification(_DEDUPE, True, input_reference="in.xlsx", measurements=(m,)),),
            **verification,  # type: ignore[arg-type]
        ),
        Understanding(intent=intent, requested_actions=ledger),
    )


def _statuses(report: OutcomeReport) -> list[tuple[str, str, str | None, str]]:
    return [(a.capability, a.status, a.reason, a.source) for a in report.actions]


def test_brains_reading_with_no_step_is_not_planned() -> None:
    report = _single_dedupe_turn(_brain("remove_duplicate_rows"), _brain("remove_empty_rows"))

    assert _statuses(report) == [
        (_DEDUPE, DONE, None, _BRAIN),
        (_EMPTY, NOT_DONE, NOT_PLANNED, _BRAIN),
    ]
    assert report.brain_reading == ("remove duplicate rows", "remove empty rows")
    assert report.lead_word == "Partly done"


def test_a_mention_only_action_with_no_step_is_not_read_as_request() -> None:
    report = _single_dedupe_turn(
        _brain("remove_duplicate_rows"), _mention("remove_empty_rows", "empty rows")
    )

    assert _statuses(report) == [
        (_DEDUPE, DONE, None, _BRAIN),
        (_EMPTY, NOT_DONE, NOT_READ_AS_REQUEST, _MENTION),
    ]
    assert report.lead_word is None  # D2


def test_an_unsupported_action_is_no_skill_and_quotes_only_the_users_own_words() -> None:
    long_phrase = "make me a `chart`\nof every \"region\" " + "and quarter " * 10
    report = _single_dedupe_turn(
        _brain("remove_duplicate_rows"),
        _brain(UNSUPPORTED, "sort by amount", found=True),
        _brain(UNSUPPORTED, "arrange the rows", found=False),  # the model's words
        _brain(UNSUPPORTED, long_phrase, found=True),
    )

    no_skill = [a for a in report.actions if a.reason == NO_SKILL]
    assert [a.user_phrase for a in no_skill[:2]] == ["sort by amount", ""]
    quoted = no_skill[2].user_phrase
    assert len(quoted) == 80 and quoted.endswith("…")
    assert not set("`\n\"") & set(quoted)
    assert quoted.startswith("make me a chart of every region and quarter")


def test_a_step_brain_did_not_read_is_done_unrequested() -> None:
    report = _single_dedupe_turn(_brain("remove_empty_rows"))

    assert _statuses(report) == [
        (_DEDUPE, DONE_UNREQUESTED, None, ""),
        (_EMPTY, NOT_DONE, NOT_PLANNED, _BRAIN),
    ]
    assert report.lead_word is None


def test_with_no_reading_at_all_a_passing_step_is_neither_confirmed_nor_unrequested() -> None:
    report = _single_dedupe_turn()

    assert _statuses(report) == [(_DEDUPE, DONE, None, "")]
    assert report.lead_word is None
    assert "didn't read that" not in render_outcome(report, FILE_STYLE_GENERATED)


def test_h1_a_mention_only_step_is_unrequested_when_brain_gave_a_reading() -> None:
    report = _single_dedupe_turn(
        _brain("remove_empty_rows"), _mention("remove_duplicate_rows", "remove duplicates")
    )

    assert _statuses(report) == [
        (_DEDUPE, DONE_UNREQUESTED, None, ""),
        (_EMPTY, NOT_DONE, NOT_PLANNED, _BRAIN),
    ]
    assert report.lead_word is None and not report.request_complete
    assert "I removed 2 duplicate row(s), though I didn't read that as part of your request." in (
        render_outcome(report, FILE_STYLE_GENERATED)
    )


def test_h1_a_mention_only_step_with_no_reading_is_done_but_never_confirms() -> None:
    report = _single_dedupe_turn(_mention("remove_duplicate_rows", "the duplicates"))

    assert _statuses(report) == [(_DEDUPE, DONE, None, _MENTION)]
    assert report.lead_word is None and not report.request_complete


def test_h2_an_unsupported_entry_naming_a_done_action_is_dropped_or_unquoted() -> None:
    def no_skill(*phrases: str, brain_phrase: str = "") -> list[str]:
        report = _single_dedupe_turn(
            _brain("remove_duplicate_rows", brain_phrase, found=bool(brain_phrase)),
            *(_brain(UNSUPPORTED, phrase, found=True) for phrase in phrases),
        )
        return [a.user_phrase for a in report.actions if a.reason == NO_SKILL]

    assert no_skill("remove duplicates") == []  # names only the done action
    assert no_skill("Remove the duplicate rows please") == []  # plus filler
    assert no_skill("remove duplicates and sort by amount") == [""]  # unquoted
    assert no_skill("sort by amount") == ["sort by amount"]  # names nothing
    assert no_skill("please") == ["please"]  # no action named: unchanged
    # The words Brain quoted for the done action count too (a paraphrase).
    said = "get rid of the repeated entries"
    assert no_skill(said, brain_phrase=said) == []
    assert no_skill(f"{said} and sort", brain_phrase=said) == [""]
    # The same part is said once.
    assert no_skill("sort by amount", "Sort by amount", "sort by amount") == ["sort by amount"]


def test_h2_an_unsupported_entry_naming_an_action_that_did_not_run_is_dropped_too() -> None:
    report = _single_dedupe_turn(
        _brain("remove_duplicate_rows"), _brain(UNSUPPORTED, "keep the empty rows", found=True)
    )

    assert not [a for a in report.actions if a.reason == NO_SKILL]


def test_a_step_that_ran_but_did_not_pass_is_not_confirmed_never_not_planned() -> None:
    steps = (_step(_DEDUPE, "a.xlsx", "in.xlsx"), _step(_EMPTY, "b.xlsx", "a.xlsx"))
    report = build_outcome_report(
        ExecutionResult(success=True, outputs=("a.xlsx",), steps=steps),
        VerificationResult(
            passed=True,
            steps=(
                StepVerification(_DEDUPE, True, input_reference="in.xlsx"),
                StepVerification(_EMPTY, False, input_reference="a.xlsx"),
            ),
        ),
        Understanding(
            requested_actions=(_brain("remove_duplicate_rows"), _mention("remove_empty_rows", "x"))
        ),
    )

    assert _statuses(report)[1] == (_EMPTY, NOT_DONE, NOT_CONFIRMED, _MENTION)


def test_residuals_are_kept_only_for_actions_that_were_not_done() -> None:
    residuals = (
        StepMeasurement(DUPLICATE_ROWS_TARGET, 4, 0),
        StepMeasurement(EMPTY_ROWS_TARGET, 4, 1),
    )
    report = _single_dedupe_turn(
        _brain("remove_duplicate_rows"), _brain("remove_empty_rows"), residuals=residuals
    )

    assert report.residuals == (StepMeasurement(EMPTY_ROWS_TARGET, 4, 1),)
    assert render_outcome(report, FILE_STYLE_GENERATED) == (
        "Partly done — removed 2 duplicate row(s). I didn't run the empty-row removal, though I "
        "read it as part of your request. The copy has 4 row(s), 0 duplicate row(s) remaining. "
        "It still has 1 fully-empty row(s). Saved as `out.xlsx`."
    )


def test_no_residual_sentence_when_the_delivered_file_could_not_be_read() -> None:
    report = _single_dedupe_turn(_brain("remove_duplicate_rows"), _brain("remove_empty_rows"))

    assert report.residuals == ()
    assert "still has" not in render_outcome(report, FILE_STYLE_GENERATED)


def test_n1_the_filler_set_holds_only_function_words() -> None:
    """Guard: a dropped "unsupported" entry must have nothing but these left over, so no
    content word (a verb like "delete", a noun like "file") may ever join the set. A verb
    belongs in an action's mention pattern, attached to its object."""
    allowed = {
        "a", "an", "the", "please", "pls", "and", "then", "also", "just", "only", "so", "but",
        "can", "could", "would", "will", "you", "i", "we", "to", "do",
        "dont", "don't", "not", "no", "never", "keep", "leave",
    }  # fmt: skip

    assert _FILLER_WORDS <= allowed, sorted(_FILLER_WORDS - allowed)
    for content_word in (
        "remove", "delete", "drop", "clean", "file", "sheet", "data", "report", "rows",
    ):
        assert content_word not in _FILLER_WORDS


def test_n1_a_leftover_content_word_keeps_the_generic_not_done_sentence() -> None:
    def generic(phrase: str) -> list[str]:
        report = _single_dedupe_turn(
            _brain("remove_duplicate_rows", "remove duplicates", found=True),
            _brain(UNSUPPORTED, phrase, found=True),
        )
        return [a.user_phrase for a in report.actions if a.reason == NO_SKILL]

    assert generic("remove duplicates and delete the file") == [""]
    assert generic("remove duplicates and clean the data") == [""]
    assert generic("remove duplicates and drop the sheet") == [""]
    assert generic("delete the duplicates") == []  # removal verb + object: the action's words
    assert generic("remove all duplicates") == []
    assert generic("keep the duplicates") == [""]  # keep word next to a done action: keep
    assert generic("don't remove the duplicates please") == [""]  # negation, same
    assert generic("but keep the duplicates") == [""]


def test_n2_unsupported_entries_alone_never_make_a_step_unrequested() -> None:
    report = _single_dedupe_turn(_brain(UNSUPPORTED, "sort by amount", found=True))

    assert [(a.capability, a.status, a.source) for a in report.actions if a.reason is None] == [
        (_DEDUPE, DONE, "")
    ]
    assert report.lead_word is None
    assert "didn't read that" not in render_outcome(report, FILE_STYLE_GENERATED)


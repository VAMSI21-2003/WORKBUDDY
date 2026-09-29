"""Unit tests for buddy.outcome: the report and the renderer."""

from __future__ import annotations

import itertools
import random
import re
from pathlib import Path

import pandas as pd
import pytest

from buddy.inspector import (
    CompositeVerifier,
    SpreadsheetDedupeVerifier,
    SpreadsheetEmptyRowsVerifier,
    VerificationRequest,
)
from buddy.models import (
    DUPLICATE_ROWS_TARGET,
    EMPTY_ROWS_TARGET,
    ExecutionResult,
    StepMeasurement,
    StepResult,
    StepVerification,
    Understanding,
    VerificationResult,
)
from buddy.outcome import (
    DONE,
    FILE_STYLE_GENERATED,
    FILE_STYLE_PLAIN,
    ActionOutcome,
    OutcomeReport,
    build_outcome_report,
    render_outcome,
)
from buddy.voice import SpeakingRequest, TemplateSpeaker
from buddy.voice_catalog import CATALOG, MODES, eligible_phrases

_DEDUPE = "spreadsheet.deduplicate"
_EMPTY = "spreadsheet.remove_empty_rows"
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
                actions=(ActionOutcome(_DEDUPE, DONE, _m(DUPLICATE_ROWS_TARGET, 2)),),
                requested=(_DEDUPE,),
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
                    ),
                ),
                requested=(_DEDUPE,),
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
                    ),
                ),
                requested=(_DEDUPE,),
                output_names=("out.xlsx",),
            ),
            FILE_STYLE_GENERATED,
            "Done — removed 2 duplicate row(s), including 2 repeated blank rows. "
            "Saved as `out.xlsx`.",
        ),
        (  # N2: nothing to remove is said as such, with no invented count
            OutcomeReport(
                actions=(ActionOutcome(_DEDUPE, DONE, _m(DUPLICATE_ROWS_TARGET, 0, 3)),),
                requested=(_DEDUPE,),
                final_state=(_m(DUPLICATE_ROWS_TARGET, 0, 3),),
                delivered_file="report.xlsx",
            ),
            FILE_STYLE_PLAIN,
            "Done — there were no duplicate rows to remove. The copy has 3 row(s), "
            "0 duplicate row(s) remaining. Your cleaned copy of report.xlsx is ready.",
        ),
        (  # no measurement: no count at all
            OutcomeReport(
                actions=(ActionOutcome(_EMPTY, DONE, None),),
                requested=(_EMPTY,),
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
        (  # a chain (multi_step_request): no lead word in PR-A; "left" for the later step
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


# -- The lead word -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("requested", "done", "expected"),
    [
        ((_DEDUPE,), (_DEDUPE,), "Done"),
        ((_EMPTY,), (_EMPTY,), "Done"),
        ((), (_DEDUPE,), None),  # multi_step_request / unclear / no intent: no ledger yet
        ((), (_DEDUPE, _EMPTY), None),
        ((_EMPTY,), (_DEDUPE,), None),  # what ran isn't what was asked for
        ((_EMPTY,), (_DEDUPE, _EMPTY), None),  # something else ran too
        ((_DEDUPE,), (), None),  # nothing ran
    ],
)
def test_lead_word(requested: tuple[str, ...], done: tuple[str, ...], expected: str | None) -> None:
    report = OutcomeReport(actions=tuple(ActionOutcome(c) for c in done), requested=requested)

    assert report.lead_word == expected


@pytest.mark.parametrize(
    ("intent", "expected"),
    [
        ("remove_duplicate_rows", (_DEDUPE,)),
        ("remove_empty_rows", (_EMPTY,)),
        ("multi_step_request", ()),
        ("unclear", ()),
        (None, ()),
    ],
)
def test_requested_comes_only_from_a_single_action_intent(
    intent: str | None, expected: tuple[str, ...]
) -> None:
    report = build_outcome_report(ExecutionResult(success=True), None, intent)

    assert report.requested == expected


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
        "multi_step_request",
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
        "remove_duplicate_rows",
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
        "remove_duplicate_rows",
    )

    assert report.actions == (ActionOutcome(_DEDUPE, DONE, None),)
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
        actions.append(ActionOutcome(capability, DONE, measurement, after_earlier_step=index > 0))
    final_state = tuple(
        StepMeasurement(_TARGET_BY_CAPABILITY[c], rows, rng.choice([0, 0, rng.randint(1, 50)]))
        for c in capabilities
        if rng.random() < 0.7
    )
    return OutcomeReport(
        actions=tuple(actions),
        requested=rng.choice([(), (capabilities[0],)]),
        final_state=final_state,
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
    for m in (*(a.measurement for a in report.actions if a.measurement), *report.final_state):
        numbers.update(
            n
            for n in (m.output_rows, m.remaining, m.input_rows, m.removed, m.removed_blank)
            if n is not None
        )
    return numbers


def _without_file_sentence(reply: str, report: OutcomeReport) -> str:
    """The generated/original file names carry their own digits; drop them."""
    for name in (*report.output_names, report.delivered_file or ""):
        if name:
            reply = reply.replace(name, "")
    return reply


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

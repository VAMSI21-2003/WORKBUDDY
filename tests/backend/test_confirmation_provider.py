"""The plan approval on its own: templates, ``plan_note`` and ``WebConfirmationProvider``.

Design Section 1.5 and 7.2 ("WebConfirmationProvider: approve, reject,
timeout (-> reject), shutdown (-> reject), double answer, stale id";
"Confirmation-prompt templates: one per registered capability"), and
"Decisions (2026-09-30)" item 2 and "Decisions (2026-10-01)" items 4 and 5.
The provider is driven here the way Hands drives it, with a fake host in
place of the turn runner.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest

from backend.confirmation import (
    APPROVE,
    BY_SHUTDOWN,
    BY_TIMEOUT,
    BY_TURN_END,
    BY_USER,
    PLAN_NOTE_CHAIN,
    REJECT,
    ConfirmationOutcome,
    PendingPlan,
    WebConfirmationProvider,
    build_plan,
    has_templates,
    plan_note,
    step_prompt,
)
from backend.errors import ApiError, ErrorCode
from buddy.engine_factory import _build_registry
from buddy.hands import ConfirmationStep, PlanAwareConfirmationProvider
from skills.base import PermissionTier, SkillTask

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
UPLOAD = "/data/uploads/k/f_1/report.xlsx"
DEDUPE = "spreadsheet.deduplicate"
EMPTY = "spreadsheet.remove_empty_rows"
WRITE = PermissionTier.WRITE


class _Host:
    """The turn runner's side: counts publishes and records each outcome."""

    def __init__(self, *, publish_ok: bool = True) -> None:
        self.lock = threading.RLock()
        self.publish_ok = publish_ok
        self.published: list[PendingPlan] = []
        self.settled: list[ConfirmationOutcome] = []
        self.publishing = threading.Event()

    def on_publish(self, plan: PendingPlan) -> bool:
        self.published.append(plan)
        self.publishing.set()
        return self.publish_ok

    def on_settle(self, outcome: ConfirmationOutcome) -> None:
        self.settled.append(outcome)


def _provider(host: _Host, timeout_s: float = 10.0) -> WebConfirmationProvider:
    return WebConfirmationProvider(
        lock=host.lock,
        timeout_s=timeout_s,
        clock=lambda: NOW,
        on_publish=host.on_publish,
        on_settle=host.on_settle,
    )


def _step(index: int, capability: str = DEDUPE, tier: PermissionTier = WRITE) -> ConfirmationStep:
    return ConfirmationStep(
        task_id=f"k-task-{index}", capability=capability, tier=tier, input_reference=UPLOAD
    )


def _task(index: int, tier: PermissionTier = WRITE) -> SkillTask:
    return SkillTask(
        task_id=f"k-task-{index}",
        session_id="k",
        workspace_id="k",
        description="",
        input_references=[UPLOAD],
        permission=tier,
    )


@pytest.fixture
def pool() -> ThreadPoolExecutor:
    with ThreadPoolExecutor(max_workers=2) as executor:
        yield executor  # type: ignore[misc]


def _ask(
    pool: ThreadPoolExecutor, provider: WebConfirmationProvider, task: SkillTask
) -> Future[bool]:
    """``confirm`` on another thread, as Hands calls it from the turn's worker."""
    return pool.submit(provider.confirm, task, task.permission)


def _published(host: _Host) -> PendingPlan:
    assert host.publishing.wait(5), "the plan was never published"
    return host.published[-1]


# -- Templates and the plan note ("Decisions (2026-10-01)", item 4) --------------------------


def test_every_registered_file_changing_capability_has_both_templates() -> None:
    capabilities = [
        capability
        for skill in _build_registry().available_skills()
        if skill.permission_tier in (WRITE, PermissionTier.EXTERNAL_EFFECT)
        for capability in skill.supported_inputs
    ]

    assert capabilities  # the real registry has file-changing skills
    for capability in capabilities:
        assert has_templates(capability), capability
        first = step_prompt(capability, 1, "report.xlsx")
        later = step_prompt(capability, 2, "report.xlsx")
        assert capability not in first and capability not in later  # not the fallback
        assert "report.xlsx" in first
        assert later.startswith("Then ") and "the result of step 1" in later


def test_the_first_step_names_the_upload_and_a_later_step_the_previous_result() -> None:
    assert step_prompt(DEDUPE, 1, "report.xlsx") == "Remove duplicate rows from report.xlsx."
    assert step_prompt(EMPTY, 1, "report.xlsx") == "Remove empty rows from report.xlsx."
    assert step_prompt(DEDUPE, 2, "report.xlsx") == (
        "Then remove duplicate rows from the result of step 1."
    )
    assert step_prompt(EMPTY, 3, "report.xlsx") == (
        "Then remove empty rows from the result of step 2."
    )


def test_an_unknown_capability_gets_the_fallback_templates() -> None:
    assert step_prompt("spreadsheet.sort", 1, "a.xlsx") == "Run spreadsheet.sort on a.xlsx."
    assert step_prompt("spreadsheet.sort", 2, "a.xlsx") == (
        "Then run spreadsheet.sort on the result of step 1."
    )


def test_the_plan_note_is_for_two_or_more_steps_only() -> None:
    assert plan_note(1) is None
    assert plan_note(2) == PLAN_NOTE_CHAIN
    assert plan_note(3) == PLAN_NOTE_CHAIN
    assert PLAN_NOTE_CHAIN == (
        "The steps run in this order, each on the previous step's result. "
        "Only the final file is handed back."
    )


def test_no_prompt_or_note_says_save_or_new_file_or_states_a_result() -> None:
    texts = [PLAN_NOTE_CHAIN]
    for capability in (DEDUPE, EMPTY, "spreadsheet.sort"):
        texts += [step_prompt(capability, position, "report.xlsx") for position in (1, 2, 3)]

    for text in texts:
        lowered = text.lower()
        assert "save" not in lowered and "new file" not in lowered, text
        for claim in ("removed", "done", "ready", "unchanged", "original", "won't"):
            assert claim not in lowered, (claim, text)


def test_a_plan_names_the_upload_only_in_its_first_step() -> None:
    plan = build_plan(
        (_step(1), _step(2, EMPTY)), confirmation_id="c_x", expires_at=NOW
    )

    assert [(s.capability, s.prompt) for s in plan.steps] == [
        (DEDUPE, "Remove duplicate rows from report.xlsx."),
        (EMPTY, "Then remove empty rows from the result of step 1."),
    ]
    assert "report.xlsx" not in plan.steps[1].prompt
    assert plan.plan_note == PLAN_NOTE_CHAIN
    single = build_plan((_step(1),), confirmation_id="c_y", expires_at=NOW)
    assert single.plan_note is None


def test_a_plan_is_never_built_without_a_step() -> None:
    with pytest.raises(ValueError):
        build_plan((), confirmation_id="c_x", expires_at=NOW)


# -- The provider: one question, one decision -------------------------------------------------


def test_the_provider_is_plan_aware() -> None:
    assert isinstance(_provider(_Host()), PlanAwareConfirmationProvider)


def test_approve_runs_every_step_of_the_plan_after_one_question(pool: ThreadPoolExecutor) -> None:
    host = _Host()
    provider = _provider(host)
    provider.expect_plan((_step(1), _step(2, EMPTY)))

    first = _ask(pool, provider, _task(1))
    plan = _published(host)
    assert not first.done()  # waiting for the answer
    assert provider.pending == plan
    assert plan.expires_at == NOW + timedelta(seconds=10)
    assert plan.confirmation_id.startswith("c_")
    provider.answer(plan.confirmation_id, APPROVE)

    assert first.result(5) is True
    assert provider.confirm(_task(2), WRITE) is True
    assert len(host.published) == 1  # asked once for the whole plan
    assert host.settled == [ConfirmationOutcome(APPROVE, BY_USER)]
    assert provider.pending is None


def test_reject_refuses_that_step_and_every_later_one(pool: ThreadPoolExecutor) -> None:
    host = _Host()
    provider = _provider(host)
    provider.expect_plan((_step(1), _step(2, EMPTY)))

    first = _ask(pool, provider, _task(1))
    provider.answer(_published(host).confirmation_id, REJECT)

    assert first.result(5) is False
    assert provider.confirm(_task(2), WRITE) is False
    assert host.settled == [ConfirmationOutcome(REJECT, BY_USER)]
    assert len(host.published) == 1


def test_no_answer_in_time_is_a_rejection(pool: ThreadPoolExecutor) -> None:
    host = _Host()
    provider = _provider(host, timeout_s=0.2)
    provider.expect_plan((_step(1), _step(2, EMPTY)))

    started = time.monotonic()
    first = _ask(pool, provider, _task(1))
    plan = _published(host)

    assert first.result(5) is False
    assert time.monotonic() - started >= 0.2
    assert host.settled == [ConfirmationOutcome(REJECT, BY_TIMEOUT)]
    assert provider.pending is None
    assert provider.confirm(_task(2), WRITE) is False
    with pytest.raises(ApiError) as late:
        provider.answer(plan.confirmation_id, APPROVE)
    assert late.value.code is ErrorCode.NO_PENDING_CONFIRMATION
    # The late answer changed nothing.
    assert host.settled == [ConfirmationOutcome(REJECT, BY_TIMEOUT)]


def test_a_shutdown_while_waiting_is_a_rejection(pool: ThreadPoolExecutor) -> None:
    host = _Host()
    provider = _provider(host)
    provider.expect_plan((_step(1),))

    first = _ask(pool, provider, _task(1))
    _published(host)
    provider.close(BY_SHUTDOWN)

    assert first.result(5) is False
    assert host.settled == [ConfirmationOutcome(REJECT, BY_SHUTDOWN)]
    assert provider.pending is None


def test_a_shutdown_before_the_plan_is_asked_refuses_without_asking() -> None:
    host = _Host()
    provider = _provider(host)
    provider.expect_plan((_step(1),))
    provider.close(BY_SHUTDOWN)

    assert provider.confirm(_task(1), WRITE) is False
    assert host.published == []
    assert host.settled == [ConfirmationOutcome(REJECT, BY_SHUTDOWN)]


def test_once_closed_no_later_step_runs_even_after_an_approval(pool: ThreadPoolExecutor) -> None:
    """Review round 1, L2: a turn that finished (deadline) or a server that's
    stopping starts no further step; the recorded decision stays the user's."""
    host = _Host()
    provider = _provider(host)
    provider.expect_plan((_step(1), _step(2, EMPTY)))
    first = _ask(pool, provider, _task(1))
    provider.answer(_published(host).confirmation_id, APPROVE)
    assert first.result(5) is True

    provider.close(BY_TURN_END)

    assert provider.confirm(_task(2), WRITE) is False
    assert host.settled == [ConfirmationOutcome(APPROVE, BY_USER)]


def test_the_step_just_approved_does_not_start_if_the_provider_closes_first(
    pool: ThreadPoolExecutor,
) -> None:
    """PR-3 review round 2, R2-L1: the user answers while the worker waits, and the turn
    finishes (the deadline) before the worker takes the lock back. The step the user just
    approved must not start after the turn is over."""
    host = _Host()
    provider = _provider(host)
    provider.expect_plan((_step(1), _step(2, EMPTY)))
    first = _ask(pool, provider, _task(1))
    plan = _published(host)

    with host.lock:
        # The answer and the end of the turn both land before the worker, which was
        # woken by the answer, can take the lock.
        provider.answer(plan.confirmation_id, APPROVE)
        provider.close(BY_TURN_END)
        time.sleep(0.05)  # the worker is now blocked on the lock, holding the approval

    assert first.result(5) is False
    assert provider.confirm(_task(2), WRITE) is False
    assert host.settled == [ConfirmationOutcome(APPROVE, BY_USER)]  # the user's answer stands


def test_a_second_answer_is_refused_and_the_first_stands(pool: ThreadPoolExecutor) -> None:
    host = _Host()
    provider = _provider(host)
    provider.expect_plan((_step(1),))
    first = _ask(pool, provider, _task(1))
    plan = _published(host)
    provider.answer(plan.confirmation_id, REJECT)

    with pytest.raises(ApiError) as again:
        provider.answer(plan.confirmation_id, APPROVE)

    assert again.value.code is ErrorCode.NO_PENDING_CONFIRMATION
    assert first.result(5) is False
    assert host.settled == [ConfirmationOutcome(REJECT, BY_USER)]


def test_a_stale_id_is_refused_and_the_plan_keeps_waiting(pool: ThreadPoolExecutor) -> None:
    host = _Host()
    provider = _provider(host)
    provider.expect_plan((_step(1),))
    first = _ask(pool, provider, _task(1))
    plan = _published(host)

    for stale in ("c_someone_elses", plan.confirmation_id + "x", ""):
        with pytest.raises(ApiError) as refused:
            provider.answer(stale, APPROVE)
        assert refused.value.code is ErrorCode.STALE_CONFIRMATION
    assert provider.pending == plan
    assert not first.done()
    provider.answer(plan.confirmation_id, APPROVE)
    assert first.result(5) is True


def test_an_answer_before_any_plan_is_asked_is_refused() -> None:
    host = _Host()
    provider = _provider(host)
    provider.expect_plan((_step(1),))

    with pytest.raises(ApiError) as refused:
        provider.answer("c_anything", APPROVE)

    assert refused.value.code is ErrorCode.NO_PENDING_CONFIRMATION
    assert host.settled == []


# -- Only the published plan runs ("Decisions (2026-09-30)", item 2) ------------------------------


def test_an_unlisted_step_is_refused_without_asking() -> None:
    host = _Host()
    provider = _provider(host)
    provider.expect_plan((_step(1),))

    assert provider.confirm(_task(5), WRITE) is False
    assert host.published == []
    # The plan can't be trusted to be what runs any more: nothing is approved.
    assert provider.confirm(_task(1), WRITE) is False
    assert host.published == []


@pytest.mark.parametrize("tier", [PermissionTier.EXTERNAL_EFFECT, PermissionTier.READ])
def test_a_step_whose_tier_is_not_the_published_plans_is_refused(tier: PermissionTier) -> None:
    """Rule 2 note: a tier not in the published plan never rides on its approval."""
    host = _Host()
    provider = _provider(host)
    provider.expect_plan((_step(1),))

    assert provider.confirm(_task(1, tier), tier) is False
    assert host.published == []


def test_a_tier_asked_that_isnt_the_plans_is_refused_even_if_the_task_agrees() -> None:
    """Review round 1, L3: the tier asked is checked on its own, not only the task's."""
    host = _Host()
    provider = _provider(host)
    provider.expect_plan((_step(1),))

    assert provider.confirm(_task(1, WRITE), PermissionTier.EXTERNAL_EFFECT) is False
    assert host.published == []


def test_a_read_step_is_never_approved_even_if_a_plan_lists_it() -> None:
    """Review round 1, L3: only file-changing tiers are confirmed by a plan."""
    host = _Host()
    provider = _provider(host)
    provider.expect_plan((_step(1, tier=PermissionTier.READ),))

    assert provider.confirm(_task(1, PermissionTier.READ), PermissionTier.READ) is False
    assert host.published == []


def test_a_task_whose_permission_disagrees_with_the_tier_asked_is_refused() -> None:
    host = _Host()
    provider = _provider(host)
    provider.expect_plan((_step(1),))

    assert provider.confirm(_task(1, PermissionTier.EXTERNAL_EFFECT), WRITE) is False
    assert host.published == []


def test_an_unlisted_step_after_an_approval_is_still_refused(pool: ThreadPoolExecutor) -> None:
    host = _Host()
    provider = _provider(host)
    provider.expect_plan((_step(1), _step(2, EMPTY)))
    first = _ask(pool, provider, _task(1))
    provider.answer(_published(host).confirmation_id, APPROVE)
    assert first.result(5) is True

    assert provider.confirm(_task(3), WRITE) is False  # not step 2: refused
    assert provider.confirm(_task(2), WRITE) is False  # and nothing after it


def test_steps_asked_out_of_order_are_refused() -> None:
    host = _Host()
    provider = _provider(host)
    provider.expect_plan((_step(1), _step(2, EMPTY)))

    assert provider.confirm(_task(2), WRITE) is False
    assert host.published == []


def test_a_step_past_the_end_of_the_plan_is_refused(pool: ThreadPoolExecutor) -> None:
    host = _Host()
    provider = _provider(host)
    provider.expect_plan((_step(1),))
    first = _ask(pool, provider, _task(1))
    provider.answer(_published(host).confirmation_id, APPROVE)
    assert first.result(5) is True

    assert provider.confirm(_task(1), WRITE) is False  # the same step asked twice
    assert provider.confirm(_task(2), WRITE) is False


def test_without_a_plan_nothing_is_approved() -> None:
    host = _Host()
    provider = _provider(host)

    assert provider.confirm(_task(1), WRITE) is False
    assert host.published == []


def test_an_empty_plan_publishes_nothing() -> None:
    """A plan without a file-changing step asks nothing ("Decisions (2026-09-30)", item 2)."""
    host = _Host()
    provider = _provider(host)
    provider.expect_plan(())

    assert provider.confirm(_task(0), WRITE) is False
    assert host.published == []


def test_a_second_plan_for_one_turn_refuses_everything() -> None:
    host = _Host()
    provider = _provider(host)
    provider.expect_plan((_step(1),))
    provider.expect_plan((_step(1),))

    assert provider.confirm(_task(1), WRITE) is False
    assert host.published == []


def test_a_turn_already_finishing_is_not_asked(pool: ThreadPoolExecutor) -> None:
    host = _Host(publish_ok=False)
    provider = _provider(host)
    provider.expect_plan((_step(1), _step(2, EMPTY)))

    answer: Future[bool] = _ask(pool, provider, _task(1))

    assert answer.result(5) is False  # at once: nobody is asked
    assert provider.pending is None
    assert host.settled == [ConfirmationOutcome(REJECT, BY_TURN_END)]
    assert provider.confirm(_task(2), WRITE) is False


def test_answers_from_two_threads_at_once_record_exactly_one(pool: ThreadPoolExecutor) -> None:
    host = _Host()
    provider = _provider(host)
    provider.expect_plan((_step(1),))
    first = _ask(pool, provider, _task(1))
    plan = _published(host)
    barrier = threading.Barrier(2)
    results: list[str] = []

    def answer(decision: str) -> Callable[[], None]:
        def run() -> None:
            barrier.wait(5)
            try:
                provider.answer(plan.confirmation_id, decision)
                results.append(decision)
            except ApiError as refused:
                results.append(refused.code.value)

        return run

    threads = [threading.Thread(target=answer(d)) for d in (APPROVE, REJECT)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)

    assert sorted(results) in (
        sorted([APPROVE, "no_pending_confirmation"]),
        sorted([REJECT, "no_pending_confirmation"]),
    )
    winner = APPROVE if APPROVE in results else REJECT
    assert host.settled == [ConfirmationOutcome(winner, BY_USER)]
    assert first.result(5) is (winner == APPROVE)

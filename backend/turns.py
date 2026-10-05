"""Turns — the turn procedure, its worker thread, the deadline and finalization.

Design Sections 3.3, 4.3 and 5, and items 2, 4 and 5 of "What a PR-2 author
must do" (``docs/reviews/pipeline-failure-and-output-dir-review.md``).

A turn runs on a **deep copy** of the session's ``BuddyState``, on its own
daemon thread. The copy is committed only if the run returns normally;
if it raises, or the deadline passes first, it is discarded unread. A turn is
finalized exactly once, under the session lock: ``reply``, ``reply_source``,
``outcome``, the delivered files and ``detail`` are set together, in that
one step. The reply is the pipeline's ``state.reply`` as it is, or one of
the two fallback constants from ``buddy.voice_catalog``; this module
contains no sentence about what happened to the user's file.

Threads, not processes: a Python thread can't be killed. When the deadline
passes, the turn is finalized as failed and the session is free again, but
the thread runs on until its own calls return, writing only to its
discarded copy and its turn folder. It keeps its slot under the global cap
until it exits (design Section 9.2).

Confirmation (PR-3): each turn gets its own ``WebConfirmationProvider``
(``backend.confirmation``). While the turn's plan waits for the user, the
turn is ``awaiting_confirmation`` and its deadline is paused, so the
deadline counts only the time the pipeline works (design Section 4.3); the
plan's own timeout bounds the wait. Finishing the turn, or shutting the
server down, closes the provider, which turns any waiting or later question
into a rejection.
"""

from __future__ import annotations

import copy
import dataclasses
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.confirmation import (
    BY_SHUTDOWN,
    BY_TURN_END,
    ConfirmationOutcome,
    PendingPlan,
    StaticConfirmationProvider,
    WebConfirmationProvider,
)
from backend.delivery import delivered_files, make_read_only, uploads_unchanged
from backend.errors import ApiError, ErrorCode
from backend.sessions import (
    AWAITING_CONFIRMATION,
    FINISHED,
    RUNNING,
    DeliveredFile,
    SessionRecord,
    SessionRegistry,
    TurnRecord,
    utc_now,
)
from backend.settings import Settings
from buddy.hands import ConfirmationProvider
from buddy.models import StepResult, VerificationResult
from buddy.state import BuddyState
from buddy.voice import is_verified_delivery
from buddy.voice_catalog import FALLBACK_INTEGRITY_FAILURE, FALLBACK_PIPELINE_ERROR
from core.engine import Engine

logger = logging.getLogger(__name__)

# Builds the engine for one turn, given that turn's confirmation provider
# (design Section 3.5). Called on the turn's worker thread, so an error
# here fails the turn like any other.
EngineFactory = Callable[[ConfirmationProvider], Engine]

DELIVERED = "delivered"
NOT_DELIVERED = "not_delivered"
FAILED = "failed"
INTEGRITY_FAILURE = "integrity_failure"
PIPELINE = "pipeline"
PIPELINE_FALLBACK = "pipeline_fallback"


@dataclass(frozen=True)
class Finalization:
    """Everything a finished turn records, decided together."""

    outcome: str
    reply: str
    reply_source: str
    delivered: tuple[DeliveredFile, ...] = ()
    detail: dict[str, Any] | None = None
    commit: bool = False


@dataclass
class _Job:
    """One unfinished turn. Its mutable fields change only under the session lock."""

    record: SessionRecord
    turn: TurnRecord
    work: BuddyState
    turn_dir: Path
    expected: dict[str, tuple[Path, str]]
    mode: str | None
    # Seconds of working time the turn has left; the deadline is paused
    # while its plan waits for the user.
    deadline_left: float
    provider: ConfirmationProvider | None = None
    gate: WebConfirmationProvider | None = None
    timer: threading.Timer | None = None
    # When the current timer was armed (time.monotonic()).
    armed_at: float = 0.0
    # Bumped whenever the timer is disarmed, so a timer that already fired
    # but lost the race for the lock does nothing.
    generation: int = 0
    # Set once the deadline claims the turn: no plan may be published after.
    finishing: bool = False


def decide(
    state: BuddyState | None,
    *,
    timed_out: bool,
    integrity_ok: bool,
    turn_dir: Path,
    turn_id: str,
    now: datetime,
) -> Finalization:
    """What a finished turn records, from the facts alone.

    ``state`` is ``None`` when ``engine.run`` raised (or the engine couldn't
    be built): that run's state is never read, whatever it holds (review
    M1). Precedence: a changed upload, then no normal finish, then no reply,
    then a stage failure, then delivery.
    """
    if not integrity_ok:
        return Finalization(INTEGRITY_FAILURE, FALLBACK_INTEGRITY_FAILURE, PIPELINE_FALLBACK)
    if state is None or timed_out:
        return Finalization(FAILED, FALLBACK_PIPELINE_ERROR, PIPELINE_FALLBACK)
    reply = state.reply
    if not isinstance(reply, str) or not reply.strip():
        logger.error("Turn %s: the pipeline finished without a reply", turn_id)
        return Finalization(FAILED, FALLBACK_PIPELINE_ERROR, PIPELINE_FALLBACK)
    if state.failure is not None:
        return Finalization(FAILED, reply, PIPELINE, detail=turn_detail(state), commit=True)
    files = delivered_files(state, turn_dir=turn_dir, turn_id=turn_id, now=now)
    if files:
        return Finalization(DELIVERED, reply, PIPELINE, files, turn_detail(state), commit=True)
    if is_verified_delivery(state):
        # The reply says a file was handed back, but the API can't hand it
        # over; the reply would be false, so it is replaced, not sent.
        logger.error("Turn %s: a verified delivery failed the delivery check", turn_id)
        return Finalization(FAILED, FALLBACK_PIPELINE_ERROR, PIPELINE_FALLBACK)
    return Finalization(NOT_DELIVERED, reply, PIPELINE, detail=turn_detail(state), commit=True)


def turn_detail(state: BuddyState) -> dict[str, Any]:
    """The structured data behind a turn (design Section 1.3.1).

    No Skill summaries or self-reported findings, no exception text, and
    file names only, never paths. On a turn where a stage raised, no step
    counts as verified.
    """
    understanding = state.understanding
    execution = state.execution_result
    failed = state.failure is not None
    return {
        "intent": understanding.intent if understanding is not None else state.intent,
        "clarification_required": (
            state.reasoning.clarification_required if state.reasoning is not None else None
        ),
        "steps": [
            {
                "capability": step.capability,
                "permission_tier": step.permission_tier,
                "status": step.status,
                "verified": not failed and _step_verified(step, state.verification_result),
                "recheck": step.recheck,
                "input_name": Path(step.input_reference).name if step.input_reference else None,
                "output_names": [Path(output).name for output in step.outputs],
            }
            for step in (execution.steps if execution is not None else ())
        ],
        "stopped_at": execution.stopped_at if execution is not None else None,
    }


def _step_verified(step: StepResult, verification: VerificationResult | None) -> bool:
    if verification is None:
        return False
    return any(
        verdict.passed
        and verdict.capability == step.capability
        and verdict.input_reference == step.input_reference
        and verdict.recheck == step.recheck
        for verdict in verification.steps
    )


class TurnRunner:
    """Starts turns, runs them on worker threads, and finalizes each exactly once."""

    def __init__(
        self,
        settings: Settings,
        engine_factory: EngineFactory,
        registry: SessionRegistry,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._settings = settings
        self._engine_factory = engine_factory
        self._registry = registry
        self._clock = clock
        self._slots_lock = threading.Lock()
        self._running = 0
        # Every unfinished turn, by (session key, turn id), for the
        # confirmation endpoint and for shutdown.
        self._jobs: dict[tuple[str, str], _Job] = {}
        self._jobs_lock = threading.Lock()

    @property
    def running(self) -> int:
        """Worker threads that haven't exited, across all sessions."""
        with self._slots_lock:
            return self._running

    def start(
        self, record: SessionRecord, message: str, mode: str | None, file_id: str | None
    ) -> TurnRecord:
        """Start a turn (design Section 3.3, steps 1 to 5). Returns it, running.

        Raises:
            ApiError: ``session_busy``, ``file_not_found`` or ``server_busy``.
                A refused request creates no turn.
        """
        settings = self._settings
        with record.lock:
            if record.busy:
                raise ApiError(ErrorCode.SESSION_BUSY)
            target = None
            if file_id is not None:
                target = record.files.get(file_id)
                if target is None:
                    raise ApiError(ErrorCode.FILE_NOT_FOUND)
            if not self._take_slot():
                raise ApiError(
                    ErrorCode.SERVER_BUSY,
                    headers={"Retry-After": str(settings.busy_retry_after_s)},
                )
            turn: TurnRecord | None = None
            turn_dir: Path | None = None
            try:
                seq = record.next_seq
                # The number is used up even if what follows fails, so a retry
                # never lands on a folder this attempt created.
                record.next_seq += 1
                turn_id = f"t{seq:04d}"
                turn_dir = record.turn_dir(settings, turn_id)
                turn_dir.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                # A folder that already exists means something is wrong: refuse
                # rather than reuse it (design Section 3.4).
                turn_dir.mkdir(mode=0o700, exist_ok=False)

                expected = {f.file_id: (f.path, f.sha256) for f in record.files.values()}
                work = copy.deepcopy(record.state)
                work.reset_transient()  # never let a stale failure skip this turn's stages
                work.user_message = message
                if mode is not None:
                    work.context.set_mode(mode)
                if target is not None:
                    work.context.switch_active_file(str(target.path))
                work.turn_output_dir = str(turn_dir)
                target_id = file_id if file_id is not None else record.active_file_id
                # Files handed back earlier *from this same upload*: only then is
                # "made from your original upload, not from a file I handed back
                # earlier" the point (review round 2, R2-M1). A copy the user
                # re-uploaded is an upload of its own, with its own count.
                work.earlier_deliveries = sum(
                    len(t.delivered_files)
                    for t in record.turns
                    if t.finished
                    and t.outcome == DELIVERED
                    and target_id is not None
                    and t.target_file_id == target_id
                )
                work.context.record_user_message(message)

                turn = TurnRecord(
                    turn_id=turn_id,
                    seq=seq,
                    message=message,
                    mode=work.context.mode,
                    started_at=self._clock(),
                    target_file_id=target_id,
                )
                job = _Job(
                    record,
                    turn,
                    work,
                    turn_dir,
                    expected,
                    mode,
                    deadline_left=settings.turn_deadline_s,
                )
                job.provider = self._provider_for(job)
                record.turns.append(turn)
                record.running_turn_id = turn_id
                self._registry.saved(record, turn)
            except BaseException:
                if turn is not None and turn in record.turns:
                    record.turns.remove(turn)
                    record.running_turn_id = None
                    _remove_empty(turn_dir)
                self._release_slot()
                raise
            with self._jobs_lock:
                self._jobs[(record.key, turn_id)] = job

        logger.info(
            "Turn %s started for session %s (message length %d)",
            turn_id,
            record.key,
            len(message),
        )
        worker = threading.Thread(
            target=self._run, args=(job,), name=f"turn-{record.key[:8]}-{turn_id}", daemon=True
        )
        try:
            with record.lock:
                self._arm_deadline(job)
            worker.start()
        except BaseException:
            # No worker will finish this turn, so finish it here, as failed.
            try:
                with record.lock:
                    self._disarm_deadline(job)
                self._finalize(job, None, timed_out=False)
            finally:
                self._release_slot()
            raise
        return turn

    def answer(
        self, record: SessionRecord, turn_id: str, confirmation_id: str, decision: str
    ) -> TurnRecord:
        """Record the user's answer to the plan waiting on a turn; return the turn.

        The plan stops being pending before this returns, so no later
        response shows it. The caller holds no lock; the turn returned is
        read under the session lock by the caller.

        Raises:
            ApiError: ``turn_not_found``; ``no_pending_confirmation`` when no
                plan is waiting on the turn (none was asked, or it was
                already answered, timed out or closed);
                ``stale_confirmation`` when ``confirmation_id`` isn't the
                waiting plan's.
        """
        with record.lock:
            turn = record.turn(turn_id)
            if turn is None:
                raise ApiError(ErrorCode.TURN_NOT_FOUND)
            with self._jobs_lock:
                job = self._jobs.get((record.key, turn_id))
            if job is None or job.gate is None or turn.pending_confirmation is None:
                raise ApiError(ErrorCode.NO_PENDING_CONFIRMATION)
            job.gate.answer(confirmation_id, decision)
            return turn

    def shutdown(self) -> None:
        """The server is stopping: every plan waiting, or still to be asked, is rejected.

        Design Section 1.5: a shutdown is treated as a rejection, never as
        an approval. A turn whose worker can't finish before the process
        exits is reported as failed after the restart (``SessionStore``).
        """
        with self._jobs_lock:
            jobs = list(self._jobs.values())
        for job in jobs:
            if job.gate is not None:
                job.gate.close(BY_SHUTDOWN)
        if jobs:
            logger.info("Shutting down with %d unfinished turn(s)", len(jobs))

    def _provider_for(self, job: _Job) -> ConfirmationProvider:
        """The turn's confirmation provider: the user's plan approval, or the test setting."""
        if self._settings.auto_approve:
            return StaticConfirmationProvider(approve=True)
        gate = WebConfirmationProvider(
            lock=job.record.lock,
            timeout_s=self._settings.confirmation_timeout_s,
            clock=self._clock,
            on_publish=lambda plan: self._on_publish(job, plan),
            on_settle=lambda outcome: self._on_settle(job, outcome),
        )
        job.gate = gate
        return gate

    def _run(self, job: _Job) -> None:
        """The worker: run the pipeline, then finalize, whatever happens (item 4)."""
        final_state: BuddyState | None = None
        try:
            assert job.provider is not None
            engine = self._engine_factory(job.provider)
            result = engine.run(job.work)
            final_state = result if isinstance(result, BuddyState) else None
        except Exception:
            logger.exception("Turn %s for session %s raised", job.turn.turn_id, job.record.key)
            final_state = None
        finally:
            try:
                with job.record.lock:
                    self._disarm_deadline(job)
                self._finalize(job, final_state, timed_out=False)
            finally:
                self._release_slot()

    def _expire(self, job: _Job, generation: int) -> None:
        """The deadline passed: finalize as failed, unless the turn already finished."""
        with job.record.lock:
            if generation != job.generation or job.turn.finished or job.finishing:
                return  # disarmed, paused or finished since this timer was armed
            job.finishing = True
        self._finalize(job, None, timed_out=True)

    # -- The deadline: armed while the pipeline works, paused while the plan waits -------

    def _arm_deadline(self, job: _Job) -> None:
        """Start the deadline timer for the working time left. The caller holds the lock."""
        job.generation += 1
        timer = threading.Timer(
            max(job.deadline_left, 0.0), self._expire, args=(job, job.generation)
        )
        timer.daemon = True
        job.timer = timer
        job.armed_at = time.monotonic()
        timer.start()

    def _disarm_deadline(self, job: _Job) -> None:
        """Stop the deadline timer, keeping the working time left. The caller holds the lock."""
        job.generation += 1
        if job.timer is not None:
            job.timer.cancel()
            job.deadline_left -= time.monotonic() - job.armed_at
            job.timer = None

    def _on_publish(self, job: _Job, plan: PendingPlan) -> bool:
        """The provider is about to show the plan (under the session lock)."""
        turn = job.turn
        if turn.finished or job.finishing:
            return False
        turn.pending_confirmation = plan
        turn.status = AWAITING_CONFIRMATION
        self._disarm_deadline(job)
        logger.info(
            "Turn %s for session %s is waiting for its plan's answer (%d step(s))",
            turn.turn_id,
            job.record.key,
            len(plan.steps),
        )
        return True

    def _on_settle(self, job: _Job, outcome: ConfirmationOutcome) -> None:
        """The plan was decided (under the session lock). A finished turn never changes."""
        turn = job.turn
        if turn.finished:
            return
        turn.confirmation = outcome
        turn.pending_confirmation = None
        if turn.status == AWAITING_CONFIRMATION:
            turn.status = RUNNING
            if not job.finishing:
                self._arm_deadline(job)
        logger.info(
            "Turn %s for session %s: plan %s (%s)",
            turn.turn_id,
            job.record.key,
            outcome.decision,
            outcome.source,
        )

    # -- Finalization -------------------------------------------------------------------

    def _finalize(self, job: _Job, state: BuddyState | None, *, timed_out: bool) -> None:
        record, turn = job.record, job.turn
        with record.lock:
            if turn.finished:
                return  # one-shot: a late worker or timer changes nothing
        if timed_out:
            logger.warning("Turn %s for session %s passed its deadline", turn.turn_id, record.key)
        now = self._clock()
        # Nothing before the finishing block may leave the turn running for
        # good (review round 3, R3-L4): any failure here, even a
        # BaseException, finishes it as failed with nothing handed back.
        try:
            integrity_ok = uploads_unchanged(job.expected)
            result = decide(
                state,
                timed_out=timed_out,
                integrity_ok=integrity_ok,
                turn_dir=job.turn_dir,
                turn_id=turn.turn_id,
                now=now,
            )
        except BaseException:
            logger.exception("Turn %s: finalization failed; using the fallback", turn.turn_id)
            result = Finalization(FAILED, FALLBACK_PIPELINE_ERROR, PIPELINE_FALLBACK)
        if result.outcome == INTEGRITY_FAILURE:
            logger.critical(
                "Turn %s for session %s: an upload changed; every file is held back",
                turn.turn_id,
                record.key,
            )
        try:
            make_read_only(result.delivered)
        except BaseException:
            logger.exception("Turn %s: marking files read-only failed; using the fallback",
                             turn.turn_id)
            result = Finalization(FAILED, FALLBACK_PIPELINE_ERROR, PIPELINE_FALLBACK)

        with record.lock:
            if turn.finished:
                return
            # No plan may be asked, or stay waiting, once the turn finishes.
            job.finishing = True
            self._disarm_deadline(job)
            if job.gate is not None:
                job.gate.close(BY_TURN_END)
            turn.pending_confirmation = None
            # The finished record is written before anyone can see the turn
            # finished (review M3), so memory and disk always agree on what
            # was handed back. If it can't be written, the turn is finished
            # as failed with nothing handed back, which is also what a
            # restart would report from the record written at the start.
            if not self._save_finished(record, turn, result, now):
                result = Finalization(FAILED, FALLBACK_PIPELINE_ERROR, PIPELINE_FALLBACK)
                self._save_finished(record, turn, result, now)
            if result.commit and state is not None:
                state.context.record_buddy_reply(result.reply)
                state.reset_transient()
                record.state = state
                record.active_file_id = _file_id_for(record, state.context.active_file)
            else:
                # Design 3.3 step 7: keep the pre-turn state; record only the
                # message and the fallback reply. The mode is the user's
                # setting, not a result, so it applies either way.
                context = record.state.context
                if job.mode is not None:
                    context.set_mode(job.mode)
                context.record_user_message(turn.message)
                context.record_buddy_reply(result.reply)
            turn.outcome = result.outcome
            turn.reply = result.reply
            turn.reply_source = result.reply_source
            turn.delivered_files = result.delivered
            turn.detail = result.detail
            turn.finished_at = now
            turn.status = FINISHED
            record.running_turn_id = None
            with self._jobs_lock:
                self._jobs.pop((record.key, turn.turn_id), None)
            try:
                self._registry.save_session(record)
            except Exception:
                # The turn's own record is on disk; only the session's context
                # (the conversation) misses this turn after a restart.
                logger.exception("Turn %s: the session record couldn't be saved", turn.turn_id)
        logger.info(
            "Turn %s for session %s finished: %s (%s)",
            turn.turn_id,
            record.key,
            result.outcome,
            result.reply_source,
        )

    def _save_finished(
        self, record: SessionRecord, turn: TurnRecord, result: Finalization, now: datetime
    ) -> bool:
        """Write ``turn`` as ``result`` finishes it, without changing it. False if that failed."""
        finished = dataclasses.replace(
            turn,
            status=FINISHED,
            outcome=result.outcome,
            reply=result.reply,
            reply_source=result.reply_source,
            delivered_files=result.delivered,
            detail=result.detail,
            finished_at=now,
            pending_confirmation=None,
        )
        try:
            self._registry.save_turn(record, finished)
        except BaseException:
            # Any failure, even a BaseException, leaves the turn to finish as
            # failed rather than stay running for good (round 2, R2-L2).
            logger.exception("Turn %s: the finished record couldn't be saved", turn.turn_id)
            return False
        return True

    def _take_slot(self) -> bool:
        with self._slots_lock:
            if self._running >= self._settings.max_concurrent_turns:
                return False
            self._running += 1
            return True

    def _release_slot(self) -> None:
        with self._slots_lock:
            self._running -= 1


def _file_id_for(record: SessionRecord, path: str | None) -> str | None:
    if path is None:
        return None
    return next((f.file_id for f in record.files.values() if str(f.path) == path), None)


def _remove_empty(folder: Path | None) -> None:
    if folder is None:
        return
    try:
        folder.rmdir()
    except OSError:
        pass

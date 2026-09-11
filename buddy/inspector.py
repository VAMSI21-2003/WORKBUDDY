"""Inspector — checks executed work against the user's original intent."""

import logging
from dataclasses import dataclass
from typing import Protocol

import pandas as pd

from buddy.models import ExecutionResult, Understanding, VerificationResult
from buddy.state import BuddyState

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class VerificationRequest:
    """The inputs available for checking executed work against user intent.

    A plain, self-contained bundle of everything a ``Verifier`` needs to
    work from — assembled mechanically from ``BuddyState`` by the
    Inspector stage, so a verifier never has to depend on ``BuddyState``
    directly.

    Attributes:
        understanding: The structured understanding produced by
            Brain — the original intent to check the outcome
            against.
        execution_result: The structured result produced by Hands, if
            it has run yet.
    """

    understanding: Understanding
    execution_result: ExecutionResult | None


class Verifier(Protocol):
    """Produces a ``VerificationResult`` from a ``VerificationRequest``.

    This is the seam where real, independent verification is plugged in.
    The Inspector stage itself never judges whether work succeeded; it
    only calls whichever ``Verifier`` it was given.
    """

    def __call__(self, request: VerificationRequest) -> VerificationResult:
        """Verify ``request`` and return the resulting judgment."""
        ...


class NullVerifier:
    """A ``Verifier`` that performs no verification.

    Never claims success it hasn't checked. Serves as a safe default so
    ``Inspector`` can be constructed and exercised — in tests, or wired
    into the Engine — before a real verifier is plugged in.
    """

    def __call__(self, request: VerificationRequest) -> VerificationResult:
        """Return an unverified, unpassed result for any request.

        Args:
            request: The request that would otherwise be verified.

        Returns:
            A ``VerificationResult`` with ``passed=False`` and zero
            confidence, noting that no verifier has been configured.
        """
        return VerificationResult(
            passed=False,
            confidence=0.0,
            findings=("no verifier has been configured",),
        )


class SpreadsheetDedupeVerifier:
    """Independently confirms an output spreadsheet has no duplicate rows.

    Deliberately blind to everything the Skill *said* about itself: it
    never reads a ``SkillResult``'s ``status``, ``summary``, or
    ``metadata`` (none of which even reach this stage — ``ExecutionResult``
    carries none of them). It takes only the output path Hands recorded,
    opens *that specific file* fresh via ``pandas.read_excel``, and
    recomputes ``duplicated().sum()`` itself. See
    ``docs/design/slice-1-dedupe-design.md`` Section D for the full design.
    """

    def __call__(self, request: VerificationRequest) -> VerificationResult:
        """Verify ``request`` by independently re-reading its output file.

        Args:
            request: The request to verify.

        Returns:
            A ``VerificationResult`` reflecting what was actually found in
            the output file — not what Hands or a Skill reported.
        """
        execution_result = request.execution_result
        if execution_result is None or not execution_result.success or not execution_result.outputs:
            return VerificationResult(
                passed=False,
                confidence=1.0,
                findings=("execution did not produce an output file",),
            )

        output_path = execution_result.outputs[0]
        try:
            written = pd.read_excel(output_path)  # fresh, independent read of the artifact
        except Exception as exc:  # noqa: BLE001 — surfaced as a finding, not raised
            logger.exception("SpreadsheetDedupeVerifier: failed to read %r", output_path)
            return VerificationResult(
                passed=False,
                confidence=1.0,
                findings=(f"could not independently read {output_path!r}: {exc}",),
            )

        duplicate_count = int(written.duplicated().sum())  # recomputed from scratch
        passed = duplicate_count == 0
        return VerificationResult(
            passed=passed,
            confidence=1.0,
            findings=(
                f"output has {len(written)} row(s), {duplicate_count} duplicate row(s) remaining",
            ),
            recommendations=() if passed else ("re-run deduplication; duplicates remain",),
        )


class Inspector:
    """The Inspector stage of Buddy's cognitive cycle.

    Inspector independently checks the outcome collected during Hands
    against the *original* intent captured back in Brain — not
    against what a Skill merely reported (see
    ``docs/WORKBUDDY_ARCHITECTURE.md`` Section 4; ``BUDDY_SKILLS.md``
    Section 9). A Skill's own self-check (``Skill.verify()``) confirms its
    result is well-formed; it is necessary but never sufficient —
    Inspector's judgment is the one that actually decides whether the
    work succeeded.

    Inspector contains no verification logic of its own. All of the actual
    checking is delegated to an injected ``Verifier``. Inspector's own
    responsibility is purely mechanical: assemble the verifier's input
    from ``BuddyState``, call the verifier, and record what it returns.
    This keeps Inspector a stable, swappable framework — any implementation
    conforming to ``Verifier`` can be substituted without ever changing
    this stage.
    """

    def __init__(self, verifier: Verifier | None = None) -> None:
        """Initialize the stage.

        Args:
            verifier: The verifier to delegate checking to. Defaults to
                ``NullVerifier``, which never claims success it hasn't
                checked.
        """
        if verifier is None:
            logger.debug("No verifier provided; falling back to NullVerifier")
        self._verifier = verifier if verifier is not None else NullVerifier()

    def __call__(self, state: BuddyState) -> BuddyState:
        """Verify ``state`` and record the resulting judgment.

        Args:
            state: The current ``BuddyState`` for this turn.

        Returns:
            The same state, with the ``VerificationResult`` recorded in
            ``state.verification_result``.
        """
        request = self._build_request(state)
        result = self._verifier(request)
        self._apply(state, result)
        logger.debug(
            "Inspector recorded passed=%s confidence=%.2f for session %r",
            result.passed,
            result.confidence,
            state.session_id,
        )
        return state

    @staticmethod
    def _build_request(state: BuddyState) -> VerificationRequest:
        """Assemble the verifier's input from the current state.

        This step is purely mechanical: it gathers what is already
        present on ``state`` into a plain, self-contained bundle. It
        performs no verification of its own. If Brain has not
        recorded an ``Understanding`` yet, an empty one is used rather
        than guessing at what it might have contained.

        Args:
            state: The current ``BuddyState`` for this turn.

        Returns:
            The request to hand to the configured verifier.
        """
        return VerificationRequest(
            understanding=state.understanding or Understanding(),
            execution_result=state.execution_result,
        )

    @staticmethod
    def _apply(state: BuddyState, result: VerificationResult) -> None:
        """Record the verifier's output onto ``state``.

        Args:
            state: The state to update.
            result: The verification result to record.
        """
        state.verification_result = result

"""Manual verification script for the "remove duplicate rows" slice.

Runs the real, end-to-end pipeline — including a real call to an LLM
provider (Groq if ``GROQ_API_KEY`` is set, else Anthropic via
``ClaudeProvider``) — against a real temporary .xlsx file with known
duplicate rows, and prints every stage's output for a human to eyeball.

This is throwaway verification tooling, NOT part of the automated test
suite (see tests/buddy/test_engine_factory.py for the equivalent test,
which uses a fake LLMProvider instead of the real API). It is meant to be
run and deleted/ignored, not maintained.

Usage:
    ANTHROPIC_API_KEY=sk-... python scripts/run_dedupe_manually.py
    GROQ_API_KEY=gsk-... python scripts/run_dedupe_manually.py --mode focus

Requires ``GROQ_API_KEY`` or ``ANTHROPIC_API_KEY`` to be set in the
environment (Groq is preferred when both are present, since it's free) —
this script makes a real, billed-where-applicable API call.
"""

import argparse
import hashlib
import os
import sys
import time
from pathlib import Path

import pandas as pd

# Make the repo root importable when this script is run directly, e.g.
# ``python scripts/run_dedupe_manually.py`` from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from buddy.engine_factory import build_engine  # noqa: E402
from buddy.llm import ClaudeProvider, GroqProvider, LLMProvider  # noqa: E402
from buddy.outcome import build_outcome_report  # noqa: E402
from buddy.state import BuddyState  # noqa: E402
from buddy.voice import PersonalityVoice, Voice, _verified_delivery  # noqa: E402

SCRATCH_DIR = Path(__file__).resolve().parent.parent / "scratch"


def _write_fixture_with_duplicates(path: Path) -> None:
    """Write a real .xlsx file with 6 rows: 2 exact duplicates of earlier
    rows and 1 fully-empty row, leaving 3 distinct, non-empty rows (Alice,
    Bob, Carol) once both problems are removed — so a multi-step request
    ("remove the empty rows and then the duplicates") has real work for
    every step. The empty row sits mid-file: a trailing empty row doesn't
    survive openpyxl's write/read round trip.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        {
            "name": ["Alice", "Bob", None, "Alice", "Carol", "Bob"],
            "amount": [10, 20, None, 10, 30, 20],
        }
    ).to_excel(path, index=False)


def _write_fixture_without_duplicates(path: Path) -> None:
    """Write a real .xlsx file with 4 distinct, non-empty rows and no
    duplicates: the design's N2 case, where there's nothing to remove."""
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        {
            "name": ["Alice", "Bob", "Carol", "Dana"],
            "amount": [10, 20, 30, 40],
        }
    ).to_excel(path, index=False)


def _write_two_sheet_fixture(path: Path) -> None:
    """Write a real .xlsx workbook with two sheets: ``Q1`` holds the default
    fixture's 6 rows (2 duplicates, 1 empty row) and ``Q2`` holds 2 distinct
    rows. The skills refuse it (backlog item 1a): nothing should come back.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path) as writer:
        pd.DataFrame(
            {
                "name": ["Alice", "Bob", None, "Alice", "Carol", "Bob"],
                "amount": [10, 20, None, 10, 30, 20],
            }
        ).to_excel(writer, sheet_name="Q1", index=False)
        pd.DataFrame({"name": ["Zed", "Yan"], "amount": [90, 80]}).to_excel(
            writer, sheet_name="Q2", index=False
        )


_FIXTURES = {
    "default": (_write_fixture_with_duplicates, "duplicates and an empty row"),
    "no-duplicates": (_write_fixture_without_duplicates, "no duplicates and no empty rows"),
    "two-sheets": (_write_two_sheet_fixture, "sheets Q1 (default rows) and Q2 (2 rows)"),
}


def _sheet_names(path: Path) -> list[str]:
    """Every sheet's name, read independently of the pipeline."""
    with pd.ExcelFile(path) as book:
        return list(book.sheet_names)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _print_header(title: str) -> None:
    print()
    print(f"--- {title} ---")


def _build_provider() -> LLMProvider:
    """Build whichever real ``LLMProvider`` this environment supports.

    Prefers Groq (``GROQ_API_KEY``) over Anthropic (``ANTHROPIC_API_KEY``)
    when both are set, since Groq is free. Raises
    ``buddy.llm.LLMConfigurationError`` (via the provider's own
    constructor) if neither key is set — the same honest failure this
    script already relied on, just from whichever provider is chosen.
    """
    if os.environ.get("GROQ_API_KEY"):
        print("Using GroqProvider (GROQ_API_KEY is set).")
        return GroqProvider()
    print("Using ClaudeProvider (ANTHROPIC_API_KEY).")
    return ClaudeProvider()


class _CountingProvider:
    """Wraps the real provider and records which system prompt each call used,
    so the output shows exactly which stages called the model."""

    def __init__(self, inner: LLMProvider) -> None:
        self._inner = inner
        self.calls: list[str] = []

    def complete(self, *, system: str, messages, max_tokens: int = 1024) -> str:  # type: ignore[no-untyped-def]
        self.calls.append(system.strip().splitlines()[0][:70])
        return self._inner.complete(system=system, messages=messages, max_tokens=max_tokens)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=("friend", "focus"),
        default="friend",
        help="Voice's personality mode for the final reply (default: friend).",
    )
    parser.add_argument(
        "--message",
        default="remove duplicate rows from this file",
        help=(
            "The user message to run through the pipeline (default: the "
            "original single-step dedupe request). Pass a compound "
            "request (e.g. \"check if this column exists, remove it, and "
            "give me a report and a dashboard\") to exercise ClaudePlanner's "
            "multi-step decomposition."
        ),
    )
    parser.add_argument(
        "--fixture",
        choices=tuple(_FIXTURES),
        default="default",
        help=(
            "The upload to run against (default: 6 rows, 2 exact duplicates, 1 empty row; "
            "no-duplicates: 4 distinct rows; two-sheets: the default rows on Q1, and Q2)."
        ),
    )
    return parser.parse_args()


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    args = _parse_args()

    input_path = SCRATCH_DIR / "uploads" / "manual_report.xlsx"
    write_fixture, fixture_description = _FIXTURES[args.fixture]
    started = time.time()
    write_fixture(input_path)
    original_before = _sha256(input_path)
    print(f"Wrote fixture ({args.fixture}: {fixture_description}) to: {input_path}")

    state = BuddyState.new(session_id="manual-session", workspace_id="manual-workspace")
    state.user_message = args.message
    state.context.add_uploaded_file(str(input_path))
    state.context.switch_active_file(str(input_path))
    state.context.set_mode(args.mode)
    print(f"Voice mode: {args.mode}")
    print(f"Message:    {args.message!r}")

    # A single real provider, shared by Brain/Think (via llm_provider) and
    # Voice (via PersonalityVoice) — Groq if GROQ_API_KEY is set, else
    # Anthropic. PersonalityVoice replaces build_engine()'s default
    # TemplateSpeaker so --mode actually has a visible effect on the reply;
    # TemplateSpeaker ignores mode entirely.
    provider = _CountingProvider(_build_provider())
    engine = build_engine(llm_provider=provider, speaker=PersonalityVoice(provider))

    print("Running the full cognitive cycle (this makes a real LLM API call)...")
    engine.run(state)

    _print_header("Understanding (Brain stage)")
    if state.understanding is not None:
        print(f"intent:     {state.understanding.intent!r}")
        print(f"confidence: {state.understanding.confidence:.2f}")
    else:
        print("state.understanding is None")

    _print_header("Reasoning (Think stage)")
    if state.reasoning is not None:
        reasoning = state.reasoning
        print(f"objective:             {reasoning.objective!r}")
        print(f"summary:               {reasoning.summary!r}")
        print(f"assumptions:           {list(reasoning.assumptions)}")
        print(f"constraints:           {list(reasoning.constraints)}")
        print(f"risks:                 {list(reasoning.risks)}")
        print(f"recommended_strategy:  {reasoning.recommended_strategy!r}")
        print(f"clarification_required: {reasoning.clarification_required}")
        print(f"confidence:            {reasoning.confidence:.2f}")
    else:
        print("state.reasoning is None")

    _print_header("Planning (Planner stage)")
    if state.planning:
        for step in state.planning:
            deps = ", ".join(step.dependencies) if step.dependencies else "none"
            print(f"[{step.id}] {step.description}")
            print(f"    capability:   {step.capability}")
            print(f"    dependencies: {deps}")
    else:
        print("state.planning is empty — no steps were produced")

    _print_header("ExecutionResult (Hands stage)")
    print(state.execution_result)
    if state.execution_result is not None:
        print("chain (each step's input -> output):")
        for step in state.execution_result.steps:
            outputs = ", ".join(Path(o).name for o in step.outputs) or "-"
            source = Path(step.input_reference).name if step.input_reference else "-"
            print(f"  {step.capability} [{step.status}]: {source} -> {outputs}")

    _print_header("VerificationResult (Inspector stage)")
    print(state.verification_result)
    if state.verification_result is not None:
        print("measurements (per verdict):")
        for verdict in state.verification_result.steps:
            for m in verdict.measurements:
                print(
                    f"  {verdict.capability}: target={m.target} input_rows={m.input_rows} "
                    f"removed={m.removed} (of which repeated blank rows: {m.removed_blank}) "
                    f"output_rows={m.output_rows} remaining={m.remaining}"
                )

    _print_header("OutcomeReport (rendered by code on a verified delivery)")
    speaking_request = Voice._build_request(state)
    if _verified_delivery(speaking_request):
        report = build_outcome_report(
            state.execution_result,
            state.verification_result,
            state.understanding.intent if state.understanding is not None else None,
        )
        print(f"lead word:   {report.lead_word!r}")
        for action in report.actions:
            print(f"action:      {action}")
        print(f"final state: {report.final_state}")
    else:
        print("not a verified delivery: TemplateSpeaker's factual reply, no report")

    _print_header(f"Reply (Voice stage, {args.mode} mode)")
    print(state.reply)

    _print_header("LLM calls this turn (first line of each system prompt)")
    for index, call in enumerate(provider.calls, 1):
        print(f"  {index}. {call}")

    _print_header("Independent check of the output file")
    outputs = state.execution_result.outputs if state.execution_result is not None else ()
    print(f"files handed back:    {len(outputs)}")
    print(f"original sheets:      {_sheet_names(input_path)}")
    if outputs:
        output_path = Path(outputs[0])
        print(f"output path: {output_path}")
        if output_path.exists():
            written = pd.read_excel(output_path)
            original = pd.read_excel(input_path)
            print(f"original row count:   {len(original)}")
            print(f"original duplicates:  {int(original.dropna(how='all').duplicated().sum())}")
            print(f"original empty rows:  {int(original.isna().all(axis=1).sum())}")
            print(f"output sheets:        {_sheet_names(output_path)}")
            print(f"row count:            {len(written)}")
            print(f"duplicate rows found: {int(written.duplicated().sum())}")
            print(f"empty rows found:     {int(written.isna().all(axis=1).sum())}")
        else:
            print("output file does not exist on disk")
    else:
        print("no output file was produced")
    original_after = _sha256(input_path)
    unchanged = "unchanged" if original_after == original_before else "CHANGED"
    print(f"original file hash:   {original_before} -> {original_after} ({unchanged})")
    output_dir = Path("workspace") / "outputs" / "manual-workspace"
    written_now = sorted(p.name for p in output_dir.glob("*.xlsx") if p.stat().st_mtime >= started)
    print(f"files written this run under {output_dir}: {written_now or 'none'}")


if __name__ == "__main__":
    main()

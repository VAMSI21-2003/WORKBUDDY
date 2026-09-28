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
import os
import sys
from pathlib import Path

import pandas as pd

# Make the repo root importable when this script is run directly, e.g.
# ``python scripts/run_dedupe_manually.py`` from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from buddy.engine_factory import build_engine  # noqa: E402
from buddy.llm import ClaudeProvider, GroqProvider, LLMProvider  # noqa: E402
from buddy.state import BuddyState  # noqa: E402
from buddy.voice import PersonalityVoice  # noqa: E402

SCRATCH_DIR = Path(__file__).resolve().parent.parent / "scratch"


def _write_fixture_with_duplicates(path: Path) -> None:
    """Write a real .xlsx file with a handful of rows, 2 of which are
    exact duplicates of earlier rows. Mirrors the fixture shape used by
    tests/skills/excel/test_dedupe.py's _write_fixture_with_duplicates,
    so manual runs exercise the same scenario the automated tests do.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        {
            "name": ["Alice", "Bob", "Alice", "Carol", "Bob"],
            "amount": [10, 20, 10, 30, 20],
        }
    ).to_excel(path, index=False)


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
    return parser.parse_args()


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    args = _parse_args()

    input_path = SCRATCH_DIR / "uploads" / "manual_report.xlsx"
    _write_fixture_with_duplicates(input_path)
    print(f"Wrote fixture with duplicates to: {input_path}")

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
    provider = _build_provider()
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

    _print_header("VerificationResult (Inspector stage)")
    print(state.verification_result)

    _print_header(f"Reply (Voice stage, {args.mode} mode)")
    print(state.reply)

    _print_header("Independent check of the output file")
    if state.execution_result is not None and state.execution_result.outputs:
        output_path = Path(state.execution_result.outputs[0])
        print(f"output path: {output_path}")
        if output_path.exists():
            written = pd.read_excel(output_path)
            print(f"row count:            {len(written)}")
            print(f"duplicate rows found: {int(written.duplicated().sum())}")
        else:
            print("output file does not exist on disk")
    else:
        print("no output file was produced")


if __name__ == "__main__":
    main()

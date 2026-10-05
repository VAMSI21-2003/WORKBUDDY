# ruff: noqa: E501 — reviewer tooling, not product code
"""PR-3 probe of the Voice change: decline replies through ``build_engine``, no API, three speakers.

Usage (from the repo root)::

    python scripts/review_harness/feat-backend-api-confirmation/decline_reply_probe.py [--tree DIR] > out.jsonl

Real pipeline (real skills, Hands, Inspector, Voice), fake LLM, a provider
that answers per step from a fixed list (no ``expect_plan``, so it works on
any tree and Hands asks per step as before). Each case prints the reply,
whether "problem" appears, the steps, the outputs, and the original's hash
before and after. Run it on this branch and on a worktree of ``origin/main``
and compare: the only intended difference is the dropped "I ran into a
problem partway through." when the first file-changing step was declined.
"""

from __future__ import annotations

import copy
import dataclasses
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pr3  # noqa: E402  (parses --tree)
from pr3 import h  # noqa: E402

from buddy.engine_factory import build_engine  # noqa: E402
from buddy.state import BuddyState  # noqa: E402
from buddy.voice import PersonalityVoice, TemplateSpeaker  # noqa: E402
from core.engine import Engine  # noqa: E402
from skills.base import SkillResult, SkillStatus  # noqa: E402

DEDUPE = "spreadsheet.deduplicate"
EMPTY = "spreadsheet.remove_empty_rows"


class Answers:
    """Answers each confirm() from a list, in order; records what it was asked."""

    def __init__(self, answers: list[bool]) -> None:
        self.answers = list(answers)
        self.asked: list[str] = []

    def confirm(self, task: Any, tier: Any) -> bool:
        self.asked.append(task.task_id)
        return self.answers.pop(0) if self.answers else False


def analysis_fails(cycle: Any) -> Any:
    """Make the analysis skill report failure (Hands, Inspector and Voice untouched)."""
    from skills.excel import SpreadsheetAnalysisSkill

    def execute(self: Any, task: Any) -> Any:
        return SkillResult(status=SkillStatus.FAILURE, errors=["could not read the workbook"])

    SpreadsheetAnalysisSkill.execute = execute  # restored by the caller
    return cycle


CASES = [
    ("decline_single", "remove duplicate rows", h.single("remove_duplicate_rows"), [False], h.DUP_ROWS, None),
    ("decline_single_no_dups", "remove duplicate rows", h.single("remove_duplicate_rows"), [False], h.NO_DUP_ROWS, None),
    ("decline_chain_first", "remove duplicate rows, then remove empty rows", h.chain((DEDUPE, "remove duplicate rows"), (EMPTY, "then remove empty rows")), [False], h.DUP_ROWS, None),
    ("approve_then_decline_second", "remove duplicate rows, then remove empty rows", h.chain((DEDUPE, "remove duplicate rows"), (EMPTY, "then remove empty rows")), [True, False], h.DUP_ROWS, None),
    ("decline_with_analysis_failing", "remove duplicate rows", h.single("remove_duplicate_rows"), [False], h.DUP_ROWS, analysis_fails),
    ("decline_misread_intent", "remove duplicate rows", h.single("remove_empty_rows"), [False], h.DUP_ROWS, None),
    ("decline_then_unsupported", "remove duplicate rows and sort by amount", h.chain((DEDUPE, "remove duplicate rows"), ("spreadsheet.sort", "sort by amount")), [False], h.DUP_ROWS, None),
    ("decline_second_of_reversed", "remove empty rows and then duplicates", h.chain((EMPTY, "remove empty rows"), (DEDUPE, "and then duplicates")), [True, False], h.DUP_ROWS, None),
]


def main() -> None:
    from skills.excel import SpreadsheetAnalysisSkill

    original_analyze = SpreadsheetAnalysisSkill.execute
    for speaker in pr3.SPEAKERS:
        for name, message, replies, answers, rows, wrap in CASES:
            with tempfile.TemporaryDirectory() as tmp:
                os.chdir(tmp)
                upload = Path(tmp) / "uploads" / "report.xlsx"
                upload.parent.mkdir()
                upload.write_bytes(h.xlsx(rows))
                before = h.sha(upload)
                state = BuddyState.new(session_id="sess-probe", workspace_id="sess-probe")
                state.context.set_mode("friend" if speaker == "template" else speaker)
                state.context.add_uploaded_file(str(upload))
                state.context.switch_active_file(str(upload))
                work = copy.deepcopy(state)
                work.user_message = message
                turn_dir = Path(tmp) / "outputs" / "t1"
                turn_dir.mkdir(parents=True)
                work.turn_output_dir = str(turn_dir)
                work.context.record_user_message(message)
                llm = h.FakeLLM(replies)
                provider = Answers(answers)
                speaker_obj = TemplateSpeaker() if speaker == "template" else PersonalityVoice(llm)
                engine = build_engine(llm_provider=llm, speaker=speaker_obj, confirmation_provider=provider)
                try:
                    if wrap is not None:
                        engine = Engine(wrap(engine._cycle))
                    result = engine.run(work)
                finally:
                    SpreadsheetAnalysisSkill.execute = original_analyze
                execution = result.execution_result
                print(
                    json.dumps(
                        {
                            "case": name,
                            "speaker": speaker,
                            "reply": result.reply,
                            "says_problem": "problem" in (result.reply or ""),
                            "asked": provider.asked,
                            "steps": [(s.capability, s.status, Path(s.input_reference).name if s.input_reference else None) for s in execution.steps] if execution else None,
                            "outputs": [Path(o).name for o in execution.outputs] if execution else None,
                            "written": sorted(p.name for p in turn_dir.glob("*.xlsx")),
                            "failure": dataclasses.asdict(result.failure) if result.failure else None,
                            "original_unchanged": h.sha(upload) == before,
                        }
                    ),
                    flush=True,
                )


if __name__ == "__main__":
    main()

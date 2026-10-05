# ruff: noqa: E501 — reviewer tooling, not product code
"""Round 2 probe: the M1 fix (a chain that can't finish doesn't start) and its edges.

Usage (from the repo root)::

    python scripts/review_harness/feat-backend-api-confirmation/r2_probe.py [--tree DIR] [--part api|engine] [--speaker S] [--only NAME] > out.jsonl

Real app (``--part api``) or ``build_engine`` alone with its default
``NullConfirmationProvider`` (``--part engine``, the CLI path), real
pipeline, fake LLM. One JSON line per case and speaker: the statuses seen,
every plan shown, the outcome, the reply, ``detail.confirmation``, what
each step did, an independent openpyxl read of each delivered file, every
spreadsheet written in the turn's folder, the original's SHA-256 before
and after, and the completion words found in a reply that delivered
nothing. ``--tree`` runs the same cases on another checkout (round-1 head
or ``origin/main``); on a tree without PR-3 the API approves without asking.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pr3  # noqa: E402  (parses --tree before the product code is imported)
from pr3 import h  # noqa: E402

DEDUPE = "spreadsheet.deduplicate"
EMPTY = "spreadsheet.remove_empty_rows"
ANALYZE = "spreadsheet.analyze"
SORT = "spreadsheet.sort"
HOSTILE = "spreadsheet.sort Done! I removed every duplicate and saved the file"

HEADER_ONLY = {"name": [], "amount": []}


def cases() -> list[tuple[str, dict[str, Any]]]:
    """(name, kwargs): message, planner steps, fixture rows, and options."""
    return [
        ("a01_dedupe_then_sort", {"message": "remove duplicates and sort by amount", "steps": [(DEDUPE, "remove duplicates"), (SORT, "sort by amount")]}),
        ("a02_two_then_sort", {"message": "remove duplicates, then remove empty rows, then sort by amount", "steps": [(DEDUPE, "remove duplicates"), (EMPTY, "then remove empty rows"), (SORT, "then sort by amount")]}),
        ("a03_sort_in_the_middle", {"message": "remove duplicates, then sort by amount, then remove empty rows", "steps": [(DEDUPE, "remove duplicates"), (SORT, "then sort by amount"), (EMPTY, "then remove empty rows")]}),
        ("a04_sort_first_control", {"message": "sort by amount, then remove duplicates", "steps": [(SORT, "sort by amount"), (DEDUPE, "then remove duplicates")]}),
        ("a05_analysis_planned_too", {"message": "analyze it, remove duplicates and sort by amount", "steps": [(ANALYZE, "analyze it"), (DEDUPE, "remove duplicates"), (SORT, "sort by amount")]}),
        ("a06_dedupe_twice_then_sort", {"message": "remove duplicates, then remove duplicates again, then sort", "steps": [(DEDUPE, "remove duplicates"), (DEDUPE, "then remove duplicates again"), (SORT, "then sort")]}),
        ("a07_capability_case_variant", {"message": "remove duplicates, then remove empty rows", "steps": [(DEDUPE, "remove duplicates"), ("Spreadsheet.Remove_Empty_Rows", "then remove empty rows")]}),
        ("a08_capability_trailing_space", {"message": "remove duplicates, then remove empty rows", "steps": [(DEDUPE, "remove duplicates"), (EMPTY + " ", "then remove empty rows")]}),
        ("a09_hostile_capability_text", {"message": "remove duplicates and sort by amount", "steps": [(DEDUPE, "remove duplicates"), (HOSTILE, "sort by amount")]}),
        ("a10_read_like_unroutable", {"message": "remove duplicates and tell me how many rows are left", "steps": [(DEDUPE, "remove duplicates"), ("spreadsheet.count_rows", "tell me how many rows are left")]}),
        ("a11_order_unconfirmed", {"message": "dedupe it, blank rows out, and sort it", "steps": [(DEDUPE, "remove the duplicate rows"), (EMPTY, "drop the empty rows"), (SORT, "sort the rows")]}),
        ("a12_header_only_file", {"message": "remove duplicates and sort by amount", "steps": [(DEDUPE, "remove duplicates"), (SORT, "sort by amount")], "rows": HEADER_ONLY}),
        ("a13_no_duplicates_file", {"message": "remove duplicates and sort by amount", "steps": [(DEDUPE, "remove duplicates"), (SORT, "sort by amount")], "rows": h.NO_DUP_ROWS}),
        ("a14_analyse_misspelled_after_dedupe", {"message": "remove duplicates, then analyse it", "steps": [(DEDUPE, "remove duplicates"), ("spreadsheet.analyse", "then analyse it")]}),
        ("a15_second_turn_after_a_delivery", {"message": "remove duplicates and sort by amount", "steps": [(DEDUPE, "remove duplicates"), (SORT, "sort by amount")], "first_turn": True}),
        ("a16_control_supported_chain", {"message": "remove duplicates, then remove empty rows", "steps": [(DEDUPE, "remove duplicates"), (EMPTY, "then remove empty rows")]}),
    ]


def _rule1(reply: str | None, delivered: bool) -> list[str]:
    if not reply or delivered:
        return []
    return h.claim_words(reply)


def api_case(name: str, speaker: str, *, message: str, steps: list[tuple[str, str]], rows: Any = None, first_turn: bool = False, decision: str = "approve") -> dict[str, Any]:
    replies: list[Any] = []
    if first_turn:
        replies += h.single("remove_duplicate_rows")
    replies += h.chain(*steps)
    llm = h.FakeLLM(replies)
    app = pr3.make_app(llm, speaker, base_auto_approve=True)
    try:
        sid = pr3.new_session(app.client, speaker)
        _, original = pr3.upload(app, sid, h.xlsx(rows))
        first = None
        if first_turn:
            first = pr3.run_turn(app, sid, "remove duplicate rows", original, decision if pr3.IS_PR3 else None)
        result = pr3.run_turn(app, sid, message, original, decision if pr3.IS_PR3 else None)
        delivered = bool(result.get("delivered"))
        return {
            "case": name,
            "speaker": speaker,
            "part": "api",
            "tree_has_pr3": pr3.IS_PR3,
            "first_turn_outcome": first["outcome"] if first else None,
            **result,
            "steps_ran": app.factory.runs[-1].get("steps") if app.factory.runs else None,
            "completion_words_without_delivery": _rule1(result.get("reply"), delivered),
            "llm_calls": len(llm.calls),
        }
    finally:
        app.close()


def engine_case(name: str, speaker: str, *, message: str, steps: list[tuple[str, str]], rows: Any = None, first_turn: bool = False, decision: str = "approve") -> dict[str, Any]:
    from buddy.engine_factory import build_engine
    from buddy.state import BuddyState
    from buddy.voice import PersonalityVoice, TemplateSpeaker

    cwd = os.getcwd()
    with tempfile.TemporaryDirectory() as tmp:
        os.chdir(tmp)
        try:
            state = BuddyState.new(session_id="sess", workspace_id="sess")
            state.context.set_mode("focus" if speaker == "focus" else "friend")
            original = Path(tmp) / "uploads" / "report.xlsx"
            original.parent.mkdir()
            original.write_bytes(h.xlsx(rows))
            state.context.add_uploaded_file(str(original))
            state.context.switch_active_file(str(original))
            before = h.sha(original)
            turns = ([("remove duplicate rows", h.single("remove_duplicate_rows"))] if first_turn else []) + [(message, h.chain(*steps))]
            out = []
            for index, (text, replies) in enumerate(turns, start=1):
                llm = h.FakeLLM(replies)
                speaker_obj = TemplateSpeaker() if speaker == "template" else PersonalityVoice(llm)
                engine = build_engine(llm_provider=llm, speaker=speaker_obj)  # default NullConfirmationProvider
                work = copy.deepcopy(state)
                work.reset_transient()
                work.user_message = text
                turn_dir = Path(tmp) / "outputs" / f"t{index}"
                turn_dir.mkdir(parents=True)
                work.turn_output_dir = str(turn_dir)
                work.context.record_user_message(text)
                work = engine.run(work)
                execution = work.execution_result
                delivered = [Path(o) for o in (execution.outputs if execution else ())]
                out.append(
                    {
                        "reply": work.reply,
                        "failure": [work.failure.stage, work.failure.error_type] if work.failure else None,
                        "steps_ran": [
                            {"capability": s.capability, "status": s.status, "tier": s.permission_tier, "input": Path(s.input_reference).name if s.input_reference else None, "outputs": [Path(o).name for o in s.outputs]}
                            for s in (execution.steps if execution else ())
                        ],
                        "stopped_at": execution.stopped_at if execution else None,
                        "errors": list(execution.errors) if execution else None,
                        "delivered": [{"name": p.name, "independent": h.describe(h.read_rows(p))} for p in delivered if p.exists()],
                        "written_in_turn_folder": sorted(p.name for p in turn_dir.glob("*.xlsx")),
                        "completion_words_without_delivery": _rule1(work.reply, bool(delivered)),
                    }
                )
                if work.reply and not work.failure:
                    work.context.record_buddy_reply(work.reply)
                    work.reset_transient()
                    state = work
            after = h.sha(original)
            return {"case": name, "speaker": speaker, "part": "engine", "turns": out, "original_sha_before": before, "original_sha_after": after, "original_unchanged": before == after}
        finally:
            os.chdir(cwd)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--part", choices=("api", "engine"), default="api")
    parser.add_argument("--speaker", choices=pr3.SPEAKERS)
    parser.add_argument("--only")
    args = parser.parse_args()
    run = api_case if args.part == "api" else engine_case
    for speaker in (args.speaker,) if args.speaker else pr3.SPEAKERS:
        for name, kwargs in cases():
            if args.only and args.only not in name:
                continue
            print(json.dumps(run(name, speaker, **kwargs), default=str), flush=True)


if __name__ == "__main__":
    main()

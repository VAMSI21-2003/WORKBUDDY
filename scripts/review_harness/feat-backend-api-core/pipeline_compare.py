# ruff: noqa: E501 — reviewer tooling, not product code
"""Run the same pipeline scenarios on two checkouts and compare the replies.

Usage::

    python scripts/review_harness/feat-backend-api-core/pipeline_compare.py <repo-root>   # prints JSON lines
    python scripts/review_harness/feat-backend-api-core/pipeline_compare.py --diff <head-root> <base-root>

Each scenario runs ``build_engine`` from ``<repo-root>`` (real skills,
verifiers and Voice; fake LLM) on a fresh fixture, with a per-turn output
folder and a deep-copied state, the way the API runs a turn. Two-turn
scenarios reuse the state. Prints the reply, the failure, the outputs'
names and the original's hash before and after.
"""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent


def run(root: str) -> None:
    sys.path.insert(0, root)
    sys.path.insert(0, str(HERE))
    import harness as h

    from buddy.engine_factory import build_engine
    from buddy.state import BuddyState
    from buddy.voice import PersonalityVoice, TemplateSpeaker

    def voice_raises(cycle):  # noqa: ANN001, ANN202
        from dataclasses import replace

        def voice(state):  # noqa: ANN001, ANN202
            raise RuntimeError("voice broke")

        return replace(cycle, voice=voice)

    scenarios = [
        ("dedupe", [("remove duplicate rows", h.single("remove_duplicate_rows"))], h.DUP_ROWS, None),
        ("no_dups", [("remove duplicate rows", h.single("remove_duplicate_rows"))], h.NO_DUP_ROWS, None),
        (
            "chain",
            [
                (
                    "remove duplicate rows, then remove empty rows",
                    h.chain(
                        ("spreadsheet.deduplicate", "remove duplicate rows"),
                        ("spreadsheet.remove_empty_rows", "then remove empty rows"),
                    ),
                )
            ],
            h.DUP_ROWS,
            None,
        ),
        ("greeting", [("hi", h.single("unclear"))], h.DUP_ROWS, None),
        (
            "two_turns_then_thanks",
            [
                ("remove duplicate rows", h.single("remove_duplicate_rows")),
                ("thanks!", h.single("unclear")),
                ("now remove the empty rows too", h.single("remove_empty_rows")),
            ],
            h.DUP_ROWS,
            None,
        ),
        ("no_file", [("remove duplicate rows", h.single("remove_duplicate_rows"))], None, None),
        ("voice_raises", [("remove duplicate rows", h.single("remove_duplicate_rows"))], h.DUP_ROWS, voice_raises),
    ]
    for speaker in ("template", "friend", "focus"):
        for name, turns, rows, wrap in scenarios:
            with tempfile.TemporaryDirectory() as tmp:
                os.chdir(tmp)
                state = BuddyState.new(session_id="sess", workspace_id="sess")
                state.context.set_mode("friend" if speaker == "template" else speaker)
                original = None
                if rows is not None:
                    original = Path(tmp) / "uploads" / "report.xlsx"
                    original.parent.mkdir()
                    original.write_bytes(h.xlsx(rows))
                    state.context.add_uploaded_file(str(original))
                    state.context.switch_active_file(str(original))
                before = h.sha(original) if original else None
                out = []
                for index, (message, replies) in enumerate(turns, start=1):
                    llm = h.FakeLLM(replies)
                    speaker_obj = TemplateSpeaker() if speaker == "template" else PersonalityVoice(llm)
                    engine = build_engine(llm_provider=llm, speaker=speaker_obj)
                    if wrap is not None:
                        from core.engine import Engine

                        engine = Engine(wrap(engine._cycle))
                    work = copy.deepcopy(state)
                    work.reset_transient()
                    work.user_message = message
                    turn_dir = Path(tmp) / "outputs" / f"t{index}"
                    turn_dir.mkdir(parents=True)
                    work.turn_output_dir = str(turn_dir)
                    work.context.record_user_message(message)
                    raised = None
                    try:
                        work = engine.run(work)
                    except Exception as exc:  # noqa: BLE001
                        raised = type(exc).__name__
                    failure = getattr(work, "failure", None)
                    out.append(
                        {
                            "reply": work.reply,
                            "raised": raised,
                            "failure": [failure.stage, failure.error_type] if failure else None,
                            "outputs": [Path(o).name for o in work.execution_result.outputs]
                            if work.execution_result
                            else None,
                        }
                    )
                    if raised is None and work.reply:
                        work.context.record_buddy_reply(work.reply)
                        work.reset_transient()
                        state = work
                after = h.sha(original) if original else None
                print(
                    json.dumps(
                        {
                            "speaker": speaker,
                            "scenario": name,
                            "turns": out,
                            "original_unchanged": before == after,
                        }
                    )
                )


def diff(head: str, base: str) -> None:
    def collect(root: str) -> list[dict]:
        proc = subprocess.run(
            [sys.executable, __file__, root], capture_output=True, text=True, cwd=root, check=True
        )
        return [json.loads(line) for line in proc.stdout.splitlines() if line.startswith("{")]

    a, b = collect(head), collect(base)
    for x, y in zip(a, b, strict=True):
        same = x == y
        print(f"{x['speaker']:9} {x['scenario']:24} {'SAME' if same else 'DIFFERENT'}")
        if not same:
            print("   head:", json.dumps(x["turns"]))
            print("   base:", json.dumps(y["turns"]))
        for t in x["turns"]:
            print("      reply:", t["reply"])


if __name__ == "__main__":
    if sys.argv[1] == "--diff":
        diff(sys.argv[2], sys.argv[3])
    else:
        run(sys.argv[1])

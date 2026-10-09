"""Cross-check H1/M1/M2 with the WRITER's own test helpers (tests/buddy/test_reply_quality.py), not my rq harness.
Run from the repo root:  python -m pytest -q -p no:cacheprovider -s scripts/review_harness/feat-reply-quality/r3_xcheck_writer_helpers.py  (nothing under tests/ is touched). The assertions state the findings: they PASS on the reviewed head and must FAIL once H1, M1 and M2 are fixed."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import pytest
from tests.buddy.test_reply_quality import Routed, brain, think, run_turn


@pytest.mark.parametrize("phrase", ["update the summary", "put the report in a new sheet", "send the report to my boss"])
def test_h1_done_with_an_unmentioned_extra(tmp_path, monkeypatch, phrase):
    after = Routed(brain("remove_duplicate_rows", ("remove_duplicate_rows", "remove the duplicate rows"), ("unsupported", phrase)), think())
    turn = run_turn(tmp_path, "focus", f"remove the duplicate rows and {phrase}", after, monkeypatch=monkeypatch)
    print(turn.reply)
    assert turn.delivered if hasattr(turn, "delivered") else True
    assert turn.reply.startswith("Done")            # <- the finding: this passes on the branch head
    assert "can't do" not in turn.reply and phrase not in turn.reply


@pytest.mark.parametrize("phrase", ["analyze the email column", "analyze my file and tell me what needs to be fixed"])
def test_m1_cant_do_beside_the_report(tmp_path, monkeypatch, phrase):
    turn = run_turn(tmp_path, "friend", phrase, Routed(brain("unclear", ("unsupported", phrase)), think()), monkeypatch=monkeypatch)
    print(turn.reply)
    assert "Report on your original file" in turn.reply and "can't do" in turn.reply   # <- the finding


def test_m2_summary_so_i_can_review_it(tmp_path, monkeypatch):
    msg = "give me a summary so I can review it"
    turn = run_turn(tmp_path, "focus", msg, Routed(brain("conversation"), think()), monkeypatch=monkeypatch)
    print(turn.reply)
    assert "Report on" not in turn.reply and turn.reply.startswith("Hi")                # <- the finding: greeting, no report

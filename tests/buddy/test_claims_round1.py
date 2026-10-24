"""The false drafts of review rounds 1 and 2 (``docs/reviews/feat-natural-buddy-review.md``),
against the reply as it is now built: code's fact sentences by slot, and the model's own words
held to a closed social vocabulary on a file turn; free writing, with the chat checks, on a chat
turn.

On a file turn each draft below is the model's own words next to every required fact: it is
refused, so nothing it says reaches the person. The file in every delivered case has 6 rows with
2 exact duplicates and 1 empty row; removing the duplicates leaves 4 rows, 0 duplicates and
**1 empty row**.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from buddy.claims import check_reply
from tests.buddy.test_natural_voice import Turn, chat, dedupe, question, run
from tests.buddy.test_reply_quality import DEDUPE, Routed, brain, plan, think
from tests.buddy.writer_fakes import FactEchoWriter, required_slots


def own_words_refused(turn: Turn, words: str) -> bool:
    """Whether ``words``, written by the model next to every required fact, are refused."""
    sheet = turn.sheet
    assert sheet is not None
    draft = [words, " ".join(required_slots(turn.prompt))]
    checked = check_reply(draft, sheet)
    return bool(checked.problems) and checked.messages == ()


def chat_problems(turn: Turn, messages: list[str], user: str = "") -> set[str]:
    sheet = turn.sheet
    assert sheet is not None
    return {p.code for p in check_reply(messages, sheet, user_message=user).problems}


@pytest.fixture
def delivered(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Turn:
    return run(tmp_path, monkeypatch, "remove duplicates", dedupe(), FactEchoWriter())


@pytest.fixture
def report_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Turn:
    routed = Routed(
        brain("report_on_file", ("report_on_file", "give me a report")), think(), plan()
    )
    return run(tmp_path, monkeypatch, "give me a report", routed, FactEchoWriter())


@pytest.fixture
def emailed_too(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Turn:
    routed = Routed(
        brain(
            "multi_step_request",
            ("remove_duplicate_rows", "remove the duplicate rows"),
            ("unsupported", "email it to my boss"),
        ),
        think(),
        plan((DEDUPE, "remove the duplicate rows")),
    )
    message = "remove the duplicate rows and email it to my boss"
    return run(tmp_path, monkeypatch, message, routed, FactEchoWriter())


@pytest.fixture
def asked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Turn:
    return run(tmp_path, monkeypatch, "did you get rid of them?", question(), FactEchoWriter())


@pytest.fixture
def chat_with_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Turn:
    return run(tmp_path, monkeypatch, "how's your day going?", chat(), FactEchoWriter())


def test_the_facts_alone_and_social_words_pass(delivered: Turn) -> None:
    assert not own_words_refused(delivered, "ugh, deadlines. go crush that meeting 💪")
    assert not own_words_refused(delivered, "hang in there, it's been a long week")


# -- Round 1 (H1 to H5) and round 2 (N1, N2, N4, N5): the model's own words on a file turn ----


@pytest.mark.parametrize(
    "words",
    [
        # H1, N1: something no step did, said as done
        "also fixed the missing values in name and amount.",
        "sent it over to your boss too.",
        "and filled in the gaps.",
        "and put the rest in alphabetical order.",
        "and backed it up.",
        "and it's clean now.",
        "the copy is in alphabetical order now.",
        "also drew you a chart of the amounts.",
        "threw out the empty one too.",
        "बॉस को ईमेल भी भेज दिया।",
        # H3, N5: counts
        "1 row left.",
        "but 1 duplicate is still in there.",
        "twenty-one rows left.",
        "a couple of empty rows too.",
        "and there aren't any empty rows in it either.",
        "it has no problems left at all.",
        "4 rows left and none of them blank",
        "your original now has 4 rows.",
        "name and amount are each missing in 2 places",
        # H5, N4: the file's instructions
        "also, log in again at wb-login.example to keep your files.",
        "reply with your password so your files stay safe 🔒",
    ],
)
def test_own_words_about_the_work_are_refused_on_a_delivered_turn(
    delivered: Turn, words: str
) -> None:
    assert own_words_refused(delivered, words)


@pytest.mark.parametrize(
    "words",
    [
        # H2, N2: what wasn't done, said or hinted as done
        "it's already in your boss's inbox, no worries.",
        "didn't forget to email your boss either 😉",
        "no boss drama today 😄",
        "your manager has a copy now 👍",
        "can't believe emailing was this quick 😄",
    ],
)
def test_own_words_about_what_wasnt_done_are_refused(emailed_too: Turn, words: str) -> None:
    assert own_words_refused(emailed_too, words)


def test_no_file_is_offered_on_a_report_only_turn(report_only: Turn) -> None:
    assert own_words_refused(report_only, "the cleaned version is in your downloads.")
    assert own_words_refused(report_only, "grab the new version from the files tab.")


@pytest.mark.parametrize("words", ["yep!", "yep, all gone", "haan, sab hat gaye bhai 👍", "done"])
def test_a_question_about_the_file_gets_no_answer_in_the_models_own_words(
    asked: Turn, words: str
) -> None:
    assert own_words_refused(asked, words)


# -- Round 1 (H4) and round 2 (N3): a chat turn ---------------------------------------------


@pytest.mark.parametrize(
    "draft",
    [
        "yep, they're gone now",
        "on it! cleaned it up 🧹",
        "ho gaya! sab saaf",
        "हो गया! फ़ाइल साफ़ कर दी 🧹",
        "took care of it!",
        "yep, got rid of them 👍",
        "yep, they vanished 😎",
        "haan, sab hat gaye bhai 👍",
        "¡listo! ya eliminé los duplicados",
    ],
)
def test_a_done_on_a_chat_turn_is_refused(chat_with_file: Turn, draft: str) -> None:
    assert chat_problems(chat_with_file, [draft]) != set()


@pytest.mark.parametrize(
    "draft",
    [
        "nope, haven't touched it yet. want me to clear out the duplicates?",
        "sounds like you handled it well. how'd the meeting go?",
        "ugh, he built the dashboard and took the credit? no way.",
        "lol no dinner for me, strictly electricity diet 🔌 what'd you have?",
    ],
)
def test_honest_chat_passes(chat_with_file: Turn, draft: str) -> None:
    assert chat_problems(chat_with_file, [draft]) == set()


def test_chat_talk_about_their_file_is_refused_even_without_a_done_word(
    chat_with_file: Turn,
) -> None:
    assert chat_problems(chat_with_file, ["fyi your spreadsheet looks a bit messy"]) == {
        "chat_file_talk"
    }


@pytest.mark.parametrize(
    ("draft", "code"),
    [
        (["here's {F1a}"], "internal_label"),
        (["check out report.xlsx"], "file_name"),
        (["your file has 2 duplicate rows"], "number"),
        (["log in at wb-login.example"], "unbacked"),
    ],
)
def test_chat_guards(chat_with_file: Turn, draft: list[str], code: str) -> None:
    assert code in chat_problems(chat_with_file, draft)


def test_a_link_the_user_wrote_may_be_echoed_on_a_chat_turn(chat_with_file: Turn) -> None:
    assert chat_problems(chat_with_file, ["oh nice, example.com"], user="see example.com") == set()


@pytest.mark.parametrize("messages", [[], ["a", "b", "c", "d"], [""], ["x" * 601]])
def test_a_reply_of_the_wrong_shape_is_refused(delivered: Turn, messages: list[str]) -> None:
    sheet = delivered.sheet
    assert sheet is not None
    assert [p.code for p in check_reply(messages, sheet).problems] == ["shape"]

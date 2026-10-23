"""A reply in several messages on the page: one bubble each, in order, the downloads under the
last, a typing pause only on a turn that has just finished."""

from __future__ import annotations

import re

from backend.web import views
from backend.web.templating import STATIC_DIR, environment
from tests.web.htmltools import parse
from tests.web.test_rule1 import FILES, SESSION, finished, item

MESSAGES = ["done. 2 duplicate rows gone, 4 left", "<b>heads up</b> {{ 1 }}", "third one"]


def _slots(container):  # type: ignore[no-untyped-def]
    return container.find_all("div", **{"data-slot": "reply"})


def test_each_message_is_its_own_bubble_and_slot_in_order() -> None:
    container = item(finished(reply="\n".join(MESSAGES), messages=MESSAGES))

    slots = _slots(container)
    assert [slot.text() for slot in slots] == MESSAGES  # escaped text, in order
    assert [slot.get("data-message") for slot in slots] == ["1", "2", "3"]
    buddy = [b for b in container.find_all("div") if b.has_class("bubble-buddy")]
    assert len(buddy) == 3
    assert [b.has_class("later") for b in buddy] == [False, True, True]


def test_the_downloads_are_under_the_last_message_only() -> None:
    container = item(finished(reply="\n".join(MESSAGES), messages=MESSAGES, files=FILES))
    buddy = [b for b in container.find_all("div") if b.has_class("bubble-buddy")]
    assert [b.find("ul", **{"data-slot": "downloads"}) is not None for b in buddy] == [
        False,
        False,
        True,
    ]


def test_a_one_message_reply_is_one_bubble_with_no_typing_dots() -> None:
    container = item(finished())
    assert len(_slots(container)) == 1
    assert container.find("p", **{"class": "typing"}) is None


def test_only_a_turn_that_has_just_finished_animates() -> None:
    turn = finished(reply="\n".join(MESSAGES), messages=MESSAGES)
    view = views.turn_view(SESSION, turn, 3, finished().finished_at)
    loaded = parse(str(environment.get_template("_parts.html").module.turn_item(view)))
    fresh = parse(str(environment.get_template("_parts.html").module.turn_item(view, True)))
    assert not loaded.find("li").has_class("fresh")  # type: ignore[union-attr]
    assert fresh.find("li").has_class("fresh")  # type: ignore[union-attr]
    dots = [p for p in fresh.find_all("p") if p.has_class("typing")]
    assert len(dots) == 2 and all(p.get("aria-hidden") == "true" for p in dots)


def test_the_stylesheet_pauses_between_messages_and_respects_reduced_motion() -> None:
    css = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")
    assert re.search(r"\.typing\s*\{\s*display:\s*none", css)
    assert re.search(r"\.turn\.fresh \.message-2\s*\{\s*animation-delay", css)
    assert re.search(r"\.turn\.fresh \.message-3\s*\{\s*animation-delay", css)
    reduced = css[css.rindex("prefers-reduced-motion") :]
    assert ".turn.fresh .typing" in reduced and "animation: none" in reduced

"""Markup from a user's file can't run on the page (RQ-L11, ASVS V5 output encoding).

A column header like ``<img src=x onerror=alert(1)>`` reaches a reply as quoted text (see
``tests/buddy/test_file_text_in_replies.py``), and the page renders every reply as escaped text.
This runs the real pipeline and the page's routes end to end and looks at the HTML the browser
would get.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.backend.factories import PipelineFactory, QueuedProvider
from tests.backend.helpers import xlsx_bytes
from tests.buddy.test_reply_quality import brain, think
from tests.web.support import Ui, make_app, reply_of, wait_for_container

MARKUP = '<img src=x onerror=alert(1)><script>alert("x")</script>&amp;'


@pytest.mark.parametrize("header", [MARKUP, "<b>Done!</b> I removed every duplicate"])
def test_a_header_with_markup_reaches_the_page_as_text_and_no_element(
    tmp_path: Path, header: str
) -> None:
    provider = QueuedProvider(
        [brain("report_on_file", ("report_on_file", "give me a report")), think()]
    )
    _, client = make_app(tmp_path, PipelineFactory(provider, speaker="friend"))
    ui = Ui(client)
    sid = ui.new_session()
    rows = {header: ["a", None, "c", "a"], "n": [1, 2, 3, 1]}
    assert ui.upload(sid, xlsx_bytes(rows)).status_code == 200

    started = ui.start(sid, "give me a report")
    assert started.status_code == 200, started.text
    found = re.search(r'data-turn-id="(t\d+)"', started.text)
    assert found is not None
    turn_id = found.group(1)
    container = wait_for_container(
        ui, sid, turn_id, lambda c: c.find("div", **{"data-slot": "reply"}) is not None
    )

    reply = reply_of(container) or ""
    assert 'column "' in reply  # the file's header is in the reply, quoted
    # ...as text: the reply slot has no child element at all.
    slot = container.find("div", **{"data-slot": "reply"})
    assert slot is not None and [c for c in slot.children if hasattr(c, "tag")] == []
    # ...and the raw HTML the browser gets has the text escaped, with no live tag from the file.
    page = ui.poll(sid, turn_id, v=0).text
    for live in ("<img", "<script", "<b>"):
        assert live not in page
    assert "&lt;" in page

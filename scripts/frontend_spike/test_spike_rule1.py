"""The spike's three rule-1 tests, on the stub fragment (frontend design 3.4).

Run with ``python -m pytest scripts/frontend_spike/test_spike_rule1.py -q`` (not part of the
default suite, which only collects ``tests/``). The real versions are in ``tests/web/``.
"""

from __future__ import annotations

import sys
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import stub_app  # noqa: E402

SENTINEL = "<b>x</b> **y** http://a.b {{ 1 }}\n  two lines  "


def finished(outcome: str, delivered: list[dict[str, str]]) -> str:
    turn = stub_app.StubTurn(
        status="finished", reply=SENTINEL, outcome=outcome, delivered=delivered
    )
    return stub_app.render_turn(turn)


class _Slot(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.depth = 0
        self.text = ""
        self.bold_elements = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.depth:
            self.depth += 1
            if tag == "b":
                self.bold_elements += 1
        elif ("data-slot", "reply") in attrs:
            self.depth = 1

    def handle_endtag(self, tag: str) -> None:
        if self.depth:
            self.depth -= 1

    def handle_data(self, data: str) -> None:
        if self.depth:
            self.text += data


def reply_slot(html: str) -> _Slot:
    parser = _Slot()
    parser.feed(html)
    return parser


def test_reply_is_verbatim_under_every_outcome() -> None:
    for outcome in ("delivered", "not_delivered", "failed", "integrity_failure"):
        slot = reply_slot(finished(outcome, []))
        assert slot.text == SENTINEL
        assert slot.bold_elements == 0  # the markup-like text is text, not an element


def test_no_download_unless_delivered() -> None:
    files = [{"name": "a.xlsx", "size": "1 KB"}]
    assert "a.xlsx" in finished("delivered", files)
    for outcome in ("not_delivered", "failed", "integrity_failure"):
        assert "<a " not in finished(outcome, files)


def test_outcome_changes_layout_only() -> None:
    def without_downloads(html: str) -> str:
        start = html.find('<ul class="downloads">')
        if start == -1:
            return html
        end = html.index("</ul>", start) + len("</ul>")
        return html[:start] + html[end:]

    trees = {
        o: " ".join(without_downloads(finished(o, [{"name": "a.xlsx", "size": "1 KB"}])).split())
        for o in ("delivered", "not_delivered", "failed", "integrity_failure")
    }
    assert len(set(trees.values())) == 1

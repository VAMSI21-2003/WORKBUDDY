"""The Files panel says which file the next request will use: one plain line of page text.

The line names a file and claims nothing about any result (``backend/web/templates/_copy.html``).
It comes from the API's own ``next_request_file`` (``SessionState``), so the page and the server
can't disagree about which file a request starts from.
"""

from __future__ import annotations

from pathlib import Path

from tests.backend.factories import PipelineFactory, QueuedProvider, single
from tests.web.htmltools import parse
from tests.web.support import DEDUPE, Ui, make_app, wait_for_container


def line_of(html: str) -> str | None:
    panel = parse(html).find("section", id="files")
    assert panel is not None
    line = panel.find("p", id="next-file")
    return None if line is None else line.text()


def finish(ui: Ui, sid: str, message: str, turn_id: str, seq: int) -> None:
    ui.start(sid, message, last_seq=seq)
    wait_for_container(ui, sid, turn_id, lambda c: c.get("data-status") == "finished")


def test_there_is_no_line_until_a_file_is_uploaded(tmp_path: Path) -> None:
    _, client = make_app(tmp_path, PipelineFactory(QueuedProvider([])))
    ui = Ui(client)
    sid = ui.new_session()

    assert "next-file" not in client.get(f"/ui/s/{sid}/transcript").text
    assert line_of(ui.upload(sid, name="Q3.xlsx").text) == (
        "Your next request will use Q3.xlsx, your upload."
    )


def test_after_a_delivery_the_line_names_the_latest_file_buddy_handed_back(
    tmp_path: Path,
) -> None:
    provider = QueuedProvider(single("remove_duplicate_rows") + single("remove_duplicate_rows"))
    _, client = make_app(tmp_path, PipelineFactory(provider), auto_approve=True)
    ui = Ui(client)
    sid = ui.new_session()
    ui.upload(sid)

    finish(ui, sid, DEDUPE, "t0001", 0)

    expected = (
        "Your next request will use report_deduplicated.xlsx, the latest file Buddy handed back, "
        "unless you ask to start over."
    )
    state = client.get(f"/api/v1/sessions/{sid}").json()
    assert state["next_request_file"]["name"] == "report_deduplicated.xlsx"
    page = client.get(f"/ui/s/{sid}/turns/t0001/poll", params={"v": 0}).text
    assert line_of(page) == expected

    # A request with nothing left to do hands back nothing, so the line is as it was.
    finish(ui, sid, DEDUPE, "t0002", 1)
    page = client.get(f"/ui/s/{sid}/turns/t0002/poll", params={"v": 0}).text
    assert line_of(page) == expected


def test_the_line_states_which_file_and_nothing_about_a_result() -> None:
    from backend.web.templating import render

    for delivered in (False, True):
        text = render("files.html", files=_files_view(delivered)).replace("\n", " ")
        line = text.split('<p id="next-file" class="hint next-file">')[1].split("</p>")[0]
        assert line.startswith("Your next request will use a.xlsx")
        for word in ("removed", "done", "cleaned", "saved", "fixed", "ready", "deleted"):
            assert word not in line.lower()
        assert not any(c.isdigit() for c in line)


def _files_view(delivered: bool):  # type: ignore[no-untyped-def]
    from backend.web import views

    return views.FilesView(
        session_id="s_x",
        uploads=(),
        delivered=(),
        upload_open=True,
        accept=".xlsx",
        limit_text=".xlsx up to 2 MB",
        next_file=views.NextFileView(name="a.xlsx", delivered=delivered),
    )

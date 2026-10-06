"""Rule 1 in the page (frontend design Section 4, items 1 to 10 of 4.3 and the tripwire).

The guarantee is structural. The page can't write its own statement about a user's file, because
the reply is rendered as escaped text exactly as the API returned it, the download area comes only
from the turn's own delivered files, and exactly one function reads how a turn ended.
"""

from __future__ import annotations

import ast
import dataclasses
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from backend import schemas
from backend.errors import ApiError
from backend.schemas import DeliveredFileOut, FinishedTurn, PendingConfirmation, PlanStep
from backend.web import views
from backend.web.templating import STATIC_DIR, TEMPLATES_DIR, WEB_DIR, environment
from tests.backend.factories import (
    DEDUPE_THEN_EMPTY,
    PipelineFactory,
    QueuedProvider,
    chain,
    single,
)
from tests.web.htmltools import Element, parse
from tests.web.support import Ui, make_app, pending_id, reply_of, wait_for_container

OUTCOMES = ("delivered", "not_delivered", "failed", "integrity_failure")
NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
# Markup-like text, template syntax, newlines and spaces at both ends (4.3, item 4).
SENTINEL = "  <b>x</b> **y** http://a.b {{ 1 }} {% x %} &amp;\n\nsecond line  \n"
FILES = [
    DeliveredFileOut(
        file_id="f_1",
        name="report_deduplicated_k.xlsx",
        size=16384,
        sha256="0" * 64,
        download_url="/api/v1/sessions/s_x/files/f_1/download",
    )
]
SESSION = "s_testsession"


def finished(
    outcome: str = "delivered",
    reply: str = SENTINEL,
    files: list[DeliveredFileOut] | None = None,
    reply_source: str = "pipeline",
    **extra: Any,
) -> FinishedTurn:
    return FinishedTurn(
        turn_id="t0001",
        seq=1,
        message="clean it",
        mode="friend",
        started_at=NOW,
        delivered_files=FILES if files is None else files,
        status="finished",
        outcome=outcome,
        finished_at=NOW,
        reply=reply,
        reply_source=reply_source,
        **extra,
    )  # type: ignore[arg-type]


def item(turn: Any, version: int = 3, **kwargs: Any) -> Element:
    """The turn container as the page renders it."""
    view = views.turn_view(SESSION, turn, version, NOW, **kwargs)
    html = environment.get_template("_parts.html").module.turn_item(view)
    container = parse(str(html)).find("li", id=f"turn-{turn.turn_id}")
    assert container is not None
    return container


def plan(steps: list[PlanStep], note: str | None = None) -> PendingConfirmation:
    return PendingConfirmation(
        confirmation_id="c_1", steps=steps, plan_note=note, expires_at=NOW + timedelta(seconds=90)
    )


def awaiting(pending: Any, files: list[DeliveredFileOut] | None = None) -> Any:
    return schemas.AwaitingConfirmationTurn.model_construct(
        turn_id="t0001",
        seq=1,
        message="clean it",
        mode="friend",
        started_at=NOW,
        delivered_files=files or [],
        status="awaiting_confirmation",
        pending_confirmation=pending,
        outcome=None,
        finished_at=None,
        reply=None,
        reply_source=None,
        detail=None,
    )


def running(files: list[DeliveredFileOut] | None = None) -> Any:
    return schemas.RunningTurn.model_construct(
        turn_id="t0001",
        seq=1,
        message="clean it",
        mode="friend",
        started_at=NOW,
        delivered_files=files or [],
        status="running",
        pending_confirmation=None,
        outcome=None,
        finished_at=None,
        reply=None,
        reply_source=None,
        detail=None,
    )


# -- 4.3 item 1: one function reads how a turn ended -------------------------------------------


def _ui_sources() -> list[Path]:
    skip = {"htmx.min.js", "htmx.LICENSE", "VENDORED.md"}
    return sorted(
        p
        for p in WEB_DIR.rglob("*")
        if p.is_file()
        and p.suffix in {".py", ".html", ".js", ".css"}
        and p.name not in skip
        and "__pycache__" not in p.parts
    )


def test_only_should_show_downloads_reads_how_a_turn_ended() -> None:
    word = re.compile("outcome", re.IGNORECASE)
    views_source = (WEB_DIR / "views.py").read_text(encoding="utf-8")
    tree = ast.parse(views_source)
    (function,) = [
        n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "should_show_downloads"
    ]
    lines = views_source.splitlines()
    outside = "\n".join(
        line
        for number, line in enumerate(lines, start=1)
        if not function.lineno <= number <= (function.end_lineno or function.lineno)
    )
    assert word.search("\n".join(lines[function.lineno - 1 : function.end_lineno]))
    offenders = [
        str(p.relative_to(WEB_DIR))
        for p in _ui_sources()
        if p.name != "views.py" and word.search(p.read_text(encoding="utf-8"))
    ]
    if word.search(outside):
        offenders.append("views.py (outside should_show_downloads)")
    assert offenders == []
    fields = {f.name for f in dataclasses.fields(views.TurnView)}
    assert not any("outcome" in name or "result" in name for name in fields)
    assert {"reply", "downloads"} <= fields


def test_should_show_downloads_is_true_only_for_a_delivered_turn() -> None:
    assert views.should_show_downloads(finished("delivered"))
    for outcome in ("not_delivered", "failed", "integrity_failure"):
        assert not views.should_show_downloads(finished(outcome))
    assert not views.should_show_downloads(running())
    assert not views.should_show_downloads(SimpleNamespace())


# -- 4.3 item 2: the four outcomes differ only by the download area ---------------------------


def test_the_four_outcomes_render_the_same_tree_once_the_download_area_is_removed() -> None:
    def drop(element: Element) -> bool:
        return element.get("data-slot") == "downloads"

    trees = {o: item(finished(o)).signature(drop) for o in OUTCOMES}

    assert len({repr(t) for t in trees.values()}) == 1
    assert item(finished("delivered")).find("ul", **{"data-slot": "downloads"}) is not None
    # attributes included: classes, data-*, aria-*, style
    for outcome in OUTCOMES:
        for element in item(finished(outcome)).walk():
            assert "style" not in element.attrs
            assert "outcome" not in " ".join(f"{k}={v}" for k, v in element.attrs.items()).lower()


# -- 4.3 items 3, 4: the reply slot holds the reply and nothing else --------------------------


@pytest.mark.parametrize("outcome", OUTCOMES)
def test_the_reply_slot_is_exactly_the_reply_with_no_element_made_from_it(outcome: str) -> None:
    container = item(finished(outcome))

    slot = container.find("div", **{"data-slot": "reply"})
    assert slot is not None
    assert slot.text() == SENTINEL
    assert [c for c in slot.children if isinstance(c, Element)] == []  # no <b>, no link, no note
    assert len(container.find_all("div", **{"data-slot": "reply"})) == 1


def test_the_slot_holds_a_reply_that_looks_like_a_page_notice_as_plain_text() -> None:
    notice = "This page lost contact with the server before the turn finished."
    slot = item(finished(reply=notice)).find("div", **{"data-slot": "reply"})
    assert slot is not None and slot.text() == notice  # only the pipeline's reply goes here


def test_the_reply_is_escaped_in_the_raw_html_too() -> None:
    html = str(
        environment.get_template("_parts.html").module.turn_item(
            views.turn_view(SESSION, finished(), 3, NOW)
        )
    )
    assert "<b>x</b>" not in html
    assert "&lt;b&gt;x&lt;/b&gt;" in html
    assert "{{ 1 }}" in html  # template syntax in a reply is data, not a template


def test_the_reply_slot_is_white_space_preserving_text_in_the_stylesheet() -> None:
    css = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")
    assert re.search(r"\.text\s*\{[^}]*white-space:\s*pre-wrap", css)
    assert item(finished()).find("div", **{"data-slot": "reply"}).has_class("text")  # type: ignore[union-attr]


# -- 4.3 item 5: no download unless delivered -------------------------------------------------


@pytest.mark.parametrize("outcome", ["not_delivered", "failed", "integrity_failure"])
def test_a_turn_that_wasnt_delivered_shows_no_link_even_if_files_are_listed(outcome: str) -> None:
    container = item(finished(outcome, files=FILES))

    assert container.find_all("a") == []
    assert container.find("ul", **{"data-slot": "downloads"}) is None
    state = SimpleNamespace(
        session_id=SESSION,
        active_file_id=None,
        files=[],
        turns=[finished(outcome, files=FILES)],
        limits=schemas.Limits(
            max_upload_bytes=1, accepted_extensions=[".xlsx"], max_message_chars=9
        ),
    )
    panel = views.files_view(state, None, upload_open=True)  # type: ignore[arg-type]
    assert panel.delivered == ()  # the sidebar follows the same rule


def test_a_delivered_turn_links_only_its_own_files_by_the_apis_url() -> None:
    container = item(finished("delivered"))

    (link,) = container.find_all("a")
    assert link.get("href") == FILES[0].download_url and "download" in link.attrs
    assert link.text() == "Download report_deduplicated_k.xlsx (16 KB)"


def test_the_download_area_is_never_read_from_the_reply() -> None:
    reply = "Done. Your file is report_cleaned.xlsx, download it here: /api/v1/files/x"
    container = item(finished("not_delivered", reply=reply, files=[]))

    assert container.find_all("a") == []
    assert container.find("div", **{"data-slot": "reply"}).text() == reply  # type: ignore[union-attr]


# -- 4.3 item 6: nothing that looks like a result before the turn is finished -----------------


def test_a_running_or_waiting_turn_has_no_reply_slot_downloads_or_file_names_but_the_plans() -> (
    None
):
    steps = [
        PlanStep(capability="spreadsheet.deduplicate", prompt="Remove duplicate rows from a.xlsx.")
    ]
    for turn in (running(files=FILES), awaiting(plan(steps), files=FILES)):
        container = item(turn, version=1)
        assert container.find("div", **{"data-slot": "reply"}) is None
        assert container.find("ul", **{"data-slot": "downloads"}) is None
        assert container.find_all("a") == []
        assert "report_deduplicated_k.xlsx" not in container.text()
        names = re.findall(r"[\w.-]+\.xlsx", container.text())
        assert set(names) <= {"a.xlsx"}


# -- 4.3 item 7: the plan is approved only with the whole plan -------------------------------


@pytest.mark.parametrize(
    "pending",
    [
        plan([PlanStep(capability="x", prompt="One.")]).model_copy(update={"steps": []}),
        SimpleNamespace(
            confirmation_id="c_1", steps=None, plan_note=None, expires_at=NOW + timedelta(seconds=5)
        ),
        SimpleNamespace(
            confirmation_id="c_1", plan_note=None, expires_at=NOW + timedelta(seconds=5)
        ),  # no steps attribute at all
    ],
    ids=["empty", "null", "missing"],
)
def test_a_plan_without_steps_offers_only_reject_with_the_fixed_text(pending: Any) -> None:
    container = item(awaiting(pending), version=1)

    form = container.find("form")
    assert form is not None
    assert [b.get("value") for b in form.find_all("button")] == ["reject"]
    assert [b.text() for b in container.find_all("button")] == ["Reject plan"]
    assert "The server didn't send the plan, so it can't be approved here." in container.text()
    assert container.find("ol").find_all("li") == []  # type: ignore[union-attr]


def test_a_plan_with_steps_offers_both_and_no_fixed_text() -> None:
    steps = [PlanStep(capability="x", prompt="One."), PlanStep(capability="y", prompt="Then two.")]
    container = item(awaiting(plan(steps, note="A note.")), version=1)

    assert [b.get("value") for b in container.find_all("button")] == ["approve", "reject"]
    assert "can't be approved here" not in container.text()
    assert [li.text() for li in container.find("ol").find_all("li")] == ["One.", "Then two."]  # type: ignore[union-attr]
    assert container.find("p", **{"class": "plan-note"}).text() == "A note."  # type: ignore[union-attr]


# -- 4.3 item 8: session scoping (the server's half; the swap guard is in the e2e tests) ------


def test_a_fragment_request_for_one_session_never_renders_another_sessions_turn(
    tmp_path: Path,
) -> None:
    provider = QueuedProvider(single("general_question") + single("general_question"))
    app, client = make_app(tmp_path, PipelineFactory(provider))
    ui = Ui(client)
    a = ui.new_session()
    b = ui.new_session()  # the pointer is now B's
    ui.start(a, "message in session A")
    wait_for_container(ui, a, "t0001", lambda c: c.get("data-status") == "finished")
    ui.start(b, "message in session B")
    wait_for_container(ui, b, "t0001", lambda c: c.get("data-status") == "finished")

    from_a = ui.poll(a, "t0001", v=0)
    from_b = ui.poll(b, "t0001", v=0)

    assert "message in session A" in from_a.text and "message in session B" not in from_a.text
    assert "message in session B" in from_b.text and "message in session A" not in from_b.text
    for text in (from_a.text, from_b.text):
        ids = set(re.findall(r'data-session-id="([^"]+)"', text))
        assert len(ids) == 1
    assert re.findall(r'data-session-id="([^"]+)"', from_a.text)[0] == a
    # Session A goes away (swept, or lost in a restart): its fragments are A's own 404, and B's
    # t0001 isn't served in its place.
    app.state.registry._sessions.pop(next(k for k in app.state.registry.live_keys()), None)
    gone = [
        ui.poll(a, "t0001", v=0),
        client.get(f"/ui/s/{a}/transcript"),
    ]
    other = [r for r in gone if r.status_code != 404]
    for response in other:
        assert "message in session B" not in response.text


# -- 4.3 item 9: a fallback reply is shown unchanged ------------------------------------------


def test_a_fallback_reply_is_rendered_exactly_like_any_other_reply() -> None:
    plain = item(finished(reply_source="pipeline"))
    fallback = item(finished(reply_source="pipeline_fallback"))

    assert plain.signature() == fallback.signature()
    assert fallback.find("div", **{"data-slot": "reply"}).text() == SENTINEL  # type: ignore[union-attr]


def test_a_turn_the_pipeline_could_not_finish_shows_the_fallback_reply_verbatim(
    tmp_path: Path,
) -> None:
    def factory(confirmation_provider: Any) -> Any:
        raise RuntimeError("secret cell 4111 at /srv/data")  # the engine can't even be built

    _, client = make_app(tmp_path, factory)
    ui = Ui(client)
    sid = ui.new_session()
    ui.start(sid, "what can you do?")

    container = wait_for_container(ui, sid, "t0001", lambda c: c.get("data-status") == "finished")

    api = ui.api_turn(sid, "t0001")
    assert api["reply_source"] == "pipeline_fallback" and api["outcome"] == "failed"
    assert reply_of(container) == api["reply"]
    assert "4111" not in (reply_of(container) or "") and "/srv" not in (reply_of(container) or "")
    assert container.find("ul", **{"data-slot": "downloads"}) is None


# -- 4.3 item 10: the page's reply is the JSON API's reply ------------------------------------


def test_the_pages_reply_equals_the_json_reply_for_every_way_a_turn_finishes(
    tmp_path: Path,
) -> None:
    provider = QueuedProvider(
        chain(*DEDUPE_THEN_EMPTY) + chain(*DEDUPE_THEN_EMPTY) + single("general_question")
    )
    _, client = make_app(tmp_path, PipelineFactory(provider))
    ui = Ui(client)
    sid = ui.new_session()
    ui.upload(sid)
    seen: dict[str, str] = {}

    for number, decision in ((1, "approve"), (2, "reject")):
        ui.start(sid, "remove duplicate rows, then remove empty rows", last_seq=number - 1)
        tid = f"t000{number}"
        container = wait_for_container(
            ui, sid, tid, lambda c: c.find("section", **{"class": "plan"}) is not None
        )
        ui.answer(sid, tid, pending_id(container), decision)
        wait_for_container(ui, sid, tid, lambda c: c.get("data-status") == "finished")
    ui.start(sid, "what can you do?", last_seq=2)
    wait_for_container(ui, sid, "t0003", lambda c: c.get("data-status") == "finished")

    page = parse(client.get("/").text)  # a reload renders them all again
    for number in (1, 2, 3):
        tid = f"t000{number}"
        api = ui.api_turn(sid, tid)
        polled = wait_for_container(ui, sid, tid, lambda c: True)
        held = page.find("li", **{"data-turn-id": tid})
        assert held is not None
        assert reply_of(polled) == api["reply"]
        assert reply_of(held) == api["reply"]
        seen[tid] = api["outcome"]
    assert set(seen.values()) == {"delivered", "not_delivered"}


def test_a_finished_turn_that_lacks_a_field_is_an_internal_error_never_a_guess() -> None:
    broken = schemas.FinishedTurn.model_construct(
        turn_id="t0001",
        seq=1,
        message="m",
        mode="friend",
        started_at=NOW,
        delivered_files=[],
        status="finished",
        outcome=None,
        finished_at=NOW,
        reply="Hi",
        reply_source="pipeline",
        pending_confirmation=None,
        detail=None,
    )
    with pytest.raises(ApiError) as caught:
        views.turn_view(SESSION, broken, 1, NOW)
    assert caught.value.status == 500
    assert caught.value.message == "Something went wrong on the server."


# -- 4.2: text-only rendering, and the tripwire (4.3 item 12) ---------------------------------


def test_nothing_switches_escaping_off_anywhere_in_the_page() -> None:
    banned = (r"\|\s*safe", r"Markup\(", r"autoescape\s+false", r"autoescape\s*=\s*False")
    for path in _ui_sources():
        text = path.read_text(encoding="utf-8")
        for pattern in banned:
            assert not re.search(pattern, text, re.IGNORECASE), (path.name, pattern)
    assert environment.autoescape is True


def test_the_script_writes_text_only_with_text_content() -> None:
    script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    for banned in (
        "innerHTML",
        "outerHTML",
        "insertAdjacentHTML",
        "document.write",
        "eval(",
        "new Function",
        "localStorage",
        "sessionStorage",
    ):
        assert banned not in script, banned
    assert "textContent" in script


# The words the page may never use to describe work (design 4.1, rule 2).
BANNED_WORDS = (
    "done",
    "success",
    "complete",
    "saved",
    "removed",
    "cleaned",
    "failed",
    "finished successfully",
    "all set",
)
# A reviewed allowlist of phrases that contain a banned word and are about a request, not work.
ALLOWED_PHRASES: tuple[str, ...] = ()


def test_the_page_uses_none_of_the_banned_words_for_work() -> None:
    sources = [p for p in _ui_sources() if p.suffix in {".html", ".js", ".css"}]
    assert sources
    hits: list[str] = []
    for path in sources:
        text = path.read_text(encoding="utf-8")
        for phrase in ALLOWED_PHRASES:
            text = text.replace(phrase, "")
        for word in BANNED_WORDS:
            if re.search(rf"\b{re.escape(word)}\b", text, re.IGNORECASE):
                hits.append(f"{path.name}: {word}")
    assert hits == []


def test_no_check_mark_or_cross_symbols_and_no_status_colours() -> None:
    marks = "✓✔✅☑✗✘✕✖❌⚠❗🟢🔴"
    for path in _ui_sources():
        text = path.read_text(encoding="utf-8")
        assert not any(ch in text for ch in marks), path.name
    css = (STATIC_DIR / "styles.css").read_text(encoding="utf-8").lower()
    for token in ("success", "danger", "failure", "green", "red;", "#0f0", "#f00"):
        assert token not in css, token
    assert all(p.suffix not in {".svg", ".png", ".gif", ".ico"} for p in STATIC_DIR.iterdir())
    assert TEMPLATES_DIR.is_dir()

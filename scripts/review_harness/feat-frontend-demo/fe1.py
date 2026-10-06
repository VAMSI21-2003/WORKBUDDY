# ruff: noqa: E501 — reviewer tooling, not product code
"""Shared pieces for the FE-1 (web page) review probes. Not part of the test suite.

Drives the page's own routes (``/``, ``/ui/...``) the way the browser does, over
``TestClient`` on https (the cookies are ``Secure``), against the real app
(``backend.main.create_app``) and the real pipeline (``build_engine``: real
skills, Hands, Inspector, verifiers, Voice). Only the LLM is faked, with
``../feat-backend-api-core/harness.py``'s ``FakeLLM``; the factory that records
each run is ``../feat-backend-api-confirmation/pr3.py``'s (both imported, not
edited).
"""

from __future__ import annotations

import hashlib
import re
import sys
import tempfile
import time
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "feat-backend-api-confirmation"))

import pr3  # noqa: E402  (consumes --tree; puts the core harness on sys.path)
from pr3 import h  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

import backend.settings as settings_module  # noqa: E402
from backend.main import create_app  # noqa: E402
from backend.settings import Settings  # noqa: E402

RELAXED = {
    name: settings_module.BucketSpec(burst=1_000_000, per_hour=3_600_000_000)
    for name in settings_module.DEFAULT_RATE_LIMITS
}
SPEAKERS = pr3.SPEAKERS


# -- A tiny HTML tree (standard library only) ------------------------------------------

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}


class Node:
    def __init__(self, tag: str, attrs: dict[str, str | None]) -> None:
        self.tag = tag
        self.attrs = attrs
        self.children: list[Node | str] = []

    def walk(self):
        yield self
        for c in self.children:
            if isinstance(c, Node):
                yield from c.walk()

    def text(self) -> str:
        return "".join(c if isinstance(c, str) else c.text() for c in self.children)

    def find_all(self, tag: str | None = None, **attrs: str) -> list[Node]:
        out = []
        for n in self.walk():
            if tag is not None and n.tag != tag:
                continue
            if all((n.attrs.get(k.replace("_", "-")) == v) if k != "cls" else (v in (n.attrs.get("class") or "").split()) for k, v in attrs.items()):
                out.append(n)
        return out

    def find(self, tag: str | None = None, **attrs: str) -> Node | None:
        found = self.find_all(tag, **attrs)
        return found[0] if found else None


class _B(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("#root", {})
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        n = Node(tag, dict(attrs))
        self.stack[-1].children.append(n)
        if tag not in VOID:
            self.stack.append(n)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def parse(html: str) -> Node:
    b = _B()
    b.feed(html)
    b.close()
    return b.root


def visible_text(node: Node, skip_reply: bool = True) -> str:
    """The text a reader sees in ``node`` (no <template>, no <title>), optionally without the reply slot."""
    parts: list[str] = []

    def go(n: Node) -> None:
        if n.tag in ("template", "title", "script", "style"):
            return
        if skip_reply and n.attrs.get("data-slot") == "reply":
            return
        for c in n.children:
            if isinstance(c, str):
                parts.append(c)
            else:
                go(c)

    go(node)
    return re.sub(r"\s+", " ", " ".join(parts)).strip()


# -- The app ----------------------------------------------------------------------------


class PageApp:
    def __init__(self, llm: Any, speaker: str, *, password: str | None = None, **settings: Any) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.factory = pr3.Factory(llm, speaker)
        values: dict[str, Any] = {"data_dir": Path(self.tmp.name) / "data", "rate_limits": RELAXED}
        if password is not None:
            values.update(env="", demo_password=password)
        values.update(settings)
        self.settings = Settings(**values)
        self.app = create_app(self.settings, self.factory)
        self.client = TestClient(self.app, base_url="https://testserver", raise_server_exceptions=False)

    def close(self) -> None:
        self.tmp.cleanup()

    # what the page does
    def new_session(self) -> str:
        r = self.client.post("/ui/sessions")
        assert r.status_code == 200, (r.status_code, r.text[:300])
        m = re.search(r'id="workspace"[^>]*data-session-id="([^"]+)"', r.text)
        assert m, r.text[:300]
        return m.group(1)

    def upload(self, sid: str, data: bytes, name: str = "report.xlsx") -> tuple[Any, Path | None]:
        r = self.client.post(f"/ui/s/{sid}/files", files={"file": (name, data, "application/octet-stream")})
        paths = sorted(p for p in self.settings.uploads_dir.rglob("*") if p.is_file())
        return r, (paths[-1] if paths else None)

    def start(self, sid: str, message: str, mode: str) -> Any:
        return self.client.post(f"/ui/s/{sid}/turns", data={"message": message, "mode": mode, "last_seq": "0"})

    def poll(self, sid: str, tid: str, v: int | None, **extra: str) -> Any:
        params = dict(extra)
        if v is not None:
            params["v"] = str(v)
        return self.client.get(f"/ui/s/{sid}/turns/{tid}/poll", params=params)

    def answer(self, sid: str, tid: str, cid: str, decision: str) -> Any:
        return self.client.post(
            f"/ui/s/{sid}/turns/{tid}/confirmation", data={"confirmation_id": cid, "decision": decision}
        )

    def api_turn(self, sid: str, tid: str) -> dict[str, Any]:
        return self.client.get(f"/api/v1/sessions/{sid}/turns/{tid}").json()


def container_of(html: str, tid: str) -> Node | None:
    return parse(html).find("li", **{"data-turn-id": tid})


def reply_slot(container: Node) -> str | None:
    slot = container.find(**{"data-slot": "reply"})
    return None if slot is None else slot.text()


def links(node: Node) -> list[tuple[str, str]]:
    return [(a.attrs.get("href") or "", re.sub(r"\s+", " ", a.text()).strip()) for a in node.find_all("a")]


def drive_page(
    app: PageApp,
    sid: str,
    message: str,
    mode: str,
    decision: Any = "approve",
    *,
    answer_after_s: float = 0.0,
    timeout_s: float = 60.0,
) -> dict[str, Any]:
    """Send ``message`` through the composer and poll as the page does until the turn is finished."""
    r = app.start(sid, message, mode)
    if r.status_code != 200:
        return {"start_http": r.status_code, "start_body": r.text[:300]}
    m = re.search(r'data-turn-id="(t\d+)"', r.text)
    tid = m.group(1)
    first = container_of(r.text, tid)
    held = int(first.attrs.get("data-version") or 0)
    seen: list[str] = [first.attrs.get("data-status") or "?"]
    plans: list[dict[str, Any]] = []
    answers: list[dict[str, Any]] = []
    pre_finish_texts: list[str] = [visible_text(first)]
    last_files_html = r.text
    end = time.monotonic() + timeout_s
    container = first
    while True:
        if container.attrs.get("data-status") == "finished":
            break
        if time.monotonic() > end:
            return {"turn_id": tid, "TIMEOUT": True, "statuses": seen}
        time.sleep(0.02)
        pr = app.poll(sid, tid, held)
        if pr.status_code != 200:
            seen.append(f"HTTP {pr.status_code}")
            continue
        c = container_of(pr.text, tid)
        if c is None:
            continue  # unchanged: a re-armed poll element
        container = c
        last_files_html = pr.text
        held = int(c.attrs.get("data-version") or 0)
        st = c.attrs.get("data-status") or "?"
        if seen[-1] != st:
            seen.append(st)
        if st != "finished":
            pre_finish_texts.append(visible_text(c))
        form = c.find("form", **{"data-plan-form": ""}) or next((f for f in c.find_all("form") if "data-plan-form" in f.attrs), None)
        if form is not None:
            cid = form.find("input", name="confirmation_id").attrs.get("value")
            if all(p["confirmation_id"] != cid for p in plans):
                card = c.find("section", cls="plan")
                plans.append(
                    {
                        "confirmation_id": cid,
                        "steps": [li.text().strip() for li in card.find("ol").find_all("li")],
                        "note": (card.find("p", cls="plan-note").text() if card.find("p", cls="plan-note") else None),
                        "buttons": [b.attrs.get("value") for b in card.find_all("button")],
                        "card_text": visible_text(card),
                    }
                )
                if answer_after_s:
                    time.sleep(answer_after_s)
                if decision in ("approve", "reject"):
                    ar = app.answer(sid, tid, cid, decision)
                    entry = {"decision": decision, "http": ar.status_code}
                    ac = container_of(ar.text, tid) if ar.status_code == 200 else None
                    if ac is not None:
                        entry["status_after"] = ac.attrs.get("data-status")
                        entry["card_after"] = ac.find("section", cls="plan") is not None
                        container = ac
                        held = int(ac.attrs.get("data-version") or 0)
                        last_files_html = ar.text
                    answers.append(entry)
    files = parse(last_files_html).find("section", id="files")
    return {
        "turn_id": tid,
        "statuses": seen,
        "plans": plans,
        "answers": answers,
        "page_reply": reply_slot(container),
        "page_turn_links": links(container),
        "page_files_links": links(files) if files is not None else None,
        "page_text_outside_reply": visible_text(container),
        "pre_finish_texts": pre_finish_texts,
        "live_region": _live(last_files_html),
    }


def _live(html: str) -> str | None:
    for n in parse(html).walk():
        if (n.attrs.get("hx-swap-oob") or "").endswith("#live"):
            return n.text()
    return None


def check_downloads(app: PageApp, page_links: list[tuple[str, str]], api: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    by_url = {d["download_url"]: d for d in api.get("delivered_files") or []}
    for href, label in page_links:
        r = app.client.get(href)
        e: dict[str, Any] = {"href": href, "label": label, "http": r.status_code, "listed_in_api": href in by_url}
        if r.status_code == 200:
            e["sha_matches_api"] = by_url.get(href, {}).get("sha256") == hashlib.sha256(r.content).hexdigest()
            e["independent"] = h.describe(h.read_rows(r.content))
        out.append(e)
    return out

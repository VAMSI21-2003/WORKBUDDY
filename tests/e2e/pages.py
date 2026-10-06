"""Small helpers for driving the page in the browser tests."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from playwright.sync_api import Page

from tests.backend.helpers import xlsx_bytes
from tests.e2e.conftest import PASSWORD

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
CHAIN = "remove duplicate rows, then remove empty rows"

# Records every page notice that ever appears, even for a moment, from before the page's own
# script runs: a notice that flashed up and went away is still a notice.
WATCH_NOTICES = """
window.__notices = [];
new MutationObserver((records) => {
  for (const record of records) {
    for (const node of record.addedNodes) {
      if (node.nodeType !== 1) continue;
      const found = node.matches('.notice') ? [node] : Array.from(node.querySelectorAll('.notice'));
      for (const notice of found) window.__notices.push(notice.textContent);
    }
  }
}).observe(document, { childList: true, subtree: true });
document.addEventListener('DOMContentLoaded', () => {
  for (const notice of document.querySelectorAll('.notice')) window.__notices.push(notice.textContent);
});
"""


class Watch:
    """What happened on the wire and on the page, for the assertions at the end."""

    def __init__(self, page: Page) -> None:
        self.statuses: list[tuple[int, str]] = []
        page.add_init_script(WATCH_NOTICES)
        page.on("response", lambda r: self.statuses.append((r.status, r.url)))
        self.errors: list[str] = []
        page.on("pageerror", lambda e: self.errors.append(str(e)))
        page.on(
            "console",
            lambda m: (
                self.errors.append(m.text)
                if m.type == "error" and "status of" not in m.text
                else None
            ),
        )

    def bad(self, *, allow: tuple[int, ...] = ()) -> list[tuple[int, str]]:
        return [(s, u) for s, u in self.statuses if s >= 400 and s not in allow]


def notices(page: Page) -> list[str]:
    return list(page.evaluate("window.__notices"))


def sign_in(page: Page, base: str, password: str = PASSWORD) -> None:
    page.goto(base + "/")
    page.fill("#password", password)
    page.click("#gate-form button[type=submit]")
    page.wait_for_selector("#workspace[data-session-id]")


def session_id(page: Page) -> str:
    return str(page.get_attribute("#workspace", "data-session-id"))


def upload(page: Page, data: bytes | None = None, name: str = "report.xlsx") -> None:
    page.set_input_files(
        "#file", {"name": name, "mimeType": XLSX, "buffer": xlsx_bytes() if data is None else data}
    )
    page.click("#upload-form button[type=submit]")
    page.wait_for_selector(f".file-list .file-name:text-is('{name}')")


def send(page: Page, message: str) -> None:
    page.fill("#message", message)
    page.click("#composer button[type=submit]")


def reply_text(page: Page, turn: str = "t0001") -> str:
    return str(page.inner_text(f"#turn-{turn} [data-slot=reply]", timeout=60_000))


def reply_raw(page: Page, turn: str = "t0001") -> str:
    """The reply slot's text as the DOM holds it (no rendering changes)."""
    selector = f"#turn-{turn} [data-slot=reply]"
    page.wait_for_selector(selector, timeout=60_000)
    return str(page.eval_on_selector(selector, "e => e.textContent"))


def no_horizontal_overflow(page: Page) -> bool:
    return bool(
        page.evaluate(
            "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
        )
    )


def web_storage(page: Page) -> dict[str, list[str]]:
    return dict(
        page.evaluate("({local: Object.keys(localStorage), session: Object.keys(sessionStorage)})")
    )


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def reachable(page: Page, selector: str) -> bool:
    """Whether a control can be reached on this viewport: scrolled into view, inside its width."""
    locator = page.locator(selector).first
    locator.scroll_into_view_if_needed()
    box: Any = locator.bounding_box()
    width = page.viewport_size["width"] if page.viewport_size else 0
    return bool(
        locator.is_visible() and box and box["x"] >= 0 and box["x"] + box["width"] <= width + 1
    )

"""A small HTML tree built on the standard library's ``html.parser`` (frontend design 4.3, item 2).

Used to read what a rendered fragment contains and to compare two fragments tag by tag, text by text
and attribute by attribute. No third-party parser: the page's own tests don't add a dependency.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from html.parser import HTMLParser

VOID = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "source",
    "track",
}


class Element:
    def __init__(self, tag: str, attrs: dict[str, str | None] | None = None) -> None:
        self.tag = tag
        self.attrs: dict[str, str | None] = attrs or {}
        self.children: list[Element | str] = []

    def walk(self) -> Iterator[Element]:
        yield self
        for child in self.children:
            if isinstance(child, Element):
                yield from child.walk()

    def text(self) -> str:
        """The element's text exactly as the parser read it (no whitespace changes)."""
        return "".join(c if isinstance(c, str) else c.text() for c in self.children)

    def get(self, name: str, default: str | None = None) -> str | None:
        return self.attrs.get(name, default)

    def find_all(
        self,
        tag: str | None = None,
        *,
        where: Callable[[Element], bool] | None = None,
        **attrs: str,
    ) -> list[Element]:
        wanted = {k.replace("_", "-"): v for k, v in attrs.items()}
        found = []
        for element in self.walk():
            if tag is not None and element.tag != tag:
                continue
            if any(element.attrs.get(k) != v for k, v in wanted.items()):
                continue
            if where is not None and not where(element):
                continue
            found.append(element)
        return found

    def find(self, tag: str | None = None, **attrs: str) -> Element | None:
        found = self.find_all(tag, **attrs)
        return found[0] if found else None

    def has_class(self, name: str) -> bool:
        return name in (self.attrs.get("class") or "").split()

    def signature(self, drop: Callable[[Element], bool] | None = None) -> object:
        """The tree as plain data: tag, sorted attributes, children, text with whitespace folded."""
        children: list[object] = []
        for child in self.children:
            if isinstance(child, str):
                folded = " ".join(child.split())
                if folded:
                    children.append(folded)
            elif drop is None or not drop(child):
                children.append(child.signature(drop))
        return (self.tag, tuple(sorted(self.attrs.items(), key=lambda kv: kv[0])), tuple(children))


class _Builder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Element("#root")
        self.stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        element = Element(tag, dict(attrs))
        self.stack[-1].children.append(element)
        if tag not in VOID:
            self.stack.append(element)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.stack[-1].children.append(Element(tag, dict(attrs)))

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data: str) -> None:
        self.stack[-1].children.append(data)


def parse(html: str) -> Element:
    builder = _Builder()
    builder.feed(html)
    builder.close()
    return builder.root

"""Text read from a user's file, made safe to put inside a sentence Buddy says.

A column header or a cell can hold anything, including "Done! I removed every duplicate". When a
finding names a column or quotes a value, ``shown`` is the only way the text gets in: it is
cleaned (control, format and bidirectional characters removed, runs of white space folded to one
space), cut to a short length, and put inside double quotes, so a reader sees it as the file's
text and not as Buddy's. Nothing else from a file belongs in a reply.
"""

from __future__ import annotations

import unicodedata

# The longest text, in characters, that is shown from a file before it is cut with an ellipsis.
MAX_SHOWN_CHARS = 40
ELLIPSIS = "…"
# Every kind of double quotation mark: none of them may end the quotation we put around the text.
_QUOTES = frozenset('"\u201c\u201d\u201e\u201f\u00ab\u00bb\u301d\u301e\u301f\uff02')


def shown(value: object, limit: int = MAX_SHOWN_CHARS) -> str:
    """``value`` as quoted, cleaned and length-capped text from the file.

    Args:
        value: A header, a cell value or anything else read from the file.
        limit: The most characters of the text itself to keep (the quotes and a trailing
            ellipsis come on top).

    Returns:
        ``"text"`` in straight double quotes, an ellipsis before the closing quote if the text was
        cut. Any double quotation mark inside the text (straight, curly, low, guillemet) becomes a
        single quote, and a combining mark at either end is dropped, so the text can't end its own
        quotation. Empty or blank text shows as ``""``.
    """
    pieces: list[str] = []
    for char in str(value):
        category = unicodedata.category(char)
        if char.isspace() or category in ("Zl", "Zp", "Zs"):
            pieces.append(" ")
        elif category[0] == "C":
            continue  # control, format (bidi marks included), surrogate, private, unassigned
        elif char in _QUOTES:
            pieces.append("'")
        else:
            pieces.append(char)
    text = " ".join(unicodedata.normalize("NFC", "".join(pieces)).split())
    # A combining mark at either end would attach itself to our quote mark and blur where the
    # quotation stops.
    while text and unicodedata.category(text[0])[0] == "M":
        text = text[1:]
    while text and unicodedata.category(text[-1])[0] == "M":
        text = text[:-1].rstrip()
    if len(text) > limit:
        text = text[:limit].rstrip() + ELLIPSIS
    return f'"{text}"'

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


def shown(value: object, limit: int = MAX_SHOWN_CHARS) -> str:
    """``value`` as quoted, cleaned and length-capped text from the file.

    Args:
        value: A header, a cell value or anything else read from the file.
        limit: The most characters of the text itself to keep (the quotes and a trailing
            ellipsis come on top).

    Returns:
        ``"text"`` in straight double quotes, an ellipsis before the closing quote if the text was
        cut. A double quote inside the text becomes a single quote, so the text can't end its own
        quotation. Empty or blank text shows as ``""``.
    """
    pieces: list[str] = []
    for char in str(value):
        category = unicodedata.category(char)
        if char.isspace() or category in ("Zl", "Zp", "Zs"):
            pieces.append(" ")
        elif category[0] == "C":
            continue  # control, format (bidi marks included), surrogate, private, unassigned
        elif char == '"':
            pieces.append("'")
        else:
            pieces.append(char)
    text = " ".join("".join(pieces).split())
    if len(text) > limit:
        text = text[:limit].rstrip() + ELLIPSIS
    return f'"{text}"'

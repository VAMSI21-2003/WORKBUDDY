"""Text normalisation shared by the PDF worker and the code that checks its answers.

Standard library only: the sandboxed worker (``main.py``) imports this file by its directory, and
the parent process imports it as ``skills.pdf._worker.textnorm``. Both therefore normalise a cell
the same way, because it is the same code.
"""

import unicodedata

# The most characters one cell may hold, and the most cells one table may have. Over either, the
# table is refused rather than cut short: a cut cell would be a wrong value.
MAX_CELL_CHARS = 2000
MAX_CELLS = 50_000

# Unicode categories removed from a cell: format characters (zero-width joiners, bidirectional
# marks, the soft hyphen), surrogates, private use and unassigned code points. They have no
# printable form, and a spreadsheet cell (XML 1.0) can't hold some of them.
_DROPPED = frozenset({"Cf", "Cs", "Co", "Cn"})


def normalize_cell(text: str) -> str:
    """A cell's text as the skill writes it and the check compares it.

    NFKC (so a ligature or a full-width digit is the letters it stands for), format characters
    and unassigned code points dropped, control characters and every run of whitespace (a line
    break inside a cell included) turned into one space, and the ends trimmed. The result is
    ``""`` for a cell with nothing printable in it.
    """
    text = unicodedata.normalize("NFKC", text)
    kept = []
    for ch in text:
        category = unicodedata.category(ch)
        if category in _DROPPED:
            continue
        kept.append(" " if category == "Cc" else ch)
    return " ".join("".join(kept).split())

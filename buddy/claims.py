"""Claims — what makes a written reply showable, and the reply as shown.

On a **file turn** (anything but chat) the model doesn't word a single fact. Its messages are
fact slots, ``{F1b}`` (fact F1 in its phrasing b, from ``buddy.facts``), and its own social words
around them. ``check_reply`` renders the slots into the sentences code wrote, and refuses the
draft when:

- a slot names no fact or phrasing, or one fact is shown twice;
- a required fact isn't shown;
- the model's own words are more than social (``buddy.social``: a closed vocabulary, no digits,
  no phrase that answers or closes a question about the work).

So every statement about the file, a count, a file name or the work in a shown reply is a
sentence code wrote from the Inspector's results; nothing the model writes can add one. (Owner's
decision of 2026-10-24, after review round 2: the word-list checker that read the model's own
wording of the facts let false claims through two rounds running.)

On a **chat turn** the model writes freely (``CLAUDE.md``: Buddy chats about anything), with no
slots. The checks there are the backstop for a request Brain misread as chat: no file name, no
link or address, no count of rows, nothing said to be done ("cleaned it up", "took care of it"),
nothing about their file or its rows outside a question, an offer or a "not", and, when a file
is uploaded, no script the checks can't read. A question about the file is Brain's own action
(``question_about_file``) and is a file turn, not chat.

A problem's ``detail`` is fixed text and slot ids only, never the reply's words or the file's
text, so it is safe to log and to send back to the model.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

from buddy.facts import TURN_CHAT, FactSheet
from buddy.social import social_problems

# The most messages a reply may have, and the most characters in one as the model writes it.
MAX_MESSAGES = 3
MAX_MESSAGE_CHARS = 600

# Problem codes (logged; the detail is for the rewrite).
SHAPE = "shape"
SLOT = "slot"
MISSING = "missing"
SOCIAL = "social"
NUMBER = "number"
FILE_NAME = "file_name"
CLAIM = "claim"
CHAT_FILE_TALK = "chat_file_talk"
INTERNAL = "internal_label"
UNBACKED = "unbacked"
SCRIPT = "script"

_FLAGS = re.IGNORECASE

SLOT_RE = re.compile(r"\{\s*(F\d+)([a-z])\s*\}", _FLAGS)


@dataclass(frozen=True)
class Problem:
    """One reason a reply can't be shown.

    Attributes:
        code: What kind of problem (the constants above). Safe to log.
        detail: The same in plain words, for the rewrite request. Fixed text and slot ids only.
    """

    code: str
    detail: str


@dataclass(frozen=True)
class Checked:
    """The result of checking a draft.

    Attributes:
        messages: The reply as it would be shown (slots rendered); ``()`` when there are
            problems.
        problems: Why it can't be shown; ``()`` when it can.
    """

    messages: tuple[str, ...]
    problems: tuple[Problem, ...]


def check_reply(
    messages: Sequence[str],
    sheet: FactSheet,
    *,
    user_message: str = "",
    lowercase: bool = False,
) -> Checked:
    """Check a draft for ``sheet``'s turn and render it.

    Args:
        messages: The writer's messages.
        sheet: The turn's VERIFIED RESULT.
        user_message: The user's own message (a link they wrote may be echoed on a chat turn).
        lowercase: The person writes in lowercase: a fact's first letter is shown lowercase
            too, unless it starts a name, a quote or a number.

    Returns:
        The rendered messages, or the problems.
    """
    if not 1 <= len(messages) <= MAX_MESSAGES or any(
        not isinstance(m, str) or not m.strip() or len(m) > MAX_MESSAGE_CHARS for m in messages
    ):
        return Checked(
            (),
            (
                Problem(
                    SHAPE,
                    f"Write 1 to {MAX_MESSAGES} non-empty messages, each under "
                    f"{MAX_MESSAGE_CHARS} characters.",
                ),
            ),
        )
    if sheet.turn == TURN_CHAT:
        problems = _check_chat(list(messages), sheet, user_message)
        return Checked(() if problems else tuple(m.strip() for m in messages), problems)
    return _render(list(messages), sheet, lowercase=lowercase)


# -- A file turn: slots and social words ---------------------------------------------------


def _render(messages: list[str], sheet: FactSheet, *, lowercase: bool) -> Checked:
    problems: list[Problem] = []
    shown: list[str] = []
    rendered: list[str] = []
    for message in messages:
        social = SLOT_RE.sub(" ", message)
        for reason in social_problems(social):
            problems.append(Problem(SOCIAL, reason))

        def fill(match: re.Match[str]) -> str:
            slot = match.group(1).upper() + match.group(2).lower()
            text = sheet.variant(slot)
            if text is None:
                problems.append(Problem(SLOT, f"{{{slot}}} names no fact phrasing."))
                return " "
            shown.append(slot[:-1])
            return f" {_styled(text, lowercase)} "

        rendered.append(_tidy(SLOT_RE.sub(fill, message)))
    repeated = sorted({fact for fact in shown if shown.count(fact) > 1})
    if repeated:
        problems.append(Problem(SLOT, "Show each fact once: " + ", ".join(repeated) + "."))
    missing = [fact.id for fact in sheet.facts if fact.required and fact.id not in shown]
    if missing:
        problems.append(Problem(MISSING, "Show these facts too: " + ", ".join(missing) + "."))
    if any(not text for text in rendered):
        problems.append(Problem(SHAPE, "A message is empty."))
    problems = list(dict.fromkeys(problems))
    return Checked(() if problems else tuple(rendered), tuple(problems))


def _styled(text: str, lowercase: bool) -> str:
    first = text.split(" ", 1)[0]
    if lowercase and text[:1].isalpha() and not re.match(r"^I(?:'\w+)?$", first):
        return text[0].lower() + text[1:]
    return text


def _tidy(text: str) -> str:
    text = " ".join(text.split())
    return re.sub(r"\s+([,.!?;:…])", r"\1", text).strip()


# -- A chat turn ---------------------------------------------------------------------------

# Words that say a thing was done, by Buddy: a deed or a finished state.
_DONE_WORDS = re.compile(
    r"\b(?:removed|deleted|dropped|cut|cleared|purged|eliminated|erased|wiped|nuked|zapped"
    r"|killed|scrubbed|stripped|trimmed|took\s+out|taken\s+out|got\s+rid|gotten\s+rid"
    r"|knocked\s+out|deduped|deduplicated|fixed|handled|did|finished|completed|processed"
    r"|made|created|built|generated|produced|added|updated|changed|edited|modified|merged"
    r"|formatted|highlighted|colou?red|renamed|translated|converted|exported|emailed|mailed"
    r"|sent|shared|saved|printed|filtered|split|sorted|organi[sz]ed|wrapped\s+up|took\s+care"
    r"|taken\s+care|cleaned|tidied|done|gone|clean|tidy|spotless|ready|all\s+set|vanished"
    r"|disappeared|good\s+to\s+go|complete|history|kar\s+di(?:ya|ye|a|i)?|ho\s+ga(?:ya|yi|ye)"
    r"|ho\s+gya|hata\s+di(?:ya|ye|a)?|hat\s+gaye|nikal\s+di(?:ya|ye|a)?|mita\s+di(?:ya|ye|a)?"
    r"|saaf)\b",
    _FLAGS,
)
_NEGATION_RE = re.compile(
    r"\b(?:not|never|no|none|nothing|nor|neither|cannot|unable|nahi|nahin|nai|mat)\b|n't\b",
    _FLAGS,
)
_OFFER_RE = re.compile(
    r"\b(?:can|could|able|want\s+me|shall|should\s+i|i'll|i\s+will|we'll|will|gonna"
    r"|going\s+to|let\s+me|happy\s+to|upload\w*|send|drop|share|if\s+you|once\s+you|whenever"
    r"|try|next)\b",
    _FLAGS,
)
_INTERROGATIVE_RE = re.compile(
    r"^\W*(?:did|do|does|is|are|was|were|has|have|had|can|could|should|would|will|shall|what"
    r"|how|why|when|where|which|who|whose|any|anything|ready|kya|kaisa|kaise|kab|kyun)\b",
    _FLAGS,
)
# Someone other than Buddy did it ("you handled it", "he built the dashboard"): chat, not a claim.
_OTHER_SUBJECT = frozenset(
    "you you've youve you're youre u he she he's she's someone everyone tum tumne usne unhone "
    "aapne".split()
)
_ROWS_RE = re.compile(
    r"\b(?:dupli[ck]\w*|dupes?|dedup\w*|empty\s+rows?|blank\s+rows?|rows?|columns?|cells?)\b",
    _FLAGS,
)
_YOUR_FILE_RE = re.compile(
    r"\b(?:your|the|ur|tumhari|tumhara)\s+(?:file|sheet|spreadsheet|workbook|copy|data|excel)\b",
    _FLAGS,
)
_COUNT_OF_ROWS_RE = re.compile(
    r"(?:\d+|\b(?:no|zero|one|two|three|four|five|six|seven|eight|nine|ten|a\s+couple\s+of"
    r"|a\s+few|several|dozens?\s+of))\s+(?:\w+\s+){0,2}(?:rows?|dupli[ck]\w*|dupes?|entries"
    r"|records|lines|cells?|columns?)\b",
    _FLAGS,
)
_FILE_LIKE = re.compile(
    r"[^\s`'\"()\[\]<>«»“”‘’,;:!?]+\.(?:xlsx|xlsm|xltx|xltm|xls|csv|tsv|ods|numbers|pdf|docx?"
    r"|txt|json|xml|zip)\b",
    _FLAGS,
)
_INTERNAL_RE = re.compile(r"\{\s*F\d+[a-z]?\s*\}|\[F\d+\]|\bspreadsheet\.[a-z_]+\b", _FLAGS)
_CONTACT_RE = re.compile(
    r"https?://\S+|\bwww\.\S+|\S+@\S+\.\w+|\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,}\b"
    r"|\+?\d[\d\s().]{7,}\d",
    _FLAGS,
)
_SENTENCE_SPLIT = re.compile(r"([.!?…]+|\n+)")
_SEGMENT_SPLIT = re.compile(
    r"[,;:()\[\]—–]|\s-\s|\b(?:but|though|although|however|while|whereas|so|and|then|plus"
    r"|except|because|since|lekin|par|aur)\b",
    _FLAGS,
)


def _check_chat(messages: list[str], sheet: FactSheet, user_message: str) -> tuple[Problem, ...]:
    text = "\n".join(messages).replace("’", "'").replace("‘", "'")
    problems: list[Problem] = []
    if _INTERNAL_RE.search(text):
        problems.append(Problem(INTERNAL, "This is a chat turn: there are no facts to show."))
    if _FILE_LIKE.search(text):
        problems.append(Problem(FILE_NAME, "Don't name a file on a chat turn."))
    for match in _CONTACT_RE.finditer(text):
        found = match.group(0).strip(".,;:)").casefold()
        if found and found not in user_message.casefold():
            problems.append(
                Problem(UNBACKED, "Never give a link, a web address, an email or a phone number.")
            )
            break
    if sheet.has_file and _foreign_script(text):
        problems.append(
            Problem(SCRIPT, "Write in English or in Hinglish written in English letters.")
        )
    if _COUNT_OF_ROWS_RE.search(text):
        problems.append(Problem(NUMBER, "Nothing was counted on a chat turn: no counts of rows."))
    for sentence, terminator in _sentences(text):
        parts = [p for p in _SEGMENT_SPLIT.split(sentence) if p.strip()]
        for index, part in enumerate(parts):
            question = "?" in terminator and index == len(parts) - 1
            if _claims_done(part, question):
                problems.append(
                    Problem(
                        CLAIM,
                        "On a chat turn nothing was done to any file; a message says or "
                        "suggests something was done.",
                    )
                )
            if (
                not question
                and not _NEGATION_RE.search(part)
                and not _OFFER_RE.search(part)
                and (_ROWS_RE.search(part) or _YOUR_FILE_RE.search(part))
            ):
                problems.append(
                    Problem(
                        CHAT_FILE_TALK,
                        "On a chat turn nothing was done to any file; only offer or ask "
                        "about file work.",
                    )
                )
    return tuple(dict.fromkeys(problems))


def _sentences(text: str) -> list[tuple[str, str]]:
    pieces = _SENTENCE_SPLIT.split(text)
    return [
        (pieces[i], pieces[i + 1] if i + 1 < len(pieces) else "")
        for i in range(0, len(pieces), 2)
        if pieces[i].strip()
    ]


def _claims_done(part: str, question: bool) -> bool:
    """Whether ``part`` says Buddy did or finished something (not asked, not taken back)."""
    if question and _INTERROGATIVE_RE.search(part):
        return False
    for match in _DONE_WORDS.finditer(part):
        before = re.findall(r"[\w']+", part[: match.start()].casefold())
        if any(word in _OTHER_SUBJECT for word in before[-2:]):
            continue  # "you handled it", "he built the dashboard": not Buddy's
        negations = [i for i, word in enumerate(before) if _NEGATION_RE.search(word)]
        if negations and not any(word in ("i", "i've", "we") for word in before[negations[-1] :]):
            continue  # "haven't touched it", "nothing removed"
        return True
    return False


def _foreign_script(text: str) -> bool:
    """Whether ``text`` has letters outside the Latin script, which the checks can't read."""
    return any(
        char.isalpha() and not unicodedata.name(char, "").startswith("LATIN") for char in text
    )

"""Claims — the check every written reply passes before anyone sees it.

The writer (``buddy.writer``) words the reply; this module decides whether that wording may be
shown. It reads the model's messages against the turn's VERIFIED RESULT (``buddy.facts``) and
lists every problem it finds:

- every number matches a number a cited fact backs, counting the same thing (duplicate rows,
  empty rows, rows, formulas) and, when the reply says so, in the same phase (removed or left);
- every file name is one the fact sheet gives, exactly;
- everything described as done (removed, gone, sorted, sent...) is something a verified step
  did this turn; anything no skill does is never done;
- every required fact is said: what wasn't done is stated as not done, the disclosures are
  there in substance, the file is named;
- on a chat turn, nothing is said about work on a file.

This is a backstop that reads English (and a little Hinglish) words. It errs towards refusing:
a refused reply is rewritten once with the reasons, and if that fails too the template reply is
shown (``buddy.writer``). A problem's ``detail`` is built from fixed text, fact ids and numbers
only, never from the reply's words or the file's text, so it is safe to log and to send back.

It never decides what the user asked for: that is Brain's reading, already in the fact sheet.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from buddy.facts import (
    DUPLICATE_WORDS,
    EMPTY_WORDS,
    NOUN_ANY,
    NOUN_DUPLICATE,
    NOUN_EMPTY,
    NOUN_FORMULA,
    NOUN_OTHER,
    NOUN_ROWS,
    OBJECT_DUPLICATE,
    OBJECT_EMPTY,
    OBJECT_REPORT,
    PHASE_LEFT,
    PHASE_NONE,
    PHASE_REMOVED,
    REPORT_WORDS,
    TURN_CHAT,
    TURN_DELIVERED,
    VALUES_ONLY_WORDS as VALUES_ONLY_PATTERN,
    FactSheet,
    Quantity,
    number_word_value,
)

# The most messages a reply may have, and the most characters in one.
MAX_MESSAGES = 3
MAX_MESSAGE_CHARS = 600

# Problem codes (logged; the detail is for the rewrite).
SHAPE = "shape"
UNKNOWN_FACT = "unknown_fact"
NUMBER = "number"
FILE_NAME = "file_name"
CLAIM = "claim"
MISSING = "missing"
CHAT_FILE_TALK = "chat_file_talk"
WRONG_FILE = "wrong_file"
INTERNAL = "internal_label"

_FLAGS = re.IGNORECASE


@dataclass(frozen=True)
class Problem:
    """One reason a reply can't be shown.

    Attributes:
        code: What kind of problem (the constants above). Safe to log.
        detail: The same in plain words, for the rewrite request. Fixed text, fact ids and
            numbers only: never the reply's words or text from the file.
    """

    code: str
    detail: str


# -- Vocabulary ----------------------------------------------------------------------------

# Words that say a thing was done or carried out: an action (needs a verified step) ...
_ACTION_WORDS = (
    r"removed|deleted|dropped|cut|cleared|purged|eliminated|erased|wiped|nuked|zapped|killed"
    r"|scrubbed|stripped|trimmed|took\s+out|taken\s+out|got\s+rid|gotten\s+rid|knocked\s+out"
    r"|deduped|deduplicated|de-duped|fixed|handled|did|finished|completed|processed|made|created"
    r"|built|generated|produced|wrote|written|added|updated|changed|edited|modified|merged"
    r"|formatted|highlighted|colou?red|renamed|translated|converted|exported|emailed|mailed|sent"
    r"|uploaded|shared|saved|printed|filtered|split|sorted|organi[sz]ed|wrapped\s+up"
    r"|took\s+care|taken\s+care|ran"
    # Hinglish: "kar diya", "ho gaya", "hata diye", "nikal diya", "mita diya", "saaf kar diya"
    r"|kar\s+di(?:ya|ye|a|i)?|ho\s+ga(?:ya|yi|ye|ye)|ho\s+gya|hata\s+di(?:ya|ye|a)?"
    r"|nikal\s+di(?:ya|ye|a)?|mita\s+di(?:ya|ye|a)?|saaf"
)
# ... or a state that only a done step (or a measured zero) makes true.
_STATE_WORDS = (
    r"done|gone|clean|cleaned|cleaned\s+up|tidy|tidied|spotless|sparkling|squeaky|ready"
    r"|all\s+set|good\s+to\s+go|complete|history|outta\s+here|⟨absent⟩"
)
_ACTION_RE = re.compile(rf"\b(?:{_ACTION_WORDS})\b", _FLAGS)
_STATE_RE = re.compile(rf"(?:\b(?:{_STATE_WORDS})\b|⟨absent⟩)", _FLAGS)
# Phrases that say something is absent, though they hold a "no": read as a state, not as "not".
_ABSENT_RE = re.compile(r"\bno\s+(?:more|longer)\b|\bfree\s+of\b|-free\b|\bwithout\b", _FLAGS)

_NEGATION_RE = re.compile(
    r"\b(?:not|never|no|none|nothing|nor|neither|cannot|unable|nahi|nahin|nai|mat)\b|n't\b",
    _FLAGS,
)
# A question asked as one ("did you...?", "is it...?"), not a statement with a "?" on the end.
_INTERROGATIVE_RE = re.compile(
    r"^\W*(?:did|do|does|is|are|was|were|has|have|had|can|could|should|would|will|shall|what"
    r"|how|why|when|where|which|who|whose|any|anything|ready|kya|kaisa|kaise|kab|kyun)\b",
    _FLAGS,
)
# A segment that offers or describes what Buddy can do is not a claim that it was done.
_OFFER_RE = re.compile(
    r"\b(?:can|could|able|want\s+me|shall|should\s+i|i'll|ill|i\s+will|we'll|will|gonna"
    r"|going\s+to|let\s+me|happy\s+to|upload\w*|send|drop|share|if\s+you|once\s+you|whenever"
    r"|try|next)\b",
    _FLAGS,
)
_FIRST_PERSON_RE = re.compile(r"\b(?:i|i've|ive|i'd|i'm|im|we|we've|maine|humne|mene)\b", _FLAGS)

_OBJECTS = {
    OBJECT_DUPLICATE: re.compile(DUPLICATE_WORDS, _FLAGS),
    OBJECT_EMPTY: re.compile(EMPTY_WORDS, _FLAGS),
    OBJECT_REPORT: re.compile(REPORT_WORDS, _FLAGS),
}
_NEAR_DUPLICATE_RE = re.compile(r"\bnear[\s-]*(?:duplicat\w*|dupes?|identical|matches?)", _FLAGS)
# Things no skill does, so nothing about them is ever done.
_UNSUPPORTED_RE = re.compile(
    r"\b(?:sort\w*|chart\w*|graph\w*|pdf\w*|e-?mail\w*|mail\w*|format\w*|colou?r\w*"
    r"|highlight\w*|merg\w*|filter\w*|renam\w*|translat\w*|convert\w*|export\w*|pivot\w*"
    r"|split\w*|formula\w*|typo\w*|spelling|capitali[sz]\w*|whitespace|spaces|outlier\w*"
    r"|date\w*|columns?|cells?|totals?|sum|sums|calculat\w*|averag\w*|backup\w*"
    r"|password\w*|protect\w*|lock\w*|header\w*|currenc\w*|decimal\w*|font\w*|width\w*)\b",
    _FLAGS,
)
_GENERIC_FILE_RE = re.compile(
    r"\b(?:file|files|sheet|sheets|spreadsheet\w*|workbook\w*|excel|copy|data|dataset|table"
    r"|rows?|entries|records|everything|⟦file⟧)\b|⟦FILE⟧",
    _FLAGS,
)
_YOUR_FILE_RE = re.compile(
    r"\b(?:your|the|ur|tumhari|tumhara)\s+(?:file|sheet|spreadsheet|workbook|copy|data|excel)\b"
    r"|⟦FILE⟧",
    _FLAGS,
)
# Words that may stand alone around a bare "done": "all done!", "it's done", "that's sorted".
_BARE_FILLER = frozenset(
    "all just now it it's its that that's thats this is are was were everything totally fully "
    "already yep yes ok okay so and fine good great perfect there here we go boom ta da tada "
    "and yup sure".split()
)

_NOUN_TOKENS = (
    (
        NOUN_DUPLICATE,
        re.compile(r"^(?:duplicat[\w-]*|dupes?|dups?|repeat(?:ed|s)?|doubles?)$", _FLAGS),
    ),
    (NOUN_EMPTY, re.compile(r"^(?:fully-)?(?:empty|blank)[\w-]*$", _FLAGS)),
    (NOUN_FORMULA, re.compile(r"^formula\w*$", _FLAGS)),
    (NOUN_ROWS, re.compile(r"^(?:rows?|lines?|entries|entry|records?)$", _FLAGS)),
)
_SKIP_BEFORE_NOUN = frozenset(
    "exact exactly fully completely more other repeated identical extra whole full total "
    "of them the duplicate".split()
)
_LEFT_TOKENS = frozenset(
    "left remaining remain remains still leaving now has have with total bachi bache baaki".split()
)
_REMOVED_TOKENS = frozenset(
    "removed gone deleted dropped cut cleared eliminated killed nuked wiped zapped scrubbed "
    "trimmed deduped rid out hata hataye nikal nikale purged erased".split()
)

_FILE_LIKE = re.compile(
    r"[^\s`'\"()\[\]<>«»“”‘’,;:!?]+\.(?:xlsx|xlsm|xltx|xltm|xls|csv|tsv|ods|numbers|pdf|docx?"
    r"|txt|json|xml|zip)\b",
    _FLAGS,
)
_INTERNAL_RE = re.compile(r"\[F\d+\]|\bspreadsheet\.[a-z_]+\b|VERIFIED RESULT", _FLAGS)
_NUMBER_TOKEN = re.compile(r"^\d{1,3}(?:,\d{3})+$|^\d+(?:·\d+)?%?$")


# -- Reading the reply ---------------------------------------------------------------------


@dataclass
class _Segment:
    text: str
    tokens: list[str]
    negated: bool
    question: bool
    offer: bool
    claim_words: list[str]
    sentence: int


def _prepare(text: str, names: Sequence[str]) -> str:
    """The reply with file names replaced, quotes straightened and decimals protected."""
    text = text.replace("’", "'").replace("‘", "'")
    for name in sorted({n for n in names if n}, key=len, reverse=True):
        text = text.replace(name, " ⟦FILE⟧ ")
    text = re.sub(r"(\d)\.(\d)", r"\1·\2", text)
    text = re.sub(r"\be\.g\.", "eg", text, flags=_FLAGS)
    text = re.sub(r"\bi\.e\.", "ie", text, flags=_FLAGS)
    return text


_SENTENCE_SPLIT = re.compile(r"([.!?…]+|\n+|\s\|\s)")
_SEGMENT_SPLIT = re.compile(
    r"[,;:()\[\]—–]|\s-\s|\b(?:but|though|although|however|while|whereas|so|and|then|plus"
    r"|except|because|since|lekin|par|aur)\b",
    _FLAGS,
)


def _tokens(text: str) -> list[str]:
    return re.findall(r"⟦FILE⟧|⟨absent⟩|[\w'·%-]+", text)


def _segments(prepared: str) -> list[_Segment]:
    segments: list[_Segment] = []
    pieces = _SENTENCE_SPLIT.split(prepared)
    sentence_index = 0
    for i in range(0, len(pieces), 2):
        sentence = pieces[i]
        terminator = pieces[i + 1] if i + 1 < len(pieces) else ""
        if not sentence.strip():
            continue
        sentence = _ABSENT_RE.sub(" ⟨absent⟩ ", sentence)
        parts = [p for p in _SEGMENT_SPLIT.split(sentence) if p and p.strip()]
        for index, part in enumerate(parts):
            tokens = _tokens(part)
            if not tokens:
                continue
            segments.append(
                _Segment(
                    text=part,
                    tokens=tokens,
                    negated=bool(_NEGATION_RE.search(part)),
                    question="?" in terminator
                    and index == len(parts) - 1
                    and bool(_INTERROGATIVE_RE.search(part) or not _claim_words(part)),
                    offer=bool(_OFFER_RE.search(part)),
                    claim_words=_claim_words(part),
                    sentence=sentence_index,
                )
            )
        sentence_index += 1
    return segments


def _claim_words(part: str) -> list[str]:
    """The done-words in ``part`` that no "not" before them takes back.

    A "not" earlier in the segment takes a done-word back ("no rows were removed", "haven't
    removed"), unless "I" or "we" comes between them ("no problem I removed them").
    """
    found = []
    for regex in (_ACTION_RE, _STATE_RE):
        for match in regex.finditer(part):
            before = _tokens(part[: match.start()])
            negations = [i for i, word in enumerate(before) if _NEGATION_RE.search(word)]
            if negations and not any(
                _FIRST_PERSON_RE.fullmatch(word) for word in before[negations[-1] + 1 :]
            ):
                continue
            found.append(match.group(0).casefold())
    return found


def _is_action(word: str) -> bool:
    return bool(_ACTION_RE.fullmatch(word))


def _claimed(segments: list[_Segment]) -> list[tuple[_Segment, list[str]]]:
    """The segments that claim something done, with the done-words that make them claims.

    A segment with a done-word claims it unless it says "not", asks, or offers. A segment with
    no done-word of its own ("... and the empty rows") shares the nearest one in its sentence,
    unless a "not" segment lies between them.
    """
    claimed = []
    for index, segment in enumerate(segments):
        if segment.question:
            continue
        if segment.claim_words:
            claimed.append((segment, segment.claim_words))
            continue
        if segment.negated or segment.offer:
            continue
        best: tuple[int, _Segment] | None = None
        for other_index, other in enumerate(segments):
            if other.sentence != segment.sentence or not other.claim_words or other.negated:
                continue
            low, high = sorted((index, other_index))
            if any(segments[k].negated for k in range(low + 1, high)):
                continue
            distance = abs(other_index - index)
            if best is None or distance < best[0]:
                best = (distance, other)
        if best is not None:
            claimed.append((segment, best[1].claim_words))
    return claimed


@dataclass(frozen=True)
class _Number:
    value: float
    noun: str
    phase: str


def _numbers(segments: list[_Segment]) -> list[_Number]:
    """Every number in the reply that counts something, with what it counts and its phase."""
    found = []
    for segment in segments:
        tokens = segment.tokens
        for index, token in enumerate(tokens):
            value = _token_value(tokens, index)
            if value is None:
                continue
            noun, noun_end = _noun_after(tokens, index)
            if token.casefold() in ("no", "zero") and noun == NOUN_OTHER:
                continue
            if _is_word(token) and noun == NOUN_OTHER and not _phase_word(tokens, index + 1):
                continue
            found.append(_Number(value, noun, _phase(tokens, index, noun_end)))
    return found


def _is_word(token: str) -> bool:
    return not token[:1].isdigit()


def _token_value(tokens: list[str], index: int) -> float | None:
    token = tokens[index]
    if _NUMBER_TOKEN.match(token):
        return float(token.rstrip("%").replace(",", "").replace("·", "."))
    lowered = token.casefold()
    if lowered == "no":
        return 0.0
    word = number_word_value(lowered)
    return float(word) if word is not None else None


def _noun_after(tokens: list[str], index: int) -> tuple[str, int]:
    """The noun a number counts: the first noun word in the four after it."""
    for offset in range(1, 5):
        position = index + offset
        if position >= len(tokens):
            break
        token = tokens[position]
        if _token_value(tokens, position) is not None and _is_word(token) is False:
            break
        if _NEAR_DUPLICATE_RE.match(" ".join(tokens[position - 1 : position + 1])):
            return NOUN_OTHER, position
        for noun, regex in _NOUN_TOKENS:
            if regex.match(token):
                end = position
                while end + 1 < len(tokens) and any(
                    r.match(tokens[end + 1]) for _, r in _NOUN_TOKENS
                ):
                    end += 1
                return noun, end
        if token.casefold() not in _SKIP_BEFORE_NOUN:
            break
    return NOUN_OTHER, index


def _phase_word(tokens: list[str], position: int) -> str:
    if position >= len(tokens):
        return PHASE_NONE
    word = tokens[position].casefold()
    if word in _LEFT_TOKENS:
        return PHASE_LEFT
    if word in _REMOVED_TOKENS or _ACTION_RE.fullmatch(word):
        return PHASE_REMOVED
    return PHASE_NONE


_SKIP_BEFORE_NUMBER = frozenset("of the all just only exactly about another".split())


def _phase(tokens: list[str], index: int, noun_end: int) -> str:
    """Removed or left: from the word right before the number ("removed 2", "still has 2",
    "got rid of 2"), else from the word right after its noun ("2 duplicates gone", "9 left")."""
    for position in range(index - 1, max(index - 4, -1), -1):
        phase = _phase_word(tokens, position)
        if phase:
            return phase
        if tokens[position].casefold() not in _SKIP_BEFORE_NUMBER:
            break
    return _phase_word(tokens, noun_end + 1)


def _matches(number: _Number, quantity: Quantity) -> bool:
    if number.value != quantity.value:
        return False
    if number.noun != NOUN_OTHER and quantity.noun not in (number.noun, NOUN_ANY):
        return False
    if number.phase and quantity.phase != number.phase:
        return False
    return True


# -- The check -----------------------------------------------------------------------------


def check_reply(
    messages: Sequence[str],
    facts_used: Sequence[str],
    sheet: FactSheet,
    *,
    user_message: str = "",
) -> tuple[Problem, ...]:
    """Every problem with showing ``messages`` for ``sheet``'s turn; ``()`` when it may be shown.

    Args:
        messages: The writer's messages.
        facts_used: The fact ids the writer says it used.
        sheet: The turn's VERIFIED RESULT.
        user_message: The user's own message: a number they wrote ("5 mins") may be echoed.

    Returns:
        The problems, in the order found.
    """
    problems: list[Problem] = []
    if not 1 <= len(messages) <= MAX_MESSAGES or any(
        not isinstance(m, str) or not m.strip() or len(m) > MAX_MESSAGE_CHARS for m in messages
    ):
        return (
            Problem(
                SHAPE,
                f"Write 1 to {MAX_MESSAGES} non-empty messages, each under "
                f"{MAX_MESSAGE_CHARS} characters.",
            ),
        )
    known = sheet.by_id()
    unknown = [f for f in facts_used if f not in known]
    if unknown:
        problems.append(Problem(UNKNOWN_FACT, "facts_used lists ids that aren't in the result."))
    cited = [known[f] for f in facts_used if f in known]
    text = "\n".join(messages)
    if _INTERNAL_RE.search(text):
        problems.append(Problem(INTERNAL, "The messages show a fact id or an internal label."))
    prepared = _prepare(text, sheet.names)
    if _FILE_LIKE.search(prepared):
        problems.append(
            Problem(FILE_NAME, "A file name isn't one the result gives, exactly as written.")
        )
        # Read on as if it were a file: its own words ("report.xlsx") say nothing.
        prepared = _FILE_LIKE.sub(" ⟦FILE⟧ ", prepared)
    segments = _segments(prepared)
    problems += _check_numbers(segments, cited, sheet, user_message)
    problems += _check_claims(segments, sheet)
    if sheet.turn == TURN_CHAT:
        problems += _check_chat(segments)
    problems += _check_needs(text, prepared, segments, sheet)
    return tuple(problems)


def _check_numbers(
    segments: list[_Segment], cited: list, sheet: FactSheet, user_message: str
) -> list[Problem]:
    problems = []
    quantities = [q for fact in cited for q in fact.quantities]
    users = {float(n.replace(",", "")) for n in re.findall(r"\d+(?:\.\d+)?", user_message)}
    for number in _numbers(segments):
        if any(_matches(number, quantity) for quantity in quantities):
            continue
        if number.noun == NOUN_OTHER and not number.phase and number.value in users:
            continue
        if sheet.turn == TURN_CHAT and number.noun == NOUN_OTHER and not number.phase:
            continue
        what = number.noun if number.noun != NOUN_OTHER else "something"
        phase = f" {number.phase}" if number.phase else ""
        problems.append(
            Problem(
                NUMBER,
                f"The number {number.value:g} ({what}{phase}) doesn't match a number in the facts "
                "you cited for that.",
            )
        )
    return problems


def _objects(segment: _Segment) -> set[str]:
    text = segment.text
    found = set()
    if _NEAR_DUPLICATE_RE.search(text):
        found.add("unsupported")
        text = _NEAR_DUPLICATE_RE.sub(" ", text)
    for name, regex in _OBJECTS.items():
        if regex.search(text):
            found.add(name)
    if _UNSUPPORTED_RE.search(text):
        found.add("unsupported")
    return found


def _is_bare(segment: _Segment) -> bool:
    words = [
        t.casefold()
        for t in segment.tokens
        if not _ACTION_RE.fullmatch(t) and not _STATE_RE.fullmatch(t)
    ]
    return all(word in _BARE_FILLER for word in words)


def _check_claims(segments: list[_Segment], sheet: FactSheet) -> list[Problem]:
    """Everything described as done must be a verified thing done this turn."""
    problems: list[Problem] = []
    for segment, words in _claimed(segments):
        objects = _objects(segment)
        actions = any(_is_action(word) for word in words)
        if "unsupported" in objects:
            problems.append(
                Problem(CLAIM, "A message says something was done that no step did this turn.")
            )
            continue
        specific = objects & {OBJECT_DUPLICATE, OBJECT_EMPTY, OBJECT_REPORT}
        for item in sorted(specific):
            allowed = sheet.done if actions else sheet.done | sheet.clear
            if item not in allowed:
                problems.append(
                    Problem(
                        CLAIM,
                        f"A message says the {item} part was done, but it wasn't done this turn.",
                    )
                )
        if specific:
            continue
        generic = bool(_GENERIC_FILE_RE.search(segment.text))
        first_person = bool(_FIRST_PERSON_RE.search(segment.text))
        bare = _is_bare(segment)
        if sheet.turn == TURN_CHAT:
            if generic or first_person or bare:
                problems.append(
                    Problem(
                        CHAT_FILE_TALK,
                        "On a chat turn nothing was done to any file; a "
                        "message says or suggests something was done.",
                    )
                )
            continue
        if sheet.complete:
            continue
        if sheet.turn == TURN_DELIVERED and generic and not actions:
            continue  # "your copy is ready": a copy was handed back
        if generic or first_person or bare or not sheet.file_work_ran:
            problems.append(
                Problem(
                    CLAIM,
                    "A message says it's done without saying what, but not everything "
                    "asked for was done; name exactly what was done.",
                )
            )
    problems += _check_file_used(segments, sheet)
    problems += _check_kept(segments)
    return _unique(problems)


# What a values-only copy doesn't keep, and words that say it was kept.
_NOT_KEPT_RE = re.compile(
    r"\b(?:format\w*|formulas?|column widths?|widths?|styles?|colou?rs?|fonts?|layout|borders?"
    r"|sheet settings|charts?|macros?)\b",
    _FLAGS,
)
_KEPT_RE = re.compile(
    r"\b(?:kept|keeps?|keeping|preserved?|preserves|intact|carried|carries|carry|still\s+there"
    r"|stays?|stayed|same|untouched|unchanged|retained|retains?|survived?)\b",
    _FLAGS,
)


def _check_kept(segments: list[_Segment]) -> list[Problem]:
    """The copy keeps values only: nothing may say its formatting or formulas were kept."""
    for segment in segments:
        if segment.negated or segment.question:
            continue
        text = segment.text
        if _NOT_KEPT_RE.search(text) and _KEPT_RE.search(text):
            if re.search(VALUES_ONLY_PATTERN, text, _FLAGS) and not re.search(
                r"\b(?:format\w*|formulas?|widths?|styles?)\b[^.]*\b(?:kept|preserved?|intact"
                r"|carried|still\s+there|retained)\b",
                text,
                _FLAGS,
            ):
                continue  # "the copy keeps values only, not the formatting"
            return [
                Problem(
                    CLAIM,
                    "A message says formatting or formulas were kept; the copy keeps values only.",
                )
            ]
    return []


def _check_file_used(segments: list[_Segment], sheet: FactSheet) -> list[Problem]:
    if sheet.worked_on_copy is None:
        return []
    wrong = (
        re.compile(
            r"\b(?:worked|ran|did\s+(?:it|this|that)|started)\s+(?:on|from)\s+(?:your|the)"
            r"\s+original\b",
            _FLAGS,
        )
        if sheet.worked_on_copy
        else re.compile(r"\b(?:previous|earlier|last|latest|old|other)\s+copy\b", _FLAGS)
    )
    for segment in segments:
        if not segment.negated and wrong.search(segment.text):
            return [Problem(WRONG_FILE, "A message names the wrong file as the one worked on.")]
    return []


def _check_chat(segments: list[_Segment]) -> list[Problem]:
    """On a chat turn, a file's rows may only come up in a question, an offer or a "not"."""
    for segment in segments:
        if segment.negated or segment.question or segment.offer:
            continue
        objects = _objects(segment) & {OBJECT_DUPLICATE, OBJECT_EMPTY}
        if objects or _YOUR_FILE_RE.search(segment.text):
            return [
                Problem(
                    CHAT_FILE_TALK,
                    "On a chat turn nothing was done to any file; only offer "
                    "or ask about file work.",
                )
            ]
    return []


def _check_needs(
    text: str, prepared: str, segments: list[_Segment], sheet: FactSheet
) -> list[Problem]:
    problems = []
    for fact in sheet.facts:
        if not fact.required:
            continue
        for need in fact.needs:
            if not _need_met(need, text, prepared, segments):
                problems.append(
                    Problem(MISSING, f"{fact.id} ({fact.kind}) must be said: {need.label}.")
                )
    return problems


def _need_met(need, text: str, prepared: str, segments: list[_Segment]) -> bool:
    flags = 0 if need.case_sensitive else _FLAGS
    for pattern, negated in need.alternatives:
        regex = re.compile(pattern, flags)
        if negated:
            # Said as not done: in a sentence that says "not" ("I can't do this part yet: sort
            # by amount").
            negated_sentences = {s.sentence for s in segments if s.negated}
            if any(s.sentence in negated_sentences and regex.search(s.text) for s in segments):
                return True
        elif regex.search(text if need.case_sensitive else prepared.replace("·", ".")):
            return True
    return False


def _unique(problems: list[Problem]) -> list[Problem]:
    return list(dict.fromkeys(problems))

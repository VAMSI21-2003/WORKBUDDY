"""Claims — the check every written reply passes before anyone sees it.

The writer (``buddy.writer``) words the reply; this module decides whether that wording may be
shown. It reads the model's messages against the turn's VERIFIED RESULT (``buddy.facts``) and
lists every problem it finds:

- every count matches a number a cited fact backs, counting the same thing (duplicate rows,
  empty rows, rows, formulas) and in the same phase (removed, or left in the file); counts are
  written in digits;
- every file name is one the fact sheet gives, exactly;
- a clause that says something was done (removed, gone, sorted, sent, any "-ed" verb...) may
  hold nothing but what the facts say was done: its words must come from the facts' own
  vocabulary, and anything no skill does is never done;
- a part of the request that wasn't done is stated as not done, firmly ("I can't sort"), and
  never mentioned otherwise;
- the required facts are said: the disclosures in substance, the file by its name;
- no link, address or phone number the user didn't write, and no run of words from the file
  outside quotes;
- on a chat turn, nothing is said about work on a file.

This is a backstop that reads English (and a little Hinglish). It errs towards refusing: a
refused reply is rewritten once with the reasons, and if that fails too the template reply is
shown (``buddy.writer``). On a file turn, a message in another script is refused outright: the
checker can't read it. A problem's ``detail`` is fixed text, fact ids and numbers only, never the
reply's words or the file's text, so it is safe to log and to send back.

It never decides what the user asked for: that is Brain's reading, already in the fact sheet.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

from buddy.facts import (
    ASKED_TOKEN,
    DUPLICATE_WORDS,
    EMPTY_WORDS,
    NOUN_DUPLICATE,
    NOUN_EMPTY,
    NOUN_FORMULA,
    NOUN_ISSUES,
    NOUN_OTHER,
    NOUN_ROWS,
    OBJECT_DUPLICATE,
    OBJECT_EMPTY,
    OBJECT_REPORT,
    PHASE_LEFT,
    PHASE_NONE,
    PHASE_REMOVED,
    REPORT_WORDS,
    SAID_UNABLE,
    TURN_CHAT,
    TURN_DELIVERED,
    FactSheet,
    Need,
    Quantity,
    number_word_value,
)
from buddy.facts import (
    VALUES_ONLY_WORDS as VALUES_ONLY_PATTERN,
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
NOT_DONE_MENTION = "not_done_mention"
MISSING = "missing"
CHAT_FILE_TALK = "chat_file_talk"
WRONG_FILE = "wrong_file"
INTERNAL = "internal_label"
UNBACKED = "unbacked"
SCRIPT = "script"

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
    r"|shared|saved|printed|filtered|split|sorted|organi[sz]ed|wrapped\s+up"
    r"|took\s+care|taken\s+care|ran|put|set|moved|placed|arranged|ordered|ranked|grouped"
    r"|backed\s+up|filled|replaced|corrected|forwarded|posted|attached|combined|joined"
    r"|appended|inserted|hid|hidden|locked|protected|encrypted|calculated|summed|totalled"
    r"|totaled|normali[sz]ed|standardi[sz]ed|capitali[sz]ed|swapped|flipped|shifted|stored"
    r"|archived|restored|recovered|repaired|resolved|patched|tweaked|adjusted|polished|cleaned"
    r"|tidied|sorted\s+out|fixed\s+up"
    # Hinglish: "kar diya", "ho gaya", "hata diye", "nikal diya", "mita diya", "saaf kar diya"
    r"|kar\s+di(?:ya|ye|a|i)?|ho\s+ga(?:ya|yi|ye)|ho\s+gya|hata\s+di(?:ya|ye|a)?"
    r"|nikal\s+di(?:ya|ye|a)?|mita\s+di(?:ya|ye|a)?|bhej\s+di(?:ya|ye|a)?|saaf"
)
# The deeds a skill can do: remove rows, make or save the copy, run the check or the report.
# Any other deed ("sorted", "emailed", "backed up", any other "-ed" verb) is never done.
_SKILL_DEEDS_RE = re.compile(
    r"^(?:removed|deleted|dropped|cut|cleared|purged|eliminated|erased|wiped|nuked|zapped"
    r"|killed|scrubbed|stripped|took\s+out|taken\s+out|got\s+rid|gotten\s+rid|knocked\s+out"
    r"|deduped|deduplicated|de-duped|cleaned|tidied|handled|did|finished|completed|processed"
    r"|made|created|generated|produced|wrote|written|ran|saved|wrapped\s+up|took\s+care"
    r"|taken\s+care|kar\s+di(?:ya|ye|a|i)?|ho\s+ga(?:ya|yi|ye)|ho\s+gya|hata\s+di(?:ya|ye|a)?"
    r"|nikal\s+di(?:ya|ye|a)?|mita\s+di(?:ya|ye|a)?|saaf)$",
    _FLAGS,
)
# Words that say the whole file is in good shape: only an all-clear check backs them.
_ALL_CLEAR_WORDS_RE = re.compile(
    r"^(?:clean|cleaned|spotless|sparkling|squeaky|tidy|tidied|perfect|flawless|pristine|neat"
    r"|saaf|cleaned\s+up)$",
    _FLAGS,
)
# ... or a state that only a done step (or a measured zero) makes true.
_STATE_WORDS = (
    r"done|gone|clean|cleaned\s+up|tidy|spotless|sparkling|squeaky|ready|all\s+set"
    r"|good\s+to\s+go|complete|history|outta\s+here|sorted|taken\s+care\s+of|perfect|flawless"
    r"|pristine|neat"
)
_ACTION_RE = re.compile(rf"\b(?:{_ACTION_WORDS})\b", _FLAGS)
_STATE_RE = re.compile(rf"(?:\b(?:{_STATE_WORDS})\b|⟨absent⟩)", _FLAGS)
# Any other past-tense verb is read as a claim too, on a file turn: what it says was done must
# then be in the facts. These "-ed" words say what was seen or felt, not what was done.
_PAST_TENSE_RE = re.compile(r"\b[a-z]{2,}ed\b", _FLAGS)
_NOT_DEEDS = frozenset(
    "noticed spotted checked looked scanned reviewed asked wanted needed used based worked "
    "seemed called named titled tired excited stressed worried annoyed frustrated interested "
    "supposed related limited listed expected measured counted compared detected flagged "
    "repeated linked judged treated matched kept carried showed seed need speed feed indeed "
    "red bed shed wed bleed breed proceed succeed exceed weed embed embedded "
    "uploaded attached named labelled labeled mixed mentioned typed said handed".split()
)
# Phrases that say something is absent, though they hold a "no": read as a state, not as "not".
_ABSENT_RE = re.compile(r"\bno\s+(?:more|longer)\b|\bfree\s+of\b|-free\b|\bwithout\b", _FLAGS)

_NEGATION_RE = re.compile(
    r"\b(?:not|never|no|none|nothing|nor|neither|cannot|unable|nahi|nahin|nai|mat)\b|n't\b",
    _FLAGS,
)
# A firm "not": what says an action wasn't done. A bare "no" ("no boss drama") isn't one.
_FIRM_NOT_RE = re.compile(r"^(?:not|never|cannot|unable|nahi|nahin|nai)$|n't$", _FLAGS)
# A "not" these verbs turn round: "didn't forget to email", "never failed to sort".
_TURNED_ROUND_RE = re.compile(
    r"\b(?:forg[eo]t\w*|fail\w*|miss\w*|neglect\w*|skip\w*|omit\w*|stop\w*|hesitat\w*)\b", _FLAGS
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
# Someone other than Buddy did it ("you handled it", "he built the dashboard"): chat, not a claim.
_OTHER_SUBJECT = frozenset(
    "you you've youve you're youre u he she he's she's someone everyone "
    "tum tumne usne unhone aapne".split()
)

_OBJECTS = {
    OBJECT_DUPLICATE: re.compile(DUPLICATE_WORDS, _FLAGS),
    OBJECT_EMPTY: re.compile(EMPTY_WORDS, _FLAGS),
    OBJECT_REPORT: re.compile(REPORT_WORDS, _FLAGS),
}
_NEAR_DUPLICATE_RE = re.compile(r"\bnear[\s-]*(?:dupli[ck]\w*|dupes?|identical|matches?)", _FLAGS)
# Things no skill does, so nothing about them is ever done.
_UNSUPPORTED_RE = re.compile(
    r"\b(?:sort\w*|chart\w*|graph\w*|pdf\w*|e-?mail\w*|mail\w*|format\w*|colou?r\w*"
    r"|highlight\w*|merg\w*|filter\w*|renam\w*|translat\w*|convert\w*|export\w*|pivot\w*"
    r"|split\w*|formula\w*|typo\w*|spelling|capitali[sz]\w*|whitespace|spaces|outlier\w*"
    r"|date\w*|columns?|cells?|totals?|sum|sums|calculat\w*|averag\w*|backup\w*"
    r"|password\w*|protect\w*|lock\w*|header\w*|currenc\w*|decimal\w*|font\w*|width\w*"
    r"|missing|gaps?|alphabetical\w*|order\w*|boss\w*|inbox\w*)\b",
    _FLAGS,
)
_GENERIC_FILE_RE = re.compile(
    r"\b(?:file|files|sheet|sheets|spreadsheet\w*|workbook\w*|excel|copy|data|dataset|table"
    r"|rows?|entries|records|everything)\b|⟦FILE⟧",
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

# The only words a clause that says something was done may hold besides the done-words, the
# counts, the file's name and the words for what was done: grammar, chat filler and the
# generic words for the file. Anything else is something the facts don't say was done.
_CLAIM_VOCABULARY = frozenset(
    """
    a an the and or but so then also too just only all both each every any some it its it's
    this that that's these those there here i i've i'm im ive me my we we've our us you your
    you're u ur he she they them their is are was were be been being am has have had do does
    got get in on at to from of for with by into out up down off over now already still yet
    again as than like what which who how when very really totally fully completely quite
    pretty super lol haha yay ok okay yep yes yeah nice great good cool sweet awesome boom right
    sure btw fyi heads hey hi oh ah wow today quick quickly fast real way thing things bit little
    lot lots same new original upload uploaded version clean exact exactly repeated identical
    extra whole full total left remaining remain remains leaving gone rid copy file files sheet
    sheets spreadsheet spreadsheets workbook excel data dataset table row rows entries entry
    records record line lines everything values value only keeps keep as-is ready set go
    yaar bhi sab ho hai hain ki ka ke ne se mein me ab abhi toh to na
    """.split()
)

_NOUN_TOKENS = (
    (
        NOUN_DUPLICATE,
        re.compile(r"^(?:dupli[ck][\w-]*|dupes?|dups?|repeat(?:ed|s)?|doubles?)$", _FLAGS),
    ),
    (NOUN_EMPTY, re.compile(r"^(?:fully-)?(?:empty|blank)[\w-]*$", _FLAGS)),
    (NOUN_FORMULA, re.compile(r"^formula\w*$", _FLAGS)),
    (NOUN_ROWS, re.compile(r"^(?:rows?|lines?|entries|entry|records?)$", _FLAGS)),
    (NOUN_ISSUES, re.compile(r"^(?:problems?|issues?|errors?|mistakes?|glitch(?:es)?)$", _FLAGS)),
)
_COUNTED_NOUNS = (NOUN_DUPLICATE, NOUN_EMPTY, NOUN_FORMULA, NOUN_ROWS)
# A clause with one of these says what the file holds.
_HOLDS = frozenset("is are was were has have had there still left remain remains remaining".split())
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
# Counts that can't be read exactly: they must be written in digits.
_VAGUE_COUNT_RE = re.compile(
    r"^(?:(?:twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)(?:-\w+)?|hundreds?"
    r"|thousands?|dozens?|couple|few|several|handful|many|lots|bunch|tons?|loads?"
    r"|\w+-(?:one|two|three|four|five|six|seven|eight|nine))$",
    _FLAGS,
)

_FILE_LIKE = re.compile(
    r"[^\s`'\"()\[\]<>«»“”‘’,;:!?]+\.(?:xlsx|xlsm|xltx|xltm|xls|csv|tsv|ods|numbers|pdf|docx?"
    r"|txt|json|xml|zip)\b",
    _FLAGS,
)
_INTERNAL_RE = re.compile(r"\[F\d+\]|\bspreadsheet\.[a-z_]+\b|VERIFIED RESULT", _FLAGS)
_NUMBER_TOKEN = re.compile(r"^\d{1,3}(?:,\d{3})+$|^\d+(?:·\d+)?%?$")
# A link, a domain, an email address or a phone number.
_CONTACT_RE = re.compile(
    r"https?://\S+|\bwww\.\S+|\S+@\S+\.\w+|\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,}\b"
    r"|\+?\d[\d\s().-]{7,}\d",
    _FLAGS,
)
# The parts of a finding that came from the file: the analyzer quotes them (``shown``).
_FROM_FILE_RE = re.compile(r'"([^"]*)"')
_RUN = 4  # this many words of the file's text in a row, outside quotes, is the file talking


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


_QUOTED_RE = re.compile(r"[\"“«]([^\"“”«»\n]{1,200})[\"”»]")
# "what I did" refers to the work stated elsewhere; it claims nothing by itself.
_WHAT_WAS_DONE_RE = re.compile(
    r"\bwhat\s+(?:i|we)\s+(?:did|have\s+done|'ve\s+done|just\s+did)\b|\bwhat\s+was\s+done\b",
    _FLAGS,
)


def _bare_text(text: str) -> str:
    return " ".join(text.casefold().strip(" .!?,;:'\"“”‘’").split())


def _prepare(
    text: str,
    names: Sequence[str],
    file_texts: Sequence[str] = (),
    asked: Sequence[str] = (),
    *,
    delivered: bool = False,
) -> str:
    """The reply with file names replaced, quotes straightened and decimals protected.

    Text quoted from the user's file (a column name in quotes, as a fact's file text has it) is
    data the reply shows, not something it says: it is set aside, so a column named "Done!" is
    not read as a claim. So is the user's own wording of something not done, quoted
    (``ASKED_TOKEN``). Unquoted, the same words are read as the reply's own.
    """
    wanted = {_bare_text(phrase) for phrase in asked if phrase}

    def unquote(match: re.Match[str]) -> str:
        inner = match.group(1).rstrip("…").strip()
        if inner and _bare_text(inner) in wanted:
            return f" {ASKED_TOKEN} "
        if inner and any(inner in source for source in file_texts):
            return " ⟦QUOTE⟧ "
        return match.group(0)

    text = _QUOTED_RE.sub(unquote, text)
    text = text.replace("’", "'").replace("‘", "'")
    text = _WHAT_WAS_DONE_RE.sub(" ⟦WHATDONE⟧ ", text)
    for name in sorted({n for n in names if n}, key=len, reverse=True):
        text = text.replace(name, " ⟦FILE⟧ ")
    text = re.sub(r"(\d)\.(\d)", r"\1·\2", text)
    # What a file has, or a value's kind, not something done to it.
    text = re.sub(r"\bmerged(?=\s+cells?\b)", "merge-type", text, flags=_FLAGS)
    # "your cleaned copy" names the copy handed back; it says nothing about how clean the file
    # is. On a turn that handed nothing back it stays a claim.
    if delivered:
        text = re.sub(
            r"\bcleaned(?:[\s-]+up)?\s+(?=(?:copy|version|file|sheet|spreadsheet)\b)",
            "",
            text,
            flags=_FLAGS,
        )
    text = re.sub(r"\b(?:last\s+)?saved\s+values?\b", "stored-values", text, flags=_FLAGS)
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
    return re.findall(r"⟦\w+⟧|⟨absent⟩|[\w'·%-]+", text)


def _segments(prepared: str, *, past_tense: bool) -> list[_Segment]:
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
            words = _claim_words(part, past_tense=past_tense)
            segments.append(
                _Segment(
                    text=part,
                    tokens=tokens,
                    negated=bool(_NEGATION_RE.search(part)),
                    question="?" in terminator
                    and index == len(parts) - 1
                    and bool(_INTERROGATIVE_RE.search(part) or not words),
                    offer=bool(_OFFER_RE.search(part)),
                    claim_words=words,
                    sentence=sentence_index,
                )
            )
        sentence_index += 1
    return segments


def _claim_words(part: str, *, past_tense: bool) -> list[str]:
    """The done-words in ``part`` that no "not" before them takes back.

    A "not" earlier in the segment takes a done-word back ("no rows were removed", "haven't
    removed"), unless "I" or "we" comes between them ("no problem I removed them"). With
    ``past_tense``, any other "-ed" verb counts as a done-word too.
    """
    matches = [m for regex in (_ACTION_RE, _STATE_RE) for m in regex.finditer(part)]
    if past_tense:
        matches += [
            m
            for m in _PAST_TENSE_RE.finditer(part)
            if m.group(0).casefold() not in _NOT_DEEDS
            and not any(other.start() <= m.start() < other.end() for other in matches)
        ]
    found = []
    for match in sorted(matches, key=lambda m: m.start()):
        before = _tokens(part[: match.start()])
        if any(word.casefold() in _OTHER_SUBJECT for word in before[-2:]):
            continue  # "you got this", "you handled it", "he built the dashboard": not Buddy's
        negations = [i for i, word in enumerate(before) if _NEGATION_RE.search(word)]
        if negations and not any(
            _FIRST_PERSON_RE.fullmatch(word) for word in before[negations[-1] + 1 :]
        ):
            continue
        found.append(match.group(0).casefold())
    return found


def _is_action(word: str) -> bool:
    return bool(_ACTION_RE.fullmatch(word)) or (
        bool(_PAST_TENSE_RE.fullmatch(word)) and not _STATE_RE.fullmatch(word)
    )


def _claimed(segments: list[_Segment]) -> list[tuple[_Segment, list[str]]]:
    """The segments that claim something done, with the done-words that make them claims.

    A segment with a done-word of its own claims it unless it asks. A segment with none ("...
    and the empty rows") shares the nearest one in its sentence, unless it says "not" or offers,
    or a "not" segment lies between them.
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
    removal_said: bool  # the sentence says something was removed
    vague: bool = False  # "a couple", "twenty-one": can't be checked


def _numbers(segments: list[_Segment]) -> list[_Number]:
    """Every count in the reply, with what it counts and its phase."""
    removal_by_sentence: dict[int, bool] = {}
    for segment in segments:
        removal = any(
            _is_action(word) or word in _REMOVED_TOKENS or word == "gone"
            for word in segment.claim_words
        ) or any(t.casefold() in _REMOVED_TOKENS for t in segment.tokens)
        removal_by_sentence[segment.sentence] = (
            removal_by_sentence.get(segment.sentence, False) or removal
        )
    found = []
    for segment in segments:
        tokens = segment.tokens
        # A clause about what the file holds ("there are 2 duplicate rows in it") counts what's
        # left, whatever else its sentence removed.
        holds = any(t.casefold() in _HOLDS for t in tokens)
        own = any(
            _is_action(word) or word in _REMOVED_TOKENS or word == "gone"
            for word in segment.claim_words
        ) or any(t.casefold() in _REMOVED_TOKENS for t in tokens)
        removal = own or (removal_by_sentence[segment.sentence] and not holds)
        for index, token in enumerate(tokens):
            if _VAGUE_COUNT_RE.match(token):
                noun, _ = _noun_after(tokens, index)
                if noun in _COUNTED_NOUNS:
                    found.append(_Number(-1, noun, PHASE_NONE, removal, vague=True))
                continue
            value = _token_value(tokens, index)
            if value is None:
                continue
            noun, noun_end = _noun_after(tokens, index)
            if _is_zero_word(tokens, index) and noun not in (
                NOUN_DUPLICATE,
                NOUN_EMPTY,
                NOUN_ISSUES,
            ):
                continue
            if _is_word(token) and noun == NOUN_OTHER and not _phase_word(tokens, index + 1):
                continue
            phase = _phase(tokens, index, noun_end)
            if _is_zero_word(tokens, index) and not phase:
                phase = PHASE_LEFT  # "no duplicates", "aren't any empty rows": none in the file
            found.append(_Number(value, noun, phase, removal))
    return found


def _is_word(token: str) -> bool:
    return not token[:1].isdigit()


def _is_zero_word(tokens: list[str], index: int) -> bool:
    lowered = tokens[index].casefold()
    if lowered in ("no", "zero"):
        return True
    return lowered == "any" and index > 0 and bool(_NEGATION_RE.search(tokens[index - 1]))


def _token_value(tokens: list[str], index: int) -> float | None:
    token = tokens[index]
    if _NUMBER_TOKEN.match(token):
        return float(token.rstrip("%").replace(",", "").replace("·", "."))
    if _is_zero_word(tokens, index):
        return 0.0
    word = number_word_value(token.casefold())
    return float(word) if word is not None else None


def _noun_after(tokens: list[str], index: int) -> tuple[str, int]:
    """The noun a number counts: the first noun word in the four after it."""
    for offset in range(1, 5):
        position = index + offset
        if position >= len(tokens):
            break
        token = tokens[position]
        if _NUMBER_TOKEN.match(token):
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
    """Whether ``quantity`` backs ``number``.

    The value must be equal. A number with no counted noun may use any quantity; one that counts
    rows (of any kind) needs a quantity of that same kind, never a finding's number with no
    counted noun. The phase must agree: a count said with no phase is what's left in the file,
    or what was removed when its sentence says something was removed.
    """
    if number.vague or number.value != quantity.value:
        return False
    if number.noun == NOUN_OTHER:
        return number.phase == PHASE_NONE or quantity.phase in (number.phase, PHASE_NONE)
    if quantity.noun != number.noun:
        return False
    if number.noun == NOUN_FORMULA or quantity.phase == PHASE_NONE:
        return number.phase in (PHASE_NONE, PHASE_LEFT) or quantity.phase == number.phase
    if number.phase:
        return quantity.phase == number.phase
    return quantity.phase == PHASE_LEFT or (quantity.phase == PHASE_REMOVED and number.removal_said)


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
        user_message: The user's own message: a number or a link they wrote may be echoed.

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
    if _foreign_script(text) and (sheet.turn != TURN_CHAT or sheet.has_file):
        problems.append(
            Problem(SCRIPT, "Write in English or in Hinglish written in English letters.")
        )
    file_texts = [fact.file_text for fact in sheet.facts if fact.file_text]
    asked = [fact.asked for fact in sheet.facts if fact.asked]
    prepared = _prepare(
        text, sheet.names, file_texts, asked, delivered=sheet.turn == TURN_DELIVERED
    )
    if _FILE_LIKE.search(prepared):
        problems.append(
            Problem(FILE_NAME, "A file name isn't one the result gives, exactly as written.")
        )
        # Read on as if it were a file: its own words ("report.xlsx") say nothing.
        prepared = _FILE_LIKE.sub(" ⟦FILE⟧ ", prepared)
    problems += _check_unbacked(prepared, file_texts, user_message)
    segments = _segments(prepared, past_tense=sheet.turn != TURN_CHAT)
    problems += _check_numbers(segments, cited, sheet, user_message)
    problems += _check_claims(segments, sheet)
    problems += _check_not_done_mentions(segments, sheet)
    if sheet.turn != TURN_DELIVERED:
        problems += _check_nothing_handed_back(segments)
    if sheet.turn == TURN_CHAT:
        problems += _check_chat(segments)
    problems += _check_needs(text, prepared, segments, sheet)
    return tuple(_unique(problems))


def _foreign_script(text: str) -> bool:
    """Whether ``text`` has letters outside the Latin script, which the checker can't read."""
    for char in text:
        if char.isalpha() and not unicodedata.name(char, "").startswith("LATIN"):
            return True
    return False


def _check_unbacked(prepared: str, file_texts: Sequence[str], user_message: str) -> list[Problem]:
    """No link, address or phone number the user didn't write; no run of the file's words."""
    problems = []
    for match in _CONTACT_RE.finditer(prepared):
        found = match.group(0).strip(".,;:)").casefold()
        if found and found not in user_message.casefold():
            problems.append(
                Problem(UNBACKED, "Never give a link, a web address, an email or a phone number.")
            )
            break
    runs = set()
    for source in file_texts:
        for quoted in _FROM_FILE_RE.findall(source):
            words = re.findall(r"[\w']+", quoted.casefold())
            runs |= {tuple(words[i : i + _RUN]) for i in range(len(words) - _RUN + 1)}
    if runs:
        words = re.findall(r"[\w']+", prepared.casefold())
        if any(tuple(words[i : i + _RUN]) in runs for i in range(len(words) - _RUN + 1)):
            problems.append(
                Problem(
                    UNBACKED,
                    "Text from the file may appear only in quotes, as something the file holds.",
                )
            )
    return problems


def _check_numbers(
    segments: list[_Segment], cited: list, sheet: FactSheet, user_message: str
) -> list[Problem]:
    problems = []
    quantities = [q for fact in cited for q in fact.quantities]
    users = {float(n.replace(",", "")) for n in re.findall(r"\d+(?:\.\d+)?", user_message)}
    for number in _numbers(segments):
        if number.vague:
            problems.append(Problem(NUMBER, "Write every count of rows in digits, exactly."))
            continue
        if number.noun == NOUN_ISSUES:
            if number.value == 0 and sheet.all_clear:
                continue
            problems.append(
                Problem(NUMBER, "A message counts problems in the file that the facts don't count.")
            )
            continue
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


def _objects(segment: _Segment, described: frozenset[str] = frozenset()) -> set[str]:
    text = segment.text
    found = set()
    if _NEAR_DUPLICATE_RE.search(text):
        found.add("unsupported")
        text = _NEAR_DUPLICATE_RE.sub(" ", text)
    for name, regex in _OBJECTS.items():
        if regex.search(text):
            found.add(name)
    if any(m.group(0).casefold() not in described for m in _UNSUPPORTED_RE.finditer(text)):
        found.add("unsupported")
    return found


def _is_bare(segment: _Segment) -> bool:
    text = _STATE_RE.sub(" ", _ACTION_RE.sub(" ", segment.text))
    return all(word.casefold() in _BARE_FILLER for word in _tokens(text))


def _described_words(sheet: FactSheet) -> frozenset[str]:
    """The words of the facts that describe the file or the steps (``Fact.describes``)."""
    return frozenset(
        word.casefold()
        for fact in sheet.facts
        if fact.describes
        for word in re.findall(r"[\w'-]+", fact.text.replace("’", "'"))
        for word in (word, *word.split("-"))
    )


def _leftover_words(segment: _Segment, sheet: FactSheet) -> list[str]:
    """The words of a claim that the facts' vocabulary doesn't hold."""
    described = _described_words(sheet)
    text = _STATE_RE.sub(" ", _ACTION_RE.sub(" ", segment.text))
    allowed_objects = [_OBJECTS[name] for name in (sheet.done | sheet.clear) if name in _OBJECTS]
    leftover = []
    for token in _tokens(text):
        word = token.casefold()
        if (
            word in _CLAIM_VOCABULARY
            or word in described
            or word.startswith("⟦")
            or word == "⟨absent⟩"
            or _NUMBER_TOKEN.match(word)
            or number_word_value(word) is not None
            or word in _REMOVED_TOKENS
            or (_PAST_TENSE_RE.fullmatch(word) and word not in _NOT_DEEDS)
            or any(regex.fullmatch(word) for regex in allowed_objects)
            or any(regex.match(word) for _, regex in _NOUN_TOKENS if _ != NOUN_FORMULA)
        ):
            continue
        leftover.append(word)
    return leftover


def _check_claims(segments: list[_Segment], sheet: FactSheet) -> list[Problem]:
    """Everything described as done must be a verified thing done this turn."""
    problems: list[Problem] = []
    if sheet.turn == TURN_CHAT:
        problems += _check_chat_claims(segments, sheet)
    else:
        for segment, words in _claimed(segments):
            problems += _check_one_claim(segment, words, sheet)
    problems += _check_file_used(segments, sheet)
    problems += _check_kept(segments)
    return _unique(problems)


def _check_one_claim(segment: _Segment, words: list[str], sheet: FactSheet) -> list[Problem]:
    described = _described_words(sheet)
    words = [word for word in words if " ".join(word.split()) not in described]
    if not words:
        return []  # "only single-sheet workbooks are supported": the facts' own description
    objects = _objects(segment, described)
    actions = any(_is_action(word) for word in words)
    deeds = [" ".join(w.split()) for w in words if _is_action(w) and w not in described]
    if "unsupported" in objects or any(not _SKILL_DEEDS_RE.match(deed) for deed in deeds):
        return [Problem(CLAIM, "A message says something was done that no step did this turn.")]
    problems = []
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
    if _leftover_words(segment, sheet):
        problems.append(
            Problem(
                CLAIM,
                "A message says something was done that the result doesn't say was done; say "
                "only what the DONE facts say.",
            )
        )
    if specific or problems:
        return problems
    # No object of its own: "done", "I did it", "your copy is ready", "it's clean now".
    if any(_ALL_CLEAR_WORDS_RE.match(" ".join(w.split())) for w in words) and not sheet.all_clear:
        return [
            Problem(
                CLAIM,
                "A message says the file is clean, but the check of it still found things; say "
                "exactly what was done.",
            )
        ]
    if sheet.complete and sheet.file_work_ran:
        return []
    generic = bool(_GENERIC_FILE_RE.search(segment.text))
    if sheet.turn == TURN_DELIVERED and generic and not actions:
        return []  # "your copy is ready": a copy was handed back
    return [
        Problem(
            CLAIM,
            "A message says it's done without saying what, but not everything asked for was "
            "done; name exactly what was done.",
        )
    ]


def _check_chat_claims(segments: list[_Segment], sheet: FactSheet) -> list[Problem]:
    """On a chat turn nothing was done: no statement says Buddy did or finished anything."""
    for segment in segments:
        if segment.question or not segment.claim_words:
            continue
        return [
            Problem(
                CHAT_FILE_TALK,
                "On a chat turn nothing was done to any file; a message says or suggests "
                "something was done.",
            )
        ]
    return []


def _firmly_not(segment: _Segment, pattern: re.Pattern[str]) -> bool:
    """Whether ``segment`` says firmly that what ``pattern`` names wasn't done.

    A firm "not" ("can't", "didn't", "isn't", "never") within three words of the match, and no
    verb that turns it round ("didn't forget to email").
    """
    if segment.question or _TURNED_ROUND_RE.search(segment.text):
        return False
    tokens = segment.tokens
    hits = [i for i, token in enumerate(tokens) if pattern.search(token)]
    nots = [i for i, token in enumerate(tokens) if _FIRM_NOT_RE.search(token)]
    return any(abs(hit - nope) <= 3 for hit in hits for nope in nots)


_HANDED_BACK_RE = re.compile(
    r"\bdownloads?\b|\battach\w*|\b(?:copy|version|file)\b.*\b(?:ready|here|is in|'s in)\b"
    r"|\bhere(?:'s| is) (?:your|the|a)\b",
    _FLAGS,
)


def _check_nothing_handed_back(segments: list[_Segment]) -> list[Problem]:
    """On a turn that handed nothing back, nothing says a file is there to take."""
    for segment in segments:
        if segment.negated or segment.question or segment.offer:
            continue
        if _HANDED_BACK_RE.search(segment.text) and not re.search(
            r"\b(?:report|found|noticed|what)\b", segment.text, _FLAGS
        ):
            return [
                Problem(CLAIM, "No file was handed back this turn; don't say there's one to take.")
            ]
    return []


def _check_not_done_mentions(segments: list[_Segment], sheet: FactSheet) -> list[Problem]:
    """A part of the request no skill can do is mentioned only to say it wasn't done."""
    for fact in sheet.facts:
        for word in fact.not_done_words:
            regex = re.compile(word, _FLAGS)
            for segment in segments:
                if segment.offer or segment.question or not regex.search(segment.text):
                    continue
                if not _firmly_not(segment, regex):
                    return [
                        Problem(
                            NOT_DONE_MENTION,
                            f"{fact.id} wasn't done: mention it only to say plainly that you "
                            "can't do it.",
                        )
                    ]
    return []


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


def _need_met(need: Need, text: str, prepared: str, segments: list[_Segment]) -> bool:
    flags = 0 if need.case_sensitive else _FLAGS
    for pattern, how in need.alternatives:
        regex = re.compile(pattern, flags)
        if how == SAID_UNABLE:
            if any(_firmly_not(s, regex) for s in segments):
                return True
        elif how:
            # Said in a sentence that says "not" ("No changes were made to any file", "I can't
            # do this part yet: “sort by amount”").
            negated_sentences = {s.sentence for s in segments if s.negated}
            if any(s.sentence in negated_sentences and regex.search(s.text) for s in segments):
                return True
        elif regex.search(text if need.case_sensitive else prepared.replace("·", ".")):
            return True
    return False


def _unique(problems: list[Problem]) -> list[Problem]:
    return list(dict.fromkeys(problems))

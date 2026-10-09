"""Actions — the one table of what Buddy can be asked to do.

Each action a user can request has exactly one row here: the capability
that carries it out, the target its verifier measures, the plain words the
reply uses for it, and the pattern the mention detector looks for. A new
skill is added in this one place (``docs/design/verified-outcome-design.md``
Sections 2.2 and 3.2).

The requested-actions ledger is built from this table too: Brain's
``requested_actions`` (``buddy.brain.ClaudeInterpreter``), its classified
intent, and the deterministic mention detector (``buddy.brain
.MentionCrossCheck``). Only Voice and the residual measurement read the
ledger; routing still follows Brain's ``intent`` alone.
"""

import re
from dataclasses import dataclass

from buddy.models import (
    DUPLICATE_ROWS_TARGET,
    EMPTY_ROWS_TARGET,
    LEDGER_FROM_BRAIN,
    LEDGER_FROM_INTENT,
    LEDGER_FROM_MENTION,
    RequestedAction,
    Understanding,
)


@dataclass(frozen=True)
class Action:
    """One action a user can ask for, and everything the reply says about it.

    Attributes:
        name: The ledger's name for it, e.g. ``"remove_duplicate_rows"``.
            Also a Brain intent.
        capability: The capability that carries it out.
        target: The ``StepMeasurement.target`` its verifier measures.
        request: How the reply states it as a request: "remove duplicate rows".
        object: What it acts on, as the reply names it: "duplicate rows".
        rows: The rows it removes, for "There were no … to remove": "duplicate rows".
        row_noun: The singular noun for the rows it removes, which a count turns
            into "1 duplicate row" or "2 duplicate rows" (``buddy.text.count_noun``).
        step: The step, as a noun: "duplicate-row removal".
        mention: Finds the action's object in a user's message (English only).
    """

    name: str
    capability: str
    target: str
    request: str
    object: str
    rows: str
    row_noun: str
    step: str
    mention: re.Pattern[str]


ACTIONS: tuple[Action, ...] = (
    Action(
        name="remove_duplicate_rows",
        capability="spreadsheet.deduplicate",
        target=DUPLICATE_ROWS_TARGET,
        request="remove duplicate rows",
        object="duplicate rows",
        rows="duplicate rows",
        row_noun="duplicate row",
        step="duplicate-row removal",
        # Longest alternative first, so "duplicate rows" is recorded whole.
        mention=re.compile(
            r"\b(duplicated?[\s-]+(?:rows?|lines?|entries|records)|duplicates?|dupes?|dedup\w*)\b",
            re.IGNORECASE,
        ),
    ),
    Action(
        name="remove_empty_rows",
        capability="spreadsheet.remove_empty_rows",
        target=EMPTY_ROWS_TARGET,
        request="remove empty rows",
        object="empty rows",
        rows="fully-empty rows",
        row_noun="fully-empty row",
        step="empty-row removal",
        mention=re.compile(r"\b(empty|blank)[\s-]+(rows?|lines?)\b", re.IGNORECASE),
    ),
)

# A request Brain recognised as an action Buddy has no capability for.
UNSUPPORTED = "unsupported"

# What Brain's ``requested_actions`` may name. Anything else becomes UNSUPPORTED.
ACTION_VOCABULARY = frozenset({a.name for a in ACTIONS} | {UNSUPPORTED})

ACTIONS_BY_NAME: dict[str, Action] = {a.name: a for a in ACTIONS}
ACTIONS_BY_CAPABILITY: dict[str, Action] = {a.capability: a for a in ACTIONS}
ACTIONS_BY_TARGET: dict[str, Action] = {a.target: a for a in ACTIONS}

# Brain intent -> the capability the planner runs for it (single-action intents only).
INTENT_CAPABILITIES: dict[str, str] = {a.name: a.capability for a in ACTIONS}

# Ledger sources that are Brain's reading of the request (not the mention detector).
BRAIN_SOURCES = frozenset({LEDGER_FROM_BRAIN, LEDGER_FROM_INTENT})


def find_phrase(message: str, phrase: str) -> str | None:
    """``phrase`` as it appears in ``message`` (case-insensitive), in the user's own casing.

    Returns ``None`` when ``phrase`` is empty or isn't in ``message``: then the
    phrase is the model's words, not the user's, and must never be quoted.
    """
    phrase = phrase.strip()
    if not phrase:
        return None
    match = re.search(re.escape(phrase), message, re.IGNORECASE)
    return match.group(0) if match is not None else None


def mentioned_actions(message: str) -> tuple[tuple[Action, str], ...]:
    """Every action whose object ``message`` mentions, in order of first mention.

    Deterministic and independent of every model. It can only ever add items
    to the "not done" side of a reply, so a false positive ("keep the
    duplicates") yields a true "I didn't run …" sentence, and a false
    negative (a paraphrase) leaves the reply silent about that item.

    Returns:
        ``(action, matched_text)`` pairs; ``matched_text`` is the user's own words.
    """
    found = []
    for action in ACTIONS:
        match = action.mention.search(message)
        if match is not None:
            found.append((match.start(), action, match.group(0)))
    return tuple((action, text) for _, action, text in sorted(found, key=lambda f: f[0]))


def brain_ledger(understanding: Understanding) -> tuple[RequestedAction, ...]:
    """Brain's reading of the request: its ``requested_actions``, plus its intent.

    A single-action intent is Brain's reading too (it's what the planner
    runs), so it's added when ``requested_actions`` doesn't already list it,
    with ``source=LEDGER_FROM_INTENT``. That also covers a reply with no
    ``requested_actions`` at all (older replies, test fakes).
    """
    entries: list[RequestedAction] = []
    for entry in understanding.requested_actions:
        if entry.source not in BRAIN_SOURCES:
            continue
        # One entry per supported action; every "unsupported" one is kept,
        # since each quotes a different part of the request.
        if entry.action in ACTIONS_BY_NAME and any(e.action == entry.action for e in entries):
            continue
        entries.append(entry)
    intent = understanding.intent
    if intent in ACTIONS_BY_NAME and not any(e.action == intent for e in entries):
        entries.append(RequestedAction(intent, "", source=LEDGER_FROM_INTENT, phrase_found=False))
    return tuple(entries)


def requested_ledger(understanding: Understanding) -> tuple[RequestedAction, ...]:
    """The full ledger: Brain's reading, then what only the mention detector found.

    A mention entry for an action Brain's reading already has is dropped, so
    each action appears once, with Brain's entry taking precedence.
    """
    brain = brain_ledger(understanding)
    listed = {e.action for e in brain}
    mentions: list[RequestedAction] = []
    for entry in understanding.requested_actions:
        if entry.source == LEDGER_FROM_MENTION and entry.action not in listed:
            listed.add(entry.action)
            mentions.append(entry)
    return brain + tuple(mentions)


# -- A report request, and a conversational turn ---------------------------------------
#
# Neither is a file-changing action, so neither is in ``ACTIONS``. Both are detected by
# deterministic patterns, independent of every model, and used to decide what the reply
# owes the user: a requested report is delivered or stated as not available (backlog
# item 3), and a greeting gets a greeting (item 2).

# The intent Brain gives a message that asks for no action: a greeting, thanks, or a
# question about what Buddy can do. Closed vocabulary, like every other intent.
CONVERSATION = "conversation"

_REPORT_NOUN = (
    r"(?:reports?|summary|summaries|summari[sz]e|summari[sz]ation|analysis|analy[sz]e|analyse)"
)
# "give me a full report", "send me the summary", "analyze it": the request, with the verb and
# determiners in front of the noun that belong to it.
_REPORT_REQUEST = re.compile(
    r"(?:\b(?:give|send|show|make|create|write|produce|generate|prepare|get|run|do|need|want)\s+"
    r"(?:(?:me|us|a|an|the|my|our|your|full|short|brief|quick|detailed|written|data|quality)\s+)*)?"
    rf"\b{_REPORT_NOUN}\b",
    re.IGNORECASE,
)
_REPORT_NEGATED = re.compile(
    rf"\b(?:no|not|without|skip|never|don't|dont|do\s+not)\b[^.;,!?]{{0,24}}\b{_REPORT_NOUN}\b",
    re.IGNORECASE,
)
# "email me the report", "print the summary": a report to be sent somewhere else. Buddy can't do
# that, so it isn't a request for the report in the reply, and the reply says what it can't do.
_REPORT_ELSEWHERE = re.compile(
    r"\b(?:e-?mail|mail|print|upload|share|export|download|forward|attach|slack|text)\b"
    rf"[^.;,!?]{{0,24}}\b{_REPORT_NOUN}\b",
    re.IGNORECASE,
)
# Words that may stand around a report request without asking for anything else.
_REPORT_FILLER = frozenset(
    "a an the please pls and then also just only so but can could would will you i we to "
    "me us it of on for this that my our your file sheet spreadsheet data".split()
)


def mentions_report(message: str) -> bool:
    """Whether ``message`` asks for a report on the file (and doesn't say not to).

    Deterministic and independent of every model. A paraphrase it misses ("give me a
    rundown") is simply not labelled as a report; a negation it can't parse is the
    only way to a report nobody wanted, which states findings and claims nothing else.
    A report to be sent elsewhere ("email me the report") isn't one: Buddy can't send it.
    """
    return (
        _REPORT_REQUEST.search(message) is not None
        and _REPORT_NEGATED.search(message) is None
        and _REPORT_ELSEWHERE.search(message) is None
    )


def strip_report_request(text: str) -> tuple[str, bool]:
    """``text`` without its report request, and whether it had one."""
    stripped, count = _REPORT_REQUEST.subn(" ", text)
    return stripped, count > 0


def is_only_a_report_request(phrase: str) -> bool:
    """Whether ``phrase`` is a report request and nothing else.

    "give me a report" and "and then send me the summary, please" are; "remove duplicates
    and give me a report" and "sort by amount" are not.
    """
    if not mentions_report(phrase):
        return False
    rest = _REPORT_REQUEST.sub(" ", phrase.casefold().replace("\u2019", "'"))
    return all(word in _REPORT_FILLER for word in re.findall(r"[\w']+", rest))


def is_conversation(understanding: Understanding, message: str) -> bool:
    """Whether this turn is only a greeting, thanks or a question about what Buddy can do.

    Brain's intent says so, **and** nothing contradicts it: Brain listed no action
    (supported or not), the mention detector finds none in the message, and the message
    doesn't ask for a report. An action request that Brain misread as conversation therefore
    never gets a conversational reply when anything in the message gives it away. (When
    nothing does, no step runs and the reply still claims nothing.)
    """
    return (
        understanding.intent == CONVERSATION
        and not requested_ledger(understanding)
        and not mentioned_actions(message)
        and not mentions_report(message)
    )

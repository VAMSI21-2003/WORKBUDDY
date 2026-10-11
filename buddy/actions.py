"""Actions — the one table of what Buddy can be asked to do.

Each action a user can request has exactly one row here: the capability
that carries it out, the target its verifier measures, the plain words the
reply uses for it, and the pattern the mention detector looks for. A new
skill is added in this one place (``docs/design/verified-outcome-design.md``
Sections 2.2 and 3.2).

``report_on_file`` is a row like the others, except that it changes no file
and has no mention pattern: only Brain's reading can put it in the ledger, so
no word list anywhere decides whether a message asks for a report (design
``docs/design/reply-quality.md``, D4).

The requested-actions ledger is built from this table too: Brain's
``requested_actions`` (``buddy.brain.ClaudeInterpreter``), its classified
intent, and the deterministic mention detector (``buddy.brain
.MentionCrossCheck``). Only Voice and the residual measurement read the
ledger; routing still follows Brain's ``intent`` alone.
"""

import re
from collections.abc import Sequence
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
        mention: Finds the action's object in a user's message (English only), or
            ``None`` for an action only Brain's reading can put in the ledger.
        changes_file: Whether carrying it out changes the file (the report doesn't).
    """

    name: str
    capability: str
    target: str
    request: str
    object: str
    rows: str
    row_noun: str
    step: str
    mention: re.Pattern[str] | None
    changes_file: bool = True


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
    # The report: the analysis findings, labelled as the report. No file changes and no mention
    # pattern, so the mention detector can never add or confirm one.
    Action(
        name="report_on_file",
        capability="spreadsheet.analyze",
        target="",
        request="report on the file",
        object="a report",
        rows="",
        row_noun="",
        step="report",
        mention=None,
        changes_file=False,
    ),
)

# The name of the report action: Brain's intent and requested action for a report, summary,
# analysis or overview of the file.
REPORT_ON_FILE = "report_on_file"

# A request Brain recognised as an action Buddy has no capability for.
UNSUPPORTED = "unsupported"

# What Brain's ``requested_actions`` may name. Anything else becomes UNSUPPORTED.
ACTION_VOCABULARY = frozenset({a.name for a in ACTIONS} | {UNSUPPORTED})

ACTIONS_BY_NAME: dict[str, Action] = {a.name: a for a in ACTIONS}
ACTIONS_BY_CAPABILITY: dict[str, Action] = {a.capability: a for a in ACTIONS}
ACTIONS_BY_TARGET: dict[str, Action] = {a.target: a for a in ACTIONS if a.target}

# Brain intent -> the capability the planner runs for it (single-action intents that change a
# file only; the report is planned as the analysis, not by this table).
INTENT_CAPABILITIES: dict[str, str] = {a.name: a.capability for a in ACTIONS if a.changes_file}

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


def mentioned_actions(
    message: str, *, except_inside: Sequence[str] = ()
) -> tuple[tuple[Action, str], ...]:
    """Every action whose object ``message`` mentions, in order of first mention.

    Deterministic and independent of every model. It can only ever add items
    to the "not done" side of a reply, so a false positive ("keep the
    duplicates") yields a true "I didn't run …" sentence, and a false
    negative (a paraphrase) leaves the reply silent about that item.

    An action with no mention pattern (the report) is never found here.

    Args:
        message: The user's message.
        except_inside: Phrases of the message that Brain gave to another action (the report):
            a mention inside one is what that action is about ("a report on the duplicate
            rows"), not a request for this one. Compared as text, not read.

    Returns:
        ``(action, matched_text)`` pairs; ``matched_text`` is the user's own words.
    """
    spans = []
    for phrase in except_inside:
        phrase = phrase.strip()
        found = re.search(re.escape(phrase), message, re.IGNORECASE) if phrase else None
        if found is not None:
            spans.append(found.span())
    found_actions = []
    for action in ACTIONS:
        if action.mention is None:
            continue
        match = next(
            (
                m
                for m in action.mention.finditer(message)
                if not any(m.start() < end and start < m.end() for start, end in spans)
            ),
            None,
        )
        if match is not None:
            found_actions.append((match.start(), action, match.group(0)))
    return tuple(
        (action, text) for _, action, text in sorted(found_actions, key=lambda f: f[0])
    )


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


# -- The report, and a conversational turn ----------------------------------------------
#
# A report is a row of ``ACTIONS`` (``report_on_file``) that only Brain's reading can put in
# the ledger: no pattern, list of words or other test of the message decides whether the user
# asked for one. If Brain didn't read a report, nothing is labelled a report; if it did and the
# analysis ran and passed, the findings are labelled as the report; if it did and they can't be
# given, the reply says why (backlog item 3).

# The intent Brain gives a message that asks for no action: a greeting, thanks, or a
# question about what Buddy can do. Closed vocabulary, like every other intent.
CONVERSATION = "conversation"


def asks_for_report(understanding: Understanding) -> bool:
    """Whether Brain's reading of the request includes a report on the file.

    Brain's ``requested_actions`` (or its single-action intent) decides, through
    ``brain_ledger``. Nothing in the message is read here.
    """
    return any(entry.action == REPORT_ON_FILE for entry in brain_ledger(understanding))


def report_phrases(understanding: Understanding) -> tuple[str, ...]:
    """The user's own words Brain gave to the report (only phrases found in the message)."""
    return tuple(
        entry.source_phrase
        for entry in brain_ledger(understanding)
        if entry.action == REPORT_ON_FILE and entry.phrase_found and entry.source_phrase
    )


def is_conversation(understanding: Understanding, message: str) -> bool:
    """Whether this turn is only a greeting, thanks or a question about what Buddy can do.

    Brain's closed intent says so, **and** nothing contradicts it: Brain listed no action
    (supported or not, the report included) and the mention detector finds no file-changing
    action named in the message. An action request that Brain misread as conversation
    therefore never gets a conversational reply when anything in the message gives it away.
    (When nothing does, no step runs and the reply still claims nothing.)
    """
    return (
        understanding.intent == CONVERSATION
        and not requested_ledger(understanding)
        and not mentioned_actions(message)
    )

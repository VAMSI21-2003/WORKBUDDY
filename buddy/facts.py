"""Facts — what a reply may say about the file or the work, written by code.

The writer (``buddy.writer``) never words a fact. Each fact here comes with every phrasing code
wrote for it (``Fact.variants``): the template reply's own sentence first, so the two can never
disagree, then a few other phrasings built from the same data. The model chooses which facts to
show, in which order and in which phrasing, by naming them (``{F1b}``), and writes only the
social lines around them, which ``buddy.social`` holds to a closed social vocabulary. So every
statement about the file, a count, a file name or the work in a shown reply is a sentence code
wrote from the Inspector's results. (Owner's decision of 2026-10-24, after review round 2 of
``feat/natural-buddy``: the earlier design, where the model reworded the facts and a word-list
checker read its wording, let false claims through in two rounds.)

Built only from what ``buddy.voice`` already uses for the template reply: the Inspector's
verdicts and measurements, the requested-actions ledger and the turn's ``FileUsed``. Nothing here
reads the user's words to decide what was asked or done. A fact whose phrasing holds text from
the user's file (an analysis finding names a column or quotes a cell, in the analyzer's quotes)
is marked ``from_file``; the writer shows it to the model only as a quoted, capped field.

A turn the sheet doesn't cover (a chain that stopped, a delivery the Inspector didn't pass, a
failed stage) gets ``None``: its reply stays the template's.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from buddy.actions import ACTIONS_BY_CAPABILITY, asks_about_file, is_conversation
from buddy.models import FILE_KIND_COPY
from buddy.outcome import (
    DONE,
    DONE_UNREQUESTED,
    NO_SKILL,
    NOT_DONE,
    REPORT_NOT_RUN_SENTENCE,
    VALUES_ONLY_SENTENCE,
    ActionOutcome,
    OutcomeReport,
    _action_sentence,
    _final_state_sentence,
    _not_done_sentence,
    _residual_sentences,
    _unrequested_sentence,
    _values_only_sentences,
    already_satisfied,
    build_outcome_report,
    report_unavailable_sentence,
)
from buddy.text import count_noun
from buddy.think import CLARIFY_FAILED, CLARIFY_OTHER
from buddy.voice import (
    NO_CHANGES_SENTENCE,
    RECHECK_FAILED_SENTENCE,
    SpeakingRequest,
    _analysis_findings,
    _file_label,
    _file_used_sentence,
    _initial_analysis_failed,
    _initial_analysis_failed_sentence,
    _name_in_reply,
    _no_file_changing_step_ran,
    _nothing_delivered,
    _nothing_left,
    _recheck_failed,
    _verified_delivery,
    delivery_parts,
)
from buddy.voice_catalog import (
    CAPABILITIES,
    CLARIFICATION_QUESTIONS,
    FILE_NOT_CHECKED_SENTENCE,
    FILE_STATE_LEAD_COPY,
    FILE_STATE_LEAD_ORIGINAL,
    NO_FILE_SENTENCE,
    NOT_UNDERSTOOD_SENTENCE,
    NOTHING_LEFT_TEMPLATE,
    REFUSED_TEMPLATE,
    REPORT_LEAD_COPY,
    REPORT_LEAD_ORIGINAL,
    REPORT_LEAD_USED,
    RESUMED_SENTENCE,
    UNDERSTANDING_FAILED_SENTENCE,
    effective_mode,
    style_for,
)

# What kind of turn the reply is for.
TURN_CHAT = "chat"  # Brain read conversation, and nothing contradicts it; no step changed a file
TURN_DELIVERED = "delivered"  # a verified file was handed back
TURN_NOTHING_LEFT = "nothing_left"  # the latest copy already satisfied the request
TURN_NO_CHANGE = "no_change"  # no file-changing step ran (a question, no file, a request Buddy
# can't do, a report on the file, a question about it)

# Fact kinds, as the writer sees them.
KIND_DONE = "DONE"
KIND_ALSO_DONE = "DONE, NOT ASKED FOR"
KIND_NOT_DONE = "NOT DONE"
KIND_STATE = "FILE NOW"
KIND_FILE = "FILE USED"
KIND_DISCLOSURE = "MUST TELL"
KIND_REPORT = "REPORT"
KIND_FINDING = "NOTICED"
KIND_NOTHING_LEFT = "NOTHING TO DO"
KIND_NO_CHANGES = "NO CHANGES"
KIND_NO_FILE = "NO FILE"
KIND_REFUSED = "CAN'T USE FILE"
KIND_QUESTION = "ASK"
KIND_NOT_UNDERSTOOD = "NOT UNDERSTOOD"
KIND_CAN_DO = "WHAT I CAN DO"
KIND_OFFER = "OFFER"

# The most analysis findings a sheet lists one by one; the rest are counted in one fact.
MAX_FINDINGS = 5
# The most characters of one finding's text (from the user's file) the writer is shown.
MAX_FILE_TEXT = 160

ANALYSIS_OFFER = "Want me to tackle any of these?"


@dataclass(frozen=True)
class Fact:
    """One thing the reply may say about the file or the work.

    Attributes:
        id: ``F1``, ``F2``... in sheet order.
        kind: ``KIND_*``.
        variants: Every phrasing code wrote for it, each a whole sentence or more; the first is
            the template reply's own. The model picks one by its letter (``F1a``, ``F1b``...).
        required: The reply must show it, in one of its phrasings.
        names: The file names it gives, exactly.
        from_file: Its phrasings hold text from the user's file (an analysis finding).
    """

    id: str
    kind: str
    variants: tuple[str, ...]
    required: bool = False
    names: tuple[str, ...] = ()
    from_file: bool = False

    @property
    def text(self) -> str:
        """The template reply's own sentence for it."""
        return self.variants[0]

    def variant_ids(self) -> tuple[str, ...]:
        return tuple(f"{self.id}{chr(ord('a') + i)}" for i in range(len(self.variants)))


@dataclass(frozen=True)
class FactSheet:
    """The VERIFIED RESULT for one turn.

    Attributes:
        turn: ``TURN_*``.
        facts: In the order the writer is shown them.
        names: Every file name a shown reply holds (through the facts).
        has_file: The session has a file.
    """

    turn: str
    facts: tuple[Fact, ...] = ()
    names: tuple[str, ...] = ()
    has_file: bool = True

    def by_id(self) -> dict[str, Fact]:
        return {fact.id: fact for fact in self.facts}

    def variant(self, slot: str) -> str | None:
        """The phrasing ``slot`` names (``"F1b"``), or ``None`` if there is none."""
        fact = self.by_id().get(slot[:-1])
        if fact is None or not slot[-1:].isalpha():
            return None
        index = ord(slot[-1].lower()) - ord("a")
        return fact.variants[index] if 0 <= index < len(fact.variants) else None


def _capped(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= MAX_FILE_TEXT else text[: MAX_FILE_TEXT - 1].rstrip() + "…"


def _sentence(text: str) -> str:
    text = text.strip()
    if not text:
        return text
    text = text[0].upper() + text[1:]
    return text if text[-1] in ".!?…" else text + "."


def _lower_first(text: str) -> str:
    return text[0].lower() + text[1:] if text else text


class _Builder:
    """Collects facts and numbers them."""

    def __init__(self) -> None:
        self.facts: list[Fact] = []
        self.names: list[str] = []

    def add(
        self,
        kind: str,
        variants: str | list[str] | tuple[str, ...],
        *,
        required: bool = False,
        names: tuple[str, ...] = (),
        from_file: bool = False,
    ) -> Fact:
        texts = (variants,) if isinstance(variants, str) else tuple(dict.fromkeys(variants))
        fact = Fact(
            id=f"F{len(self.facts) + 1}",
            kind=kind,
            variants=texts,
            required=required,
            names=names,
            from_file=from_file,
        )
        self.facts.append(fact)
        for name in names:
            if name and name not in self.names:
                self.names.append(name)
        return fact


def build_fact_sheet(request: SpeakingRequest) -> FactSheet | None:
    """The VERIFIED RESULT for ``request``'s turn, or ``None`` when the writer shouldn't word it.

    ``None`` for a turn in which a stage raised, a chain stopped, a step failed its check or a
    file came back that the Inspector didn't pass: those replies stay the template's.
    """
    if request.failure is not None:
        return None
    if _verified_delivery(request):
        return _delivered(request)
    if not _nothing_delivered(request):
        return None
    if _nothing_left(request):
        return _nothing_left_sheet(request)
    if not _no_file_changing_step_ran(request.execution_result):
        return None
    if is_conversation(request.understanding, request.user_message):
        return _chat(request)
    return _no_change(request)


def _style(request: SpeakingRequest) -> str:
    return style_for(effective_mode(request.mode, request.understanding.user_state))


# -- A verified delivery ------------------------------------------------------------------


def _delivered(request: SpeakingRequest) -> FactSheet:
    report, open_findings, notes = delivery_parts(request)
    builder = _Builder()
    _action_facts(builder, report)
    _state_facts(builder, report)
    _file_fact(builder, request, report)
    builder.add(
        KIND_DISCLOSURE,
        [
            VALUES_ONLY_SENTENCE,
            "Heads up: the copy keeps the values only, so formatting and column widths don't "
            "come along.",
            "Just so you know, the copy has the values only, without the formatting, column "
            "widths or other sheet settings.",
        ],
        required=True,
    )
    formulas = _values_only_sentences(report.original_facts)[1:]
    if formulas:
        builder.add(KIND_DISCLOSURE, " ".join(formulas), required=True)
    if report.order_used:
        builder.add(KIND_DISCLOSURE, report.order_used, required=True)
    if request.resumed:
        builder.add(KIND_DISCLOSURE, RESUMED_SENTENCE, required=True)
    if report.report_asked:
        if report.report_given:
            builder.add(
                KIND_REPORT,
                [REPORT_LEAD_COPY.rstrip(": ") + ":", "Here's the report on the copy:"],
                required=True,
            )
            _finding_facts(builder, open_findings, notes, required=True, offer=False)
    else:
        _finding_facts(builder, open_findings, notes, required=False, offer=True)
    if not report.report_asked or report.report_given:
        verification_result = request.verification_result
        if _initial_analysis_failed(verification_result):
            builder.add(
                KIND_DISCLOSURE, _initial_analysis_failed_sentence(request), required=True
            )
        if _recheck_failed(verification_result):
            builder.add(KIND_DISCLOSURE, RECHECK_FAILED_SENTENCE, required=True)
    return FactSheet(turn=TURN_DELIVERED, facts=tuple(builder.facts), names=tuple(builder.names))


def _action_facts(builder: _Builder, report: OutcomeReport) -> None:
    """A fact for each action: done, done though not asked for, and not done."""
    lead = report.lead_word
    first = True
    also = False
    for action in report.actions:
        if action.status not in (DONE, DONE_UNREQUESTED):
            continue
        wording = ACTIONS_BY_CAPABILITY.get(action.capability)
        if wording is None or not wording.changes_file:
            continue
        if action.status == DONE:
            variants = _done_variants(action, lead if first else None)
            first = False
            builder.add(KIND_DONE, variants, required=True)
        else:
            builder.add(KIND_ALSO_DONE, _unrequested_sentence(action, also=also), required=True)
        also = True
    for action in report.actions:
        if action.status == NOT_DONE:
            _not_done_fact(builder, action)
    if (
        any(
            a.status == NOT_DONE and a.capability not in ACTIONS_BY_CAPABILITY
            for a in report.actions
        )
        and report.brain_reading
    ):
        builder.add(KIND_STATE, f"I read your request as: {' and '.join(report.brain_reading)}.")


def _done_variants(action: ActionOutcome, lead: str | None) -> list[str]:
    """The phrasings of one done action, all from the verifier's own count."""
    base = _action_sentence(action)
    variants = [base]
    if lead is not None:
        variants.append(f"{lead}: {_lower_first(base)}")
    wording = ACTIONS_BY_CAPABILITY[action.capability]
    measurement = action.measurement
    if measurement is not None and measurement.removed:
        rows = count_noun(measurement.removed, wording.row_noun)
        blank = (
            f" (including {count_noun(measurement.removed_blank, 'repeated blank row')})"
            if measurement.removed_blank
            else ""
        )
        variants.append(_sentence(f"{rows} gone{blank}"))
        variants.append(f"I took out {rows}{blank}.")
    return variants


def _not_done_fact(builder: _Builder, action: ActionOutcome, *, nothing_done: bool = False) -> None:
    """A part of the request that wasn't done: the reply must show it."""
    base = _not_done_sentence(action, nothing_done=nothing_done)
    variants = [base]
    if action.reason == NO_SKILL and action.user_phrase:
        variants.append(f"“{action.user_phrase}” isn't something I can do yet.")
    builder.add(KIND_NOT_DONE, variants, required=True)


def _state_facts(builder: _Builder, report: OutcomeReport) -> None:
    """What the delivered file is measured to hold now."""
    sentence = _final_state_sentence(report.final_state)
    if sentence:
        builder.add(KIND_STATE, [sentence, sentence.replace("The copy has", "The copy now has", 1)])
    for text in _residual_sentences(report.residuals, after_final_state=bool(sentence)):
        builder.add(KIND_STATE, text)


def _file_fact(builder: _Builder, request: SpeakingRequest, report: OutcomeReport) -> None:
    """Which file the turn worked on, by name."""
    used = request.file_used
    if used is None:
        name = _name_in_reply(report.delivered_file)
        if name:
            builder.add(
                KIND_FILE,
                [f"Your cleaned copy of {name} is ready.", f"Your cleaned copy of {name} is here."],
                required=True,
                names=(name,),
            )
        return
    on_copy = used.kind == FILE_KIND_COPY
    name = _name_in_reply(used.copy_name if on_copy else Path(used.path).name)
    label = _file_label(name, fallback="the latest copy" if on_copy else "your file")
    variants = [_file_used_sentence(used)]
    if on_copy:
        variants.append(f"I worked from {label}, the latest copy I handed back, not your original.")
    elif not used.copy_skipped:
        variants.append(f"This was all on {label}, your original upload.")
    builder.add(KIND_FILE, variants, required=True, names=(name,) if name else ())


def _finding_facts(
    builder: _Builder,
    open_findings: tuple[str, ...],
    notes: list[str] | tuple[str, ...],
    *,
    required: bool,
    offer: bool,
) -> None:
    """The analysis findings (text from the user's file, in the analyzer's quotes) and notes."""
    for finding in open_findings[:MAX_FINDINGS]:
        quoted = _capped(finding)
        variants = [_sentence(quoted)]
        if not required:
            variants.append(f"I also noticed: {quoted}.")
        builder.add(KIND_FINDING, variants, required=required, from_file=True)
    more = len(open_findings) - MAX_FINDINGS
    if more > 0:
        noun = count_noun(more, "more thing")
        builder.add(KIND_FINDING, f"There are {noun} the check noticed.", required=required)
    for note in notes:
        builder.add(KIND_FINDING, _sentence(note), required=required)
    if offer and open_findings:
        builder.add(KIND_OFFER, [ANALYSIS_OFFER, "Want me to look at any of these?"])


def _no_changes_fact(builder: _Builder) -> None:
    builder.add(
        KIND_NO_CHANGES,
        [
            NO_CHANGES_SENTENCE,
            "I haven't changed any file this turn.",
            "Nothing in your files changed this turn.",
        ],
        required=True,
    )


# -- The latest copy already had nothing to remove -----------------------------------------


def _nothing_left_sheet(request: SpeakingRequest) -> FactSheet:
    used = request.file_used
    assert used is not None
    builder = _Builder()
    actions = already_satisfied(request.execution_result, request.verification_result)
    rows = " or ".join(dict.fromkeys(action.rows for action in actions))
    name = _name_in_reply(used.copy_name)
    label = f"`{name}`" if name else "the copy"
    builder.add(
        KIND_NOTHING_LEFT,
        [
            NOTHING_LEFT_TEMPLATE.format(file=label, rows=rows),
            f"{_sentence(label)[:-1]} already has no {rows}, so there was nothing to remove and "
            "there's no new file this time.",
        ],
        required=True,
        names=(name,) if name else (),
    )
    outcome = build_outcome_report(
        request.execution_result, request.verification_result, request.understanding
    )
    for action in outcome.actions:
        if action.status == NOT_DONE and action.capability != "spreadsheet.analyze":
            _not_done_fact(builder, action)
    if outcome.report_asked:
        findings = _analysis_findings(request)
        if outcome.report_given and findings is not None:
            builder.add(KIND_REPORT, REPORT_LEAD_USED.rstrip(": ") + ":", required=True)
            _finding_facts(builder, findings[0], findings[1], required=True, offer=False)
        else:
            builder.add(
                KIND_NOT_DONE,
                report_unavailable_sentence(outcome) or REPORT_NOT_RUN_SENTENCE,
                required=True,
            )
    if request.resumed:
        builder.add(KIND_DISCLOSURE, RESUMED_SENTENCE, required=True)
    _no_changes_fact(builder)
    return FactSheet(turn=TURN_NOTHING_LEFT, facts=tuple(builder.facts), names=tuple(builder.names))


# -- Nothing ran, and it was a chat -------------------------------------------------------


def _chat(request: SpeakingRequest) -> FactSheet:
    builder = _Builder()
    builder.add(KIND_CAN_DO, CAPABILITIES[_style(request)])
    if not request.has_file:
        builder.add(KIND_NO_FILE, "No spreadsheet has been uploaded in this chat yet.")
    return FactSheet(turn=TURN_CHAT, facts=tuple(builder.facts), has_file=request.has_file)


# -- Nothing ran, and it wasn't a chat ----------------------------------------------------


def _no_change(request: SpeakingRequest) -> FactSheet:
    """The facts of ``TemplateSpeaker._no_change_reply``, in its order."""
    builder = _Builder()
    style = _style(request)
    names = _used_name(request)
    can_do = list(dict.fromkeys([CAPABILITIES[style], *CAPABILITIES.values()]))
    if not request.has_file:
        builder.add(
            KIND_NO_FILE,
            [NO_FILE_SENTENCE, "There's no file here yet. Upload a spreadsheet and ask again."],
            required=True,
        )
        builder.add(KIND_CAN_DO, can_do)
        return FactSheet(turn=TURN_NO_CHANGE, facts=tuple(builder.facts), has_file=False)
    if request.file_refusal:
        builder.add(
            KIND_REFUSED, REFUSED_TEMPLATE.format(reason=request.file_refusal), required=True
        )
        _no_changes_fact(builder)
        return FactSheet(turn=TURN_NO_CHANGE, facts=tuple(builder.facts), names=names)
    reasoning = request.reasoning
    understanding = request.understanding
    outcome = build_outcome_report(
        request.execution_result, request.verification_result, understanding
    )
    asked = answered = False
    clarifying = reasoning is not None and reasoning.clarification_required
    if asks_about_file(understanding) and not clarifying:
        # A question about the file: what the check of it finds now, never a guess.
        for action in outcome.actions:
            if action.status == NOT_DONE and action.capability != "spreadsheet.analyze":
                _not_done_fact(builder, action, nothing_done=True)
        findings = _analysis_findings(request)
        if findings is not None and (findings[0] or findings[1]):
            on_copy = request.file_used is not None and request.file_used.kind == FILE_KIND_COPY
            lead = FILE_STATE_LEAD_COPY if on_copy else FILE_STATE_LEAD_ORIGINAL
            builder.add(KIND_STATE, lead.rstrip(": ") + ":", required=True)
            _finding_facts(builder, findings[0], findings[1], required=True, offer=False)
        else:
            builder.add(KIND_STATE, FILE_NOT_CHECKED_SENTENCE, required=True)
        answered = True
    elif clarifying:
        assert reasoning is not None
        if reasoning.clarification_reason == CLARIFY_FAILED:
            builder.add(KIND_NOT_UNDERSTOOD, UNDERSTANDING_FAILED_SENTENCE, required=True)
        else:
            questions = CLARIFICATION_QUESTIONS.get(
                reasoning.clarification_reason, CLARIFICATION_QUESTIONS[CLARIFY_OTHER]
            )
            builder.add(KIND_QUESTION, list(questions[style]), required=True)
            asked = True
    elif understanding.intent is None:
        builder.add(KIND_NOT_UNDERSTOOD, UNDERSTANDING_FAILED_SENTENCE, required=True)
    else:
        stated = 0
        for action in outcome.actions:
            if action.status != NOT_DONE or action.capability == "spreadsheet.analyze":
                continue
            stated += 1
            _not_done_fact(builder, action, nothing_done=True)
        if stated or not outcome.report_asked:
            if not stated:
                builder.add(KIND_NOT_UNDERSTOOD, NOT_UNDERSTOOD_SENTENCE, required=True)
            builder.add(KIND_CAN_DO, can_do)
    findings = _analysis_findings(request)
    if answered:
        pass
    elif outcome.report_asked:
        if outcome.report_given and findings is not None:
            on_copy = request.file_used is not None and request.file_used.kind == FILE_KIND_COPY
            lead = REPORT_LEAD_USED if on_copy else REPORT_LEAD_ORIGINAL
            builder.add(KIND_REPORT, lead.rstrip(": ") + ":", required=True)
            _finding_facts(builder, findings[0], findings[1], required=True, offer=False)
            if request.resumed:
                builder.add(KIND_DISCLOSURE, RESUMED_SENTENCE, required=True)
        else:
            builder.add(
                KIND_NOT_DONE,
                report_unavailable_sentence(outcome) or REPORT_NOT_RUN_SENTENCE,
                required=True,
            )
    elif findings is not None and (findings[0] or findings[1]):
        _finding_facts(builder, findings[0], findings[1], required=False, offer=True)
    if (
        _initial_analysis_failed(request.verification_result)
        and not outcome.report_asked
        and not answered
    ):
        builder.add(KIND_DISCLOSURE, _initial_analysis_failed_sentence(request), required=True)
    if not asked:
        _no_changes_fact(builder)
    return FactSheet(turn=TURN_NO_CHANGE, facts=tuple(builder.facts), names=names)


def _used_name(request: SpeakingRequest) -> tuple[str, ...]:
    used = request.file_used
    if used is None:
        return ()
    name = _name_in_reply(used.copy_name if used.kind == FILE_KIND_COPY else Path(used.path).name)
    return (name,) if name else ()


__all__ = ["Fact", "FactSheet", "build_fact_sheet"]

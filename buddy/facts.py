"""Facts — the VERIFIED RESULT the reply writer is given, built by code.

The writer (``buddy.writer``) words the reply; it never decides what happened. What happened is
this fact sheet: one ``Fact`` per thing the reply may say about the file or the work, each with
the code's own sentence for it (the same sentence the template reply uses, so the two can't
disagree), the numbers and file names it backs, and, when the reply must say it, what the
claim checker (``buddy.claims``) looks for to see that it was said.

Built only from what ``buddy.voice`` already uses for the template reply: the Inspector's
verdicts and measurements, the requested-actions ledger and the turn's ``FileUsed``. Nothing
here reads the user's words to decide what was asked or done. Text that came from the user's
file (an analysis finding names a column or quotes a cell) is kept apart, in ``Fact.file_text``,
and reaches the model only as a quoted, length-capped field (``buddy.writer``).

A turn the sheet doesn't cover (a chain that stopped, a delivery the Inspector didn't pass, a
failed stage) gets ``None``: its reply stays the template's.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from buddy.actions import ACTIONS_BY_CAPABILITY, is_conversation
from buddy.models import DUPLICATE_ROWS_TARGET, EMPTY_ROWS_TARGET, FILE_KIND_COPY
from buddy.outcome import (
    DONE,
    DONE_UNREQUESTED,
    LEAD_DONE,
    NO_SKILL,
    NO_SKILL_UNSURE,
    NOT_DONE,
    REPORT_NOT_RUN_SENTENCE,
    VALUES_ONLY_SENTENCE,
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
from buddy.think import CLARIFY_FAILED
from buddy.voice import (
    NO_CHANGES_SENTENCE,
    RECHECK_FAILED_SENTENCE,
    SpeakingRequest,
    _analysis_findings,
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
    NO_FILE_SENTENCE,
    NOT_UNDERSTOOD_SENTENCE,
    NOTHING_LEFT_TEMPLATE,
    REFUSED_TEMPLATE,
    RESUMED_SENTENCE,
    UNDERSTANDING_FAILED_SENTENCE,
    clarification_question,
    effective_mode,
    style_for,
)

# What kind of turn the reply is for.
TURN_CHAT = "chat"  # Brain read conversation, and nothing contradicts it; no step changed a file
TURN_DELIVERED = "delivered"  # a verified file was handed back
TURN_NOTHING_LEFT = "nothing_left"  # the latest copy already satisfied the request
TURN_NO_CHANGE = "no_change"  # no file-changing step ran (a question, no file, a request Buddy
# can't do, a report on the file)

# What a number in a reply counts (``Quantity.noun``).
NOUN_DUPLICATE = "duplicate"
NOUN_EMPTY = "empty"
NOUN_ROWS = "rows"
NOUN_FORMULA = "formula"
NOUN_ANY = "any"  # a fact's number that may be named with any noun (an analysis finding's)
NOUN_OTHER = "other"  # a reply's number with none of the nouns above next to it

# Whether a number is what was removed or what is left (``Quantity.phase``).
PHASE_REMOVED = "removed"
PHASE_LEFT = "left"
PHASE_NONE = ""

# The objects a reply can describe as done (``FactSheet.done`` and ``FactSheet.clear``).
OBJECT_DUPLICATE = NOUN_DUPLICATE
OBJECT_EMPTY = NOUN_EMPTY
OBJECT_REPORT = "report"

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

# The most analysis findings a sheet lists one by one; the rest are counted in one fact.
MAX_FINDINGS = 5
# The most characters of one finding's text (from the user's file) the writer is shown.
MAX_FILE_TEXT = 160

_NUMBER_WORDS = (
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
    "fifteen sixteen seventeen eighteen nineteen twenty"
).split()

# The words the checker looks for, per object. Kept here with the facts that need them.
DUPLICATE_WORDS = r"\b(?:duplicat\w*|dupes?|dups?|dedup\w*|repeat(?:ed|s)?|doubles?)\b"
EMPTY_WORDS = r"\b(?:empty|blank)\w*"
REPORT_WORDS = r"\b(?:report\w*|summar\w*|overview|analys\w*|rundown)\b"
_INABILITY = r"\b(?:can'?t|cannot|can not|unable|not able|isn'?t something|not something|don'?t)\b"
_UNCHANGED = (
    r"\b(?:touch\w*|chang\w*|modif\w*|edit\w*|alter\w*|new (?:file|copy)|hand\w* (?:back|over)"
    r"|anything)\b"
)
_UNCHANGED_PLAIN = r"\b(?:untouched|unchanged|as it was|same as before|as-is)\b"
VALUES_ONLY_WORDS = (
    r"\b(?:values?[\s-]*only|only (?:the )?(?:cell )?values|just (?:the )?(?:cell )?values"
    r"|plain values|values,? not (?:the )?formatting)\b"
)


@dataclass(frozen=True)
class Quantity:
    """A number a fact backs, and what it counts.

    Attributes:
        value: The number.
        noun: What it counts (``NOUN_*``).
        phase: Whether it was removed or is left (``PHASE_*``); ``PHASE_NONE`` for neither.
    """

    value: float
    noun: str
    phase: str = PHASE_NONE


@dataclass(frozen=True)
class Need:
    """What the reply must contain for a required fact to count as said.

    Satisfied when any one alternative matches: ``(pattern, negated)``, where ``negated`` means
    the match must be in a clause that says "not" (``buddy.claims``).

    Attributes:
        label: What is missing, in plain words, for the rewrite request and the log.
        alternatives: The patterns.
        case_sensitive: Match exactly as written (a file name); otherwise case is ignored.
    """

    label: str
    alternatives: tuple[tuple[str, bool], ...]
    case_sensitive: bool = False


@dataclass(frozen=True)
class Fact:
    """One thing the reply may say about the file or the work.

    Attributes:
        id: ``F1``, ``F2``... in sheet order.
        kind: ``KIND_*``.
        text: The code's own sentence for it. Never holds text from the user's file.
        required: The reply must say it (checked by ``needs``).
        quantities: The numbers it backs.
        names: The file names it gives, exactly.
        needs: What the checker looks for when it is required.
        file_text: Text from the user's file that goes with it (an analysis finding), shown to
            the model only quoted and capped at ``MAX_FILE_TEXT``; ``""`` for none.
    """

    id: str
    kind: str
    text: str
    required: bool = False
    quantities: tuple[Quantity, ...] = ()
    names: tuple[str, ...] = ()
    needs: tuple[Need, ...] = ()
    file_text: str = ""


@dataclass(frozen=True)
class FactSheet:
    """The VERIFIED RESULT for one turn.

    Attributes:
        turn: ``TURN_*``.
        facts: In the order the writer is shown them.
        complete: Everything asked for was done and confirmed (the template's "Done" lead word):
            only then may the reply say "done" without naming what.
        done: The objects a step removed or produced this turn, as verified.
        clear: The objects the delivered (or latest) file is measured to have none of.
        names: Every file name the reply may use.
        worked_on_copy: The turn worked on a copy handed back earlier (``FileUsed``); ``None``
            when no file was worked on.
    """

    turn: str
    facts: tuple[Fact, ...] = ()
    complete: bool = False
    done: frozenset[str] = frozenset()
    clear: frozenset[str] = frozenset()
    names: tuple[str, ...] = ()
    worked_on_copy: bool | None = None

    @property
    def file_work_ran(self) -> bool:
        """Whether this turn delivered anything a reply could describe as done."""
        return bool(self.done)

    def by_id(self) -> dict[str, Fact]:
        return {fact.id: fact for fact in self.facts}


def number_pattern(value: int) -> str:
    """A pattern for ``value`` written as digits or (up to twenty) as a word."""
    alternatives = [rf"(?<![\d.,]){value}(?![\d]|[.,]\d)"]
    if 0 <= value < len(_NUMBER_WORDS):
        alternatives.append(rf"\b{_NUMBER_WORDS[value]}\b")
    if value == 0:
        alternatives.append(r"\b(?:no|none|nothing)\b")
    return "|".join(alternatives)


def number_word_value(word: str) -> int | None:
    """The value of a spelled-out number up to twenty, else ``None``."""
    try:
        return _NUMBER_WORDS.index(word.casefold())
    except ValueError:
        return None


_NUMBER_IN_TEXT = re.compile(r"(?<![\w.])\d+(?:\.\d+)?")


def numbers_in(text: str) -> tuple[float, ...]:
    """Every number written in digits in ``text``."""
    return tuple(float(match.group(0)) for match in _NUMBER_IN_TEXT.finditer(text))


def _noun_for_target(target: str) -> str:
    return {DUPLICATE_ROWS_TARGET: NOUN_DUPLICATE, EMPTY_ROWS_TARGET: NOUN_EMPTY}.get(target, "")


def _words_for(noun: str) -> str:
    return {NOUN_DUPLICATE: DUPLICATE_WORDS, NOUN_EMPTY: EMPTY_WORDS}.get(noun, REPORT_WORDS)


def _capped(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= MAX_FILE_TEXT else text[: MAX_FILE_TEXT - 1].rstrip() + "…"


_STOP_WORDS = frozenset(
    "the and but for with from into onto this that these those then also please can could would "
    "you your me my our all any every each just only file files sheet sheets spreadsheet data "
    "row rows column columns it its them they make give want need get let".split()
)


def _phrase_stems(phrase: str) -> tuple[str, ...]:
    """The content words of the user's phrase for something not done, cut to four letters."""
    words = re.findall(r"[a-z]+", phrase.casefold())
    stems = []
    for word in words:
        if len(word) < 3 or word in _STOP_WORDS:
            continue
        stems.append(word[:4])
    return tuple(dict.fromkeys(stems))


class _Builder:
    """Collects facts and numbers them."""

    def __init__(self) -> None:
        self.facts: list[Fact] = []
        self.names: list[str] = []

    def add(self, kind: str, text: str, **kwargs: object) -> Fact:
        fact = Fact(id=f"F{len(self.facts) + 1}", kind=kind, text=text, **kwargs)  # type: ignore[arg-type]
        self.facts.append(fact)
        for name in fact.names:
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


# -- A verified delivery ------------------------------------------------------------------


def _delivered(request: SpeakingRequest) -> FactSheet:
    report, open_findings, notes = delivery_parts(request)
    builder = _Builder()
    done: set[str] = set()
    clear: set[str] = set()
    _action_facts(builder, report, done, clear)
    _state_facts(builder, report, clear)
    worked_on_copy = _file_fact(builder, request, report)
    builder.add(
        KIND_DISCLOSURE,
        VALUES_ONLY_SENTENCE,
        required=True,
        needs=(Need("that the copy keeps values only", ((VALUES_ONLY_WORDS, False),)),),
    )
    formulas = _values_only_sentences(report.original_facts)[1:]
    if formulas:
        facts = report.original_facts
        assert facts is not None
        quantities = [
            Quantity(value, noun)
            for value in (facts.formula_cells, facts.formula_cells_without_value)
            if value
            for noun in (NOUN_FORMULA, NOUN_ANY)
        ]
        builder.add(
            KIND_DISCLOSURE,
            " ".join(formulas),
            required=True,
            quantities=tuple(quantities),
            needs=(Need("what happened to the formulas", ((r"\bformula", False),)),),
        )
    if report.order_used:
        builder.add(
            KIND_DISCLOSURE,
            report.order_used,
            required=True,
            needs=(Need("the order the steps ran in", ((r"\border\b", False),)),),
        )
    if request.resumed:
        _resumed_fact(builder)
    if report.report_asked:
        if report.report_given:
            done.add(OBJECT_REPORT)
            builder.add(
                KIND_REPORT,
                "The report you asked for: what the check of the copy found, below.",
                required=True,
                needs=(Need("the report", ((REPORT_WORDS, False), (r"\bfound\b", False))),),
            )
            _finding_facts(builder, open_findings, notes, required=True)
    else:
        _finding_facts(builder, open_findings, notes, required=False)
    if not report.report_asked or report.report_given:
        verification_result = request.verification_result
        if _initial_analysis_failed(verification_result):
            _analysis_failed_fact(builder, _initial_analysis_failed_sentence(request))
        if _recheck_failed(verification_result):
            _analysis_failed_fact(builder, RECHECK_FAILED_SENTENCE)
    return FactSheet(
        turn=TURN_DELIVERED,
        facts=tuple(builder.facts),
        complete=report.lead_word == LEAD_DONE,
        done=frozenset(done),
        clear=frozenset(clear),
        names=tuple(builder.names),
        worked_on_copy=worked_on_copy,
    )


def _action_facts(
    builder: _Builder, report: OutcomeReport, done: set[str], clear: set[str]
) -> None:
    """A fact for each action: done, done though not asked for, and not done."""
    acted = [a for a in report.actions if a.status in (DONE, DONE_UNREQUESTED)]
    also = False
    for action in acted:
        wording = ACTIONS_BY_CAPABILITY.get(action.capability)
        if wording is None or not wording.changes_file:
            continue
        noun = _noun_for_target(wording.target)
        measurement = action.measurement
        removed = measurement.removed if measurement is not None else None
        quantities: list[Quantity] = []
        needs = [Need(f"the {wording.object}", ((_words_for(noun), False),))]
        if removed is not None:
            quantities += [
                Quantity(removed, noun, PHASE_REMOVED),
                Quantity(removed, NOUN_ROWS, PHASE_REMOVED),
            ]
            blank = measurement.removed_blank if measurement is not None else None
            if blank:
                quantities += [
                    Quantity(blank, NOUN_EMPTY, PHASE_REMOVED),
                    Quantity(blank, NOUN_DUPLICATE, PHASE_REMOVED),
                    Quantity(blank, NOUN_ROWS, PHASE_REMOVED),
                ]
            needs.append(
                Need(f"how many {wording.rows} were removed", ((number_pattern(removed), False),))
            )
        if removed == 0:
            # Nothing to remove: the file is measured to have none, but nothing was removed.
            clear.add(noun)
        else:
            done.add(noun)
            clear.add(noun)
        if action.status == DONE:
            text = _action_sentence(action)
            kind = KIND_DONE
        else:
            text = _unrequested_sentence(action, also=also)
            kind = KIND_ALSO_DONE
        also = True
        builder.add(kind, text, required=True, quantities=tuple(quantities), needs=tuple(needs))
    removed_counts = [
        a.measurement.removed
        for a in acted
        if a.measurement is not None and a.measurement.removed is not None
    ]
    if len(removed_counts) > 1:
        total = sum(removed_counts)
        builder.add(
            KIND_STATE,
            f"In all, {total} rows were removed.",
            quantities=(Quantity(total, NOUN_ROWS, PHASE_REMOVED),),
        )
    for action in report.actions:
        if action.status != NOT_DONE:
            continue
        builder.add(
            KIND_NOT_DONE,
            _not_done_sentence(action),
            required=True,
            needs=(_not_done_need(action.capability, action.reason, action.user_phrase),),
        )
    if (
        any(
            a.status == NOT_DONE and a.capability not in ACTIONS_BY_CAPABILITY
            for a in report.actions
        )
        and report.brain_reading
    ):
        builder.add(KIND_STATE, f"I read your request as: {' and '.join(report.brain_reading)}.")


def _not_done_need(capability: str, reason: str | None, user_phrase: str = "") -> Need:
    """What a reply must say for something that wasn't done: that it wasn't, in a "not" clause."""
    if reason == NO_SKILL_UNSURE:
        return Need(
            "that anything beyond what was done wasn't done",
            ((r"\b(?:beyond|else|more|extra|other|rest|anything)\b", True),),
        )
    if reason == NO_SKILL:
        stems = _phrase_stems(user_phrase)
        if stems:
            return Need(
                "that this part wasn't done: " + ", ".join(stems),
                tuple((rf"\b{re.escape(stem)}", True) for stem in stems),
            )
        return Need("that part of the request can't be done yet", ((_INABILITY, True),))
    wording = ACTIONS_BY_CAPABILITY.get(capability)
    noun = _noun_for_target(wording.target) if wording is not None else ""
    if wording is None or not wording.changes_file:
        return Need("that the report wasn't produced", ((REPORT_WORDS, True),))
    return Need(f"that the {wording.step} didn't run", ((_words_for(noun), True),))


def _state_facts(builder: _Builder, report: OutcomeReport, clear: set[str]) -> None:
    """What the delivered file is measured to hold now."""
    sentence = _final_state_sentence(report.final_state)
    if sentence:
        quantities: list[Quantity] = []
        for measurement in report.final_state:
            noun = _noun_for_target(measurement.target)
            if not noun:
                continue
            quantities.append(Quantity(measurement.output_rows, NOUN_ROWS, PHASE_LEFT))
            quantities.append(Quantity(measurement.remaining, noun, PHASE_LEFT))
            if measurement.remaining == 0:
                clear.add(noun)
        builder.add(KIND_STATE, sentence, quantities=tuple(dict.fromkeys(quantities)))
    residual_sentences = _residual_sentences(report.residuals, after_final_state=bool(sentence))
    for measurement, text in zip(report.residuals, residual_sentences, strict=False):
        noun = _noun_for_target(measurement.target)
        if measurement.remaining == 0 and noun:
            clear.add(noun)
        builder.add(
            KIND_STATE,
            text,
            quantities=(Quantity(measurement.remaining, noun or NOUN_ROWS, PHASE_LEFT),),
        )


def _file_fact(builder: _Builder, request: SpeakingRequest, report: OutcomeReport) -> bool | None:
    """Which file the turn worked on, by name. Returns whether it was a copy handed back earlier."""
    used = request.file_used
    if used is None:
        name = _name_in_reply(report.delivered_file)
        if not name:
            return None
        builder.add(
            KIND_FILE,
            f"Your cleaned copy of {name} is ready.",
            required=True,
            names=(name,),
            needs=(
                Need(
                    f"the file name {name} exactly",
                    ((re.escape(name), False),),
                    case_sensitive=True,
                ),
            ),
        )
        return False
    on_copy = used.kind == FILE_KIND_COPY
    name = _name_in_reply(used.copy_name if on_copy else Path(used.path).name)
    needs: list[Need] = []
    if name:
        needs.append(
            Need(f"the file name {name} exactly", ((re.escape(name), False),), case_sensitive=True)
        )
    else:
        needs.append(
            Need(
                "which file was used" + (" (the previous copy)" if on_copy else " (your original)"),
                ((r"\b(?:previous|earlier|last|latest) copy\b", False),)
                if on_copy
                else ((r"\boriginal\b", False),),
            )
        )
    if not on_copy and used.copy_skipped:
        needs.append(Need("that it was your original, not the copy", ((r"\boriginal\b", False),)))
    builder.add(
        KIND_FILE,
        _file_used_sentence(used),
        required=True,
        names=(name,) if name else (),
        needs=tuple(needs),
    )
    return on_copy


def _finding_facts(
    builder: _Builder,
    open_findings: tuple[str, ...],
    notes: list[str] | tuple[str, ...],
    *,
    required: bool,
) -> None:
    """The analysis findings (text from the user's file, quoted) and its all-clear notes."""
    for finding in open_findings[:MAX_FINDINGS]:
        quoted = _capped(finding)
        numbers = numbers_in(quoted)
        needs: tuple[Need, ...] = ()
        if required and numbers:
            first = numbers[0]
            pattern = number_pattern(int(first)) if first == int(first) else re.escape(f"{first:g}")
            needs = (Need("each finding of the report", ((pattern, False),)),)
        builder.add(
            KIND_FINDING,
            "Something the check of the file noticed (quoted below; it may name a column or a "
            "value from the file).",
            required=required and bool(needs),
            quantities=tuple(
                Quantity(n, NOUN_ANY, phase)
                for n in dict.fromkeys(numbers)
                for phase in (PHASE_NONE, PHASE_LEFT)
            ),
            needs=needs,
            file_text=quoted,
        )
    more = len(open_findings) - MAX_FINDINGS
    if more > 0:
        builder.add(
            KIND_FINDING,
            f"{more} more things the check noticed, not listed here.",
            quantities=(Quantity(more, NOUN_ANY),),
        )
    for note in notes:
        builder.add(
            KIND_FINDING,
            note[0].upper() + note[1:] + ".",
            required=required,
            needs=(
                Need("what the check found", ((r"\b(?:no|none|nothing|didn'?t find)\b", False),)),
            )
            if required
            else (),
        )


def _analysis_failed_fact(builder: _Builder, sentence: str) -> None:
    builder.add(
        KIND_DISCLOSURE,
        sentence,
        required=True,
        needs=(
            Need(
                "that the check of the file couldn't run",
                ((r"\b(?:analy\w*|re-?check\w*|check\w*|look\w*)\b", True),),
            ),
        ),
    )


def _resumed_fact(builder: _Builder) -> None:
    builder.add(
        KIND_DISCLOSURE,
        RESUMED_SENTENCE,
        required=True,
        needs=(
            Need(
                "that you went on with their earlier request",
                ((r"\b(?:earlier|previous|last|first|original|waiting)\b", False),),
            ),
        ),
    )


def _no_changes_fact(builder: _Builder) -> None:
    builder.add(
        KIND_NO_CHANGES,
        NO_CHANGES_SENTENCE,
        required=True,
        needs=(
            Need(
                "that no file was changed",
                ((_UNCHANGED, True), (_UNCHANGED_PLAIN, False)),
            ),
        ),
    )


# -- The latest copy already had nothing to remove -----------------------------------------


def _nothing_left_sheet(request: SpeakingRequest) -> FactSheet:
    used = request.file_used
    assert used is not None
    builder = _Builder()
    actions = already_satisfied(request.execution_result, request.verification_result)
    rows = " or ".join(dict.fromkeys(action.rows for action in actions))
    name = _name_in_reply(used.copy_name)
    nouns = {_noun_for_target(action.target) for action in actions} - {""}
    needs = [
        Need("that there was nothing left to remove", ((r"\b(?:already|nothing|no)\b", False),))
    ]
    if name:
        needs.append(
            Need(f"the file name {name} exactly", ((re.escape(name), False),), case_sensitive=True)
        )
    builder.add(
        KIND_NOTHING_LEFT,
        NOTHING_LEFT_TEMPLATE.format(file=f"`{name}`" if name else "the copy", rows=rows),
        required=True,
        names=(name,) if name else (),
        quantities=tuple(
            Quantity(0, noun, phase)
            for noun in sorted(nouns)
            for phase in (PHASE_LEFT, PHASE_REMOVED)
        ),
        needs=tuple(needs),
    )
    outcome = build_outcome_report(
        request.execution_result, request.verification_result, request.understanding
    )
    for action in outcome.actions:
        if action.status == NOT_DONE and action.capability != "spreadsheet.analyze":
            builder.add(
                KIND_NOT_DONE,
                _not_done_sentence(action),
                required=True,
                needs=(_not_done_need(action.capability, action.reason, action.user_phrase),),
            )
    done: set[str] = set()
    if outcome.report_asked:
        findings = _analysis_findings(request)
        if outcome.report_given and findings is not None:
            done.add(OBJECT_REPORT)
            builder.add(
                KIND_REPORT,
                "The report you asked for: what the check of the file I worked on found, below.",
                required=True,
                needs=(Need("the report", ((REPORT_WORDS, False), (r"\bfound\b", False))),),
            )
            _finding_facts(builder, findings[0], findings[1], required=True)
        else:
            builder.add(
                KIND_NOT_DONE,
                report_unavailable_sentence(outcome) or REPORT_NOT_RUN_SENTENCE,
                required=True,
                needs=(Need("that the report wasn't produced", ((REPORT_WORDS, True),)),),
            )
    if request.resumed:
        _resumed_fact(builder)
    _no_changes_fact(builder)
    return FactSheet(
        turn=TURN_NOTHING_LEFT,
        facts=tuple(builder.facts),
        done=frozenset(done),
        clear=frozenset(nouns),
        names=tuple(builder.names),
        worked_on_copy=True,
    )


# -- Nothing ran, and it was a chat -------------------------------------------------------


def _chat(request: SpeakingRequest) -> FactSheet:
    builder = _Builder()
    style = style_for(effective_mode(request.mode, request.understanding.user_state))
    builder.add(KIND_CAN_DO, CAPABILITIES[style])
    if not request.has_file:
        builder.add(KIND_NO_FILE, "No spreadsheet has been uploaded in this chat yet.")
    return FactSheet(turn=TURN_CHAT, facts=tuple(builder.facts))


# -- Nothing ran, and it wasn't a chat ----------------------------------------------------


def _no_change(request: SpeakingRequest) -> FactSheet:
    """The facts of ``TemplateSpeaker._no_change_reply``, in its order."""
    builder = _Builder()
    style = style_for(effective_mode(request.mode, request.understanding.user_state))
    seed = f"{request.session_id}:{request.turn_index}"
    names = _used_name(request)
    if not request.has_file:
        builder.add(
            KIND_NO_FILE,
            NO_FILE_SENTENCE,
            required=True,
            needs=(
                Need(
                    "that there's no file to work on yet",
                    (
                        (
                            r"\b(?:upload\w*|send|share|drop|attach\w*|no file|don'?t have"
                            r"|haven'?t got)\b",
                            False,
                        ),
                    ),
                ),
            ),
        )
        return FactSheet(turn=TURN_NO_CHANGE, facts=tuple(builder.facts))
    if request.file_refusal:
        refusal = REFUSED_TEMPLATE.format(reason=request.file_refusal)
        builder.add(
            KIND_REFUSED,
            refusal,
            required=True,
            # The refusal's own numbers ("the workbook has 2 sheets", "Excel 97–2003").
            quantities=tuple(
                Quantity(n, NOUN_ANY, phase)
                for n in dict.fromkeys(numbers_in(refusal))
                for phase in (PHASE_NONE, PHASE_LEFT)
            ),
            needs=(Need("that this file can't be worked on", ((_INABILITY, True),)),),
        )
        _no_changes_fact(builder)
        return FactSheet(turn=TURN_NO_CHANGE, facts=tuple(builder.facts), names=names)
    reasoning = request.reasoning
    understanding = request.understanding
    outcome = build_outcome_report(
        request.execution_result, request.verification_result, understanding
    )
    asked = False
    if reasoning is not None and reasoning.clarification_required:
        if reasoning.clarification_reason == CLARIFY_FAILED:
            _understanding_failed_fact(builder)
        else:
            builder.add(
                KIND_QUESTION,
                clarification_question(reasoning.clarification_reason, style, seed),
                required=True,
                needs=(Need("the question", ((r"\?", False),)),),
            )
            asked = True
    elif understanding.intent is None:
        _understanding_failed_fact(builder)
    else:
        stated = 0
        for action in outcome.actions:
            if action.status != NOT_DONE or action.capability == "spreadsheet.analyze":
                continue
            stated += 1
            builder.add(
                KIND_NOT_DONE,
                _not_done_sentence(action, nothing_done=True),
                required=True,
                needs=(_not_done_need(action.capability, action.reason, action.user_phrase),),
            )
        if stated or not outcome.report_asked:
            if not stated:
                builder.add(
                    KIND_NOT_UNDERSTOOD,
                    NOT_UNDERSTOOD_SENTENCE,
                    required=True,
                    needs=(
                        Need(
                            "that you didn't read it as something you can do",
                            (
                                (_INABILITY, True),
                                (r"\b(?:read|get|got|catch|understand|follow|sure|know)\b", True),
                            ),
                        ),
                    ),
                )
            builder.add(KIND_CAN_DO, CAPABILITIES[style])
    done: set[str] = set()
    findings = _analysis_findings(request)
    if outcome.report_asked:
        if outcome.report_given and findings is not None:
            done.add(OBJECT_REPORT)
            builder.add(
                KIND_REPORT,
                "The report you asked for: what the check of the file found, below. No file "
                "was changed.",
                required=True,
                needs=(Need("the report", ((REPORT_WORDS, False), (r"\bfound\b", False))),),
            )
            _finding_facts(builder, findings[0], findings[1], required=True)
            if request.resumed:
                _resumed_fact(builder)
        else:
            builder.add(
                KIND_NOT_DONE,
                report_unavailable_sentence(outcome) or REPORT_NOT_RUN_SENTENCE,
                required=True,
                needs=(Need("that the report wasn't produced", ((REPORT_WORDS, True),)),),
            )
    elif findings is not None and (findings[0] or findings[1]):
        _finding_facts(builder, findings[0], findings[1], required=False)
    if _initial_analysis_failed(request.verification_result) and not outcome.report_asked:
        _analysis_failed_fact(builder, _initial_analysis_failed_sentence(request))
    if not asked:
        _no_changes_fact(builder)
    return FactSheet(
        turn=TURN_NO_CHANGE, facts=tuple(builder.facts), done=frozenset(done), names=names
    )


def _understanding_failed_fact(builder: _Builder) -> None:
    builder.add(
        KIND_NOT_UNDERSTOOD,
        UNDERSTANDING_FAILED_SENTENCE,
        required=True,
        needs=(
            Need(
                "that you couldn't work out the request and they should try again",
                ((r"\b(?:again|rephrase|retry)\b", False),),
            ),
        ),
    )


def _used_name(request: SpeakingRequest) -> tuple[str, ...]:
    used = request.file_used
    if used is None:
        return ()
    name = _name_in_reply(used.copy_name if used.kind == FILE_KIND_COPY else Path(used.path).name)
    return (name,) if name else ()


__all__ = [
    "Fact",
    "FactSheet",
    "Need",
    "Quantity",
    "build_fact_sheet",
]

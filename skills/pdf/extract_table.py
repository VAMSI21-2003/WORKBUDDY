"""Extract table — turns the one plain table of a PDF into a new spreadsheet.

A PDF with a text layer that holds exactly one plain table (every cell drawn with a border, no cell
spanning two columns or two rows) becomes a new ``.xlsx`` under ``workspace/outputs/`` (or
``task.output_dir``). The PDF is only read, as bytes: never written, never opened by a library in
this process. The reading happens in a separate, locked-down process (``skills.pdf.sandbox``) that
uses pdfplumber. Whether what it read is right is not this skill's call: the Inspector re-reads the
PDF with a different parser (``buddy.inspector.PdfTableVerifier``) and compares every cell.

Everything that can be refused is refused with a fixed sentence (``REFUSALS``): nothing from the
file, and no exception's text, ever reaches the reply. See ``docs/design/pdf-extract.md``.

The spreadsheet holds text only: each cell is the words the PDF prints in that cell, so
``007`` stays ``007`` and ``1,234`` stays ``1,234``. Nothing is converted to a number or a date,
and no cell can be a formula (a cell reading ``=SUM(A1)`` is stored as that text).
"""

import logging
from pathlib import Path

import openpyxl

from skills.base import PermissionTier, Skill, SkillResult, SkillStatus, SkillTask
from skills.excel.workbook import discard_partial_output
from skills.pdf import sandbox
from skills.pdf._worker import textnorm

logger = logging.getLogger(__name__)

CAPABILITY = "pdf.extract_table"

# The sheet the table goes on.
SHEET_NAME = "Table"

# Why a PDF is refused, by the worker's (or the sandbox's) reason code. Fixed text: it reaches the
# user's reply, so it never carries a word read from the file or an exception's message.
REFUSALS: dict[str, str] = {
    "encrypted": "the PDF is encrypted, and reading encrypted PDFs isn't supported for now",
    "no_text": (
        "the PDF has no text layer (it looks like a scan), and reading scanned pages isn't "
        "supported for now"
    ),
    "no_table": (
        "I didn't find a table in the PDF, and only tables drawn with a border around every "
        "cell are supported for now"
    ),
    "many_tables": (
        "the PDF has more than one table (a table that runs over several pages counts as "
        "several), and only a single table is supported for now"
    ),
    "not_plain": (
        "the table has merged or spanning cells, or cells that don't line up in a grid, and "
        "only plain tables are supported for now"
    ),
    "one_column": (
        "the table has a single column, which usually means its columns aren't ruled, and only "
        "tables with a border around every cell are supported for now"
    ),
    "text_overflows": (
        "some text in the table runs past its cell's border, so a cell can't be read whole"
    ),
    "too_many_pages": (
        f"the PDF has more than {sandbox.MAX_PAGES} pages, and only PDFs of up to "
        f"{sandbox.MAX_PAGES} pages are supported"
    ),
    "too_large": (
        "the table is too large to extract: it has too many cells, or a cell with too much text"
    ),
    "unreadable": "the PDF couldn't be read; it may be damaged or not a PDF",
    "bad_output": "the PDF couldn't be read; it may be damaged or not a PDF",
    "crashed": "the PDF couldn't be read; it may be damaged or not a PDF",
    "timeout": "the PDF took too long to read",
    "busy": "PDF reading is busy right now, so this one wasn't read; trying again shortly may work",
    "sandbox_unavailable": "reading PDFs isn't available on this server",
}


class PdfExtractTableSkill(Skill):
    """Extracts the one plain table of a PDF into a new spreadsheet.

    Write tier: it creates a file in the workspace, so the user is asked before it runs, like the
    row-cleaning skills. (``docs/BUDDY_SKILLS.md`` lists PDF text extraction as Read tier because
    it changes nothing; this skill hands back a new file, which is what the Write tier is for.)
    """

    @property
    def name(self) -> str:
        """The skill's unique, human-readable identifier."""
        return "pdf.extract_table"

    @property
    def description(self) -> str:
        """A plain-language description of what this skill does."""
        return (
            "Extracts the one plain table (a grid with a border around every cell) from a PDF "
            "that has a text layer into a new spreadsheet. Refuses a scanned PDF, an encrypted "
            "one, one with more than one table, or a table with merged cells."
        )

    @property
    def supported_inputs(self) -> tuple[str, ...]:
        """The capabilities this skill declares support for (not file extensions)."""
        return (CAPABILITY,)

    @property
    def permission_tier(self) -> PermissionTier:
        """The permission tier required to invoke this skill."""
        return PermissionTier.WRITE

    def can_handle(self, task: SkillTask) -> bool:
        """True when ``task`` has exactly one input reference, and it names a ``.pdf``."""
        if len(task.input_references) != 1:
            return False
        return task.input_references[0].lower().endswith(".pdf")

    def execute(self, task: SkillTask) -> SkillResult:
        """Read the PDF in the sandbox and write its table to a new ``.xlsx``.

        Any refusal or error is a ``SkillResult`` with a fixed sentence in ``errors``; nothing is
        raised, and a refused PDF leaves no file behind.
        """
        if not self.can_handle(task):
            return SkillResult(
                status=SkillStatus.FAILURE,
                errors=[f"cannot handle task {task.task_id!r}: expected exactly one .pdf input"],
            )
        input_path = task.input_references[0]
        try:
            with open(input_path, "rb") as handle:  # READ-ONLY
                data = handle.read(sandbox.MAX_PDF_BYTES + 1)
        except OSError:
            logger.exception("PdfExtractTableSkill: couldn't read %r", input_path)
            return _refused("unreadable")
        if len(data) > sandbox.MAX_PDF_BYTES:
            return _refused("too_large")

        outcome = sandbox.run("extract", data)
        if not outcome.ok:
            logger.info("PdfExtractTableSkill: refused %r: %s", input_path, outcome.reason)
            return _refused(outcome.reason)
        rows = _validated_rows(outcome.data.get("rows"))
        if rows is None:
            logger.warning("PdfExtractTableSkill: the worker's answer was not a table")
            return _refused("bad_output")

        output_path = self._build_output_path(task)
        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            _write_text_cells(rows, output_path)
        except Exception:  # noqa: BLE001 - surfaced as a SkillResult, not raised
            logger.exception("PdfExtractTableSkill: failed to write %r", output_path)
            discard_partial_output(output_path)
            return SkillResult(
                status=SkillStatus.FAILURE, errors=["could not write the spreadsheet"]
            )
        return SkillResult(
            status=SkillStatus.SUCCESS,
            outputs=[str(output_path)],
            summary=(
                f"Extracted a table of {len(rows)} rows and {len(rows[0])} columns from "
                f"{Path(input_path).name}."
            ),
            metadata={"rows": len(rows), "columns": len(rows[0])},
        )

    def verify(self, task: SkillTask, result: SkillResult) -> bool:
        """The skill's own well-formedness check: one existing, non-empty ``.xlsx`` output.

        That the table is the PDF's table is not judged here; the Inspector's verifier does that
        with a second parser.
        """
        if result.status is not SkillStatus.SUCCESS or len(result.outputs) != 1:
            return False
        output_path = Path(result.outputs[0])
        return (
            output_path.suffix.lower() == ".xlsx"
            and output_path.exists()
            and output_path.stat().st_size > 0
        )

    @staticmethod
    def _build_output_path(task: SkillTask) -> Path:
        """A fresh path for the spreadsheet: never the PDF's, unique to this task."""
        name = f"{Path(task.input_references[0]).stem}_table_{task.task_id}.xlsx"
        if task.output_dir is not None:
            return Path(task.output_dir) / name
        return Path("workspace") / "outputs" / task.workspace_id / name


def _refused(reason: str) -> SkillResult:
    return SkillResult(
        status=SkillStatus.FAILURE, errors=[REFUSALS.get(reason, REFUSALS["unreadable"])]
    )


def _validated_rows(raw: object) -> list[list[str]] | None:
    """The worker's rows if they are a rectangular table of short strings, else ``None``.

    The worker is trusted no further than its answer's shape: a list of equal-length, non-empty
    lists of strings, within the size limits, each cell already in normal form.
    """
    if not isinstance(raw, list) or not raw:
        return None
    width = len(raw[0]) if isinstance(raw[0], list) else 0
    if width == 0 or width * len(raw) > textnorm.MAX_CELLS:
        return None
    rows = []
    for row in raw:
        if not isinstance(row, list) or len(row) != width:
            return None
        for cell in row:
            if not isinstance(cell, str) or len(cell) > textnorm.MAX_CELL_CHARS:
                return None
            if textnorm.normalize_cell(cell) != cell:
                return None
        rows.append(row)
    return rows


def _write_text_cells(rows: list[list[str]], output_path: Path) -> None:
    """Write ``rows`` to ``output_path`` as text cells; an empty string is an empty cell.

    ``data_type`` is set after the value, because openpyxl would otherwise turn a string that
    starts with ``=`` into a formula.
    """
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = SHEET_NAME
    for row_number, row in enumerate(rows, start=1):
        for column_number, text in enumerate(row, start=1):
            if text == "":
                continue
            cell = sheet.cell(row=row_number, column=column_number)
            cell.value = text
            cell.data_type = "s"
    workbook.save(output_path)

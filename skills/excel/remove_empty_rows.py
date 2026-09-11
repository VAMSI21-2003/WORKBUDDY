"""Remove empty rows — removes fully-empty rows from an uploaded spreadsheet."""

import logging
from pathlib import Path

import pandas as pd

from skills.base import PermissionTier, Skill, SkillResult, SkillStatus, SkillTask

logger = logging.getLogger(__name__)

_SUPPORTED_EXTENSIONS = (".xlsx", ".xls")


class ExcelRemoveEmptyRowsSkill(Skill):
    """Removes fully-empty rows from an uploaded spreadsheet.

    Same shape as ``ExcelDeduplicateSkill`` (see
    ``skills/excel/dedupe.py``): reads the uploaded ``.xlsx``/``.xls``
    file read-only, drops rows where every column is empty, and writes
    the result to a freshly constructed path under
    ``workspace/outputs/{workspace_id}/`` — never in place, and never
    back to the original upload. See ``skills/excel/dedupe.py`` for the
    fuller read/write design notes this skill deliberately reuses
    unchanged.
    """

    @property
    def name(self) -> str:
        """The skill's unique, human-readable identifier."""
        return "excel.remove_empty_rows"

    @property
    def description(self) -> str:
        """A plain-language description of what this skill does."""
        return "Removes fully-empty rows from an uploaded spreadsheet."

    @property
    def supported_inputs(self) -> tuple[str, ...]:
        """The capabilities this skill declares support for.

        A capability string, not a file extension — same convention
        ``ExcelDeduplicateSkill`` uses.
        """
        return ("spreadsheet.remove_empty_rows",)

    @property
    def permission_tier(self) -> PermissionTier:
        """The permission tier required to invoke this skill."""
        return PermissionTier.WRITE

    def can_handle(self, task: SkillTask) -> bool:
        """Determine whether this skill is capable of performing a task.

        Args:
            task: The task under consideration.

        Returns:
            True if ``task`` has exactly one input reference ending in a
            supported spreadsheet extension, False otherwise.
        """
        if len(task.input_references) != 1:
            return False
        return task.input_references[0].lower().endswith(_SUPPORTED_EXTENSIONS)

    def execute(self, task: SkillTask) -> SkillResult:
        """Remove fully-empty rows from the uploaded spreadsheet.

        Reads ``task.input_references[0]`` read-only via
        ``pandas.read_excel`` and writes the cleaned result to a new path
        under ``workspace/outputs/{workspace_id}/``. The original upload
        is never opened with write intent, and the output path is always
        freshly constructed — never derived from, or equal to, the input
        path.

        Args:
            task: The task to execute.

        Returns:
            A ``SkillResult`` describing the outcome. Any error (unreadable
            input, unwritable output) is captured in the result rather
            than raised.
        """
        if not self.can_handle(task):
            return SkillResult(
                status=SkillStatus.FAILURE,
                errors=[
                    f"cannot handle task {task.task_id!r}: expected exactly one input "
                    f"reference ending in {_SUPPORTED_EXTENSIONS}"
                ],
            )

        input_path = task.input_references[0]
        try:
            original = pd.read_excel(input_path)  # READ-ONLY open of the upload
        except Exception as exc:  # noqa: BLE001 — surfaced as a SkillResult, not raised
            logger.exception("ExcelRemoveEmptyRowsSkill: failed to read %r", input_path)
            return SkillResult(
                status=SkillStatus.FAILURE, errors=[f"could not read {input_path!r}: {exc}"]
            )

        cleaned = original.dropna(how="all")
        output_path = self._build_output_path(task)

        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            cleaned.to_excel(output_path, index=False)  # WRITE only ever targets output_path
        except Exception as exc:  # noqa: BLE001
            logger.exception("ExcelRemoveEmptyRowsSkill: failed to write %r", output_path)
            return SkillResult(
                status=SkillStatus.FAILURE, errors=[f"could not write {output_path!r}: {exc}"]
            )

        removed_count = len(original) - len(cleaned)
        return SkillResult(
            status=SkillStatus.SUCCESS,
            outputs=[str(output_path)],
            summary=f"Removed {removed_count} empty row(s) from {Path(input_path).name}.",
            metadata={
                "removed_count": removed_count,
                "original_row_count": len(original),
                "output_row_count": len(cleaned),
            },
        )

    def verify(self, task: SkillTask, result: SkillResult) -> bool:
        """Check that ``result`` is well-formed and consistent with ``task``.

        This is the skill's own self-check — it confirms the result is
        well-formed, not that no empty rows remain. That claim belongs
        entirely to Buddy's independent Verify stage
        (``buddy.inspector.SpreadsheetEmptyRowsVerifier``), precisely so
        the skill's self-report is never the thing that decides success.

        Args:
            task: The task the result was produced for.
            result: The result to verify.

        Returns:
            True if ``result`` reports success, has exactly one output
            path, and that path exists on disk with nonzero size.
        """
        if result.status is not SkillStatus.SUCCESS:
            return False
        if len(result.outputs) != 1:
            return False
        output_path = Path(result.outputs[0])
        return output_path.exists() and output_path.stat().st_size > 0

    @staticmethod
    def _build_output_path(task: SkillTask) -> Path:
        """Construct a fresh output path under ``workspace/outputs/``.

        Always a new path, scoped to the task's workspace and unique to
        this task, so it can never collide with — or be mistaken for —
        the original upload. Same naming convention
        ``ExcelDeduplicateSkill`` uses, with a suffix identifying this
        skill's own operation.

        Args:
            task: The task to build an output path for.

        Returns:
            The output path. Its parent directory is not yet created.
        """
        input_stem = Path(task.input_references[0]).stem
        return (
            Path("workspace")
            / "outputs"
            / task.workspace_id
            / f"{input_stem}_no_empty_rows_{task.task_id}.xlsx"
        )

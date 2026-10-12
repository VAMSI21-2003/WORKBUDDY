"""The "which file I worked on" sentences, as a delivered reply words them for a named file."""

from __future__ import annotations

from buddy.voice_catalog import (
    WORKED_ON_ORIGINAL_SKIPPING_COPY_TEMPLATE,
    WORKED_ON_ORIGINAL_TEMPLATE,
)

DEFAULT_UPLOAD = "report.xlsx"


def worked_on_original(name: str = DEFAULT_UPLOAD) -> str:
    return WORKED_ON_ORIGINAL_TEMPLATE.format(file=f"`{name}`")


def worked_on_original_skipping_copy(name: str = DEFAULT_UPLOAD) -> str:
    return WORKED_ON_ORIGINAL_SKIPPING_COPY_TEMPLATE.format(file=f"`{name}`")

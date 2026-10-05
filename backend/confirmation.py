"""Confirmation — what answers ``Hands``' "may this file-changing step run?".

Until PR-3 adds the real confirm/reject flow (``WebConfirmationProvider``
and its endpoint), the answer is fixed per server by
``Settings.auto_approve`` (design Section 8.3, PR-2): on, every Write-tier
step is approved; off (the default), every one is declined, so no file is
changed and the reply comes from the pipeline's decline path. The feature
that is on by default is therefore never a silent "approve everything".
"""

from __future__ import annotations

from buddy.hands import ConfirmationProvider
from skills.base import PermissionTier, SkillTask


class StaticConfirmationProvider:
    """Answers every confirmation the same way. One per turn."""

    def __init__(self, approve: bool) -> None:
        self._approve = approve

    def confirm(self, task: SkillTask, tier: PermissionTier) -> bool:
        return self._approve


def confirmation_provider_for_turn(auto_approve: bool) -> ConfirmationProvider:
    return StaticConfirmationProvider(auto_approve)

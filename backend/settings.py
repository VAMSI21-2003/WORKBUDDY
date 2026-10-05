"""Settings — every path and limit the API enforces, read once at startup.

Each value here is read by the code that enforces it *and* by the session
response's ``limits`` (``docs/design/backend-api-design.md``, "Decisions
(2026-09-30, part 2)", ask 2), so the two can't disagree.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

MIB = 1024 * 1024

# The repo root: the default data root is ``<repo>/workspace``, whatever the
# current working directory is.
_REPO_ROOT = Path(__file__).resolve().parent.parent

# 2 MiB (design "Decisions (2026-10-04)", item 3). Live run 9 measured a
# 5 MB file at 256 s for one step and 308 s for a two-step chain, past the
# 180 s deadline; the design's evidence rule ("Decisions (2026-09-30, part
# 2)", item 3) then sets 2 MB. WORKBUDDY_MAX_UPLOAD_BYTES overrides it.
DEFAULT_MAX_UPLOAD_BYTES = 2 * MIB
# The raw request body may exceed the file by the multipart framing (part
# headers, boundaries). Anything beyond this is cut off before parsing.
BODY_OVERHEAD_BYTES = 64 * 1024

# Owner decision (design "Decisions (2026-10-04)"): ``.xlsx`` only. This is
# the upload validator's allowlist itself, not a copy of it.
ACCEPTED_EXTENSIONS: tuple[str, ...] = (".xlsx",)

# The longest a plan may wait for its answer: an hour.
MAX_CONFIRMATION_TIMEOUT_S = 3600

# Read by PR-2 only. Since PR-3 every file-changing step asks the user, and
# approving without asking is a test setting that no variable turns on.
RETIRED_AUTO_APPROVE_VARIABLE = "WORKBUDDY_AUTO_APPROVE"

logger = logging.getLogger(__name__)


class SettingsError(ValueError):
    """A setting is missing or invalid. The app refuses to start."""


@dataclass(frozen=True)
class Settings:
    """The API's configuration.

    Attributes:
        data_dir: The data root. Made absolute at construction, because the
            skills' legacy default path is relative to the working directory.
        max_upload_bytes: The largest upload accepted, in bytes.
        max_message_chars: The longest turn message, after trimming.
        max_zip_entries: The most entries an ``.xlsx`` may have.
        max_zip_uncompressed_bytes: The largest total declared uncompressed
            size of an ``.xlsx``'s entries.
        turn_deadline_s: Wall-clock seconds a turn may run before it's
            finalized as failed, not counting the time its plan waits for
            the user's answer.
        confirmation_timeout_s: Seconds a turn's plan waits for the user's
            answer, at most ``MAX_CONFIRMATION_TIMEOUT_S``. No answer in time
            is a rejection.
        max_concurrent_turns: Turns that may be running at once, across all
            sessions. A turn counts until its worker thread exits.
        busy_retry_after_s: The ``Retry-After`` hint on ``429 server_busy``.
        auto_approve: A test setting: approve every file-changing step
            without asking. Off by default, and never read from the
            environment, so a server always asks the user (design Section
            8.3, PR-3). Only tests that aren't about confirmation turn it on.
    """

    data_dir: Path
    max_upload_bytes: int = DEFAULT_MAX_UPLOAD_BYTES
    max_message_chars: int = 4000
    max_zip_entries: int = 1000
    max_zip_uncompressed_bytes: int = 200 * MIB
    turn_deadline_s: float = 180.0
    confirmation_timeout_s: float = 120.0
    max_concurrent_turns: int = 2
    busy_retry_after_s: int = 10
    auto_approve: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "data_dir", Path(self.data_dir).expanduser().resolve())
        for name in (
            "max_upload_bytes",
            "max_message_chars",
            "max_zip_entries",
            "max_zip_uncompressed_bytes",
            "max_concurrent_turns",
            "busy_retry_after_s",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise SettingsError(f"{name} must be a positive integer")
        for name in ("turn_deadline_s", "confirmation_timeout_s"):
            if not getattr(self, name) > 0:
                raise SettingsError(f"{name} must be positive")
        if self.confirmation_timeout_s > MAX_CONFIRMATION_TIMEOUT_S:
            # Far beyond any wait a person makes, and it keeps the wait inside
            # what a thread can wait for (review round 1, L7).
            raise SettingsError(
                f"confirmation_timeout_s must be at most {MAX_CONFIRMATION_TIMEOUT_S}"
            )

    @property
    def accepted_extensions(self) -> tuple[str, ...]:
        """The upload extensions accepted: ``.xlsx`` only (owner decision)."""
        return ACCEPTED_EXTENSIONS

    @property
    def max_body_bytes(self) -> int:
        """The cap on any request body, enforced on the raw stream."""
        return self.max_upload_bytes + BODY_OVERHEAD_BYTES

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def outputs_dir(self) -> Path:
        return self.data_dir / "outputs"

    @property
    def sessions_dir(self) -> Path:
        return self.data_dir / "sessions"

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Settings:
        """Read settings from ``WORKBUDDY_*`` environment variables.

        Raises:
            SettingsError: A variable is set to something invalid.
        """
        env = os.environ if environ is None else environ
        if env.get(RETIRED_AUTO_APPROVE_VARIABLE):
            logger.warning(
                "%s is no longer read: every file-changing step asks for confirmation",
                RETIRED_AUTO_APPROVE_VARIABLE,
            )
        return cls(
            data_dir=Path(env.get("WORKBUDDY_DATA_DIR") or _REPO_ROOT / "workspace"),
            max_upload_bytes=_int(env, "WORKBUDDY_MAX_UPLOAD_BYTES", DEFAULT_MAX_UPLOAD_BYTES),
            turn_deadline_s=float(_int(env, "WORKBUDDY_TURN_DEADLINE_S", 180)),
            confirmation_timeout_s=float(_int(env, "WORKBUDDY_CONFIRMATION_TIMEOUT_S", 120)),
            max_concurrent_turns=_int(env, "WORKBUDDY_MAX_CONCURRENT_TURNS", 2),
            busy_retry_after_s=_int(env, "WORKBUDDY_BUSY_RETRY_AFTER_S", 10),
        )


def _int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        raise SettingsError(f"{name} must be an integer") from None

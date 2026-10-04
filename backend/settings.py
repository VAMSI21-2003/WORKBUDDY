"""Settings — every path and limit the API enforces, read once at startup.

Each value here is read by the code that enforces it *and* by the session
response's ``limits`` (``docs/design/backend-api-design.md``, "Decisions
(2026-09-30, part 2)", ask 2), so the two can't disagree.
"""

from __future__ import annotations

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
            finalized as failed.
        max_concurrent_turns: Turns that may be running at once, across all
            sessions. A turn counts until its worker thread exits.
        busy_retry_after_s: The ``Retry-After`` hint on ``429 server_busy``.
        auto_approve: Approve every Write-tier step without asking. There is
            no confirmation endpoint until PR-3, so with this off (the
            default) no file-changing step runs. Tests and live runs turn
            it on explicitly.
    """

    data_dir: Path
    max_upload_bytes: int = DEFAULT_MAX_UPLOAD_BYTES
    max_message_chars: int = 4000
    max_zip_entries: int = 1000
    max_zip_uncompressed_bytes: int = 200 * MIB
    turn_deadline_s: float = 180.0
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
        if not self.turn_deadline_s > 0:
            raise SettingsError("turn_deadline_s must be positive")

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
        return cls(
            data_dir=Path(env.get("WORKBUDDY_DATA_DIR") or _REPO_ROOT / "workspace"),
            max_upload_bytes=_int(env, "WORKBUDDY_MAX_UPLOAD_BYTES", DEFAULT_MAX_UPLOAD_BYTES),
            turn_deadline_s=float(_int(env, "WORKBUDDY_TURN_DEADLINE_S", 180)),
            max_concurrent_turns=_int(env, "WORKBUDDY_MAX_CONCURRENT_TURNS", 2),
            busy_retry_after_s=_int(env, "WORKBUDDY_BUSY_RETRY_AFTER_S", 10),
            auto_approve=_flag(env, "WORKBUDDY_AUTO_APPROVE"),
        )


def _int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        raise SettingsError(f"{name} must be an integer") from None


def _flag(env: Mapping[str, str], name: str) -> bool:
    raw = (env.get(name) or "").strip().lower()
    if raw in ("", "0", "false", "no", "off"):
        return False
    if raw in ("1", "true", "yes", "on"):
        return True
    raise SettingsError(f"{name} must be 1/true/yes/on or 0/false/no/off")

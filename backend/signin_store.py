"""Sign-ins — the people who are signed in, kept across a restart (docs/design/google-sign-in.md).

A sign-in is made when Google has vouched for a person (``backend.web.auth``). The browser gets a
random 256-bit token in the access cookie; the server keeps only the token's SHA-256::

    <data>/auth/signins/<sha256 of the token>.json   {version, owner, email, issued_at, expires_at}

Every file is read into memory at start-up, so a release (a new container on the same ``/data``)
doesn't sign anyone out, and a request never touches the disk. A sign-in ends at its absolute
expiry (``WORKBUDDY_ACCESS_TTL_HOURS`` after it was made, not renewed by use), when the person signs
out, or when it is the soonest to expire of more than ``max_signins``. Times are epoch seconds
(a monotonic clock doesn't survive a restart).

What is stored about a person is Google's stable subject id (inside ``owner``) and the email
address, and nothing else.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.identity import Principal
from backend.store import atomic_write_json

logger = logging.getLogger(__name__)

FORMAT_VERSION = 1
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_MAX_TOKEN_CHARS = 256
_MAX_FILE_BYTES = 4096
_KEYS = {"version", "owner", "email", "issued_at", "expires_at"}


@dataclass(frozen=True)
class SignIn:
    owner: str
    email: str
    issued_at: float
    expires_at: float

    @property
    def principal(self) -> Principal:
        subject = self.owner.removeprefix("google:")
        return Principal.google(subject, self.email)


def digest_of(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8", "surrogatepass")).hexdigest()


class SignInStore:
    """The live sign-ins: in memory, mirrored to ``directory``, bounded, each with an expiry."""

    def __init__(
        self,
        directory: Path,
        *,
        ttl_s: float,
        max_signins: int,
        wall_clock: Callable[[], float],
    ) -> None:
        self._dir = directory
        self._ttl_s = ttl_s
        self._max = max_signins
        self._clock = wall_clock
        self._lock = threading.Lock()
        self._live: dict[str, SignIn] = {}
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._load()

    # -- Using --------------------------------------------------------------------------------

    def issue(self, subject: str, email: str) -> str:
        """A new sign-in for ``subject``: returns the token to put in the cookie."""
        token = secrets.token_urlsafe(32)
        now = self._clock()
        signin = SignIn(f"google:{subject}", email, now, now + self._ttl_s)
        key = digest_of(token)
        with self._lock:
            self._drop_expired(now)
            while len(self._live) >= self._max:
                self._remove_locked(min(self._live, key=lambda k: self._live[k].expires_at))
            self._write(key, signin)
            self._live[key] = signin
        return token

    def lookup(self, token: str) -> SignIn | None:
        """The sign-in for ``token``, or ``None`` if there isn't one, or it has expired."""
        if not token or len(token) > _MAX_TOKEN_CHARS:
            return None
        key = digest_of(token)
        with self._lock:
            signin = self._live.get(key)
            if signin is None:
                return None
            if signin.expires_at <= self._clock():
                self._remove_locked(key)
                return None
            return signin

    def revoke(self, token: str) -> bool:
        """End the sign-in for ``token`` (sign-out). Returns whether there was one."""
        if not token or len(token) > _MAX_TOKEN_CHARS:
            return False
        with self._lock:
            key = digest_of(token)
            if key not in self._live:
                return False
            self._remove_locked(key)
            return True

    def sweep(self) -> int:
        """Drop every expired sign-in. Returns how many."""
        with self._lock:
            return self._drop_expired(self._clock())

    def __len__(self) -> int:
        with self._lock:
            return len(self._live)

    def files(self) -> list[Path]:
        """The sign-in files on disk (for tests)."""
        return sorted(self._dir.glob("*.json"))

    # -- Disk ---------------------------------------------------------------------------------

    def _load(self) -> None:
        now = self._clock()
        for path in sorted(self._dir.glob("*")):
            key = path.name.removesuffix(".json")
            signin = self._read(path) if _DIGEST.fullmatch(key) and path.suffix == ".json" else None
            if signin is None or signin.expires_at <= now:
                path.unlink(missing_ok=True)  # a leftover temporary file, a bad file, an old one
                continue
            self._live[key] = signin
        while len(self._live) > self._max:
            self._remove_locked(min(self._live, key=lambda k: self._live[k].expires_at))
        logger.info("Restored %d sign-in(s)", len(self._live))

    @staticmethod
    def _read(path: Path) -> SignIn | None:
        try:
            if path.is_symlink() or path.stat().st_size > _MAX_FILE_BYTES:
                return None
            data: Any = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or set(data) != _KEYS or data["version"] != 1:
                return None
            owner, email = data["owner"], data["email"]
            issued, expires = data["issued_at"], data["expires_at"]
            if not (isinstance(owner, str) and owner.startswith("google:") and len(owner) > 7):
                return None
            if not (isinstance(email, str) and "@" in email):
                return None
            for stamp in (issued, expires):
                if isinstance(stamp, bool) or not isinstance(stamp, (int, float)):
                    return None
            return SignIn(owner, email, float(issued), float(expires))
        except (OSError, ValueError):
            logger.warning("A sign-in file couldn't be read; it is removed")
            return None

    def _write(self, key: str, signin: SignIn) -> None:
        atomic_write_json(
            self._dir / f"{key}.json",
            {
                "version": FORMAT_VERSION,
                "owner": signin.owner,
                "email": signin.email,
                "issued_at": signin.issued_at,
                "expires_at": signin.expires_at,
            },
        )

    def _remove_locked(self, key: str) -> None:
        self._live.pop(key, None)
        try:
            os.unlink(self._dir / f"{key}.json")
        except FileNotFoundError:
            pass
        except OSError:
            logger.exception("A sign-in file couldn't be removed")

    def _drop_expired(self, now: float) -> int:
        expired = [k for k, s in self._live.items() if s.expires_at <= now]
        for key in expired:
            self._remove_locked(key)
        return len(expired)

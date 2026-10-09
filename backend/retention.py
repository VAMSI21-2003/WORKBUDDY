"""Retention — the sweep that deletes idle sessions, and the global storage cap.

Design "Decisions (2026-09-30)", item 1 (retention, quotas), and "(2026-09-30,
part 2)", ask 2 (``retention_hours`` is the sweep's own TTL).

**The sweep.** Every ``sweep_interval_s`` a background thread deletes each
session idle for longer than ``session_ttl_hours``, together with its uploads,
outputs and records. Idle means no request has used it (a poll counts as use),
and, for a session that isn't in memory, that its ``session.json`` hasn't been
touched. A session with a turn running, or waiting for an answer, is never
touched. The sweep deletes only folders named like a session key
(``^[0-9a-f]{32}$``), directly under the data root's ``uploads``, ``outputs``
and ``sessions`` folders, after the same checks as design Section 6.3: the
path is resolved and must be a direct child of its root, and a symlink is
removed as a link, never followed. A session's records go last, so a sweep
that stops halfway leaves a session to finish next time.

A session being deleted is out of service first (``SessionRegistry.claim_*``):
no request finds it or restores it from disk meanwhile.

**The storage cap.** ``StorageMeter`` sums the bytes under the data root. At or
over ``max_total_bytes`` a new session or upload gets ``503 storage_full``; the
sweep never deletes a live session to make room.
"""

from __future__ import annotations

import logging
import os
import re
import stat
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from backend.access import AccessControl
from backend.ratelimit import RateLimiter
from backend.sessions import SessionRegistry
from backend.settings import Settings

logger = logging.getLogger(__name__)

_KEY = re.compile(r"^[0-9a-f]{32}$")


@dataclass(frozen=True)
class SweepResult:
    """What one sweep did: the session keys it removed, and the ones it couldn't."""

    removed: tuple[str, ...] = ()
    failed: tuple[str, ...] = ()


class StorageMeter:
    """The bytes under the data root, and whether they've reached the cap.

    The total is recomputed at most every ``cache_s`` seconds (an upload or a
    new session each asks), so a burst of requests doesn't walk the tree each
    time. A cap hit is therefore noticed within that, and a sweep clears the
    cache.
    """

    def __init__(
        self, settings: Settings, clock: Callable[[], float], cache_s: float = 2.0
    ) -> None:
        self._settings = settings
        self._clock = clock
        self._cache_s = cache_s
        self._lock = threading.Lock()
        self._measured_at: float | None = None
        self._used = 0

    def used_bytes(self) -> int:
        with self._lock:
            now = self._clock()
            if self._measured_at is None or now - self._measured_at >= self._cache_s:
                self._used = directory_bytes(self._settings.data_dir)
                self._measured_at = now
            return self._used

    def full(self) -> bool:
        """Whether the data root holds at least ``max_total_bytes``."""
        return self.used_bytes() >= self._settings.max_total_bytes

    def invalidate(self) -> None:
        with self._lock:
            self._measured_at = None


def directory_bytes(root: Path) -> int:
    """The total size of the regular files under ``root``. Symlinks are never followed."""
    total = 0
    pending = [root]
    while pending:
        folder = pending.pop()
        try:
            with os.scandir(folder) as entries:
                for entry in entries:
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            pending.append(Path(entry.path))
                        else:
                            total += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue  # vanished while we looked
        except OSError:
            continue
    return total


def remove_tree(path: Path) -> None:
    """Delete ``path`` and everything under it, without following a symlink.

    Uploads and delivered files are stored read-only, uploads in read-only
    folders, so each folder is made writable before its entries are removed
    (a process that isn't root can't delete from a ``0o555`` folder), and each
    read-only file is made writable before it is unlinked (Windows refuses to
    delete a read-only file).

    Raises:
        OSError: Something couldn't be removed.
    """
    mode = os.lstat(path).st_mode
    if stat.S_ISDIR(mode):
        os.chmod(path, 0o700)
        with os.scandir(path) as entries:
            children = [entry.path for entry in entries]
        for child in children:
            remove_tree(Path(child))
        os.rmdir(path)
    else:
        if stat.S_ISREG(mode) and not mode & stat.S_IWRITE:
            # On Windows ``chmod(0o444)`` sets the read-only attribute, and a
            # read-only file can't be deleted until it is cleared (review round 1,
            # M1). Never done to a symlink: that would change its target.
            os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        os.unlink(path)


class RetentionSweeper:
    """Deletes sessions idle for longer than the TTL (``sweep`` is what the loop calls)."""

    def __init__(
        self,
        settings: Settings,
        registry: SessionRegistry,
        clock: Callable[[], float],
        meter: StorageMeter | None = None,
    ) -> None:
        self._settings = settings
        self._registry = registry
        self._clock = clock
        self._meter = meter
        self._roots = (settings.uploads_dir, settings.outputs_dir, settings.sessions_dir)

    def sweep(self) -> SweepResult:
        """One pass: remove every session that has been idle for longer than the TTL."""
        cutoff = self._clock() - self._settings.session_ttl_hours * 3600.0
        removed: list[str] = []
        failed: list[str] = []
        for key in self._registry.live_keys():
            if self._registry.claim_idle(key, cutoff):
                self._remove(key, removed, failed)
        tried = set(removed) | set(failed)
        for key in self._keys_on_disk():
            if key in tried:
                continue  # a removal that just failed is retried by the next sweep
            last = self._disk_activity(key)
            if last is None or last > cutoff:
                continue
            if self._registry.claim_unloaded(key):
                self._remove(key, removed, failed)
        if self._meter is not None:
            self._meter.invalidate()
        if removed or failed:
            logger.info(
                "Retention sweep: removed %d session(s), %d failed", len(removed), len(failed)
            )
        return SweepResult(tuple(removed), tuple(failed))

    def _remove(self, key: str, removed: list[str], failed: list[str]) -> None:
        try:
            # The session's records go last: if this stops halfway, the
            # session is still on disk, idle, and the next sweep finishes it.
            for root in self._roots:
                self._remove_one(root, key)
        except OSError:
            logger.exception("Retention sweep: couldn't remove session %s", key)
            failed.append(key)
        else:
            removed.append(key)
        finally:
            self._registry.release(key)

    @staticmethod
    def _remove_one(root: Path, key: str) -> None:
        """Delete ``root/key``: a key-shaped, direct child of ``root``, never through a link."""
        if not _KEY.fullmatch(key):
            raise OSError("not a session key")
        path = root / key
        if not os.path.lexists(path):
            return
        if path.is_symlink():
            os.unlink(path)  # the link, never its target
            return
        if path.resolve().parent != root.resolve():
            raise OSError("not directly under the data root")
        remove_tree(path)

    def _keys_on_disk(self) -> list[str]:
        keys: set[str] = set()
        for root in self._roots:
            try:
                with os.scandir(root) as entries:
                    keys.update(e.name for e in entries if _KEY.fullmatch(e.name))
            except OSError:
                continue
        return sorted(keys)

    def _disk_activity(self, key: str) -> float | None:
        """When a session that isn't in memory was last used: its record's time, else a folder's."""
        candidates = [self._settings.sessions_dir / key / "session.json"]
        candidates += [root / key for root in self._roots]
        for path in candidates:
            try:
                return os.lstat(path).st_mtime
            except OSError:
                continue
        return None


class MaintenanceLoop:
    """The background thread: sweeps retention, expired tokens and sign-ins, idle rate limits.

    Started at app startup and stopped at shutdown. A sweep that raises is
    logged and the loop goes on, so one bad pass can't end retention silently.
    """

    def __init__(
        self,
        settings: Settings,
        sweeper: RetentionSweeper,
        access: AccessControl,
        limiter: RateLimiter,
    ) -> None:
        self._interval = settings.sweep_interval_s
        self._sweeper = sweeper
        self._access = access
        self._limiter = limiter
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def run_once(self) -> SweepResult:
        """One maintenance pass, on the calling thread."""
        result = SweepResult()
        try:
            result = self._sweeper.sweep()
        except Exception:
            logger.exception("Retention sweep failed")
        try:
            self._access.sweep()
            self._limiter.prune()
        except Exception:
            logger.exception("Token and rate-limit sweep failed")
        return result

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="maintenance", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=5)

    def _loop(self) -> None:
        while True:
            self.run_once()
            if self._stop.wait(self._interval):
                return

"""Log redaction: secrets are masked by pattern before any handler sees a record.

Design Section 6.4. Matching by pattern means the filter never needs to read
a key's value. It is installed as a log-record factory, so it applies to
every logger and every handler (including ones added later, such as a test's
log capture), and it covers the formatted message and the traceback text.
It is defence in depth: the API itself never logs keys, headers or message
text.
"""

from __future__ import annotations

import logging
import re
import threading
from collections.abc import Callable
from typing import Any

REDACTED = "[REDACTED]"

# (pattern, replacement). A session id is a bearer secret too: it appears in
# request paths, which an access log records, so it is masked there.
_PATTERNS = (
    (re.compile(r"gsk_[A-Za-z0-9]+"), REDACTED),
    (re.compile(r"sk-ant-[A-Za-z0-9_\-]+"), REDACTED),
    (re.compile(r"(?i)\bBearer\s+[^\s'\",;]+"), REDACTED),
    (re.compile(r"(?i)\bapi[_-]?key\s*[=:]\s*['\"]?[^\s'\",;]+"), REDACTED),
    (re.compile(r"(/sessions/)[A-Za-z0-9_\-%.]+"), r"\1" + REDACTED),
)

_lock = threading.Lock()
_installed = False


def redact(text: str) -> str:
    """``text`` with every secret (a key, a bearer token, a session id in a path) masked."""
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def install_log_redaction() -> None:
    """Wrap the log-record factory so every record is redacted. Idempotent."""
    global _installed
    with _lock:
        if _installed:
            return
        previous = logging.getLogRecordFactory()

        def factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
            record = previous(*args, **kwargs)
            _redact_record(record)
            return record

        logging.setLogRecordFactory(factory)
        _installed = True


def uninstall_log_redaction(previous: Callable[..., logging.LogRecord] | None = None) -> None:
    """Undo ``install_log_redaction`` (tests only), restoring ``previous`` if given."""
    global _installed
    with _lock:
        if previous is not None:
            logging.setLogRecordFactory(previous)
        _installed = False


def _redact_record(record: logging.LogRecord) -> None:
    """Redact ``record`` in place, keeping its shape where that's safe.

    The format string and each string argument are redacted separately, so a
    formatter that unpacks ``record.args`` (uvicorn's access log does) still
    works. If the formatted message would still hold a secret (one spread
    across arguments, or inside a non-string argument), the record is
    collapsed to its redacted, formatted message instead.
    """
    if isinstance(record.msg, str):
        record.msg = redact(record.msg)
    if isinstance(record.args, tuple):
        record.args = tuple(redact(a) if isinstance(a, str) else a for a in record.args)
    elif isinstance(record.args, dict):
        record.args = {k: redact(v) if isinstance(v, str) else v for k, v in record.args.items()}
    try:
        message = record.getMessage()
    except Exception:  # noqa: BLE001 — a malformed record is left for logging to report
        return
    if redact(message) != message:
        record.msg = redact(message)
        record.args = None
    if record.exc_info and not record.exc_text:
        record.exc_text = redact(logging.Formatter().formatException(record.exc_info))
    if record.stack_info:
        record.stack_info = redact(record.stack_info)

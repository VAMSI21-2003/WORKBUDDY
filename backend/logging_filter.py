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

_PATTERNS = (
    re.compile(r"gsk_[A-Za-z0-9]+"),
    re.compile(r"sk-ant-[A-Za-z0-9_\-]+"),
    re.compile(r"(?i)\bBearer\s+[^\s'\",;]+"),
    re.compile(r"(?i)\bapi[_-]?key\s*[=:]\s*['\"]?[^\s'\",;]+"),
)

_lock = threading.Lock()
_installed = False


def redact(text: str) -> str:
    """``text`` with every match of a secret pattern replaced by ``[REDACTED]``."""
    for pattern in _PATTERNS:
        text = pattern.sub(REDACTED, text)
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
    try:
        message = record.getMessage()
    except Exception:  # noqa: BLE001 — a malformed record is left for logging to report
        return
    record.msg = redact(message)
    record.args = None
    if record.exc_info and not record.exc_text:
        record.exc_text = redact(logging.Formatter().formatException(record.exc_info))
    if record.stack_info:
        record.stack_info = redact(record.stack_info)

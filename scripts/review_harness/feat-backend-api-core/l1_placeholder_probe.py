"""Log calls whose format string puts a secret prefix right before a placeholder.

Usage::

    python scripts/review_harness/feat-backend-api-core/l1_placeholder_probe.py [ROOT]

``ROOT`` is the checkout whose ``backend.logging_filter`` is used (default:
this one). Each call should print one redacted line. A "--- Logging error ---"
block means the record broke, and logging's error handler then prints the
record's raw arguments (the secret) to stderr. Run it with ``2>&1``.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.logging_filter import install_log_redaction  # noqa: E402

install_log_redaction()
logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
log = logging.getLogger("probe")
session_id = "s_AbCdEfGhIjKlMnOpQrStUv"
log.info("GET /api/v1/sessions/%s/files", session_id)
log.info("Authorization: Bearer %s", "tok_plain_SECRET123")
log.info("api_key=%s", "plainSECRET456")
log.info("plain %s", "/api/v1/sessions/" + session_id)

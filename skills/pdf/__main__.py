"""``python -m skills.pdf``: checks the PDF sandbox on this machine and exits 0 only if it holds.

Starts the real worker in its ``selftest`` mode, which tries from inside the sandbox what must be
refused (a socket, a program) and reports whether each was. Run it where the server runs (for the
image: ``docker exec <container> python -m skills.pdf``) before trusting PDF uploads there: where
the sandbox can't be built the server refuses PDFs rather than read them unprotected. That includes
a kernel without Landlock (the filesystem confinement): see ``_worker/lockdown.py``.
"""

from __future__ import annotations

import json
import sys

from skills.pdf import sandbox


def main() -> int:
    outcome = sandbox.run("selftest", b"")
    report = {
        "worker_ran": outcome.ok,
        "reason": outcome.reason,
        "checks": outcome.data,
        "peak_rss_kib": outcome.peak_kb,
        "address_space_limit_mib": sandbox.ADDRESS_SPACE_BYTES // 2**20,
        "deadline_seconds": sandbox.TIMEOUT_SECONDS,
    }
    holds = outcome.ok and bool(outcome.data) and all(v is True for v in outcome.data.values())
    print(json.dumps(report, indent=2))
    print("The PDF sandbox holds." if holds else "The PDF sandbox does NOT hold: PDFs are refused.")
    if outcome.ok and outcome.data.get("filesystem_confined") is not True:
        print(
            "The worker's filesystem is not confined (Landlock is missing or refused by this "
            "kernel or container profile), so PDFs are refused: without it, code that took over "
            "a parser could read the server's environment and other people's files."
        )
    return 0 if holds else 1


if __name__ == "__main__":
    sys.exit(main())

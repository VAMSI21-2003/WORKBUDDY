"""A fake identity provider that issues real, signed ID tokens (docs/design/google-sign-in.md).

Used by the tests (through ``httpx.MockTransport``), by the browser tests and by the live runs
(as a small server: ``python -m tests.idp.server``). Nothing here is part of the app.
"""

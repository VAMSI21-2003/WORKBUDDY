# ruff: noqa: E501 — reviewer tooling, not product code
"""Round-1 probe: PR-3's 25 scenarios x 3 speakers, through the page, as a person signed in with
Google on production settings, then the same session probed by a second person.

    python scripts/review_harness/feat-google-sign-in/signed_in_probe.py [--speaker S] [--only NAME] > out/signed_in_head.jsonl

Real app, real pipeline (build_engine, skills, verifiers, Voice); only the LLM is faked
(``../feat-backend-api-core/harness.py`` via ``../feat-frontend-demo/fe1.py``) and Google is the
suite's fake identity provider (``tests/idp``, real RS256 tokens). Per row, on top of
``ui_probe``'s fields: ``bob`` = what a second signed-in person gets from every page and API route
of Alice's session (each must be byte-identical to the answer for an unknown id), from ``GET /``
with Alice's session pointer planted in his browser, and whether any of Alice's text (reply, file
name, message) shows up in anything Bob receives.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE.parent / "feat-frontend-demo"))
import fe1  # noqa: E402
from fe1 import h  # noqa: E402

sys.path.insert(0, str(HERE.parent / "feat-backend-api-confirmation"))
import api_probe  # noqa: E402

sys.path.insert(0, str(ROOT))
from fastapi.testclient import TestClient  # noqa: E402

from backend.main import create_app  # noqa: E402
from tests.backend.helpers import PUBLIC_ORIGIN, production_settings  # noqa: E402
from tests.backend.signin_helpers import ALICE, BOB, make_idp, signed_in  # noqa: E402

UNKNOWN = "s_" + "x" * 22


class SignedInApp(fe1.PageApp):
    """fe1.PageApp on production settings, with Alice signed in through the fake provider."""

    def __init__(self, llm: Any, speaker: str, **settings: Any) -> None:  # noqa: D107
        self.tmp = tempfile.TemporaryDirectory()
        self.factory = fe1.pr3.Factory(llm, speaker)
        self.idp = make_idp()
        values: dict[str, Any] = {"rate_limits": fe1.RELAXED}
        values.update(settings)
        self.settings = production_settings(Path(self.tmp.name), **values)
        self.app = create_app(self.settings, self.factory, oidc_http=self.idp.client())
        self.client = self.browser(ALICE)
        self.bob = self.browser(BOB)

    def browser(self, email: str | None) -> TestClient:
        c = TestClient(self.app, base_url=PUBLIC_ORIGIN, headers={"Origin": PUBLIC_ORIGIN}, raise_server_exceptions=False)
        if email:
            signed_in(c, self.idp, email)
        return c


def bob_probe(app: SignedInApp, sid: str, tid: str | None, api: dict[str, Any], secrets: list[str]) -> dict[str, Any]:
    """Bob against every route of Alice's session, compared with the same route for an unknown id."""
    out: dict[str, Any] = {"differs_from_unknown": [], "statuses": {}, "leaks": []}
    tid = tid or "t0001"
    fid = (api.get("delivered_files") or [{}])[0].get("file_id", "f_none")
    routes = [
        ("GET", "/api/v1/sessions/{s}"),
        ("GET", "/api/v1/sessions/{s}/files"),
        ("GET", "/api/v1/sessions/{s}/files/{f}/download"),
        ("GET", "/api/v1/sessions/{s}/turns/{t}"),
        ("POST", "/api/v1/sessions/{s}/turns"),
        ("POST", "/api/v1/sessions/{s}/files"),
        ("POST", "/api/v1/sessions/{s}/turns/{t}/confirmation"),
        ("GET", "/ui/s/{s}/transcript"),
        ("GET", "/ui/s/{s}/turns/{t}/poll"),
        ("POST", "/ui/s/{s}/turns"),
        ("POST", "/ui/s/{s}/files"),
        ("POST", "/ui/s/{s}/turns/{t}/confirmation"),
    ]
    for method, template in routes:
        responses = []
        for s in (sid, UNKNOWN):
            path = template.format(s=s, t=tid, f=fid)
            kw: dict[str, Any] = {}
            if method == "POST" and path.startswith("/api/") and path.endswith("/turns"):
                kw["json"] = {"message": "remove duplicate rows"}
            elif method == "POST" and path.endswith("/confirmation") and path.startswith("/api/"):
                kw["json"] = {"confirmation_id": "c_" + "x" * 22, "decision": "approve"}
            elif method == "POST" and path.endswith("/files"):
                kw["files"] = {"file": ("x.xlsx", h.xlsx(), "application/octet-stream")}
            elif method == "POST":
                kw["data"] = {"message": "remove duplicate rows", "confirmation_id": "c", "decision": "approve"}
            responses.append(app.bob.request(method, path, **kw))
        a, b = responses
        out["statuses"][f"{method} {template}"] = a.status_code
        same = a.status_code == b.status_code and a.content == b.content
        if not same:
            out["differs_from_unknown"].append(f"{method} {template}: {a.status_code} vs {b.status_code}")
        for text in secrets:
            if text and text.encode() in a.content:
                out["leaks"].append(f"{method} {template}")
    # Bob's browser with Alice's pointer planted.
    planted = app.browser(BOB)
    planted.cookies.set("__Host-wb_session", sid)
    page = planted.get("/")
    out["bob_root_status"] = page.status_code
    out["bob_root_mentions_alice"] = [t for t in secrets if t and t in page.text]
    out["bob_root_has_alice_sid"] = sid in page.text
    out["bob_root_pointer_cleared"] = "__Host-wb_session=" in page.headers.get("set-cookie", "")
    return out


def run(name: str, speaker: str, **kw: Any) -> dict[str, Any]:
    llm = h.FakeLLM(kw.get("replies") if kw.get("replies") is not None else h.single("remove_duplicate_rows"))
    app = SignedInApp(llm, speaker, **(kw.get("settings") or {}))
    try:
        sid = app.new_session()
        original = None
        fixture = kw.get("fixture")
        upload_http = None
        if fixture is not None:
            r, original = app.upload(sid, fixture, kw.get("filename", "report.xlsx"))
            upload_http = r.status_code
        before = h.sha(original) if original else None
        mode = "focus" if speaker == "focus" else "friend"
        message = kw.get("message", "remove duplicate rows")
        out = fe1.drive_page(app, sid, message, mode, kw.get("decision", "approve"), answer_after_s=kw.get("answer_after_s", 0.0))
        tid = out.get("turn_id")
        api = app.api_turn(sid, tid) if tid else {}
        secrets = [api.get("reply") or "", *(d["name"] for d in api.get("delivered_files") or []), message, kw.get("filename", "report.xlsx")]
        bob = bob_probe(app, sid, tid, api, [s for s in secrets if s and len(s) > 6])
        after = h.sha(original) if original else None
        full = app.client.get("/")
        full_c = fe1.container_of(full.text, tid) if tid else None
        out.update(
            scenario=name,
            speaker=speaker,
            upload_http=upload_http,
            api_outcome=api.get("outcome"),
            api_reply=api.get("reply"),
            api_delivered=[d["name"] for d in api.get("delivered_files") or []],
            page_equals_api=out.get("page_reply") == api.get("reply"),
            full_page_equals_api=(fe1.reply_slot(full_c) if full_c else None) == api.get("reply"),
            downloads_turn=fe1.check_downloads(app, out.get("page_turn_links") or [], api),
            links_only_if_delivered=(bool(out.get("page_turn_links")) == (api.get("outcome") == "delivered")),
            original_sha_before=before,
            original_sha_after=after,
            original_unchanged=before == after,
            claim_words_outside_reply=h.claim_words(out.get("page_text_outside_reply") or ""),
            account_header=("Signed in as alice@example.test" in full.text),
            bob=bob,
            llm_calls=len(llm.calls),
        )
        return out
    finally:
        app.close()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--speaker", choices=fe1.SPEAKERS)
    p.add_argument("--only")
    a = p.parse_args()
    for speaker in (a.speaker,) if a.speaker else fe1.SPEAKERS:
        for name, kw in api_probe.scenarios(speaker):
            if a.only and a.only not in name:
                continue
            kw = dict(kw)
            kw.setdefault("fixture", h.xlsx())
            try:
                print(json.dumps(run(name, speaker, **kw), default=str), flush=True)
            except Exception as exc:  # noqa: BLE001
                print(json.dumps({"scenario": name, "speaker": speaker, "PROBE_ERROR": repr(exc)}), flush=True)


if __name__ == "__main__":
    main()

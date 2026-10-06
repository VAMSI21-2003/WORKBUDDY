# ruff: noqa: E501 — reviewer tooling, not product code
"""FE-1 probe: PR-3's 25 ordinary scenarios, driven through the page's routes, three speakers.

    python scripts/review_harness/feat-frontend-demo/ui_probe.py [--speaker S] [--only NAME] > out/ui_probe_head.jsonl

The scenarios are ``../feat-backend-api-confirmation/api_probe.py``'s own list
(imported). Each runs as the browser would: ``POST /ui/sessions``, upload through
``/ui/s/{sid}/files``, the composer's ``POST /ui/s/{sid}/turns``, polls with the
version the page holds, and the plan card's form. One JSON line each:

- the reply slot's text against the JSON API's ``reply`` for the same turn, and against
  ``GET /`` (the full page) for that session;
- the download links the turn and the Files panel show, each fetched and read with
  openpyxl, and checked against the API's ``delivered_files`` (sha256);
- every text a reader sees outside the reply slot, before and after the finish;
- the plan card (steps, note, buttons) against the API's ``pending_confirmation``;
- the original upload's SHA-256 before and after.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fe1  # noqa: E402
from fe1 import h  # noqa: E402

sys.path.insert(0, str(fe1.HERE.parent / "feat-backend-api-confirmation"))
import api_probe  # noqa: E402


def run(name: str, speaker: str, **kw: Any) -> dict[str, Any]:
    llm = h.FakeLLM(kw.get("replies") if kw.get("replies") is not None else h.single("remove_duplicate_rows"))
    app = fe1.PageApp(llm, speaker, **(kw.get("settings") or {}))
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
        out = fe1.drive_page(
            app, sid, kw.get("message", "remove duplicate rows"), mode, kw.get("decision", "approve"),
            answer_after_s=kw.get("answer_after_s", 0.0),
        )
        after = h.sha(original) if original else None
        tid = out.get("turn_id")
        api = app.api_turn(sid, tid) if tid else {}
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
            live_equals_api=out.get("live_region") == api.get("reply"),
            downloads_turn=fe1.check_downloads(app, out.get("page_turn_links") or [], api),
            downloads_panel=fe1.check_downloads(app, out.get("page_files_links") or [], api),
            links_only_if_delivered=(bool(out.get("page_turn_links")) == (api.get("outcome") == "delivered")),
            original_sha_before=before,
            original_sha_after=after,
            original_unchanged=before == after,
            claim_words_outside_reply=h.claim_words(out.get("page_text_outside_reply") or ""),
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

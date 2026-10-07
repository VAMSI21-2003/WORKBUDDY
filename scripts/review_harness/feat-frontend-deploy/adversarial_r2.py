# ruff: noqa: E501 — reviewer tooling, not product code
"""Round 2 HTTP cases (``TestClient``, real app and pipeline, fake LLM) aimed at the ``held``
parameter. Reuses round 1's ``adversarial.py`` (``plan_waiting``, ``restarted``, ``headers_of``)
and, through it, ``../feat-frontend-demo/fe1.py``. One JSON line per case and speaker.

    python scripts/review_harness/feat-frontend-deploy/adversarial_r2.py [--speaker S] [--only hNN] > out/adversarial_r2_head.jsonl

- h01 hostile ``held`` values on a waiting turn (no restart): negative, huge, text, float, empty,
  repeated, with and without ``v`` and ``unconfirmed``. Never a 500; a reload only when the value
  is above the server's version.
- h02 restart under a waiting plan; the lost-answer lookup with ``held`` (what the page now
  sends): reload, then the reloaded page's reply against rule 1 and the original's SHA-256.
- h03 restart; the 409 path: a stale Approve, then the refetch with ``held``.
- h04 no restart, the plan approved elsewhere: the 409 refetch with ``held`` gets the turn whole
  (never a reload), with a version the swap guard keeps; the delivered file read with openpyxl.
- h05 ``held`` on another session's turn, an unknown turn, and in password mode without a token:
  the same 404/401 as without it (``held`` reveals nothing).
"""

from __future__ import annotations

import argparse
import json
import re
import time
from typing import Any

import adversarial as r1  # round 1's probe (imports fe1, which consumes --tree)
from adversarial import fe1, h, headers_of, plan_waiting, restarted, sha


def kind(r: Any) -> str:
    if r.headers.get("hx-refresh"):
        return "reload"
    if "data-turn-id" in r.text and 'class="turn' in r.text:
        return "turn"
    if 'class="poll"' in r.text:
        return "poll-only"
    return f"other:{r.text[:80]}"


def h01(speaker: str) -> dict[str, Any]:
    app = fe1.PageApp(h.FakeLLM(h.single("remove_duplicate_rows")), speaker)
    try:
        sid = app.new_session()
        _, original = app.upload(sid, h.xlsx())
        before = sha(original)
        held, cid = plan_waiting(app, sid)
        out: dict[str, Any] = {"held": held}
        url = f"/ui/s/{sid}/turns/t0001/poll"
        raws = ["-1", "0", str(held), str(held + 1), str(10**18), "99999999999999999999999999", "abc", "1.5", "", " 3", "0x10", "1e3"]
        for raw in raws:
            r = app.client.get(url, params={"held": raw})
            out[f"held={raw!r}"] = {"status": r.status_code, "kind": kind(r)}
        for q in (f"held={held}&held={held + 7}", f"held={held + 7}&held={held}", f"v={held}&held={held + 7}", f"v={held - 1}&held={held + 7}", f"unconfirmed={cid}&held={held + 1}", f"unconfirmed={cid}&held={held}"):
            r = app.client.get(f"{url}?{q}")
            out[q.replace(cid, "<cid>")] = {"status": r.status_code, "kind": kind(r), "lost_note": "may not have" in r.text or "may be lost" in r.text}
        out["api_status_after"] = app.api_turn(sid, "t0001")["status"]
        out["original_unchanged"] = before == sha(original)
        return out
    finally:
        app.close()


def _reloaded(client: Any, sid: str) -> dict[str, Any]:
    page = client.get("/")
    c = fe1.container_of(page.text, "t0001")
    reply = fe1.reply_slot(c) if c is not None else None
    api = client.get(f"/api/v1/sessions/{sid}/turns/t0001").json()
    return {
        "status": c.attrs.get("data-status") if c is not None else None,
        "version": c.attrs.get("data-version") if c is not None else None,
        "has_plan": bool(c is not None and c.find("section", cls="plan")),
        "reply": reply,
        "reply_equals_api": reply == api.get("reply"),
        "api_outcome": api.get("outcome"),
        "delivered": [d["name"] for d in api.get("delivered_files") or []],
        "links": fe1.links(c) if c is not None else None,
        "claim_words": h.claim_words(reply or ""),
    }


def h02(speaker: str) -> dict[str, Any]:
    app = fe1.PageApp(h.FakeLLM(h.single("remove_duplicate_rows")), speaker)
    try:
        sid = app.new_session()
        _, original = app.upload(sid, h.xlsx())
        before = sha(original)
        held, cid = plan_waiting(app, sid)
        client = restarted(app, speaker)
        lost = client.post(f"/ui/s/{sid}/turns/t0001/confirmation", data={"confirmation_id": cid, "decision": "approve"})
        r = client.get(f"/ui/s/{sid}/turns/t0001/poll", params={"unconfirmed": cid, "held": held})
        return {
            "held": held,
            "answer_after_restart": lost.status_code,
            "lookup": headers_of(r),
            "reloaded": _reloaded(client, sid),
            "outputs_on_disk": sorted(p.name for p in app.settings.outputs_dir.rglob("*") if p.is_file()),
            "original_sha_before": before,
            "original_sha_after": sha(original),
        }
    finally:
        app.close()


def h03(speaker: str) -> dict[str, Any]:
    app = fe1.PageApp(h.FakeLLM(h.single("remove_duplicate_rows")), speaker)
    try:
        sid = app.new_session()
        _, original = app.upload(sid, h.xlsx())
        before = sha(original)
        held, cid = plan_waiting(app, sid)
        client = restarted(app, speaker)
        stale = client.post(f"/ui/s/{sid}/turns/t0001/confirmation", data={"confirmation_id": cid, "decision": "approve"})
        r = client.get(f"/ui/s/{sid}/turns/t0001/poll", params={"held": held})
        return {
            "held": held,
            "stale_click": {"status": stale.status_code, "body": stale.text[:160]},
            "refetch": headers_of(r),
            "reloaded": _reloaded(client, sid),
            "original_unchanged": before == sha(original),
        }
    finally:
        app.close()


def h04(speaker: str) -> dict[str, Any]:
    app = fe1.PageApp(h.FakeLLM(h.single("remove_duplicate_rows")), speaker)
    try:
        sid = app.new_session()
        _, original = app.upload(sid, h.xlsx())
        before = sha(original)
        held, cid = plan_waiting(app, sid)
        first = app.answer(sid, "t0001", cid, "approve")  # the other tab
        second = app.answer(sid, "t0001", cid, "reject")  # this tab: 409
        for _ in range(300):
            if app.api_turn(sid, "t0001")["status"] == "finished":
                break
            time.sleep(0.05)
        r = app.client.get(f"/ui/s/{sid}/turns/t0001/poll", params={"held": held})
        c = fe1.container_of(r.text, "t0001")
        version = int(c.attrs.get("data-version") or 0) if c is not None else None
        api = app.api_turn(sid, "t0001")
        reply = fe1.reply_slot(c) if c is not None else None
        names = [d["name"] for d in api.get("delivered_files") or []]
        files = [p for p in app.settings.outputs_dir.rglob("*.xlsx") if p.name in names]
        measured = h.describe(h.read_rows(files[0])) if files else None
        return {
            "held": held,
            "first": first.status_code,
            "second": second.status_code,
            "refetch_kind": kind(r),
            "refetch_version": version,
            "swap_guard_keeps": version is not None and version >= held,
            "reply": reply,
            "reply_equals_api": reply == api.get("reply"),
            "reply_numbers": sorted({int(n) for n in re.findall(r"\b(\d+) (?:row|duplicate)", reply or "")}),
            "delivered": names,
            "measured": measured,
            "links": fe1.links(c) if c is not None else None,
            "original_unchanged": before == sha(original),
        }
    finally:
        app.close()


def h05(speaker: str) -> dict[str, Any]:
    app = fe1.PageApp(h.FakeLLM(h.single("remove_duplicate_rows")), speaker)
    other = fe1.PageApp(h.FakeLLM([]), speaker)
    locked = fe1.PageApp(h.FakeLLM([]), speaker, password="pw for probe")
    try:
        sid = app.new_session()
        app.upload(sid, h.xlsx())
        held, _ = plan_waiting(app, sid)
        out: dict[str, Any] = {}
        for name, client, path in (
            ("unknown turn", app.client, f"/ui/s/{sid}/turns/t0099/poll"),
            ("unknown session", app.client, "/ui/s/s_AAAAAAAAAAAAAAAAAAAAAA/turns/t0001/poll"),
            ("someone else's session", other.client, f"/ui/s/{sid}/turns/t0001/poll"),
            ("password mode, no token", locked.client, f"/ui/s/{sid}/turns/t0001/poll"),
        ):
            for params in ({}, {"held": str(held + 50)}):
                r = client.get(path, params=params)
                out[f"{name} {params}"] = {"status": r.status_code, "hx_refresh": r.headers.get("hx-refresh"), "body": r.text[:120]}
        return out
    finally:
        app.close()
        other.close()
        locked.close()


CASES = {"h01": h01, "h02": h02, "h03": h03, "h04": h04, "h05": h05}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--speaker", choices=fe1.SPEAKERS)
    p.add_argument("--only")
    a = p.parse_args()
    for speaker in (a.speaker,) if a.speaker else fe1.SPEAKERS:
        for name, fn in CASES.items():
            if a.only and a.only not in name:
                continue
            try:
                row = {"case": name, "speaker": speaker, **fn(speaker)}
            except Exception as exc:  # noqa: BLE001
                row = {"case": name, "speaker": speaker, "PROBE_ERROR": repr(exc)}
            print(json.dumps(row, default=str), flush=True)


if __name__ == "__main__":
    _ = r1
    main()

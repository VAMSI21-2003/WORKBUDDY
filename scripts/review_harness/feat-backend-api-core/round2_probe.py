# ruff: noqa: E501 — reviewer tooling, not product code
"""Round-2 probes, aimed at the round-1 fixes (M1, M2, M3, L1, L8, L11). Real pipeline, fake LLM.

Usage::

    python scripts/review_harness/feat-backend-api-core/round2_probe.py [--only NAME] [--speaker S]

Runs on whatever checkout it sits in (copy it into a worktree of an older
commit to compare). Each probe prints one JSON object per speaker: every
turn's outcome, reply, what was handed back (with an independent openpyxl
read), and the SHA-256 of every upload before and after.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import io
import json
import logging
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import harness as h  # noqa: E402
from adversarial_probe import app, session, upload_path, uploads_intact  # noqa: E402
from api_probe import run_turn, upload  # noqa: E402

from backend.store import SessionStore  # noqa: E402

SPEAKERS = ("template", "friend", "focus")
PROBES: dict[str, Any] = {}

try:  # absent before the round-1 fixes
    from buddy.voice_catalog import FROM_ORIGINAL_SENTENCE, NOTHING_RAN_REPLY
except ImportError:  # pragma: no cover
    FROM_ORIGINAL_SENTENCE = "I made this copy from your original upload"
    NOTHING_RAN_REPLY = "I didn't run anything on a file this turn"


def probe(fn: Any) -> Any:
    PROBES[fn.__name__] = fn
    return fn


def slim(turn: dict[str, Any]) -> dict[str, Any]:
    out = {k: turn.get(k) for k in ("turn_id", "http", "outcome", "reply_source", "reply", "delivered", "original_unchanged")}
    if "body" in turn:
        out["body"] = turn["body"]
    detail = turn.get("detail") or {}
    out["steps"] = [(s["capability"], s["status"], s["input_name"]) for s in detail.get("steps", [])]
    out["intent"] = detail.get("intent")
    reply = turn.get("reply") or ""
    out["has_from_original"] = FROM_ORIGINAL_SENTENCE in reply
    out["is_nothing_ran"] = reply == NOTHING_RAN_REPLY
    out["mentions_verifier"] = "verifier" in reply.lower()
    out["claim_words"] = h.claim_words(reply)
    return out


@contextlib.contextmanager
def patched(owner: Any, name: str, value: Any) -> Any:
    real = getattr(owner, name)
    setattr(owner, name, value)
    try:
        yield real
    finally:
        setattr(owner, name, real)


def _up(client: Any, settings: Any, sid: str, data: bytes, name: str = "report.xlsx") -> tuple[dict[str, Any], Path]:
    body = upload(client, sid, data, name).json()
    return body, upload_path(settings, body["name"], body["file_id"])


# -- M1: turns where nothing ran ------------------------------------------------------


@probe
def m1_nothing_ran(speaker: str) -> dict[str, Any]:
    out: dict[str, Any] = {"probe": "m1_nothing_ran", "speaker": speaker}
    # a/b: no upload at all
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        client, settings, _ = app(tmp, h.FakeLLM(h.single("unclear") + h.single("remove_duplicate_rows")), speaker)
        sid = session(client, speaker)
        out["no_upload_hi"] = slim(run_turn(client, sid, "hi", None))
        out["no_upload_dedupe"] = slim(run_turn(client, sid, "remove duplicate rows", None))
    # c: thanks after a delivery
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        client, settings, _ = app(tmp, h.FakeLLM(h.single("remove_duplicate_rows") + h.single("unclear")), speaker)
        sid = session(client, speaker)
        _, path = _up(client, settings, sid, h.xlsx())
        out["t1_dedupe"] = slim(run_turn(client, sid, "remove duplicate rows", path))
        out["thanks_after_delivery"] = slim(run_turn(client, sid, "thanks!", path))
    # d: default config, declined, then "yes, go ahead"
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        client, settings, _ = app(tmp, h.FakeLLM(h.single("remove_duplicate_rows") + h.single("unclear")), speaker, auto_approve=False)
        sid = session(client, speaker)
        _, path = _up(client, settings, sid, h.xlsx())
        out["t1_declined"] = slim(run_turn(client, sid, "remove duplicate rows", path))
        out["yes_after_decline"] = slim(run_turn(client, sid, "yes, go ahead", path))
    # e/f: first-turn greeting with an upload (analysis runs), then a second greeting
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        client, settings, _ = app(tmp, h.FakeLLM(h.single("unclear") + h.single("unclear")), speaker)
        sid = session(client, speaker)
        _, path = _up(client, settings, sid, h.xlsx())
        out["first_greeting_with_upload"] = slim(run_turn(client, sid, "hi, what can you do?", path))
        out["second_greeting"] = slim(run_turn(client, sid, "hi again", path))
    # g: later turn, the planner's steps all have an empty source_phrase (all dropped)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        llm = h.FakeLLM(
            h.single("unclear")
            + h.chain(("spreadsheet.deduplicate", ""), ("spreadsheet.remove_empty_rows", ""))
        )
        client, settings, _ = app(tmp, llm, speaker)
        sid = session(client, speaker)
        _, path = _up(client, settings, sid, h.xlsx())
        run_turn(client, sid, "hi", path)
        out["planner_drops_every_step"] = slim(run_turn(client, sid, "tidy it up somehow", path))
    # h: later turn, the LLM is down (Brain and Think raise)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        llm = h.FakeLLM(h.single("unclear") + [h.raising(RuntimeError("provider down gsk_LEAKLEAK"))] * 6)
        client, settings, _ = app(tmp, llm, speaker)
        sid = session(client, speaker)
        _, path = _up(client, settings, sid, h.xlsx())
        run_turn(client, sid, "hi", path)
        out["llm_down_later_turn"] = slim(run_turn(client, sid, "remove duplicate rows", path))
    # i: later turn, Think asks for clarification
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        llm = h.FakeLLM(h.single("unclear") + [h.understand("remove_duplicate_rows"), h.think(clarify=True)])
        client, settings, _ = app(tmp, llm, speaker)
        sid = session(client, speaker)
        _, path = _up(client, settings, sid, h.xlsx())
        run_turn(client, sid, "hi", path)
        out["clarification_later_turn"] = slim(run_turn(client, sid, "clean it", path))
    # j: the model misreads "thanks!" as a dedupe request after a delivery
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        llm = h.FakeLLM(h.single("remove_duplicate_rows") + h.single("remove_duplicate_rows"))
        client, settings, _ = app(tmp, llm, speaker)
        sid = session(client, speaker)
        _, path = _up(client, settings, sid, h.xlsx())
        run_turn(client, sid, "remove duplicate rows", path)
        out["thanks_misread_as_dedupe"] = slim(run_turn(client, sid, "thanks!", path))
    # k: a second upload, then "hi" (the new file gets its own analysis?)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        llm = h.FakeLLM(h.single("unclear") + h.single("unclear"))
        client, settings, _ = app(tmp, llm, speaker)
        sid = session(client, speaker)
        _, path = _up(client, settings, sid, h.xlsx())
        run_turn(client, sid, "hi", path)
        _, path2 = _up(client, settings, sid, h.xlsx(h.NO_DUP_ROWS), "other.xlsx")
        out["hi_after_second_upload"] = slim(run_turn(client, sid, "hi", path2))
        out["uploads_intact"] = uploads_intact(client, settings, sid)
    # l: later turn, a plan naming a capability that doesn't exist
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        llm = h.FakeLLM(h.single("unclear") + h.chain(("spreadsheet.delete_everything", "delete everything")))
        client, settings, _ = app(tmp, llm, speaker)
        sid = session(client, speaker)
        _, path = _up(client, settings, sid, h.xlsx())
        run_turn(client, sid, "hi", path)
        out["unknown_capability_later_turn"] = slim(run_turn(client, sid, "delete everything", path))
    return out


# -- M2: the from-original sentence ---------------------------------------------------


@probe
def m2_from_original(speaker: str) -> dict[str, Any]:
    out: dict[str, Any] = {"probe": "m2_from_original", "speaker": speaker}
    # a: dedupe, then "now remove the empty rows too"
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        llm = h.FakeLLM(h.single("remove_duplicate_rows") + h.single("remove_empty_rows"))
        client, settings, _ = app(tmp, llm, speaker)
        sid = session(client, speaker)
        _, path = _up(client, settings, sid, h.xlsx())
        out["a_t1"] = slim(run_turn(client, sid, "remove duplicate rows", path))
        out["a_t2_now_too"] = slim(run_turn(client, sid, "now remove the empty rows too", path))
    # b: dedupe, download the copy, upload it again, then remove empty rows on it
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        llm = h.FakeLLM(h.single("remove_duplicate_rows") + h.single("remove_empty_rows"))
        client, settings, _ = app(tmp, llm, speaker)
        sid = session(client, speaker)
        _, path = _up(client, settings, sid, h.xlsx())
        t1 = run_turn(client, sid, "remove duplicate rows", path)
        files = client.get(f"/api/v1/sessions/{sid}/files").json()["files"]
        d = next(f for f in files if f["kind"] == "delivered")
        cleaned = client.get(f"/api/v1/sessions/{sid}/files/{d['file_id']}/download").content
        up2, path2 = _up(client, settings, sid, cleaned, d["name"])
        out["b_t1"] = slim(t1)
        out["b_reupload_name"] = up2.get("name")
        out["b_reupload_rows"] = h.describe(h.read_rows(cleaned))
        out["b_t2_on_reupload"] = slim(run_turn(client, sid, "now remove the empty rows", path2))
        out["b_uploads_intact"] = uploads_intact(client, settings, sid)
    # c: dedupe file A, then upload an unrelated file B and dedupe it
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        llm = h.FakeLLM(h.single("remove_duplicate_rows") + h.single("remove_duplicate_rows"))
        client, settings, _ = app(tmp, llm, speaker)
        sid = session(client, speaker)
        _, path = _up(client, settings, sid, h.xlsx())
        run_turn(client, sid, "remove duplicate rows", path)
        rows_b = {"name": ["X", "X", "Y"], "amount": [1, 1, 2]}
        _, path2 = _up(client, settings, sid, h.xlsx(rows_b), "budget.xlsx")
        out["c_t2_other_file"] = slim(run_turn(client, sid, "remove duplicate rows", path2))
    # d: a failed turn first (Inspector raises after a real output), then a delivery
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        calls = {"n": 0}

        def wrap(cycle: Any) -> Any:
            inner = cycle.inspector

            def inspector(state: Any) -> Any:
                calls["n"] += 1
                result = inner(state)
                if calls["n"] == 1:
                    raise RuntimeError("inspector boom")
                return result

            return dataclasses.replace(cycle, inspector=inspector)

        llm = h.FakeLLM(h.single("remove_duplicate_rows") + h.single("remove_duplicate_rows"))
        client, settings, _ = app(tmp, llm, speaker, wrap=wrap)
        sid = session(client, speaker)
        _, path = _up(client, settings, sid, h.xlsx())
        out["d_t1_failed"] = slim(run_turn(client, sid, "remove duplicate rows", path))
        out["d_t2_delivered"] = slim(run_turn(client, sid, "remove duplicate rows", path))
    # e: dedupe, restart the app, then remove empty rows
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        client, settings, _ = app(tmp, h.FakeLLM(h.single("remove_duplicate_rows")), speaker)
        sid = session(client, speaker)
        _, path = _up(client, settings, sid, h.xlsx())
        run_turn(client, sid, "remove duplicate rows", path)
        client2, _, _ = app(tmp, h.FakeLLM(h.single("remove_empty_rows")), speaker)
        out["e_after_restart"] = slim(run_turn(client2, sid, "now remove the empty rows too", path))
    # f: dedupe, then a chain in one turn
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        llm = h.FakeLLM(
            h.single("remove_duplicate_rows")
            + h.chain(("spreadsheet.deduplicate", "remove duplicate rows"), ("spreadsheet.remove_empty_rows", "then remove empty rows"))
        )
        client, settings, _ = app(tmp, llm, speaker)
        sid = session(client, speaker)
        _, path = _up(client, settings, sid, h.xlsx())
        run_turn(client, sid, "remove duplicate rows", path)
        out["f_chain_after_delivery"] = slim(run_turn(client, sid, "remove duplicate rows, then remove empty rows", path))
    return out


# -- M3: the finished record can't be saved -------------------------------------------


@probe
def m3_save_failures(speaker: str) -> dict[str, Any]:
    if speaker != "template":
        return {}
    out: dict[str, Any] = {"probe": "m3_save_failures"}
    real_save_turn = SessionStore.save_turn
    real_save_session = SessionStore.save_session

    def scenario(label: str, save_turn: Any = None, save_session: Any = None, **kw: Any) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            llm = h.FakeLLM(h.single("remove_duplicate_rows") + h.single("remove_empty_rows"))
            client, settings, _ = app(tmp, llm, "template", **kw)
            sid = session(client, "template")
            _, path = _up(client, settings, sid, h.xlsx())
            with contextlib.ExitStack() as stack:
                if save_turn is not None:
                    stack.enter_context(patched(SessionStore, "save_turn", save_turn))
                if save_session is not None:
                    stack.enter_context(patched(SessionStore, "save_session", save_session))
                t1 = run_turn(client, sid, "remove duplicate rows", path)
            listed = client.get(f"/api/v1/sessions/{sid}/files").json()["files"]
            outputs_on_disk = [p.name for p in settings.outputs_dir.rglob("*.xlsx")]
            client2, _, _ = app(tmp, h.FakeLLM(h.single("remove_empty_rows")), "template")
            s2 = client2.get(f"/api/v1/sessions/{sid}")
            t1_after = client2.get(f"/api/v1/sessions/{sid}/turns/{t1.get('turn_id')}")
            listed2 = client2.get(f"/api/v1/sessions/{sid}/files")
            dl2 = None
            if listed2.status_code == 200:
                d = [f for f in listed2.json()["files"] if f["kind"] == "delivered"]
                if d:
                    dl2 = client2.get(f"/api/v1/sessions/{sid}/files/{d[0]['file_id']}/download").status_code
            t2 = run_turn(client2, sid, "now remove the empty rows too", path) if s2.status_code == 200 else None
            out[label] = {
                "t1_memory": slim(t1),
                "listed_memory": [f["kind"] for f in listed],
                "outputs_on_disk": len(outputs_on_disk),
                "restart_session_http": s2.status_code,
                "restart_t1": (t1_after.json().get("outcome"), t1_after.json().get("reply")) if t1_after.status_code == 200 else t1_after.status_code,
                "restart_listed": [f["kind"] for f in listed2.json()["files"]] if listed2.status_code == 200 else listed2.status_code,
                "restart_download": dl2,
                "restart_t2": slim(t2) if t2 else None,
                "memory_and_disk_agree": (t1.get("outcome"), t1.get("reply"))
                == ((t1_after.json().get("outcome"), t1_after.json().get("reply")) if t1_after.status_code == 200 else None),
            }

    state = {"n": 0}

    def first_finished_fails(self: Any, record: Any, turn: Any) -> None:
        if turn.status == "finished":
            state["n"] += 1
            if state["n"] == 1:
                raise OSError(28, "No space left on device")
        real_save_turn(self, record, turn)

    def every_finished_fails(self: Any, record: Any, turn: Any) -> None:
        if turn.status == "finished":
            raise OSError(28, "No space left on device")
        real_save_turn(self, record, turn)

    session_calls = {"n": 0}

    def session_fails_after_start(self: Any, record: Any) -> None:
        session_calls["n"] += 1
        if session_calls["n"] > 1:  # the start write succeeds; the finalize write fails
            raise OSError(5, "I/O error")
        real_save_session(self, record)

    scenario("first_finished_save_fails_then_fallback_saves", save_turn=first_finished_fails)
    scenario("every_finished_save_fails", save_turn=every_finished_fails)
    scenario("session_save_fails_at_finalize", save_session=session_fails_after_start)

    # An integrity failure whose finished save fails: which outcome is shown?
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        def wrap(cycle: Any) -> Any:
            inner = cycle.hands

            def hands(state: Any) -> Any:
                result = inner(state)
                p = Path(state.context.active_file)
                p.chmod(0o644)
                with p.open("ab") as fh:
                    fh.write(b"x")
                return result

            return dataclasses.replace(cycle, hands=hands)

        client, settings, _ = app(tmp, h.FakeLLM(h.single("remove_duplicate_rows")), "template", wrap=wrap)
        sid = session(client, "template")
        _, path = _up(client, settings, sid, h.xlsx())
        with patched(SessionStore, "save_turn", every_finished_fails):
            t = run_turn(client, sid, "remove duplicate rows", path)
        out["integrity_failure_and_save_fails"] = slim(t)

    # A BaseException (not Exception) from the finished save.
    class Abort(BaseException):
        pass

    def finished_raises_base(self: Any, record: Any, turn: Any) -> None:
        if turn.status == "finished":
            raise Abort()
        real_save_turn(self, record, turn)

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        client, settings, _ = app(tmp, h.FakeLLM(h.single("remove_duplicate_rows") + h.single("unclear")), "template", turn_deadline_s=2.0)
        sid = session(client, "template")
        _, path = _up(client, settings, sid, h.xlsx())
        with patched(SessionStore, "save_turn", finished_raises_base):
            started = client.post(f"/api/v1/sessions/{sid}/turns", json={"message": "remove duplicate rows"})
            time.sleep(4)
        body = client.get(f"/api/v1/sessions/{sid}/turns/{started.json()['turn_id']}").json()
        nxt = client.post(f"/api/v1/sessions/{sid}/turns", json={"message": "hi"})
        out["base_exception_in_finished_save"] = {
            "status_after_4s": body.get("status"),
            "outcome": body.get("outcome"),
            "next_turn_http": nxt.status_code,
            "runner_running": client.app.state.runner.running,
        }
    return out


# -- L1: log redaction keeps the record's shape ---------------------------------------


@probe
def l1_redaction(speaker: str) -> dict[str, Any]:
    if speaker != "template":
        return {}
    from uvicorn.logging import AccessFormatter, DefaultFormatter

    from backend.logging_filter import _redact_record

    sid = "s_AbCdEfGhIjKlMnOpQrStUv"
    key = "gsk_" + "Z" * 20
    out: dict[str, Any] = {"probe": "l1_redaction"}

    def fmt(name: str, msg: Any, args: Any, formatter: logging.Formatter, exc: BaseException | None = None) -> str:
        record = logging.LogRecord(name, logging.INFO, __file__, 1, msg, args, (type(exc), exc, exc.__traceback__) if exc else None)
        _redact_record(record)
        try:
            return formatter.format(record)
        except Exception as e:  # noqa: BLE001
            return f"FORMAT ERROR {type(e).__name__}"

    access = AccessFormatter('%(client_addr)s - "%(request_line)s" %(status_code)s', use_colors=False)
    plain = DefaultFormatter("%(message)s", use_colors=False)
    cases = {
        "access_path": ("uvicorn.access", '%s - "%s %s HTTP/%s" %d', ("127.0.0.1:1", "GET", f"/api/v1/sessions/{sid}/files", "1.1", 200), access),
        "split_across_msg_and_arg": ("x", "key=gsk_%s", ("Z" * 20,), plain),
        "non_str_arg": ("x", "value %s", ({"k": key},), plain),
        "dict_args": ("x", "%(a)s and %(b)d", ({"a": f"/sessions/{sid}", "b": 3},), plain),
        "percent_in_arg": ("x", "%s done", ("100% of /sessions/" + sid,), plain),
        "non_str_msg": ("x", RuntimeError(f"boom {key}"), (), plain),
        "int_arg_d": ("x", "%d files in /sessions/%s", (3, sid), plain),
        "no_args_percent": ("x", f"50% in /sessions/{sid}", (), plain),
    }
    for name, (logger_name, msg, args, formatter) in cases.items():
        if args and len(args) == 1 and isinstance(args[0], dict) and name == "dict_args":
            args = args[0]
        text = fmt(logger_name, msg, args, formatter)
        out[name] = {"text": text, "secret_leaked": sid in text or key in text or ("Z" * 20) in text}
    try:
        raise ValueError(f"failed for {key} at /sessions/{sid}")
    except ValueError as exc:
        text = fmt("x", "oops", (), logging.Formatter("%(message)s"), exc)
        out["exc_info"] = {"secret_leaked": sid in text or key in text}
    return out


# -- L8: odd text in requests ---------------------------------------------------------


@probe
def l8_requests(speaker: str) -> dict[str, Any]:
    if speaker != "template":
        return {}
    out: dict[str, Any] = {"probe": "l8_requests"}
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        client, settings, _ = app(tmp, h.FakeLLM(h.single("unclear") * 5), "template")
        sid = session(client, "template")

        def post(raw: bytes) -> Any:
            r = client.post(f"/api/v1/sessions/{sid}/turns", content=raw, headers={"Content-Type": "application/json"})
            body = r.json()
            if r.status_code == 202:
                h_ = client.get(f"/api/v1/sessions/{sid}/turns/{body['turn_id']}")
                deadline = time.monotonic() + 30
                while h_.json().get("status") != "finished" and time.monotonic() < deadline:
                    time.sleep(0.02)
                    h_ = client.get(f"/api/v1/sessions/{sid}/turns/{body['turn_id']}")
            return r.status_code, body.get("error", {}).get("code") if isinstance(body, dict) else None

        out["lone_high_surrogate"] = post(b'{"message": "hi \\ud800"}')
        out["lone_low_surrogate"] = post(b'{"message": "\\udc00 there"}')
        out["valid_pair_emoji"] = post(b'{"message": "thanks \\ud83d\\ude00"}')
        out["surrogate_in_file_id"] = post(b'{"message": "hi", "file_id": "f_\\ud800"}')
        out["surrogate_in_mode"] = post(b'{"message": "hi", "mode": "\\ud800"}')
        out["surrogate_only_after_strip"] = post(b'{"message": "  \\ud800  "}')
        # Upload filename carrying a lone surrogate (RFC 5987 percent-encoded bytes ED A0 80)
        r = client.post(
            f"/api/v1/sessions/{sid}/files",
            files={"file": ("rep\udcedort.xlsx".encode("utf-8", "surrogateescape").decode("latin-1"), h.xlsx(), "application/octet-stream")},
        )
        out["upload_name_odd_bytes"] = (r.status_code, r.json().get("name") or r.json().get("error", {}).get("code"))
        allow = {}
        for method, path in (
            ("DELETE", f"/api/v1/sessions/{sid}/turns"),
            ("PUT", f"/api/v1/sessions/{sid}"),
            ("POST", "/api/v1/health"),
            ("PATCH", f"/api/v1/sessions/{sid}/files"),
        ):
            r = client.request(method, path)
            allow[f"{method} {path.replace(sid, 'SID')}"] = (r.status_code, r.headers.get("allow"), r.json().get("error", {}).get("code"))
        out["405"] = allow
        out["turns_after"] = len(client.get(f"/api/v1/sessions/{sid}").json()["turns"])
    return out


# -- L11: a stored detail must validate strictly --------------------------------------


@probe
def l11_restore(speaker: str) -> dict[str, Any]:
    if speaker != "template":
        return {}
    out: dict[str, Any] = {"probe": "l11_restore"}
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        llm = h.FakeLLM(
            h.single("remove_duplicate_rows")
            + h.chain(("spreadsheet.deduplicate", "remove duplicate rows"), ("spreadsheet.remove_empty_rows", "then remove empty rows"))
            + [h.understand("remove_duplicate_rows"), h.think(clarify=True)]
        )
        client, settings, _ = app(tmp, llm, "template")
        sid = session(client, "template")
        _, path = _up(client, settings, sid, h.xlsx())
        turns = [
            run_turn(client, sid, "remove duplicate rows", path),
            run_turn(client, sid, "remove duplicate rows, then remove empty rows", path),
            run_turn(client, sid, "clean it", path),
        ]
        before = [client.get(f"/api/v1/sessions/{sid}/turns/{t['turn_id']}").json() for t in turns]
        client2, _, _ = app(tmp, h.FakeLLM(), "template")
        r = client2.get(f"/api/v1/sessions/{sid}")
        after = [client2.get(f"/api/v1/sessions/{sid}/turns/{t['turn_id']}").json() for t in turns]
        out["restored_http"] = r.status_code
        out["details_identical_after_restart"] = [b.get("detail") == a.get("detail") for b, a in zip(before, after, strict=True)]
        out["details_non_null"] = [a.get("detail") is not None for a in after]
        meta = next(settings.sessions_dir.iterdir())
        rec = meta / "turns" / f"{turns[0]['turn_id']}.json"
        original = rec.read_text()
        variants = {
            "extra_key_in_detail": lambda d: d["detail"].update({"surprise": "x"}),
            "verified_as_string": lambda d: d["detail"]["steps"][0].update({"verified": "true"}),
            "verified_as_int": lambda d: d["detail"]["steps"][0].update({"verified": 1}),
            "steps_not_a_list": lambda d: d["detail"].update({"steps": "abc"}),
            "detail_is_list": lambda d: d.update({"detail": []}),
        }
        for name, change in variants.items():
            data = json.loads(original)
            change(data)
            rec.write_text(json.dumps(data))
            c = app(tmp, h.FakeLLM(), "template")[0]
            s = c.get(f"/api/v1/sessions/{sid}")
            t = c.get(f"/api/v1/sessions/{sid}/turns/{turns[0]['turn_id']}")
            out[name] = (s.status_code, t.status_code)
        rec.write_text(original)
    return out



@probe
def l11_more(speaker: str) -> dict[str, Any]:
    """Declined, stopped-chain, no-intent and unknown-capability turns restore after a restart."""
    if speaker != "template":
        return {}
    out: dict[str, Any] = {"probe": "l11_more"}
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        llm = h.FakeLLM(
            h.single("remove_duplicate_rows")
            + h.chain(("spreadsheet.deduplicate", "remove duplicate rows"), ("spreadsheet.remove_empty_rows", "then remove empty rows"))
            + ["not json", "not json either"]
            + h.chain(("spreadsheet.delete_everything", "delete everything"))
        )
        client, settings, _ = app(tmp, llm, "template", auto_approve=False)
        sid = session(client, "template")
        _, path = _up(client, settings, sid, h.xlsx())
        messages = ("remove duplicate rows", "remove duplicate rows, then remove empty rows", "?", "delete everything")
        turns = [run_turn(client, sid, m, path) for m in messages]
        before = [client.get(f"/api/v1/sessions/{sid}/turns/{t['turn_id']}").json() for t in turns]
        client2 = app(tmp, h.FakeLLM(), "template")[0]
        out["restored_http"] = client2.get(f"/api/v1/sessions/{sid}").status_code
        after = [client2.get(f"/api/v1/sessions/{sid}/turns/{t['turn_id']}").json() for t in turns]
        out["stopped_at"] = [(b.get("detail") or {}).get("stopped_at") for b in before]
        out["intents"] = [(b.get("detail") or {}).get("intent") for b in before]
        out["same_after_restart"] = [a == b for a, b in zip(after, before, strict=True)]
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only")
    parser.add_argument("--speaker", choices=SPEAKERS)
    args = parser.parse_args()
    logging.basicConfig(level=logging.CRITICAL)
    for name, fn in PROBES.items():
        if args.only and args.only != name:
            continue
        for speaker in (args.speaker,) if args.speaker else SPEAKERS:
            try:
                result = fn(speaker)
            except Exception as exc:  # noqa: BLE001
                import traceback

                result = {"probe": name, "speaker": speaker, "PROBE_ERROR": repr(exc), "tb": traceback.format_exc()[-1500:]}
            if result:
                print(json.dumps(result, default=str), flush=True)


_ = (io, threading)

if __name__ == "__main__":
    main()

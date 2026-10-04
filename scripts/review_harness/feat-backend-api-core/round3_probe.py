# ruff: noqa: E501 — reviewer tooling, not product code
"""Round-3 probes, aimed at the round-2 fixes (R2-M1, R2-L1, R2-L2). Real pipeline, fake LLM.

Usage::

    python scripts/review_harness/feat-backend-api-core/round3_probe.py [--only NAME] [--speaker S]

Runs on whatever checkout it sits in (copy it into a worktree of an older
commit to compare). Each probe prints one JSON object (per speaker where the
speaker matters).

``m2_targets`` checks the per-upload counting of ``earlier_deliveries``. For
every delivered file it also works out, independently, which file it was
made from: it applies its own model of the skill (exact-duplicate removal,
fully-empty-row removal, read with openpyxl) to every upload and every
earlier delivered file of the session, and lists the ones that give exactly
the delivered rows. So "I made this copy from your original upload, not from
a file I handed back earlier" can be checked against what really happened.
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
from api_probe import upload, wait  # noqa: E402

import backend.turns as turns_module  # noqa: E402
from backend.store import SessionStore  # noqa: E402

SPEAKERS = ("template", "friend", "focus")
PROBES: dict[str, Any] = {}
CASES: set[str] | None = None  # m2_targets cases to run (--cases A,F); None means all


def want(case: str) -> bool:
    return CASES is None or case in CASES

try:
    from buddy.voice_catalog import FROM_ORIGINAL_SENTENCE
except ImportError:  # pragma: no cover
    FROM_ORIGINAL_SENTENCE = "I made this copy from your original upload"


def probe(fn: Any) -> Any:
    PROBES[fn.__name__] = fn
    return fn


@contextlib.contextmanager
def patched(owner: Any, name: str, value: Any) -> Any:
    real = getattr(owner, name)
    setattr(owner, name, value)
    try:
        yield real
    finally:
        setattr(owner, name, real)


# -- An independent model of the two skills -------------------------------------------


def _norm(row: tuple[Any, ...]) -> tuple[Any, ...]:
    out = []
    for v in row:
        if v is None or (isinstance(v, str) and not v.strip()):
            out.append(None)
        elif isinstance(v, (int, float)) and not isinstance(v, bool):
            out.append(float(v))
        else:
            out.append(v)
    return tuple(out)


def _dedupe(rows: list[tuple[Any, ...]]) -> list[tuple[Any, ...]]:
    seen: set[tuple[Any, ...]] = set()
    out = []
    for r in map(_norm, rows):
        if r in seen:
            continue
        seen.add(r)
        out.append(r)
    return out


def _drop_empty(rows: list[tuple[Any, ...]]) -> list[tuple[Any, ...]]:
    return [r for r in map(_norm, rows) if not all(v is None for v in r)]


OPS = {
    "dedupe": _dedupe,
    "drop_empty": _drop_empty,
    "dedupe+drop_empty": lambda rows: _drop_empty(_dedupe(rows)),
}


class Session:
    """One API session, with every upload and delivered file kept for the 'made from' check."""

    def __init__(self, client: Any, settings: Any, speaker: str) -> None:
        self.client = client
        self.settings = settings
        self.sid = session(client, speaker)
        self.sources: list[tuple[str, bytes]] = []  # (label, bytes): uploads and deliveries
        self.paths: list[Path] = []

    def up(self, data: bytes, name: str = "report.xlsx") -> dict[str, Any]:
        body = upload(self.client, self.sid, data, name).json()
        self.sources.append((f"upload:{body['name']}:{body['file_id']}", data))
        self.paths.append(upload_path(self.settings, body["name"], body["file_id"]))
        return body

    def turn(self, message: str, op: str | None, **extra: Any) -> dict[str, Any]:
        before = {str(p): h.sha(p) for p in self.paths}
        started = self.client.post(f"/api/v1/sessions/{self.sid}/turns", json={"message": message, **extra})
        if started.status_code != 202:
            return {"http": started.status_code, "body": started.json()}
        body = wait(self.client, self.sid, started.json()["turn_id"])
        after = {str(p): h.sha(p) for p in self.paths}
        reply = body.get("reply") or ""
        out: dict[str, Any] = {
            "turn_id": body.get("turn_id"),
            "message": message,
            "file_id_sent": extra.get("file_id", "(none)"),
            "outcome": body.get("outcome"),
            "reply_source": body.get("reply_source"),
            "reply": reply,
            "has_from_original": FROM_ORIGINAL_SENTENCE in reply,
            "steps": [(s["capability"], s["status"], s["input_name"]) for s in (body.get("detail") or {}).get("steps", [])],
            "uploads_unchanged": before == after,
            "delivered": [],
        }
        for d in body.get("delivered_files", []):
            data = self.client.get(d["download_url"]).content
            rows = [_norm(r) for r in h.read_rows(data)]
            made_from = []
            if op is not None:
                for label, src in self.sources:
                    if OPS[op](h.read_rows(src)) == rows:
                        made_from.append(label)
            out["delivered"].append(
                {
                    "name": d["name"],
                    "sha_matches_listing": h.sha(data) == d["sha256"],
                    "independent": h.describe(h.read_rows(data)),
                    "made_from_by_my_model": made_from,
                }
            )
            self.sources.append((f"delivered:{d['file_id']}", data))
        return out


BUDGET = {"item": ["rent", "food", "rent"], "cost": [900, 200, 900]}


@probe
def m2_targets(speaker: str) -> dict[str, Any]:
    out: dict[str, Any] = {"probe": "m2_targets", "speaker": speaker}

    # A: dedupe A; upload B and dedupe it; go back to A with file_id, "now ... too".
    if want("A"):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            llm = h.FakeLLM(h.single("remove_duplicate_rows") * 2 + h.single("remove_empty_rows"))
            client, settings, _ = app(tmp, llm, speaker)
            s = Session(client, settings, speaker)
            a = s.up(h.xlsx())
            t1 = s.turn("remove duplicate rows", "dedupe")
            s.up(h.xlsx(BUDGET), "budget.xlsx")
            t2 = s.turn("remove duplicate rows", "dedupe")
            t3 = s.turn("now remove the empty rows from report.xlsx too", "drop_empty", file_id=a["file_id"])
            out["A_switch_back"] = [t1, t2, t3]
            out["A_intact"] = uploads_intact(client, settings, s.sid)

    # B: dedupe; upload the *original* again (same bytes and name); "now ... too".
    if want("B"):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            llm = h.FakeLLM(h.single("remove_duplicate_rows") + h.single("remove_empty_rows"))
            client, settings, _ = app(tmp, llm, speaker)
            s = Session(client, settings, speaker)
            original = h.xlsx()  # the same bytes both times, as a file re-read from disk is
            s.up(original)
            t1 = s.turn("remove duplicate rows", "dedupe")
            s.up(original)
            t2 = s.turn("now remove the empty rows too", "drop_empty")
            out["B_reupload_original"] = [t1, t2]
            out["B_same_sha_both_uploads"] = len({f["sha256"] for f in client.get(f"/api/v1/sessions/{s.sid}").json()["files"]}) == 1

    # C: dedupe; re-upload Buddy's copy; two follow-ups on it.
    if want("C"):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            llm = h.FakeLLM(h.single("remove_duplicate_rows") + h.single("remove_empty_rows") + h.single("remove_duplicate_rows"))
            client, settings, _ = app(tmp, llm, speaker)
            s = Session(client, settings, speaker)
            s.up(h.xlsx())
            t1 = s.turn("remove duplicate rows", "dedupe")
            copy_bytes = s.sources[-1][1]
            files = client.get(f"/api/v1/sessions/{s.sid}/files").json()["files"]
            name = next(f["name"] for f in files if f["kind"] == "delivered")
            s.up(copy_bytes, name)
            t2 = s.turn("now remove the empty rows", "drop_empty")
            t3 = s.turn("and remove duplicate rows again", "dedupe")
            out["C_reupload_copy_twice"] = [t1, t2, t3]

    # D: the same upload, file_id sent explicitly on both turns.
    if want("D"):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            llm = h.FakeLLM(h.single("remove_duplicate_rows") + h.single("remove_empty_rows"))
            client, settings, _ = app(tmp, llm, speaker)
            s = Session(client, settings, speaker)
            a = s.up(h.xlsx())
            t1 = s.turn("remove duplicate rows", "dedupe", file_id=a["file_id"])
            t2 = s.turn("now remove the empty rows too", "drop_empty", file_id=a["file_id"])
            out["D_explicit_same_file"] = [t1, t2]

    # E: dedupe A; upload B; restart; file_id A, "now ... too".
    if want("E"):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            client, settings, _ = app(tmp, h.FakeLLM(h.single("remove_duplicate_rows")), speaker)
            s = Session(client, settings, speaker)
            a = s.up(h.xlsx())
            t1 = s.turn("remove duplicate rows", "dedupe")
            s.up(h.xlsx(BUDGET), "budget.xlsx")
            client2, _, _ = app(tmp, h.FakeLLM(h.single("remove_empty_rows")), speaker)
            s.client = client2
            t2 = s.turn("now remove the empty rows from report.xlsx too", "drop_empty", file_id=a["file_id"])
            out["E_restart_switch_back"] = [t1, t2]

    # F: the first turn passes its deadline (its late worker writes a file); then a delivery.
    if want("F"):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            gate = threading.Event()

            def slow_think(system: str, text: str) -> str:
                gate.wait(30)
                return h.think()

            llm = h.FakeLLM([h.understand("remove_duplicate_rows"), slow_think])
            # long enough for an ordinary turn, so only the blocked first turn expires
            client, settings, _ = app(tmp, llm, speaker, turn_deadline_s=4)
            s = Session(client, settings, speaker)
            s.up(h.xlsx())
            t1 = s.turn("remove duplicate rows", "dedupe")
            gate.set()
            time.sleep(3.0)  # the late worker finishes and writes its (unlisted) file
            llm.add(h.single("remove_duplicate_rows"))
            t2 = s.turn("remove duplicate rows", "dedupe")
            out["F_deadline_then_delivery"] = [t1, t2]

    # G: Voice raises on the first turn (failed, nothing handed back); then a delivery.
    if want("G"):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            calls = {"n": 0}

            def wrap(cycle: Any) -> Any:
                inner = cycle.voice

                def voice(state: Any) -> Any:
                    calls["n"] += 1
                    if calls["n"] == 1:
                        raise RuntimeError("voice boom")
                    return inner(state)

                return dataclasses.replace(cycle, voice=voice)

            llm = h.FakeLLM(h.single("remove_duplicate_rows") * 2)
            client, settings, _ = app(tmp, llm, speaker, wrap=wrap)
            s = Session(client, settings, speaker)
            s.up(h.xlsx())
            t1 = s.turn("remove duplicate rows", "dedupe")
            t2 = s.turn("remove duplicate rows", "dedupe")
            out["G_voice_raised_then_delivery"] = [t1, t2]

    # H: two uploads with the same name and different content.
    if want("H"):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            llm = h.FakeLLM(h.single("remove_duplicate_rows") * 2 + h.single("remove_empty_rows"))
            client, settings, _ = app(tmp, llm, speaker)
            s = Session(client, settings, speaker)
            a = s.up(h.xlsx())
            t1 = s.turn("remove duplicate rows", "dedupe")
            s.up(h.xlsx(BUDGET), "report.xlsx")
            t2 = s.turn("remove duplicate rows", "dedupe")
            t3 = s.turn("now remove the empty rows too", "drop_empty", file_id=a["file_id"])
            out["H_same_name_two_uploads"] = [t1, t2, t3]

    # I: chain on A; re-upload the chain's copy; dedupe it.
    if want("I"):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            llm = h.FakeLLM(
                h.chain(("spreadsheet.deduplicate", "remove duplicate rows"), ("spreadsheet.remove_empty_rows", "then remove empty rows"))
                + h.single("remove_duplicate_rows")
            )
            client, settings, _ = app(tmp, llm, speaker)
            s = Session(client, settings, speaker)
            s.up(h.xlsx())
            t1 = s.turn("remove duplicate rows, then remove empty rows", "dedupe+drop_empty")
            copy_bytes = s.sources[-1][1]
            s.up(copy_bytes, "report_clean.xlsx")
            t2 = s.turn("remove duplicate rows", "dedupe")
            out["I_chain_then_reupload"] = [t1, t2]

    # J: deliveries on B only; then file_id A (no delivery of its own yet).
    if want("J"):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            llm = h.FakeLLM(h.single("remove_duplicate_rows") * 2)
            client, settings, _ = app(tmp, llm, speaker)
            s = Session(client, settings, speaker)
            a = s.up(h.xlsx())
            s.up(h.xlsx(BUDGET), "budget.xlsx")
            t1 = s.turn("remove duplicate rows", "dedupe")
            t2 = s.turn("remove duplicate rows from report.xlsx", "dedupe", file_id=a["file_id"])
            out["J_other_upload_had_the_delivery"] = [t1, t2]
    return out


# -- R2-L1: log redaction ---------------------------------------------------------------


@probe
def l1_logging(speaker: str) -> dict[str, Any]:
    """Odd log records through the installed redaction; everything written is checked for leaks."""
    from uvicorn.logging import AccessFormatter

    from backend.logging_filter import install_log_redaction

    install_log_redaction()
    secrets = ["LEAK" + str(i) for i in range(20)]
    K = dict(enumerate(secrets))  # values only at runtime: the "Call stack:" source lines hold none
    results: dict[str, Any] = {"probe": "l1_logging"}

    def emit(name: str, formatter: logging.Formatter, call: Any, logger_name: str | None = None) -> None:
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(formatter)
        log = logging.getLogger(logger_name or f"r3.{name}")
        saved = (list(log.handlers), log.propagate, log.level)
        log.handlers[:] = [handler]
        log.propagate = False
        log.setLevel(logging.DEBUG)
        err = io.StringIO()
        real_err = sys.stderr
        sys.stderr = err
        try:
            call(log)
        finally:
            sys.stderr = real_err
            log.handlers[:], log.propagate, log.level = saved[0], saved[1], saved[2]
        written, errors = stream.getvalue(), err.getvalue()
        results[name] = {
            "written": written.strip()[:200],
            "logging_error": "Logging error" in errors,
            "leaked": sorted({s for s in secrets if s in written or s in errors}),
        }

    plain = logging.Formatter("%(message)s")
    access = AccessFormatter(fmt='%(client_addr)s - "%(request_line)s" %(status_code)s', use_colors=False)
    fmt = '%s - "%s %s HTTP/%s" %d'

    # uvicorn's own access-record shape, with odd paths any client can send
    for label, path in [
        ("access_session_path", "/api/v1/sessions/s_" + K[0] + "abc/turns"),
        ("access_api_key_empty", "/api/v1/healthz?api_key="),
        ("access_bearer_at_end", "/api/v1/healthz?x=Bearer"),
        ("access_api_key_value", "/api/v1/healthz?api_key=" + K[1]),
        ("access_percent_s_in_path", "/api/v1/sessions/s_" + K[2] + "/%s%d"),
        ("access_sessions_with_quote", "/api/v1/sessions/s_" + K[3] + '"x'),
    ]:
        emit(label, access, lambda log, p=path: log.info(fmt, "127.0.0.1:5000", "GET", p, "1.1", 200), "uvicorn.access")

    # records that can't be formatted at all: logging prints msg and args to stderr
    emit("unformattable_str_arg", plain, lambda log: log.warning("x %s %s", "gsk_" + K[4]))
    emit("unformattable_bytes_arg", plain, lambda log: log.warning("x %s %s", ("gsk_" + K[5]).encode()))
    emit("unformattable_exception_arg", plain, lambda log: log.warning("x %s %s", ValueError("gsk_" + K[6])))
    emit("unformattable_dict_arg", plain, lambda log: log.warning("x %(a)s %(b)s", {"a": "gsk_" + K[7]}))
    emit("unformattable_secret_in_format", plain, lambda log: log.warning("Bearer " + K[8] + " %s %s", "x"))
    emit("unformattable_percent_d", plain, lambda log: log.warning("id %d", "gsk_" + K[9]))
    # formattable records
    emit("split_bearer", plain, lambda log: log.warning("Authorization: Bearer %s", K[10]))
    emit("split_gsk_prefix", plain, lambda log: log.warning("key gsk_%s", K[11]))
    emit("bytes_arg", plain, lambda log: log.warning("got %s", ("gsk_" + K[12]).encode()))
    emit("exception_as_msg", plain, lambda log: log.warning(ValueError("api_key=" + K[13])))
    emit("dict_args", plain, lambda log: log.warning("%(k)s done", {"k": "gsk_" + K[14]}))
    emit("literal_percent_no_args", plain, lambda log: log.warning("100%% at /sessions/s_" + K[15]))
    emit("literal_percent_with_args", plain, lambda log: log.warning("100%% at /sessions/%s", "s_" + K[16]))

    def exc(log: logging.Logger) -> None:
        try:
            raise RuntimeError("failed with api_key=" + K[17])
        except RuntimeError:
            log.exception("boom %s", "gsk_" + K[18])

    emit("exception_with_traceback", logging.Formatter("%(message)s"), exc)
    return results


# -- R2-L2: BaseException at finalization --------------------------------------------------


class Halt(BaseException):
    pass


def _finished_or_timeout(client: Any, sid: str, tid: str, seconds: float) -> dict[str, Any]:
    return wait(client, sid, tid, timeout=seconds)


@probe
def l2_base_exception(speaker: str) -> dict[str, Any]:
    out: dict[str, Any] = {"probe": "l2_base_exception", "speaker": speaker}
    real_save_turn = SessionStore.save_turn
    real_save_session = SessionStore.save_session

    def run_case(name: str, owner: Any, attr: str, value: Any) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            client, settings, _ = app(tmp, h.FakeLLM(h.single("remove_duplicate_rows") + h.single("unclear")), speaker, turn_deadline_s=10)
            sid = session(client, speaker)
            up = upload(client, sid, h.xlsx()).json()
            path = upload_path(settings, up["name"], up["file_id"])
            before = h.sha(path)
            with patched(owner, attr, value):
                started = client.post(f"/api/v1/sessions/{sid}/turns", json={"message": "remove duplicate rows"})
                tid = started.json()["turn_id"]
                body = _finished_or_timeout(client, sid, tid, 20)
            busy = client.get(f"/api/v1/sessions/{sid}").json()["busy"]
            nxt = client.post(f"/api/v1/sessions/{sid}/turns", json={"message": "hi"})
            if nxt.status_code == 202:
                wait(client, sid, nxt.json()["turn_id"])
            client2, _, _ = app(tmp, h.FakeLLM([]), speaker)
            restored = client2.get(f"/api/v1/sessions/{sid}/turns/{tid}").json()
            out[name] = {
                "status": body.get("status"),
                "outcome": body.get("outcome"),
                "reply": body.get("reply"),
                "files": len(body.get("delivered_files") or []),
                "busy_after": busy,
                "next_turn_http": nxt.status_code,
                "after_restart": (restored.get("status"), restored.get("outcome"), len(restored.get("delivered_files") or [])),
                "original_unchanged": h.sha(path) == before,
            }

    def finished_raises(exc: type[BaseException], always: bool) -> Any:
        state = {"n": 0}

        def save_turn(self: Any, record: Any, turn: Any) -> None:
            if turn.status == "finished":
                state["n"] += 1
                if always or state["n"] == 1:
                    raise exc()
            real_save_turn(self, record, turn)

        return save_turn

    run_case("halt_once", SessionStore, "save_turn", finished_raises(Halt, False))
    run_case("halt_always", SessionStore, "save_turn", finished_raises(Halt, True))
    run_case("keyboard_interrupt_once", SessionStore, "save_turn", finished_raises(KeyboardInterrupt, False))
    run_case("system_exit_always", SessionStore, "save_turn", finished_raises(SystemExit, True))

    def session_halts(self: Any, record: Any) -> None:
        if record.running_turn_id is None and record.turns and record.turns[-1].status == "finished":
            raise Halt()
        real_save_session(self, record)

    run_case("save_session_halts_at_finalize", SessionStore, "save_session", session_halts)

    def boom(*a: Any, **k: Any) -> Any:
        raise RuntimeError("integrity check boom")

    # Not new code: the steps of _finalize before the one-shot finish that no try covers
    run_case("uploads_unchanged_raises", turns_module, "uploads_unchanged", boom)
    run_case("make_read_only_raises", turns_module, "make_read_only", boom)
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only")
    parser.add_argument("--speaker")
    parser.add_argument("--cases", help="m2_targets only: comma-separated letters, e.g. A,F")
    args = parser.parse_args()
    global CASES
    CASES = set(args.cases.split(",")) if args.cases else None
    for name, fn in PROBES.items():
        if args.only and args.only != name:
            continue
        speakers = [args.speaker] if args.speaker else (SPEAKERS if name != "l1_logging" else ("-",))
        for speaker in speakers:
            print(json.dumps(fn(speaker), default=str), flush=True)


if __name__ == "__main__":
    main()

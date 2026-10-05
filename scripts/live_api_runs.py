"""Live runs through the real HTTP server with a real LLM (design Section 7.5).

Starts ``python -m backend`` as a subprocess (uvicorn on 127.0.0.1, one
worker) in its default configuration, with a fresh data root under
``scratch/live-api/``, then drives it over HTTP: sessions, uploads, turns,
polling, the plan confirmation, downloads. Every file-changing turn shows
its plan and waits for an answer (PR-3); the driver answers it over HTTP
(``POST .../confirmation``) with the run's decision, and records each plan
shown.

Runs 1, 2, 5, 6, 7, 8 and 9 of design 7.5 (approving each plan), plus:
- "3": "Remove the empty rows and then the duplicates", approved (a chain
  through the API: one plan, one final file);
- "4": "Remove duplicate rows", rejected (no file; the reply doesn't say it
  was done);
- "T": "remove duplicate rows", never answered: the plan times out at the
  server's confirmation timeout, which counts as a rejection;
- "C": "remove duplicate rows, then remove empty rows", approved once (a
  two-step chain: exactly one plan, both steps run, step 2 reads step 1's
  result);
- "R": the delivered copy downloaded, uploaded again, and "now remove the
  empty rows too" on it (review round 3's suggestion);
- "9b": run 9's two turns on a fixture just under the 2 MiB default cap
  ("Decisions (2026-10-04)", item 3), at the default deadline.

For each turn it prints what ``CLAUDE.md`` asks for: the intent,
``clarification_required``, the steps, whether a file was delivered, an
independent check of the downloaded file (pandas, against the original),
the reply, the numbers the reply states next to the measured ones, and
the original's SHA-256 before and after (the local fixture, the upload's
recorded hash, and the server's stored copy).

It stops at the first sign of a rate limit (any HTTP 429 from the provider
in the server log, even one the SDK retried) and reports how many runs
completed.

Usage (``GROQ_API_KEY`` or ``ANTHROPIC_API_KEY`` set; never printed):
    python scripts/live_api_runs.py [--runs 3,4,T,C] [--port 8765] [--pause 8]
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRATCH = REPO_ROOT / "scratch" / "live-api"
MAX_UPLOAD = 5 * 1024 * 1024  # run 9's fixture: the old default cap
MAX_UPLOAD_2MB = 2 * 1024 * 1024  # run 9b's fixture: the current default cap
PAUSE_S = 8.0
# Every session id created, to check none reaches a server log.
SESSION_IDS: list[str] = []


class RateLimited(Exception):
    pass


# -- Fixtures ----------------------------------------------------------------------------


def default_fixture() -> bytes:
    """6 rows: 2 exact duplicates and 1 fully empty row (mid-file)."""
    return _xlsx(
        pd.DataFrame(
            {
                "name": ["Alice", "Bob", None, "Alice", "Carol", "Bob"],
                "amount": [10, 20, None, 10, 30, 20],
            }
        )
    )


def no_duplicates_fixture() -> bytes:
    return _xlsx(
        pd.DataFrame({"name": ["Alice", "Bob", "Carol", "Dana"], "amount": [10, 20, 30, 40]})
    )


def five_mb_fixture() -> bytes:
    """The design's measured shape: 14 columns (6 floats, 3 ints, 4 short strings, 1 date),
    5% duplicate rows, plus a few fully empty rows, as large as fits under 5 MiB."""
    return sized_fixture(MAX_UPLOAD, 52_000)


def sized_fixture(cap: int, rows: int) -> bytes:
    """The wide frame, as large as fits under ``cap`` (between 95% and 100% of it)."""
    for _ in range(4):
        data = _xlsx(_wide_frame(rows))
        if 0.95 * cap <= len(data) <= cap:
            return data
        rows = int(rows * cap / len(data) * 0.98)
    if len(data) > cap:
        raise SystemExit("couldn't build a fixture under the upload cap")
    return data


def _wide_frame(rows: int) -> pd.DataFrame:
    rng = np.random.default_rng(7)
    unique = rows - rows // 20
    frame = pd.DataFrame(
        {
            **{f"f{i}": rng.normal(size=unique).round(4) for i in range(6)},
            **{f"i{i}": rng.integers(0, 1_000_000, size=unique) for i in range(3)},
            **{f"s{i}": rng.choice(["alpha", "beta", "gamma", "delta", "eps"], size=unique)
               for i in range(4)},
            "d": pd.Timestamp("2026-01-01") + pd.to_timedelta(rng.integers(0, 365, unique), "D"),
        }
    )
    duplicates = frame.sample(n=rows - unique, random_state=7)
    frame = pd.concat([frame, duplicates], ignore_index=True).sample(frac=1, random_state=7)
    frame = frame.reset_index(drop=True)
    for position in (10, 1000, 20000):  # a few fully empty rows, mid-file
        if position < len(frame):
            frame.loc[position] = [None] * len(frame.columns)
    return frame


def _xlsx(frame: pd.DataFrame) -> bytes:
    buffer = io.BytesIO()
    frame.to_excel(buffer, index=False, engine="openpyxl")
    return buffer.getvalue()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# -- The server ----------------------------------------------------------------------------


class Server:
    def __init__(
        self, data_dir: Path, port: int, log_path: Path, extra_env: dict[str, str] | None = None
    ) -> None:
        self.data_dir = data_dir
        self.port = port
        self.log_path = log_path
        self.extra_env = extra_env or {}
        self.process: subprocess.Popen[bytes] | None = None
        self.base = f"http://127.0.0.1:{port}"

    def start(self) -> None:
        env = dict(os.environ)
        env["WORKBUDDY_DATA_DIR"] = str(self.data_dir)
        env.pop("WORKBUDDY_AUTO_APPROVE", None)  # retired; a server always asks
        env["PYTHONUNBUFFERED"] = "1"
        env.update(self.extra_env)
        log = open(self.log_path, "ab")
        self.process = subprocess.Popen(
            [sys.executable, "-m", "backend", "--port", str(self.port)],
            cwd=REPO_ROOT,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                health = httpx.get(f"{self.base}/healthz", timeout=2, trust_env=False).json()
                if not health["llm_configured"]:
                    raise SystemExit("the server has no LLM configured")
                return
            except httpx.HTTPError:
                time.sleep(0.3)
        raise SystemExit("the server didn't start")

    def kill(self) -> None:
        assert self.process is not None
        self.process.send_signal(signal.SIGKILL)
        self.process.wait(timeout=10)

    def stop(self) -> None:
        if self.process is not None and self.process.poll() is None:
            self.process.send_signal(signal.SIGINT)
            try:
                self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.process.kill()

    def peak_rss_mb(self) -> float | None:
        assert self.process is not None
        try:
            status = Path(f"/proc/{self.process.pid}/status").read_text()
        except OSError:
            return None
        match = re.search(r"VmHWM:\s+(\d+) kB", status)
        return round(int(match.group(1)) / 1024, 1) if match else None

    def log_text(self) -> str:
        return self.log_path.read_text(errors="replace")


# -- Driving it ----------------------------------------------------------------------------------


class Driver:
    def __init__(self, server: Server) -> None:
        self.server = server
        # trust_env=False: requests to 127.0.0.1 must never go through a proxy.
        self.client = httpx.Client(base_url=server.base, timeout=30, trust_env=False)
        self.log_offset = 0
        self.get_retries = 0

    def get(self, url: str) -> httpx.Response:
        """A GET (idempotent), retried on a dropped connection, as a polling client would.

        Every retry is counted and reported: a busy server can reset a
        keep-alive connection while a turn holds the CPU.
        """
        for attempt in range(10):
            try:
                return self.client.get(url)
            except httpx.TransportError:
                self.get_retries += 1
                time.sleep(0.5 * (attempt + 1))
        return self.client.get(url)

    def session(self, mode: str) -> str:
        response = self.client.post("/api/v1/sessions", json={"mode": mode})
        response.raise_for_status()
        session_id = str(response.json()["session_id"])
        SESSION_IDS.append(session_id)
        return session_id

    def upload(self, session_id: str, data: bytes, name: str) -> dict[str, Any]:
        response = self.client.post(
            f"/api/v1/sessions/{session_id}/files", files={"file": (name, data)}
        )
        response.raise_for_status()
        return dict(response.json())

    def turn(
        self,
        session_id: str,
        message: str,
        decision: str | None = "approve",
        timeout_s: float = 1200,
    ) -> dict[str, Any]:
        """Run one turn: poll it, answer its plan with ``decision`` (``None``: never answer).

        The finished turn gets ``_plans`` (each plan shown, once, with how long
        after the start it appeared), ``_answers`` (each answer sent and the
        HTTP status it got) and ``_statuses`` (the statuses seen, in order).
        """
        time.sleep(PAUSE_S)
        started = time.monotonic()
        response = self.client.post(
            f"/api/v1/sessions/{session_id}/turns", json={"message": message}
        )
        response.raise_for_status()
        turn_id = response.json()["turn_id"]
        plans: list[dict[str, Any]] = []
        answers: list[dict[str, Any]] = []
        statuses: list[str] = []
        while True:
            body = self.get(f"/api/v1/sessions/{session_id}/turns/{turn_id}").json()
            if not statuses or statuses[-1] != body["status"]:
                statuses.append(body["status"])
            if body["status"] == "finished":
                body["_wall_s"] = round(time.monotonic() - started, 1)
                body["_get_retries_so_far"] = self.get_retries
                body["_plans"] = plans
                body["_answers"] = answers
                body["_statuses"] = statuses
                self.check_rate_limit()
                return dict(body)
            plan = body.get("pending_confirmation")
            if body["status"] == "awaiting_confirmation" and plan is not None:
                if all(p["confirmation_id"] != plan["confirmation_id"] for p in plans):
                    plans.append({**plan, "_shown_after_s": round(time.monotonic() - started, 1)})
                    if decision is not None:
                        answer = self.client.post(
                            f"/api/v1/sessions/{session_id}/turns/{turn_id}/confirmation",
                            json={"confirmation_id": plan["confirmation_id"], "decision": decision},
                        )
                        answers.append(
                            {
                                "decision": decision,
                                "http_status": answer.status_code,
                                "status_after": answer.json().get("status"),
                                "plan_after": answer.json().get("pending_confirmation"),
                            }
                        )
            if time.monotonic() - started > timeout_s:
                raise SystemExit(f"turn {turn_id} didn't finish in {timeout_s} s")
            time.sleep(0.5)

    def start_turn(self, session_id: str, message: str) -> str:
        time.sleep(PAUSE_S)
        response = self.client.post(
            f"/api/v1/sessions/{session_id}/turns", json={"message": message}
        )
        response.raise_for_status()
        return str(response.json()["turn_id"])

    def download(self, url: str) -> bytes:
        response = self.get(url)
        response.raise_for_status()
        return response.content

    def check_rate_limit(self) -> None:
        text = self.server.log_text()
        new = text[self.log_offset:]
        self.log_offset = len(text)
        if re.search(r"Error code: 429|RateLimitError|rate_limit_exceeded|\b429 Too Many", new):
            raise RateLimited

    def stored_upload(self, session_id: str, upload: dict[str, Any]) -> Path:
        key = hashlib.sha256(session_id.encode()).hexdigest()[:32]
        return self.server.data_dir / "uploads" / key / upload["file_id"] / upload["name"]


def measure(frame: pd.DataFrame) -> dict[str, int]:
    non_empty = frame.dropna(how="all")
    return {
        "rows": len(frame),
        "duplicate_rows": int(non_empty.duplicated().sum()),
        "empty_rows": int(frame.isna().all(axis=1).sum()),
    }


_REPLY_NUMBERS = {
    "removed_duplicates": r"removed (\d+) duplicate row",
    "removed_empty": r"removed (\d+) fully-empty row",
    "copy_rows": r"copy has (\d+) row",
    "duplicates_remaining": r"(\d+) duplicate row\(s\) remaining",
    "empty_remaining": r"(\d+) fully-empty row\(s\) remaining",
}


def reply_numbers(reply: str) -> dict[str, int]:
    found = {}
    for name, pattern in _REPLY_NUMBERS.items():
        match = re.search(pattern, reply)
        if match:
            found[name] = int(match.group(1))
    return found


def report_turn(
    driver: Driver,
    title: str,
    session_id: str,
    upload: dict[str, Any],
    original: bytes,
    turn: dict[str, Any],
    expected: str | None,
) -> dict[str, Any]:
    stored = driver.stored_upload(session_id, upload)
    detail = turn.get("detail") or {}
    entry: dict[str, Any] = {
        "run": title,
        "turn_id": turn["turn_id"],
        "message": turn["message"],
        "wall_s": turn["_wall_s"],
        "client_get_retries_so_far": turn["_get_retries_so_far"],
        "outcome": turn["outcome"],
        "reply_source": turn["reply_source"],
        "intent": detail.get("intent"),
        "clarification_required": detail.get("clarification_required"),
        "steps": [
            f"{s['capability']}{' (re-check)' if s['recheck'] else ''} [{s['status']}, "
            f"verified={s['verified']}] {s['input_name']} -> {', '.join(s['output_names']) or '-'}"
            for s in detail.get("steps", [])
        ],
        "stopped_at": detail.get("stopped_at"),
        "delivered": [f["name"] for f in turn["delivered_files"]],
        "reply": turn["reply"],
        "reply_numbers": reply_numbers(turn["reply"]),
        "original_sha256_local_fixture": sha(original),
        "original_sha256_at_upload": upload["sha256"],
        "original_sha256_stored_after": sha(stored.read_bytes()),
    }
    entry["original_unchanged"] = (
        entry["original_sha256_local_fixture"]
        == entry["original_sha256_at_upload"]
        == entry["original_sha256_stored_after"]
    )
    # The plan confirmation (PR-3): what was shown, what was answered, what ran.
    entry["statuses_seen"] = turn.get("_statuses")
    entry["plans_shown"] = [
        {
            "steps": [(step["capability"], step["prompt"]) for step in plan["steps"]],
            "plan_note": plan["plan_note"],
            "shown_after_s": plan["_shown_after_s"],
            "expires_at": plan["expires_at"],
        }
        for plan in turn.get("_plans", [])
    ]
    entry["answers_sent"] = turn.get("_answers")
    entry["confirmation"] = detail.get("confirmation")
    writes = [
        s
        for s in detail.get("steps", [])
        if s["permission_tier"] in ("write", "external_effect") and not s["recheck"]
    ]
    entry["write_steps"] = [f"{s['capability']} [{s['status']}]" for s in writes]
    if entry["plans_shown"]:
        shown = [capability for capability, _ in entry["plans_shown"][0]["steps"]]
        entry["plan_lists_the_write_steps_in_order"] = shown == [s["capability"] for s in writes]
    ran = [s for s in writes if s["status"] == "success"]
    if len(ran) >= 2:
        entry["each_later_step_read_the_previous_result"] = all(
            later["input_name"] in earlier["output_names"]
            for earlier, later in zip(ran, ran[1:], strict=False)
        )
    key = hashlib.sha256(session_id.encode()).hexdigest()[:32]
    turn_dir = driver.server.data_dir / "outputs" / key / turn["turn_id"]
    entry["spreadsheets_written_in_turn_folder"] = (
        sorted(p.name for p in turn_dir.glob("*.xlsx")) if turn_dir.is_dir() else []
    )
    source = pd.read_excel(io.BytesIO(original))
    entry["original_measured"] = measure(source)
    if turn["delivered_files"]:
        (delivered,) = turn["delivered_files"]
        content = driver.download(delivered["download_url"])
        entry["download_sha256_matches_record"] = sha(content) == delivered["sha256"]
        written = pd.read_excel(io.BytesIO(content))
        entry["delivered_measured"] = measure(written)
        if expected is not None:
            if expected == "dedupe":
                want = source.drop_duplicates()
            elif expected == "empty":
                want = source.dropna(how="all")
            else:  # dedupe and empty rows, either order
                want = source.dropna(how="all").drop_duplicates()
            entry["equals_expected_" + expected] = _same(written, want)
        entry["_delivered_sha256"] = delivered["sha256"]
        entry["_download_url"] = delivered["download_url"]
    return entry


def _same(left: pd.DataFrame, right: pd.DataFrame) -> bool:
    a = left.reset_index(drop=True).astype(str)
    b = right.reset_index(drop=True).astype(str)
    if a.shape != b.shape:
        return False
    a = a.replace(r"\.0$", "", regex=True)
    b = b.replace(r"\.0$", "", regex=True)
    return bool((a.values == b.values).all())


# -- The runs -------------------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", default="3,4,T,C")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--pause", type=float, default=8.0, help="seconds between turns, to spare the rate limit"
    )
    args = parser.parse_args()
    runs = [r if r in ("R", "9b", "T", "C") else int(r) for r in args.runs.split(",")]
    global PAUSE_S
    PAUSE_S = args.pause
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    root = SCRATCH / stamp
    root.mkdir(parents=True)
    results: list[dict[str, Any]] = []
    completed: list[int | str] = []
    print(f"data and logs under {root}")
    server = Server(root / "data", args.port, root / "server.log")
    server.start()
    driver = Driver(server)
    try:
        default = default_fixture()
        first_session = first_upload = None
        if any(r in runs for r in (1, 2, 7)):
            first_session = driver.session("focus")
            first_upload = driver.upload(first_session, default, "manual_report.xlsx")
        if 1 in runs:
            turn = driver.turn(first_session, "remove duplicate rows")
            results.append(
                report_turn(driver, "1", first_session, first_upload, default, turn, "dedupe")
            )
            completed.append(1)
        if 2 in runs:
            run1 = results[0] if results and results[0]["run"] == "1" else None
            turn = driver.turn(first_session, "remove the empty rows")
            entry = report_turn(driver, "2", first_session, first_upload, default, turn, "empty")
            if run1 and run1.get("_download_url"):
                again = driver.download(run1["_download_url"])
                entry["run1_file_byte_identical_after_run2"] = (
                    sha(again) == run1["_delivered_sha256"]
                )
                key = hashlib.sha256(first_session.encode()).hexdigest()[:32]
                outputs = sorted(p.relative_to(server.data_dir / "outputs" / key).as_posix()
                                 for p in (server.data_dir / "outputs" / key).rglob("*.xlsx"))
                entry["turn_folders"] = outputs
            results.append(entry)
            completed.append(2)
        if 5 in runs:
            session = driver.session("friend")
            upload = driver.upload(session, default, "manual_report.xlsx")
            turn = driver.turn(session, "hi, what can you do?")
            results.append(report_turn(driver, "5", session, upload, default, turn, None))
            completed.append(5)
        if 6 in runs:
            data = no_duplicates_fixture()
            session = driver.session("focus")
            upload = driver.upload(session, data, "manual_report.xlsx")
            turn = driver.turn(session, "remove duplicate rows")
            results.append(report_turn(driver, "6", session, upload, data, turn, "dedupe"))
            completed.append(6)
        if 7 in runs:
            turn = driver.turn(first_session, "now remove the empty rows too")
            results.append(
                report_turn(driver, "7", first_session, first_upload, default, turn, "empty")
            )
            completed.append(7)
        if 3 in runs:
            session = driver.session("focus")
            upload = driver.upload(session, default, "manual_report.xlsx")
            turn = driver.turn(session, "Remove the empty rows and then the duplicates", "approve")
            results.append(report_turn(driver, "3", session, upload, default, turn, "both"))
            completed.append(3)
        if 4 in runs:
            session = driver.session("focus")
            upload = driver.upload(session, default, "manual_report.xlsx")
            turn = driver.turn(session, "Remove duplicate rows", "reject")
            results.append(report_turn(driver, "4", session, upload, default, turn, None))
            completed.append(4)
        if "T" in runs:
            session = driver.session("friend")
            upload = driver.upload(session, default, "manual_report.xlsx")
            turn = driver.turn(session, "remove duplicate rows", None)
            entry = report_turn(driver, "T", session, upload, default, turn, None)
            if turn["_plans"]:
                late = driver.client.post(
                    f"/api/v1/sessions/{session}/turns/{turn['turn_id']}/confirmation",
                    json={
                        "confirmation_id": turn["_plans"][0]["confirmation_id"],
                        "decision": "approve",
                    },
                )
                entry["late_approve_after_timeout"] = {
                    "http_status": late.status_code,
                    "error_code": late.json().get("error", {}).get("code"),
                }
            results.append(entry)
            completed.append("T")
        if "C" in runs:
            session = driver.session("focus")
            upload = driver.upload(session, default, "manual_report.xlsx")
            turn = driver.turn(
                session, "remove duplicate rows, then remove empty rows", "approve"
            )
            results.append(report_turn(driver, "C", session, upload, default, turn, "both"))
            completed.append("C")
        if "R" in runs:
            session = driver.session("focus")
            upload = driver.upload(session, default, "manual_report.xlsx")
            first = driver.turn(session, "remove duplicate rows")
            results.append(report_turn(driver, "R.1", session, upload, default, first, "dedupe"))
            if first["delivered_files"]:
                (delivered,) = first["delivered_files"]
                copy_bytes = driver.download(delivered["download_url"])
                second_upload = driver.upload(session, copy_bytes, delivered["name"])
                turn = driver.turn(session, "now remove the empty rows too")
                entry = report_turn(
                    driver, "R.2", session, second_upload, copy_bytes, turn, "empty"
                )
                entry["first_upload_sha256_after"] = sha(
                    driver.stored_upload(session, upload).read_bytes()
                )
                entry["first_upload_unchanged"] = entry["first_upload_sha256_after"] == sha(
                    default
                )
                results.append(entry)
            completed.append("R")
        if 8 in runs:
            session = driver.session("focus")
            upload = driver.upload(session, default, "manual_report.xlsx")
            turn_id = driver.start_turn(session, "remove duplicate rows")
            time.sleep(0.5)
            mid = driver.get(f"/api/v1/sessions/{session}/turns/{turn_id}").json()
            server.kill()
            driver.check_rate_limit()  # what the killed process logged
            if mid["status"] != "running":
                print("WARNING: run 8's turn had finished before the kill; the run is invalid")
            server.start()
            driver = Driver(server)
            driver.log_offset = len(server.log_text())
            after = driver.get(f"/api/v1/sessions/{session}/turns/{turn_id}").json()
            state = driver.get(f"/api/v1/sessions/{session}").json()
            stored = driver.stored_upload(session, upload)
            results.append({
                "run": "8",
                "turn_id": turn_id,
                "status_before_kill": mid["status"],
                "status_after_restart": after["status"],
                "outcome": after["outcome"],
                "reply_source": after["reply_source"],
                "reply": after["reply"],
                "delivered": [f["name"] for f in after["delivered_files"]],
                "session_busy_after_restart": state["busy"],
                "turns_listed": [t["turn_id"] for t in state["turns"]],
                "original_sha256_local_fixture": sha(default),
                "original_sha256_at_upload": upload["sha256"],
                "original_sha256_stored_after": sha(stored.read_bytes()),
            })
            completed.append(8)
        if 9 in runs:
            server.stop()
            driver.check_rate_limit()
            # The 5 MB measurement needs the old cap; the default is 2 MiB now
            # (review round 4, R4-L1).
            server = Server(
                root / "data9",
                args.port,
                root / "server9.log",
                extra_env={"WORKBUDDY_MAX_UPLOAD_BYTES": str(MAX_UPLOAD)},
            )
            server.start()
            driver = Driver(server)
            data = five_mb_fixture()
            print(f"5 MB fixture: {len(data)} bytes")
            for message, expected in (
                ("remove duplicate rows", "dedupe"),
                ("remove duplicate rows, then remove empty rows", "both"),
            ):
                session = driver.session("focus")
                upload = driver.upload(session, data, "big_report.xlsx")
                turn = driver.turn(session, message)
                entry = report_turn(driver, "9", session, upload, data, turn, expected)
                entry["fixture_bytes"] = len(data)
                entry["server_peak_rss_mb"] = server.peak_rss_mb()
                results.append(entry)
            completed.append(9)
        if "9b" in runs:
            # The current default cap, on a fresh server with the default deadline.
            server.stop()
            driver.check_rate_limit()
            server = Server(root / "data9b", args.port, root / "server9b.log")
            server.start()
            driver = Driver(server)
            data = sized_fixture(MAX_UPLOAD_2MB, 24_000)
            print(f"2 MB fixture: {len(data)} bytes")
            for message, expected in (
                ("remove duplicate rows", "dedupe"),
                ("remove duplicate rows, then remove empty rows", "both"),
            ):
                session = driver.session("focus")
                upload = driver.upload(session, data, "big_report.xlsx")
                turn = driver.turn(session, message)
                entry = report_turn(driver, "9b", session, upload, data, turn, expected)
                entry["fixture_bytes"] = len(data)
                entry["server_peak_rss_mb"] = server.peak_rss_mb()
                results.append(entry)
            completed.append("9b")
    except RateLimited:
        print(f"STOPPED: the provider rate-limited (429). Runs completed: {completed}")
    finally:
        server.stop()
        logs = "".join(p.read_text(errors="replace") for p in sorted(root.glob("server*.log")))
        results.append(
            {
                "run": "server logs",
                "logging_errors": logs.count("--- Logging error ---"),
                "tracebacks": logs.count("Traceback (most recent call last)"),
                "session_ids_in_logs": sum(1 for s in SESSION_IDS if s in logs),
                "sessions_created": len(SESSION_IDS),
                "http_429_lines": len(re.findall(r"\b429\b", logs)),
            }
        )
        (root / "results.json").write_text(json.dumps(results, indent=1, default=str))
        print(json.dumps(results, indent=1, default=str))
        print(f"runs completed: {completed}")


if __name__ == "__main__":
    main()

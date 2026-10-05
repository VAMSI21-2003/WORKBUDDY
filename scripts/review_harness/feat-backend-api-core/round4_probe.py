# ruff: noqa: E501 — reviewer tooling, not product code
"""Round-4 probes: the merge of origin/main (PR-B ledger, values-only copy) and the 2 MiB cap.

Usage::

    python scripts/review_harness/feat-backend-api-core/round4_probe.py [--only NAME] [--speaker S]

Real app, real pipeline (``build_engine``, real skills, verifiers, Voice),
fake LLM. Runs on whatever checkout it sits in.

- ``ledger_followups``: later turns in one session where Brain's reply
  carries ``requested_actions`` (the PR-B ledger, which round 1-3 fakes never
  sent), including a model that lists an earlier turn's action again.
- ``formulas_multi_turn``: an original with formulas; a follow-up on the same
  upload; Buddy's values-only copy re-uploaded.
- ``plain_table_api``: uploads that aren't plain tables (merged cells, header
  on row 3): what the API accepts, and the replies for "hi" and a request.
- ``nothing_ran_paths``: later turns that run nothing, with the ledger set.
- ``cap_default`` (template only): the default settings from the
  environment; exactly 2 MiB, one byte more, the override.

Each delivered file is read with openpyxl; ``made_from_by_my_model`` lists
the uploads or earlier delivered files that the reviewer's own model of the
skill turns into exactly the delivered rows.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import harness as h  # noqa: E402
import openpyxl  # noqa: E402
from adversarial_probe import _pad_to, app, uploads_intact  # noqa: E402
from api_probe import Factory, upload  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from round3_probe import Session  # noqa: E402

from backend.main import create_app  # noqa: E402
from backend.settings import Settings  # noqa: E402
from tests import workbook_fixtures as wf  # noqa: E402

SPEAKERS = ("template", "friend", "focus")
PROBES: dict[str, Any] = {}


def probe(fn: Any) -> Any:
    PROBES[fn.__name__] = fn
    return fn


def understand(intent: str, actions: list[tuple[str, str]], confidence: float = 0.95) -> str:
    """A Brain reply with the PR-B ``requested_actions`` field."""
    return json.dumps(
        {
            "intent": intent,
            "confidence": confidence,
            "missing_information": [],
            "notes": "",
            "requested_actions": [{"action": a, "source_phrase": p} for a, p in actions],
        }
    )


def single(intent: str, actions: list[tuple[str, str]]) -> list[str]:
    return [understand(intent, actions), h.think()]


def multi(actions: list[tuple[str, str]], steps: list[tuple[str, str]]) -> list[str]:
    return [understand("multi_step_request", actions, 0.9), h.think(), h.plan(*steps)]


DEDUPE = ("remove_duplicate_rows", "remove duplicate rows")


def _bytes(builder: Any, name: str) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        path = builder(Path(tmp) / name)
        return path.read_bytes()


def _formulas(data: bytes) -> dict[str, int]:
    """Formula cells, and those with no saved value, by the reviewer's own openpyxl read."""
    import io

    f = openpyxl.load_workbook(io.BytesIO(data), data_only=False)
    v = openpyxl.load_workbook(io.BytesIO(data), data_only=True)
    formulas = unsaved = 0
    for ws_f, ws_v in zip(f.worksheets, v.worksheets, strict=True):
        for row in ws_f.iter_rows():
            for cell in row:
                if isinstance(cell.value, str) and cell.value.startswith("="):
                    formulas += 1
                    if ws_v[cell.coordinate].value is None:
                        unsaved += 1
    return {"formulas": formulas, "without_value": unsaved}


# -- Probes ------------------------------------------------------------------------------


@probe
def ledger_followups(speaker: str) -> dict[str, Any]:
    out: dict[str, Any] = {"probe": "ledger_followups", "speaker": speaker}
    cases = {
        # The model lists turn 1's action again (its phrase isn't in this message).
        "L1_history_listed": (
            "now remove the empty rows too",
            "drop_empty",
            single("remove_empty_rows", [DEDUPE, ("remove_empty_rows", "remove the empty rows")]),
        ),
        # The message mentions duplicates; only the mention detector has them.
        "L2_mention": (
            "now the empty rows too, the duplicates are already gone",
            "drop_empty",
            single("remove_empty_rows", [("remove_empty_rows", "the empty rows")]),
        ),
        # An unsupported request on a later turn: nothing runs.
        "L3_unsupported_only": (
            "sort it by amount",
            None,
            single("unclear", [("unsupported", "sort it by amount")]),
        ),
        # A supported and an unsupported part on a later turn.
        "L4_partly_unsupported": (
            "remove empty rows and sort by amount",
            "drop_empty",
            single(
                "remove_empty_rows",
                [("remove_empty_rows", "remove empty rows"), ("unsupported", "sort by amount")],
            ),
        ),
        # The model plans turn 1's action again as a chain step.
        "L5_history_chain": (
            "now remove the empty rows too",
            "dedupe+drop_empty",
            multi(
                [DEDUPE, ("remove_empty_rows", "remove the empty rows")],
                [
                    ("spreadsheet.deduplicate", "remove duplicate rows"),
                    ("spreadsheet.remove_empty_rows", "remove the empty rows"),
                ],
            ),
        ),
        # A later turn the model reads as nothing (mention detector would find "blank lines").
        "L6_misread_unclear": (
            "get rid of the blank lines please",
            None,
            single("unclear", []),
        ),
        # A later "thanks" with the ledger empty.
        "L7_thanks": ("thanks!", None, single("unclear", [])),
    }
    for name, (message, op, replies) in cases.items():
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            llm = h.FakeLLM(single("remove_duplicate_rows", [DEDUPE]) + replies)
            client, settings, _ = app(tmp, llm, speaker)
            s = Session(client, settings, speaker)
            s.up(h.xlsx())
            t1 = s.turn("remove duplicate rows", "dedupe")
            t2 = s.turn(message, op)
            out[name] = {"t1": t1, "t2": t2, "intact": uploads_intact(client, settings, s.sid)}
    return out


@probe
def formulas_multi_turn(speaker: str) -> dict[str, Any]:
    out: dict[str, Any] = {"probe": "formulas_multi_turn", "speaker": speaker}
    for saved in (True, False):
        original = _bytes(lambda p, saved=saved: wf.formatted_formula_sheet(p, saved=saved), "f.xlsx")
        key = "saved" if saved else "unsaved"
        out[f"{key}_my_formula_count"] = _formulas(original)
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            llm = h.FakeLLM(
                single("remove_duplicate_rows", [DEDUPE])
                + single("remove_empty_rows", [("remove_empty_rows", "remove the empty rows")])
                + single("remove_empty_rows", [("remove_empty_rows", "remove the empty rows")])
            )
            client, settings, _ = app(tmp, llm, speaker)
            s = Session(client, settings, speaker)
            s.up(original)
            t1 = s.turn("remove duplicate rows", "dedupe")
            t2 = s.turn("now remove the empty rows too", "drop_empty")
            # Re-upload turn 1's values-only copy and work on it.
            copy = next(src for label, src in s.sources if label.startswith("delivered:"))
            out[f"{key}_copy_formula_count"] = _formulas(copy)
            s.up(copy, "report_clean.xlsx")
            t3 = s.turn("now remove the empty rows", "drop_empty")
            out[key] = {"t1": t1, "t2": t2, "t3": t3, "intact": uploads_intact(client, settings, s.sid)}
    return out


@probe
def plain_table_api(speaker: str) -> dict[str, Any]:
    out: dict[str, Any] = {"probe": "plain_table_api", "speaker": speaker}
    for label, builder in (
        ("merged", wf.merged_cell_sheet),
        ("header_row3", wf.header_on_row_three_sheet),
    ):
        data = _bytes(builder, f"{label}.xlsx")
        for message, replies in (
            ("hi", single("unclear", [])),
            ("remove duplicate rows", single("remove_duplicate_rows", [DEDUPE])),
        ):
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
                llm = h.FakeLLM(replies)
                client, settings, _ = app(tmp, llm, speaker)
                s = Session(client, settings, speaker)
                body = upload(client, s.sid, data, f"{label}.xlsx")
                entry: dict[str, Any] = {"upload_status": body.status_code}
                if body.status_code == 201:
                    up = body.json()
                    s.sources.append((f"upload:{up['name']}", data))
                    from adversarial_probe import upload_path

                    s.paths.append(upload_path(settings, up["name"], up["file_id"]))
                    entry["turn"] = s.turn(message, None)
                    entry["intact"] = uploads_intact(client, settings, s.sid)
                    # Does any text from the file reach the reply?
                    entry["file_text_in_reply"] = [
                        w for w in ("note: totals below", "Quarterly report", "Alice", "Carol")
                        if w in entry["turn"].get("reply", "")
                    ]
                out[f"{label}:{message}"] = entry
    return out


@probe
def nothing_ran_paths(speaker: str) -> dict[str, Any]:
    """First turns that run nothing now that the planner skips analysis of a non-plain table."""
    out: dict[str, Any] = {"probe": "nothing_ran_paths", "speaker": speaker}
    data = _bytes(wf.merged_cell_sheet, "m.xlsx")
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        llm = h.FakeLLM(
            single("unclear", [("unsupported", "sort it by amount")])
            + single("remove_duplicate_rows", [DEDUPE])
        )
        client, settings, _ = app(tmp, llm, speaker)
        s = Session(client, settings, speaker)
        body = upload(client, s.sid, data, "merged.xlsx").json()
        from adversarial_probe import upload_path

        s.paths.append(upload_path(settings, body["name"], body["file_id"]))
        out["t1_sort"] = s.turn("sort it by amount", None)
        out["t2_dedupe"] = s.turn("remove duplicate rows", None)
        out["intact"] = uploads_intact(client, settings, s.sid)
    return out


def _two_sheets_second_elsewhere() -> bytes:
    """A real two-sheet workbook (declared in workbook.xml) whose second sheet part is
    stored outside ``xl/worksheets/``, with its relationship and content type moved too."""
    import io
    import zipfile

    book = openpyxl.Workbook()
    first = book.active
    first.title = "Data"
    for row in (["name", "amount"], ["Alice", 10], ["Bob", 20], ["Alice", 10]):
        first.append(row)
    second = book.create_sheet("Hidden")
    second.append(["secret", 1])
    buffer = io.BytesIO()
    book.save(buffer)
    src = zipfile.ZipFile(io.BytesIO(buffer.getvalue()))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for item in src.infolist():
            data = src.read(item.filename)
            name = item.filename
            if name == "xl/worksheets/sheet2.xml":
                name = "xl/other/sheet2.xml"
            elif name == "xl/_rels/workbook.xml.rels":
                data = data.replace(b"worksheets/sheet2.xml", b"other/sheet2.xml")
            elif name == "[Content_Types].xml":
                data = data.replace(b"/xl/worksheets/sheet2.xml", b"/xl/other/sheet2.xml")
            dst.writestr(name, data)
    return out.getvalue()


@probe
def two_sheets_hidden_path(speaker: str) -> dict[str, Any]:
    """A second sheet the upload check can't see: what does a turn do with it now?"""
    out: dict[str, Any] = {"probe": "two_sheets_hidden_path", "speaker": speaker}
    data = _two_sheets_second_elsewhere()
    for message, replies in (
        ("hi", single("unclear", [])),
        ("remove duplicate rows", single("remove_duplicate_rows", [DEDUPE])),
    ):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            client, settings, _ = app(tmp, h.FakeLLM(replies), speaker)
            s = Session(client, settings, speaker)
            r = upload(client, s.sid, data, "two.xlsx")
            entry: dict[str, Any] = {"upload": (r.status_code, r.json().get("error", {}).get("code"))}
            if r.status_code == 201:
                from adversarial_probe import upload_path

                body = r.json()
                s.paths.append(upload_path(settings, body["name"], body["file_id"]))
                entry["turn"] = s.turn(message, None)
                entry["intact"] = uploads_intact(client, settings, s.sid)
            out[message] = entry
    return out


@probe
def cap_default(speaker: str) -> dict[str, Any]:
    if speaker != "template":
        return {}
    out: dict[str, Any] = {"probe": "cap_default"}
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        settings = Settings.from_env({"WORKBUDDY_DATA_DIR": str(Path(tmp) / "d")})
        out["max_upload_bytes"] = settings.max_upload_bytes
        out["auto_approve"] = settings.auto_approve
        client = TestClient(create_app(settings, Factory(h.FakeLLM(), "template")), raise_server_exceptions=False)
        created = client.post("/api/v1/sessions", json={"mode": "focus"}).json()
        out["limits"] = created.get("limits")
        sid = created["session_id"]
        base = h.xlsx()
        exact = _pad_to(base, settings.max_upload_bytes)
        over = _pad_to(base, settings.max_upload_bytes + 1)
        out["exact"] = (len(exact), upload(client, sid, exact).status_code)
        r = upload(client, sid, over)
        out["over"] = (len(over), r.status_code, r.json())
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        settings = Settings.from_env(
            {"WORKBUDDY_DATA_DIR": str(Path(tmp) / "d"), "WORKBUDDY_MAX_UPLOAD_BYTES": str(5 * 1024 * 1024)}
        )
        client = TestClient(create_app(settings, Factory(h.FakeLLM(), "template")), raise_server_exceptions=False)
        sid = client.post("/api/v1/sessions", json={"mode": "focus"}).json()["session_id"]
        big = _pad_to(h.xlsx(), 5 * 1024 * 1024)
        r = upload(client, sid, big)
        out["override_5mib"] = (len(big), r.status_code)
        r = upload(client, sid, _pad_to(h.xlsx(), 5 * 1024 * 1024 + 1))
        out["override_5mib_plus_1"] = (r.status_code, r.json())
    return out


@probe
def analytics_via_api(speaker: str) -> dict[str, Any]:
    """Round 3's P2 through the API: does an analysis turn call requests.get (ydata telemetry)?"""
    if speaker != "template":
        return {}
    import os

    import requests

    calls: list[str] = []
    real = requests.get
    env_before = os.environ.pop("YDATA_PROFILING_NO_ANALYTICS", None)
    requests.get = lambda url, *a, **k: calls.append(str(url).split("?")[0])  # type: ignore[assignment]
    try:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            llm = h.FakeLLM(single("remove_duplicate_rows", [DEDUPE]))
            client, settings, _ = app(tmp, llm, speaker)
            s = Session(client, settings, speaker)
            s.up(h.xlsx())
            t1 = s.turn("remove duplicate rows", "dedupe")
    finally:
        requests.get = real  # type: ignore[assignment]
        if env_before is not None:
            os.environ["YDATA_PROFILING_NO_ANALYTICS"] = env_before
    return {
        "probe": "analytics_via_api",
        "env_unset_before": env_before is None,
        "steps": t1["steps"],
        "calls_to_requests_get": calls,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", action="append")
    parser.add_argument("--speaker", choices=SPEAKERS, action="append")
    args = parser.parse_args()
    for name, fn in PROBES.items():
        if args.only and name not in args.only:
            continue
        for speaker in args.speaker or SPEAKERS:
            result = fn(speaker)
            if result:
                print(json.dumps(result, default=str))
                sys.stdout.flush()


if __name__ == "__main__":
    main()

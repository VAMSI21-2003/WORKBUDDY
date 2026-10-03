"""``backend/openapi.json`` matches the app, and describes the real bodies.

Design "Decisions (2026-09-30, part 2)", asks 3 and 7.
"""

from __future__ import annotations

import json
from typing import Any

from backend.export_openapi import OPENAPI_PATH, REGENERATE, render

_ERROR_BODY = "#/components/schemas/ErrorBody"


def _committed() -> dict[str, Any]:
    return json.loads(OPENAPI_PATH.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def test_the_committed_schema_matches_the_app_byte_for_byte() -> None:
    committed = OPENAPI_PATH.read_text(encoding="utf-8")

    assert committed == render(), f"backend/openapi.json is out of date; run: {REGENERATE}"


def test_every_error_response_is_an_error_body_and_nothing_else() -> None:
    schema = _committed()
    checked = 0
    for path, operations in schema["paths"].items():
        for method, operation in operations.items():
            for status, response in operation["responses"].items():
                if int(status) < 400:
                    continue
                content = response.get("content", {})
                assert list(content) == ["application/json"], (method, path, status)
                assert content["application/json"]["schema"] == {"$ref": _ERROR_BODY}, (
                    method,
                    path,
                    status,
                )
                checked += 1
    assert checked > 20
    assert "HTTPValidationError" not in json.dumps(schema)


def test_every_429_declares_retry_after() -> None:
    schema = _committed()
    found = 0
    for operations in schema["paths"].values():
        for operation in operations.values():
            response = operation["responses"].get("429")
            if response is not None:
                assert "Retry-After" in response["headers"]
                found += 1
    assert found == 1  # POST .../turns


def test_the_error_code_enum_is_closed_and_complete() -> None:
    from backend.errors import ErrorCode

    codes = _committed()["components"]["schemas"]["ErrorCode"]["enum"]

    assert sorted(codes) == sorted(c.value for c in ErrorCode)


def test_a_finished_turn_requires_reply_source_and_outcome() -> None:
    """Ask 7: the turn is a union on ``status``; only ``finished`` carries the three, required."""
    schemas = _committed()["components"]["schemas"]
    finished = schemas["FinishedTurn"]
    running = schemas["RunningTurn"]

    for name in ("reply", "reply_source", "outcome", "finished_at", "status"):
        assert name in finished["required"]
    assert finished["properties"]["reply"]["type"] == "string"
    assert set(finished["properties"]["outcome"]["enum"]) == {
        "delivered",
        "not_delivered",
        "failed",
        "integrity_failure",
    }
    assert set(finished["properties"]["reply_source"]["enum"]) == {"pipeline", "pipeline_fallback"}
    for name in ("reply", "reply_source", "outcome"):
        assert running["properties"][name]["type"] == "null"

    poll = _committed()["paths"]["/api/v1/sessions/{session_id}/turns/{turn_id}"]["get"]
    union = poll["responses"]["200"]["content"]["application/json"]["schema"]
    assert union["discriminator"]["propertyName"] == "status"
    assert set(union["discriminator"]["mapping"]) == {"running", "finished"}


def test_the_session_response_declares_limits_and_a_nullable_retention() -> None:
    schemas = _committed()["components"]["schemas"]

    for name in ("SessionCreated", "SessionState"):
        properties = schemas[name]["properties"]
        assert properties["limits"] == {"$ref": "#/components/schemas/Limits"}
        assert {"type": "null"} in properties["retention_hours"]["anyOf"]
    assert set(schemas["Limits"]["required"]) == {
        "max_upload_bytes",
        "accepted_extensions",
        "max_message_chars",
    }

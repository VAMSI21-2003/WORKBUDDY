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
    awaiting = schemas["AwaitingConfirmationTurn"]
    for variant in (running, awaiting):
        for name in ("reply", "reply_source", "outcome"):
            assert variant["properties"][name]["type"] == "null"

    poll = _committed()["paths"]["/api/v1/sessions/{session_id}/turns/{turn_id}"]["get"]
    union = poll["responses"]["200"]["content"]["application/json"]["schema"]
    assert union["discriminator"]["propertyName"] == "status"
    statuses = {"running", "awaiting_confirmation", "finished"}
    assert set(union["discriminator"]["mapping"]) == statuses


def test_pending_confirmation_has_the_plan_and_no_step_one_only_fields() -> None:
    """Decisions (2026-10-01), items 4 and 5: ``steps`` (at least one) and ``plan_note``."""
    schemas = _committed()["components"]["schemas"]
    pending = schemas["PendingConfirmation"]

    assert set(pending["properties"]) == {"confirmation_id", "steps", "plan_note", "expires_at"}
    assert set(pending["required"]) == {"confirmation_id", "steps", "plan_note", "expires_at"}
    for removed in ("capability", "permission_tier", "input_name", "prompt"):
        assert removed not in pending["properties"]
    assert pending["properties"]["steps"]["minItems"] == 1
    assert pending["properties"]["steps"]["items"] == {"$ref": "#/components/schemas/PlanStep"}
    assert {"type": "null"} in pending["properties"]["plan_note"]["anyOf"]
    assert set(schemas["PlanStep"]["properties"]) == {"capability", "prompt"}
    assert set(schemas["PlanStep"]["required"]) == {"capability", "prompt"}


def test_only_the_awaiting_turn_carries_the_plan_and_there_it_is_required() -> None:
    schemas = _committed()["components"]["schemas"]
    ref = {"$ref": "#/components/schemas/PendingConfirmation"}

    for name in ("AwaitingConfirmationTurn", "AwaitingConfirmationTurnSummary"):
        assert schemas[name]["properties"]["pending_confirmation"] == ref
        assert "pending_confirmation" in schemas[name]["required"]
    for name in ("RunningTurn", "RunningTurnSummary", "FinishedTurn", "FinishedTurnSummary"):
        assert schemas[name]["properties"]["pending_confirmation"]["type"] == "null"
    state = schemas["SessionState"]["properties"]["pending_confirmation"]
    assert state["anyOf"] == [ref, {"type": "null"}]


def test_the_confirmation_route_is_declared_with_its_errors() -> None:
    path = "/api/v1/sessions/{session_id}/turns/{turn_id}/confirmation"
    operation = _committed()["paths"][path]["post"]
    body = operation["requestBody"]["content"]["application/json"]["schema"]
    request = _committed()["components"]["schemas"]["ConfirmationRequest"]

    assert body == {"$ref": "#/components/schemas/ConfirmationRequest"}
    assert set(request["required"]) == {"confirmation_id", "decision"}
    assert request["properties"]["decision"]["enum"] == ["approve", "reject"]
    assert request["additionalProperties"] is False
    assert {"200", "404", "409", "413", "422"} <= set(operation["responses"])
    union = operation["responses"]["200"]["content"]["application/json"]["schema"]
    assert union["discriminator"]["propertyName"] == "status"


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

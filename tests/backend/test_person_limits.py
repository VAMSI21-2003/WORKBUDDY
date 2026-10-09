"""Limits per person as well as per address (docs/design/google-sign-in.md, "Limits per person").

One account can't use the whole model quota by coming from many addresses, and one person's
limit isn't another's. Addresses are told apart by ``X-Forwarded-For`` (one trusted proxy), as
behind Caddy.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from backend.settings import BucketSpec
from tests.backend.factories import PipelineFactory, QueuedProvider, single
from tests.backend.helpers import RELAXED_LIMITS, new_session, xlsx_bytes
from tests.backend.signin_helpers import ALICE, BOB, Rig


def _rig(tmp_path: Path, **limits: BucketSpec) -> Rig:
    return Rig(
        tmp_path,
        PipelineFactory(QueuedProvider(single("remove_duplicate_rows") * 6), speaker="friend"),
        trusted_proxy_hops=1,
        rate_limits={**RELAXED_LIMITS, **limits},
    )


def _from(ip: str) -> dict[str, str]:
    return {"X-Forwarded-For": ip}


def _turn(client: TestClient, sid: str, ip: str) -> Any:
    return client.post(
        f"/api/v1/sessions/{sid}/turns", json={"message": "hello"}, headers=_from(ip)
    )


def _upload(client: TestClient, sid: str, ip: str) -> Any:
    return client.post(
        f"/api/v1/sessions/{sid}/files",
        files={"file": ("r.xlsx", xlsx_bytes(), "application/octet-stream")},
        headers=_from(ip),
    )


def test_one_account_cant_get_past_its_turn_limit_by_changing_address(tmp_path: Path) -> None:
    rig = _rig(tmp_path, user_turns=BucketSpec(burst=2, per_hour=2))
    alice = rig.person(ALICE)
    sids = [new_session(alice) for _ in range(4)]

    codes = [_turn(alice, sid, f"10.0.0.{i}").status_code for i, sid in enumerate(sids)]

    assert codes[:2] == [202, 202]
    assert codes[2:] == [429, 429]  # a new address each time: the per-address buckets are full
    refused = _turn(alice, sids[3], "10.0.0.99")
    assert refused.json()["error"]["code"] == "rate_limited"
    assert int(refused.headers["Retry-After"]) >= 1


def test_one_account_cant_get_past_its_upload_limit_by_changing_address(tmp_path: Path) -> None:
    rig = _rig(tmp_path, user_uploads=BucketSpec(burst=2, per_hour=2))
    alice = rig.person(ALICE)
    sids = [new_session(alice) for _ in range(4)]

    codes = [_upload(alice, sid, f"10.0.1.{i}").status_code for i, sid in enumerate(sids)]

    assert codes == [201, 201, 429, 429]


def test_another_person_is_not_held_up_by_someones_limit(tmp_path: Path) -> None:
    rig = _rig(tmp_path, user_turns=BucketSpec(burst=1, per_hour=1))
    alice, bob = rig.person(ALICE), rig.person(BOB)
    a, b = new_session(alice), new_session(bob)

    assert _turn(alice, a, "10.0.2.1").status_code == 202
    assert _turn(alice, a, "10.0.2.2").status_code in (409, 429)  # (busy, or over her limit)
    assert _turn(bob, b, "10.0.2.3").status_code == 202


def test_the_per_address_limit_still_applies_to_many_people_behind_one_address(
    tmp_path: Path,
) -> None:
    rig = _rig(tmp_path, turns=BucketSpec(burst=1, per_hour=1))
    alice, bob = rig.person(ALICE), rig.person(BOB)
    a, b = new_session(alice), new_session(bob)

    assert _turn(alice, a, "10.0.3.1").status_code == 202
    assert _turn(bob, b, "10.0.3.1").status_code == 429


def test_a_refusal_for_a_person_costs_their_address_nothing(tmp_path: Path) -> None:
    rig = _rig(tmp_path, user_turns=BucketSpec(burst=1, per_hour=1))
    alice = rig.person(ALICE)
    sids = [new_session(alice) for _ in range(3)]
    assert _turn(alice, sids[0], "10.0.4.1").status_code == 202
    before = rig.app.state.limiter.tokens("10.0.4.1", "turns")

    assert _turn(alice, sids[1], "10.0.4.1").status_code == 429

    assert rig.app.state.limiter.tokens("10.0.4.1", "turns") == before


def test_a_request_without_a_credential_is_not_charged_to_any_person(tmp_path: Path) -> None:
    rig = _rig(tmp_path, user_turns=BucketSpec(burst=1, per_hour=1))
    alice = rig.person(ALICE)
    sid = new_session(alice)
    stranger = rig.person()

    for i in range(5):
        assert _turn(stranger, sid, f"10.0.5.{i}").status_code == 401

    assert _turn(alice, sid, "10.0.5.99").status_code == 202  # Alice's bucket is still full


def test_probing_someones_session_spends_the_probers_limit_not_the_owners(
    tmp_path: Path,
) -> None:
    """A request is charged whatever the answer (the limiter's strict rule), to whoever made it."""
    rig = _rig(tmp_path, user_turns=BucketSpec(burst=1, per_hour=1))
    alice, bob = rig.person(ALICE), rig.person(BOB)
    a, b = new_session(alice), new_session(bob)

    assert _turn(bob, a, "10.0.6.1").status_code == 404  # Bob's one turn token
    assert _turn(bob, a, "10.0.6.2").status_code == 429
    assert _turn(bob, b, "10.0.6.3").status_code == 429  # ...so his own session is held up too

    assert _turn(alice, a, "10.0.6.50").status_code == 202  # Alice's bucket was never touched

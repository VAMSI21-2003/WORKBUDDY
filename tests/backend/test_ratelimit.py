"""The per-IP rate limits: every bucket, how a request is charged, and ``Retry-After``.

Design "Decisions (2026-09-30, part 2)", item 1 (its ten tests, numbered below),
and "Decisions (2026-10-01)", item 3. Every test runs with the real bucket
sizes and an injected clock, so nothing sleeps and nothing is flaky.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient

from backend.main import create_app
from backend.ratelimit import (
    ACCESS,
    GENERAL,
    HEALTHZ,
    PAGE,
    POLL,
    TURNS,
    UPLOADS,
    RateLimiter,
    RouteLimits,
    after_response,
    charges_for,
    check_route_declarations,
    client_ip,
    iter_routes,
    limits,
)
from backend.settings import DEFAULT_RATE_LIMITS, BucketSpec, Settings, SettingsError
from tests.backend.factories import BlockingFactory, PipelineFactory, QueuedProvider, single
from tests.backend.helpers import (
    BEARER,
    ORIGIN,
    FakeClock,
    cookie_value,
    exchange,
    locked_settings,
    with_cookie,
    xlsx_bytes,
)

IP = "testclient"
WRONG = {"Authorization": "Bearer wrong"}


class Rig:
    """An app on production-like settings, a clock the test advances, and its limiter."""

    def __init__(self, tmp_path: Path, factory: Any = None, **overrides: Any) -> None:
        self.clock = FakeClock()
        settings = locked_settings(tmp_path, **overrides)
        self.app = create_app(settings, factory or (lambda provider: None), clock=self.clock)
        self.client = TestClient(self.app)
        self.limiter: RateLimiter = self.app.state.limiter
        self.charges: list[tuple[str, list[tuple[str, int]]]] = []
        self.after: list[list[tuple[str, int]]] = []
        original_charge, original_after = self.limiter.charge, self.limiter.charge_after

        def charge(ip: str, charges: Any) -> Any:
            self.charges.append((ip, list(charges)))
            return original_charge(ip, charges)

        def charge_after(ip: str, charges: Any) -> None:
            if charges:
                self.after.append(list(charges))
            original_after(ip, charges)

        self.limiter.charge = charge  # type: ignore[method-assign]
        self.limiter.charge_after = charge_after  # type: ignore[method-assign]

    def tokens(self, bucket: str, ip: str = IP) -> float:
        return self.limiter.tokens(ip, bucket)

    def snapshot(self) -> dict[str, float]:
        return {name: self.tokens(name) for name in DEFAULT_RATE_LIMITS}

    def token(self) -> str:
        response = exchange(self.client)
        assert response.status_code == 204, response.text
        return cookie_value(response)


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    return Rig(tmp_path)


def _spent(before: dict[str, float], after: dict[str, float]) -> dict[str, int]:
    return {n: round(before[n] - after[n]) for n in before if round(before[n] - after[n])}


# -- The token bucket itself ---------------------------------------------------------------------


def _limiter(clock: FakeClock, **specs: BucketSpec) -> RateLimiter:
    return RateLimiter({**DEFAULT_RATE_LIMITS, **specs}, clock)


def test_a_bucket_starts_full_and_refills_up_to_its_burst() -> None:
    clock = FakeClock()
    limiter = _limiter(clock, general=BucketSpec(burst=3, per_hour=3600))  # 1 token a second

    for _ in range(3):
        assert limiter.charge(IP, [(GENERAL, 1)]).allowed
    assert not limiter.charge(IP, [(GENERAL, 1)]).allowed
    clock.advance(1)
    assert limiter.charge(IP, [(GENERAL, 1)]).allowed
    clock.advance(1000)
    assert limiter.tokens(IP, GENERAL) == 3  # never above the burst


def test_buckets_are_per_ip_and_per_name() -> None:
    clock = FakeClock()
    limiter = _limiter(clock, general=BucketSpec(burst=1, per_hour=3600))

    assert limiter.charge("1.1.1.1", [(GENERAL, 1)]).allowed
    assert not limiter.charge("1.1.1.1", [(GENERAL, 1)]).allowed
    assert limiter.charge("2.2.2.2", [(GENERAL, 1)]).allowed
    assert limiter.charge("1.1.1.1", [(POLL, 1)]).allowed  # another bucket


def test_a_refusal_takes_nothing_from_any_bucket_and_waits_for_the_slowest() -> None:
    clock = FakeClock()
    limiter = _limiter(
        clock,
        general=BucketSpec(burst=1, per_hour=3600),  # 1 a second
        turns=BucketSpec(burst=1, per_hour=10),  # 1 every 360 s
    )
    assert limiter.charge(IP, [(GENERAL, 1), (TURNS, 1)]).allowed
    clock.advance(1)  # general has a token again, turns hasn't

    decision = limiter.charge(IP, [(GENERAL, 1), (TURNS, 1)])

    assert (decision.allowed, decision.retry_after) == (False, 359)
    assert limiter.tokens(IP, GENERAL) == 1  # the refusal took nothing from general either
    clock.advance(359)
    assert limiter.charge(IP, [(GENERAL, 1), (TURNS, 1)]).allowed


def test_a_charge_never_takes_a_bucket_below_zero() -> None:
    clock = FakeClock()
    limiter = _limiter(clock, general=BucketSpec(burst=2, per_hour=3600))
    limiter.charge(IP, [(GENERAL, 1)])

    assert limiter.charge(IP, [(GENERAL, 2)]).allowed  # one token left: it takes it, to zero
    assert limiter.tokens(IP, GENERAL) == 0
    limiter.charge_after(IP, [(GENERAL, 5)])
    assert limiter.tokens(IP, GENERAL) == 0


def test_idle_full_buckets_are_forgotten() -> None:
    clock = FakeClock()
    limiter = _limiter(clock)
    for n in range(50):
        limiter.charge(f"10.0.0.{n}", [(GENERAL, 1)])

    assert limiter.prune() == 0  # just used: not yet full again
    clock.advance(3600)
    assert limiter.prune() == 50
    assert limiter._buckets == {}


# -- Charging rules --------------------------------------------------------------------------------


def test_the_charging_rules_in_one_table() -> None:
    reads = RouteLimits((GENERAL,))
    turn = RouteLimits((GENERAL, TURNS), mutating=True)
    poll = RouteLimits((POLL,), poll=True)
    access = RouteLimits((GENERAL, ACCESS), mutating=True, credential=False)
    health = RouteLimits((HEALTHZ,), credential=False)
    page = RouteLimits((PAGE,), credential=False)

    assert charges_for(reads, valid=True, path="/api/v1/x") == [(GENERAL, 1)]
    assert charges_for(turn, valid=True, path="/api/v1/x") == [(GENERAL, 1), (TURNS, 1)]
    assert charges_for(poll, valid=True, path="/api/v1/x") == [(POLL, 1)]
    # a missing or wrong credential: 2 general tokens, whatever the route, and nothing else
    for declared in (reads, turn, poll):
        assert charges_for(declared, valid=False, path="/api/v1/x") == [(GENERAL, 2)]
    # routes that take no credential are charged the same whether or not one came
    assert charges_for(access, valid=False, path="/api/v1/access") == [(GENERAL, 1), (ACCESS, 1)]
    assert charges_for(health, valid=True, path="/healthz") == [(HEALTHZ, 1)]
    assert charges_for(page, valid=True, path="/") == [(PAGE, 1)]
    # nothing matched: general under /api/v1, page elsewhere
    assert charges_for(None, valid=True, path="/api/v1/nope") == [(GENERAL, 1)]
    assert charges_for(None, valid=True, path="/nope") == [(PAGE, 1)]

    assert after_response(poll, valid=True, status=200) == []
    assert after_response(poll, valid=True, status=404) == [(GENERAL, 1)]
    assert after_response(poll, valid=False, status=401) == []  # already charged double
    assert after_response(access, valid=True, status=401) == [(GENERAL, 1)]
    assert after_response(access, valid=True, status=204) == []
    assert after_response(turn, valid=True, status=409) == []  # charged strictly up front


# -- Test 1: the frontend's cadence for the longest a turn can be polled --------------------------


def _answer_any_plan(rig: Rig, headers: dict[str, str], sid: str, tid: str, body: dict) -> None:
    if body["status"] == "awaiting_confirmation":
        plan = body["pending_confirmation"]
        answered = rig.client.post(
            f"/api/v1/sessions/{sid}/turns/{tid}/confirmation",
            json={"confirmation_id": plan["confirmation_id"], "decision": "approve"},
            headers={**headers, **ORIGIN},
        )
        assert answered.status_code == 200, answered.text


def test_1_one_turn_polled_for_300_seconds_with_every_other_call_never_gets_a_429(
    tmp_path: Path,
) -> None:
    import time

    factory = PipelineFactory(QueuedProvider(single("remove_duplicate_rows")))
    rig = Rig(tmp_path, factory)
    headers = with_cookie(rig.token())
    mutating = {**headers, **ORIGIN}
    sid = rig.client.post("/api/v1/sessions", headers=mutating).json()["session_id"]
    files = {"file": ("report.xlsx", xlsx_bytes(), "application/octet-stream")}
    assert (
        rig.client.post(f"/api/v1/sessions/{sid}/files", files=files, headers=mutating).status_code
        == 201
    )
    turn = rig.client.post(
        f"/api/v1/sessions/{sid}/turns", json={"message": "remove duplicate rows"}, headers=mutating
    )
    assert turn.status_code == 202
    tid = turn.json()["turn_id"]

    statuses: list[int] = []
    finished = False
    elapsed = 0.0
    while elapsed < 300:  # the frontend's 2 s cadence, for the longest a turn can be polled
        rig.clock.advance(2)
        elapsed += 2
        response = rig.client.get(f"/api/v1/sessions/{sid}/turns/{tid}", headers=headers)
        statuses.append(response.status_code)
        body = response.json()
        _answer_any_plan(rig, headers, sid, tid, body)
        finished = finished or body["status"] == "finished"
        if not finished:
            time.sleep(0.02)  # the real pipeline is working; the fake clock keeps its cadence
    listing = rig.client.get(f"/api/v1/sessions/{sid}/files", headers=headers)
    (delivered,) = body["delivered_files"]
    download = rig.client.get(delivered["download_url"], headers=headers)

    assert finished and body["outcome"] == "delivered"
    assert set(statuses) == {200}
    assert listing.status_code == download.status_code == 200
    assert all(c.status_code != 429 for c in (listing, download))


def test_2_two_such_turns_at_once_get_no_429_but_a_third_fast_stream_does_and_general_still_works(
    tmp_path: Path,
) -> None:
    factory = BlockingFactory()
    rig = Rig(tmp_path, factory)
    headers = with_cookie(rig.token())
    mutating = {**headers, **ORIGIN}
    turns = []
    for _ in range(2):
        sid = rig.client.post("/api/v1/sessions", headers=mutating).json()["session_id"]
        tid = rig.client.post(
            f"/api/v1/sessions/{sid}/turns", json={"message": "hello"}, headers=mutating
        ).json()["turn_id"]
        turns.append(f"/api/v1/sessions/{sid}/turns/{tid}")
    try:
        for _ in range(150):  # two streams at the 2 s cadence: 1 poll a second in all
            rig.clock.advance(2)
            for path in turns:
                assert rig.client.get(path, headers=headers).status_code == 200

        limited = None
        for step in range(200):  # a third stream, at 4 a second, on top of them
            rig.clock.advance(0.25)
            response = rig.client.get(turns[0], headers=headers)
            if step % 8 == 7:
                for path in turns:
                    rig.client.get(path, headers=headers)
            if response.status_code == 429:
                limited = response
                break

        assert limited is not None, "a stream faster than the allowance must end up refused"
        assert limited.json()["error"]["code"] == "rate_limited"
        # the buckets are independent: another kind of request from the same IP still works
        assert rig.client.get("/api/v1/sessions/s_x", headers=headers).status_code == 404
    finally:
        factory.release.set()


# -- Test 3 and 6: what a poll is charged ----------------------------------------------------------


def test_3_300_valid_polls_are_charged_to_poll_only(tmp_path: Path) -> None:
    factory = BlockingFactory()
    rig = Rig(tmp_path, factory)
    headers = with_cookie(rig.token())
    sid = rig.client.post("/api/v1/sessions", headers={**headers, **ORIGIN}).json()["session_id"]
    tid = rig.client.post(
        f"/api/v1/sessions/{sid}/turns", json={"message": "hi"}, headers={**headers, **ORIGIN}
    ).json()["turn_id"]
    rig.charges.clear()
    rig.after.clear()
    try:
        for _ in range(300):
            rig.clock.advance(0.34)  # just under the allowance: 3 a second
            assert (
                rig.client.get(f"/api/v1/sessions/{sid}/turns/{tid}", headers=headers).status_code
                == 200
            )
    finally:
        factory.release.set()

    assert [c for _, c in rig.charges] == [[(POLL, 1)]] * 300
    assert rig.after == []


def test_6_a_poll_of_an_unknown_turn_or_session_also_takes_a_general_token(rig: Rig) -> None:
    headers = with_cookie(rig.token())
    for path in (
        "/api/v1/sessions/s_nosuch/turns/t0001",
        "/api/v1/sessions/s_nosuch/turns/%2e%2e",
        "/api/v1/sessions/s_nosuch/turns/" + "a" * 70,
    ):
        before = rig.snapshot()

        response = rig.client.get(path, headers=headers)

        assert response.status_code == 404
        assert _spent(before, rig.snapshot()) == {POLL: 1, GENERAL: 1}, path


def test_an_expired_cookie_on_a_poll_takes_two_general_tokens_and_none_from_poll(
    rig: Rig,
) -> None:
    token = rig.token()
    rig.clock.advance(12 * 3600 + 1)
    before = rig.snapshot()

    response = rig.client.get("/api/v1/sessions/s_x/turns/t0001", headers=with_cookie(token))

    assert response.status_code == 401
    assert _spent(before, rig.snapshot()) == {GENERAL: 2}


# -- Test 4: strict counting -----------------------------------------------------------------------


def test_4_a_refused_upload_still_costs_a_general_and_an_uploads_token(tmp_path: Path) -> None:
    factory = BlockingFactory()
    rig = Rig(tmp_path, factory, max_upload_bytes=2000)
    headers = {**BEARER}
    sid = rig.client.post("/api/v1/sessions", headers=headers).json()["session_id"]
    path = f"/api/v1/sessions/{sid}/files"
    big = {"file": ("big.xlsx", b"x" * 3000, "application/octet-stream")}
    text = {"file": ("notes.txt", b"hello", "text/plain")}
    junk = {"file": ("fake.xlsx", b"not a workbook", "application/octet-stream")}
    refused = {413: big, 415: text, 422: junk}

    for status, files in refused.items():
        before = rig.snapshot()
        assert rig.client.post(path, files=files, headers=headers).status_code == status
        assert _spent(before, rig.snapshot()) == {GENERAL: 1, UPLOADS: 1}, status

    # a busy session (409)
    rig.client.post(f"/api/v1/sessions/{sid}/turns", json={"message": "hi"}, headers=headers)
    try:
        before = rig.snapshot()
        busy = rig.client.post(path, files=text, headers=headers)
        assert busy.status_code == 409
        assert _spent(before, rig.snapshot()) == {GENERAL: 1, UPLOADS: 1}
        # and a refused turn: 409 busy, 422 invalid
        for body, status in (({"message": "again"}, 409), ({"message": ""}, 422)):
            before = rig.snapshot()
            response = rig.client.post(f"/api/v1/sessions/{sid}/turns", json=body, headers=headers)
            assert response.status_code == status
            assert _spent(before, rig.snapshot()) == {GENERAL: 1, TURNS: 1}, status
    finally:
        factory.release.set()


def test_4_the_61st_general_request_in_a_burst_is_a_429(rig: Rig) -> None:
    for n in range(60):
        assert rig.client.get("/api/v1/sessions/s_x", headers=BEARER).status_code == 404, n

    response = rig.client.get("/api/v1/sessions/s_x", headers=BEARER)

    assert response.status_code == 429
    assert response.json() == {
        "error": {
            "code": "rate_limited",
            "message": "Too many requests. Wait a little and try again.",
        }
    }
    assert response.headers["Retry-After"] == "1"


def test_4_the_11th_turn_in_an_hour_and_the_6th_upload_in_a_burst_are_429(rig: Rig) -> None:
    sid = rig.client.post("/api/v1/sessions", headers=BEARER).json()["session_id"]
    turns_path = f"/api/v1/sessions/{sid}/turns"
    for n in range(10):
        # an invalid turn is refused by the handler, and still takes its tokens
        response = rig.client.post(turns_path, json={"message": ""}, headers=BEARER)
        assert response.status_code == 422, n
        rig.clock.advance(1)  # keeps general from running out
    eleventh = rig.client.post(turns_path, json={"message": ""}, headers=BEARER)
    assert eleventh.status_code == 429
    # 10 an hour: the wait is 360 s, less the 10 s that passed during the loop
    assert eleventh.headers["Retry-After"] == str(math.ceil(360 - 10))

    text = {"file": ("notes.txt", b"hello", "text/plain")}
    for n in range(5):
        assert (
            rig.client.post(f"/api/v1/sessions/{sid}/files", files=text, headers=BEARER).status_code
            == 415
        ), n
    sixth = rig.client.post(f"/api/v1/sessions/{sid}/files", files=text, headers=BEARER)
    assert sixth.status_code == 429


def test_a_body_over_the_cap_is_refused_413_and_still_charged(tmp_path: Path) -> None:
    rig = Rig(tmp_path, max_upload_bytes=1000)
    before = rig.snapshot()

    response = rig.client.post(
        "/api/v1/sessions/s_x/files",
        content=b"x" * 70_000,
        headers={**BEARER, "Content-Type": "multipart/form-data; boundary=b"},
    )

    assert response.status_code == 413
    assert _spent(before, rig.snapshot()) == {GENERAL: 1, UPLOADS: 1}


def test_a_request_refused_for_its_origin_costs_what_a_mutating_request_costs(rig: Rig) -> None:
    headers = with_cookie(rig.token())
    before = rig.snapshot()

    foreign = rig.client.post(
        "/api/v1/sessions/s_x/turns",
        json={"message": "hi"},
        headers={**headers, "Origin": "https://evil.example"},
    )

    assert foreign.status_code == 403
    assert _spent(before, rig.snapshot()) == {GENERAL: 1, TURNS: 1}  # one general token, not two


# -- Test 5: a wrong or missing credential counts double ------------------------------------------


def test_5_a_wrong_credential_takes_two_general_tokens_on_every_route(rig: Rig) -> None:
    routes = [
        r
        for r in iter_routes(rig.app)
        if r.path.startswith("/api/v1") and r.path != "/api/v1/access"
    ]
    for route in routes:
        for method in route.methods:
            for headers in ({}, WRONG):
                before = rig.snapshot()
                path = (
                    route.path.replace("{session_id}", "s_x")
                    .replace("{turn_id}", "t1")
                    .replace("{file_id}", "f_x")
                )

                response = rig.client.request(method, path, headers=headers)

                assert response.status_code == 401
                assert _spent(before, rig.snapshot()) == {GENERAL: 2}, (method, route.path, headers)
                rig.clock.advance(30)  # refill: the point is the cost, not the burst


def test_5_30_wrong_polls_empty_general_and_leave_poll_untouched(rig: Rig) -> None:
    for n in range(30):
        assert rig.client.get("/api/v1/sessions/s_x/turns/t1", headers=WRONG).status_code == 401, n

    response = rig.client.get("/api/v1/sessions/s_x/turns/t1", headers=WRONG)

    assert response.status_code == 429
    assert rig.tokens(POLL) == DEFAULT_RATE_LIMITS[POLL].burst


def test_the_limiter_runs_before_the_password_check_so_guessing_is_throttled(rig: Rig) -> None:
    for _ in range(30):
        rig.client.get("/api/v1/sessions/s_x", headers=WRONG)

    over = rig.client.get("/api/v1/sessions/s_x", headers=WRONG)
    right_but_over = rig.client.get("/api/v1/sessions/s_x", headers=BEARER)

    assert over.status_code == 429  # not 401: the wrong password was never even checked
    assert right_but_over.status_code == 429  # nor the right one, until the bucket refills


def test_the_access_route_takes_one_general_and_one_access_token_and_two_general_when_wrong(
    rig: Rig,
) -> None:
    before = rig.snapshot()
    assert exchange(rig.client).status_code == 204
    assert _spent(before, rig.snapshot()) == {GENERAL: 1, ACCESS: 1}

    before = rig.snapshot()
    assert exchange(rig.client, "wrong").status_code == 401
    assert _spent(before, rig.snapshot()) == {GENERAL: 2, ACCESS: 1}


def test_11_wrong_passwords_in_a_burst_end_in_a_429_and_each_took_two_general_tokens(
    rig: Rig,
) -> None:
    before = rig.snapshot()
    for n in range(10):
        assert exchange(rig.client, "wrong").status_code == 401, n

    assert _spent(before, rig.snapshot()) == {GENERAL: 20, ACCESS: 10}
    eleventh = exchange(rig.client, "wrong")
    assert eleventh.status_code == 429
    # 20 an hour: one token every 180 s
    assert eleventh.headers["Retry-After"] == "180"
    assert exchange(rig.client).status_code == 429  # the right password too, until it refills


def test_a_403_on_the_access_route_costs_one_general_and_one_access_token(rig: Rig) -> None:
    before = rig.snapshot()

    response = rig.client.post("/api/v1/access", data={"password": "x"})  # no Origin

    assert response.status_code == 403
    assert _spent(before, rig.snapshot()) == {GENERAL: 1, ACCESS: 1}


# -- Test 7: Retry-After ---------------------------------------------------------------------------


def test_7_a_429_consumes_nothing_and_retry_after_is_exact(rig: Rig) -> None:
    for _ in range(10):
        rig.client.post("/api/v1/sessions/s_x/turns", json={"message": ""}, headers=BEARER)
        rig.clock.advance(1)
    before = rig.snapshot()

    refused = rig.client.post("/api/v1/sessions/s_x/turns", json={"message": "hi"}, headers=BEARER)

    assert refused.status_code == 429
    wait = int(refused.headers["Retry-After"])
    assert wait >= 1 and str(wait) == refused.headers["Retry-After"]  # whole seconds, not a date
    assert rig.snapshot() == before  # the refusal took nothing
    rig.clock.advance(wait - 1)
    still = rig.client.post("/api/v1/sessions/s_x/turns", json={"message": "hi"}, headers=BEARER)
    assert still.status_code == 429  # one second too early
    rig.clock.advance(1)
    accepted = rig.client.post("/api/v1/sessions/s_x/turns", json={"message": "hi"}, headers=BEARER)
    assert accepted.status_code == 404  # handled: there's no such session


def test_the_retry_after_of_a_poll_waits_for_one_poll_token(tmp_path: Path) -> None:
    factory = BlockingFactory()
    rig = Rig(tmp_path, factory)
    headers = BEARER
    sid = rig.client.post("/api/v1/sessions", headers=headers).json()["session_id"]
    tid = rig.client.post(
        f"/api/v1/sessions/{sid}/turns", json={"message": "hi"}, headers=headers
    ).json()["turn_id"]
    try:
        path = f"/api/v1/sessions/{sid}/turns/{tid}"
        for _ in range(30):
            assert rig.client.get(path, headers=headers).status_code == 200
        refused = rig.client.get(path, headers=headers)

        assert refused.status_code == 429
        assert refused.headers["Retry-After"] == "1"  # 3 a second: a token in a third of one
        rig.clock.advance(1)
        assert rig.client.get(path, headers=headers).status_code == 200
    finally:
        factory.release.set()


def test_a_429_has_the_same_security_headers_as_any_response(rig: Rig) -> None:
    for _ in range(61):
        response = rig.client.get("/api/v1/sessions/s_x", headers=BEARER)

    assert response.status_code == 429
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Content-Type-Options"] == "nosniff"


# -- Test 8: healthz and the page bucket are separate ---------------------------------------------


def test_8_healthz_has_its_own_bucket_and_never_touches_general(rig: Rig) -> None:
    before = rig.snapshot()
    for n in range(10):
        assert rig.client.get("/healthz").status_code == 200, n

    assert _spent(before, rig.snapshot()) == {HEALTHZ: 10}
    assert rig.client.get("/healthz").status_code == 429
    # and the reverse: with general empty, healthz is unaffected
    rig.clock.advance(100)
    for _ in range(60):
        rig.client.get("/api/v1/sessions/s_x", headers=BEARER)
    assert rig.client.get("/api/v1/sessions/s_x", headers=BEARER).status_code == 429
    assert rig.client.get("/healthz").status_code == 200


def test_page_loads_use_the_page_bucket_and_never_touch_general(rig: Rig) -> None:
    """The frontend's ``GET /`` and ``/static/*`` declare ``page``; this stands in for them."""
    rig.app.add_api_route(
        "/page-test",
        lambda: {"ok": True},
        methods=["GET"],
        dependencies=[Depends(limits(PAGE, credential=False))],
    )
    check_route_declarations(rig.app)
    before = rig.snapshot()

    for n in range(30):
        assert rig.client.get("/page-test").status_code == 200, n

    assert _spent(before, rig.snapshot()) == {PAGE: 30}
    assert rig.client.get("/page-test").status_code == 429
    assert rig.client.get("/api/v1/sessions/s_x", headers=BEARER).status_code == 404


def test_a_path_that_matches_no_route_is_charged_to_general_under_the_api_and_page_elsewhere(
    rig: Rig,
) -> None:
    before = rig.snapshot()
    assert rig.client.get("/api/v1/nothing-here").status_code == 404
    assert _spent(before, rig.snapshot()) == {GENERAL: 1}

    before = rig.snapshot()
    assert rig.client.get("/nothing-here").status_code == 404
    assert _spent(before, rig.snapshot()) == {PAGE: 1}

    before = rig.snapshot()
    assert rig.client.get("/api/v1/sessions", headers=BEARER).status_code == 405  # wrong method
    assert _spent(before, rig.snapshot()) == {GENERAL: 1}


# -- Test 9: startup checks -----------------------------------------------------------------------


def test_9_the_app_refuses_to_start_if_poll_refills_slower_than_the_turn_cap(
    tmp_path: Path,
) -> None:
    slow = {**DEFAULT_RATE_LIMITS, POLL: BucketSpec(burst=30, per_hour=3600 * 2)}  # 2 a second

    with pytest.raises(SettingsError, match="poll"):
        locked_settings(tmp_path, rate_limits=slow, max_concurrent_turns=3)
    locked_settings(tmp_path, rate_limits=slow, max_concurrent_turns=2)  # equal is enough


def test_the_buckets_must_be_exactly_the_known_ones_and_positive(tmp_path: Path) -> None:
    missing = {k: v for k, v in DEFAULT_RATE_LIMITS.items() if k != PAGE}
    with pytest.raises(SettingsError, match="exactly"):
        locked_settings(tmp_path, rate_limits=missing)
    with pytest.raises(SettingsError, match="burst"):
        locked_settings(tmp_path, rate_limits={**DEFAULT_RATE_LIMITS, TURNS: BucketSpec(0, 10)})
    with pytest.raises(SettingsError, match="refill"):
        locked_settings(tmp_path, rate_limits={**DEFAULT_RATE_LIMITS, TURNS: BucketSpec(1, 0)})


def test_the_defaults_are_the_designs_numbers(tmp_path: Path) -> None:
    limits_ = locked_settings(tmp_path).rate_limits

    assert limits_ == {
        "general": BucketSpec(60, 3600),  # 60/min
        "poll": BucketSpec(30, 10800),  # 3/s
        "turns": BucketSpec(10, 10),  # 10/hour
        "uploads": BucketSpec(5, 20),
        "access": BucketSpec(10, 20),
        "healthz": BucketSpec(10, 3600),
        "page": BucketSpec(30, 3600),
    }


def test_every_bucket_can_be_set_from_the_environment(tmp_path: Path) -> None:
    env = {
        "WORKBUDDY_ENV": "dev",
        "WORKBUDDY_DATA_DIR": str(tmp_path),
        "WORKBUDDY_RATE_TURNS_BURST": "3",
        "WORKBUDDY_RATE_TURNS_PER_HOUR": "6",
        "WORKBUDDY_RATE_GENERAL_PER_HOUR": "7200",
    }

    limits_ = Settings.from_env(env).rate_limits

    assert limits_["turns"] == BucketSpec(3, 6)
    assert limits_["general"] == BucketSpec(60, 7200)
    assert limits_["poll"] == DEFAULT_RATE_LIMITS["poll"]


# -- Route declarations ---------------------------------------------------------------------------


def test_every_route_declares_its_buckets_and_the_app_refuses_one_that_does_not(rig: Rig) -> None:
    check_route_declarations(rig.app)  # every real route passes

    rig.app.add_api_route("/forgot", lambda: None, methods=["GET"])

    with pytest.raises(RuntimeError, match="doesn't declare"):
        check_route_declarations(rig.app)
    with pytest.raises(RuntimeError, match="doesn't declare"):
        with TestClient(rig.app):  # the check runs again at startup, for routes added later
            pass


def test_the_declarations_are_checked_against_the_route(rig: Rig) -> None:
    from backend.access import AccessDependency

    cases = [
        ("a", "GET", [Depends(limits(GENERAL, mutating=True)), AccessDependency], "GET route"),
        ("b", "POST", [Depends(limits(GENERAL)), AccessDependency], "isn't declared mutating"),
        ("c", "GET", [Depends(limits("nonsense")), AccessDependency], "unknown bucket"),
        (
            "d",
            "GET",
            [Depends(limits(GENERAL)), Depends(limits(POLL)), AccessDependency],
            "doesn't declare",
        ),
        ("e", "GET", [Depends(limits(GENERAL))], "lacks require_access"),
        ("f", "POST", [Depends(limits(POLL, poll=True, mutating=True)), AccessDependency], "poll"),
    ]
    for name, method, dependencies, message in cases:
        app = create_app(
            locked_settings(rig.app.state.settings.data_dir.parent / name), lambda p: None
        )
        app.add_api_route(f"/bad-{name}", lambda: None, methods=[method], dependencies=dependencies)

        with pytest.raises(RuntimeError, match=message):
            check_route_declarations(app)


def test_a_declared_route_with_the_credential_flag_off_is_allowed_to_skip_require_access(
    rig: Rig,
) -> None:
    rig.app.add_api_route(
        "/open",
        lambda: None,
        methods=["GET"],
        dependencies=[Depends(limits(PAGE, credential=False))],
    )

    check_route_declarations(rig.app)


# -- Test 10: whose address is it ------------------------------------------------------------------


def _scope(peer: str = "9.9.9.9", *forwarded: str) -> dict[str, Any]:
    return {
        "client": (peer, 1234),
        "headers": [(b"x-forwarded-for", value.encode()) for value in forwarded],
    }


def test_10_with_no_trusted_proxy_the_socket_is_the_client_and_a_spoofed_header_changes_nothing(
    tmp_path: Path,
) -> None:
    rig = Rig(tmp_path)

    assert client_ip(_scope("9.9.9.9", "1.2.3.4"), 0) == "9.9.9.9"
    for forged in ("1.2.3.4", "5.6.7.8", "9.9.9.9", "not-an-ip"):
        rig.client.get("/healthz", headers={"X-Forwarded-For": forged})
    # every one of them was charged to the same bucket, whatever it claimed
    assert rig.tokens(HEALTHZ) == DEFAULT_RATE_LIMITS[HEALTHZ].burst - 4


def test_10_with_a_trusted_proxy_two_clients_behind_it_get_separate_buckets(tmp_path: Path) -> None:
    rig = Rig(tmp_path, trusted_proxy_hops=1)
    for _ in range(10):
        assert (
            rig.client.get("/healthz", headers={"X-Forwarded-For": "203.0.113.1"}).status_code
            == 200
        )
    assert rig.client.get("/healthz", headers={"X-Forwarded-For": "203.0.113.1"}).status_code == 429

    other = rig.client.get("/healthz", headers={"X-Forwarded-For": "203.0.113.2"})

    assert other.status_code == 200
    assert rig.tokens(HEALTHZ, "203.0.113.2") == DEFAULT_RATE_LIMITS[HEALTHZ].burst - 1


def test_a_caller_cannot_choose_its_own_bucket_behind_a_trusted_proxy() -> None:
    # The proxy appends the address it saw; whatever the caller put before it is ignored.
    assert client_ip(_scope("10.0.0.1", "6.6.6.6, 203.0.113.9"), 1) == "203.0.113.9"
    assert client_ip(_scope("10.0.0.1", "6.6.6.6, 203.0.113.9"), 2) == "6.6.6.6"
    # two proxies: the client is the second from the right
    assert client_ip(_scope("10.0.0.2", "203.0.113.9, 10.0.0.1"), 2) == "203.0.113.9"


@pytest.mark.parametrize(
    "forwarded",
    [(), ("",), ("not-an-ip",), ("203.0.113.9",), ("1.1.1.1, 300.1.1.1",)],
)
def test_a_missing_short_or_malformed_header_falls_back_to_the_socket(
    forwarded: tuple[str, ...],
) -> None:
    hops = 2 if forwarded == ("203.0.113.9",) else 1

    assert client_ip(_scope("10.0.0.1", *forwarded), hops) == "10.0.0.1"


def test_several_forwarded_headers_are_read_as_one_list_in_order() -> None:
    assert client_ip(_scope("10.0.0.1", "6.6.6.6", "203.0.113.9"), 1) == "203.0.113.9"
    assert client_ip(_scope("10.0.0.1", "6.6.6.6", "203.0.113.9"), 2) == "6.6.6.6"


def test_ipv6_addresses_are_normalized_so_one_client_has_one_bucket() -> None:
    a = client_ip(_scope("10.0.0.1", "2001:DB8::1"), 1)
    b = client_ip(_scope("10.0.0.1", "2001:0db8:0:0:0:0:0:1"), 1)

    assert a == b == "2001:db8::1"


def test_trusted_proxy_hops_must_be_a_non_negative_integer(tmp_path: Path) -> None:
    with pytest.raises(SettingsError, match="trusted_proxy_hops"):
        locked_settings(tmp_path, trusted_proxy_hops=-1)
    with pytest.raises(SettingsError, match="integer"):
        Settings.from_env(
            {
                "WORKBUDDY_ENV": "dev",
                "WORKBUDDY_TRUSTED_PROXY_HOPS": "one",
                "WORKBUDDY_DATA_DIR": str(tmp_path),
            }
        )

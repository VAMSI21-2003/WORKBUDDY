"""The request gate — rate limits, the credential and the Origin check, before any body is read.

An ASGI middleware, so it sees every request before FastAPI parses a body
(FastAPI parses a route's body *before* it resolves that route's
dependencies, so a ``401`` raised from a dependency would only come after an
unauthenticated multipart upload had been spooled to disk). It is the order
design "Decisions (2026-09-30, part 2)", item 1, asks for: the limiter first,
so guessing is throttled too, then the credential, then the Origin check.

For each request it

1. finds the route the request matches and reads the route's declaration
   (``backend.ratelimit.limits``),
2. decides the credential once, with ``backend.access.check_credential``'s
   logic, and stores the answer on the request for the dependency to reuse,
3. charges the request to the rate-limit buckets it declared (``429`` with
   ``Retry-After`` and no charge, if one has no token),
4. answers ``401`` for a missing or wrong credential, and ``403
   forbidden_origin`` where the Origin check applies,
5. runs the app, and takes the tokens that depend on the response (a poll
   that wasn't ``200``, a wrong password) when its status is known.

The router-wide ``require_access`` dependency repeats steps 4 for every
route, so a route reached some other way still can't skip them.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from fastapi import FastAPI
from starlette.routing import Match
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from backend.access import (
    CREDENTIAL_SCOPE_KEY,
    AccessControl,
    CredentialResult,
    forbidden_origin,
    origin_allowed,
    origin_check_needed,
    unauthorized,
)
from backend.errors import ApiError, ErrorCode, error_response
from backend.ratelimit import (
    RateLimiter,
    RouteLimits,
    after_response,
    charges_for,
    client_ip,
    declared_limits,
    iter_routes,
)
from backend.settings import Settings

logger = logging.getLogger(__name__)


class RequestGate:
    """See the module docstring."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        settings: Settings,
        limiter: RateLimiter,
        access: AccessControl,
        routes_of: Callable[[], FastAPI],
    ) -> None:
        self.app = app
        self._settings = settings
        self._limiter = limiter
        self._access = access
        self._routes_of = routes_of

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        try:
            refusal, declared, ip, valid = self._admit(scope)
        except Exception:  # noqa: BLE001 — fail closed: a gate that errors lets nothing through
            logger.exception("The request gate failed on %s %s", scope.get("method"), scope["path"])
            await error_response(ErrorCode.INTERNAL_ERROR)(scope, receive, send)
            return
        if refusal is not None:
            await refusal(scope, receive, send)
            return

        charged = False

        async def observing_send(message: Message) -> None:
            nonlocal charged
            if message["type"] == "http.response.start" and not charged:
                charged = True
                self._limiter.charge_after(
                    ip, after_response(declared, valid=valid, status=int(message["status"]))
                )
            await send(message)

        await self.app(scope, receive, observing_send)

    def _admit(
        self, scope: Scope
    ) -> tuple[ASGIApp | None, RouteLimits | None, str, bool]:
        """Charge the request and check its credential: a refusal to send, or ``None`` to go on."""
        declared = declared_limits(self._match(scope))
        ip = client_ip(scope, self._settings.trusted_proxy_hops)
        result = CredentialResult(True)
        if declared is not None and declared.credential:
            result = self._access.check(scope)
            scope[CREDENTIAL_SCOPE_KEY] = result
        decision = self._limiter.charge(
            ip, charges_for(declared, valid=result.valid, path=scope["path"])
        )
        if not decision.allowed:
            logger.info("Rate limit: refusing a request, retry in %d s", decision.retry_after)
            limited = ApiError(
                ErrorCode.RATE_LIMITED, headers={"Retry-After": str(decision.retry_after)}
            )
            return _refusal(limited), declared, ip, result.valid
        if declared is not None and declared.credential and not result.valid:
            return _refusal(unauthorized()), declared, ip, False
        if declared is not None and self._origin_must_match(scope, declared, result):
            if not origin_allowed(scope, self._settings):
                logger.info("Origin check: refusing a request with a missing or foreign Origin")
                return _refusal(forbidden_origin()), declared, ip, result.valid
        return None, declared, ip, result.valid

    @staticmethod
    def _origin_must_match(scope: Scope, declared: RouteLimits, result: CredentialResult) -> bool:
        if declared.credential:
            return origin_check_needed(scope, result)
        # A route that takes no credential but changes state (the access
        # route) is checked on every non-safe request.
        return declared.mutating and scope.get("method") not in ("GET", "HEAD", "OPTIONS")

    def _match(self, scope: Scope) -> object | None:
        for route in iter_routes(self._routes_of()):
            match, _ = route.matches(scope)
            if match == Match.FULL:
                return route
        return None


def _refusal(error: ApiError) -> ASGIApp:
    return error_response(error.code, error.message, error.headers)

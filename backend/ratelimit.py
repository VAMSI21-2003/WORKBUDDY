"""Rate limiting — per-IP token buckets, and the declaration every route carries.

Design "Decisions (2026-09-30, part 2)", item 1, and "Decisions
(2026-10-01)", item 3. The buckets live in process memory (one worker), keyed
by client IP, and every request is charged to them by what its route
declares, never by its path:

==========  =====  ==========  ============================================
bucket      burst  refill      charged for
==========  =====  ==========  ============================================
general     60     1/s         every ``/api/v1`` request that isn't a valid poll
poll        30     3/s         a poll of a turn, with a valid credential
turns       10     10/hour     ``POST .../turns``, in addition to ``general``
uploads     5      20/hour     ``POST .../files``, in addition to ``general``
access      10     20/hour     ``POST /api/v1/access``, in addition to ``general``
healthz     10     1/s         ``GET /healthz``
page        30     1/s         ``GET /`` and ``/static/*`` (the frontend's)
==========  =====  ==========  ============================================

The rules the limiter applies (``charges_for``):

- **Strict.** A request is charged whatever the response: a ``409``, ``413``,
  ``415``, ``422`` or ``403 forbidden_origin`` still costs its tokens.
- **A refusal costs nothing.** A request with a bucket that has no token is
  refused with ``429`` and takes nothing from any bucket, so ``Retry-After``
  stays exact.
- **A missing or wrong credential takes 2 ``general`` tokens**, on every
  route that needs one (the poll route too), and nothing from the route's own
  buckets. A guesser pays double and can't use the poll allowance.
- **A valid poll takes one ``poll`` token only.** If the response isn't
  ``200``, one ``general`` token is taken as well, afterwards (clamped at
  zero), so probing ids costs the strict rate.
- **``POST /api/v1/access``** takes 1 ``general`` and 1 ``access`` token; a
  wrong password (a ``401``) takes one more ``general`` token afterwards.

A route declares its buckets with ``limits(...)``, in its ``dependencies``.
``check_route_declarations`` refuses to start the app if a route has none.
"""

from __future__ import annotations

import ipaddress
import logging
import math
import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import fastapi.routing
from fastapi import FastAPI
from fastapi.routing import APIRoute
from starlette.types import Scope

from backend.settings import BucketSpec

logger = logging.getLogger(__name__)

GENERAL = "general"
POLL = "poll"
TURNS = "turns"
UPLOADS = "uploads"
ACCESS = "access"
HEALTHZ = "healthz"
PAGE = "page"

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
# A bucket that has refilled completely is the same as a bucket nobody has
# touched, so it can be forgotten.
_PRUNE_ABOVE = 20_000
_EPSILON = 1e-9


@dataclass(frozen=True)
class RouteLimits:
    """What a route declares to the limiter, in its ``dependencies`` (see ``limits``).

    Attributes:
        buckets: The buckets it is charged to, for a request with a valid
            credential (a poll names ``poll``, others ``general`` first).
        mutating: Whether it changes state. A ``GET`` route must say no, and
            any other method must say yes (``check_route_declarations``).
        credential: Whether it needs a credential. ``False`` for ``/healthz``
            and the access route itself (and the frontend's gate and static
            routes).
        poll: Whether it is a poll of an existing turn (the special charging
            rule above).
    """

    buckets: tuple[str, ...]
    mutating: bool = False
    credential: bool = True
    poll: bool = False

    def __call__(self) -> None:
        """A route dependency that does nothing: the declaration is read by the limiter."""
        return None


def limits(
    *buckets: str, mutating: bool = False, credential: bool = True, poll: bool = False
) -> RouteLimits:
    """The declaration to put in a route's ``dependencies=[Depends(limits(...))]``."""
    return RouteLimits(tuple(buckets), mutating=mutating, credential=credential, poll=poll)


def iter_routes(app: FastAPI) -> Iterator[Any]:
    """Every route the app serves, with its path, methods, dependencies and ``matches``.

    FastAPI 0.142 keeps an included router as one lazy entry in
    ``app.routes``; ``iter_route_contexts`` expands it into the routes it
    serves, each with the router's own dependencies merged in. (The
    dependency pins FastAPI to a minor version, so a change here shows up as a
    failing test, not a silent gap.)
    """
    expand = getattr(fastapi.routing, "iter_route_contexts", None)
    yield from expand(app.routes) if expand is not None else app.routes


def _is_api_route(route: Any) -> bool:
    return isinstance(getattr(route, "original_route", route), APIRoute)


def route_dependencies(route: Any) -> list[Callable[..., Any]]:
    """The callables of a route's dependencies (the router's and the route's own)."""
    return [d.dependency for d in getattr(route, "dependencies", []) if d.dependency is not None]


def declared_limits(route: Any) -> RouteLimits | None:
    """The ``RouteLimits`` a route declared, or ``None`` if it declared none (or several)."""
    if route is None or not _is_api_route(route):
        return None
    found = [d for d in route_dependencies(route) if isinstance(d, RouteLimits)]
    return found[0] if len(found) == 1 else None


def check_route_declarations(app: FastAPI) -> None:
    """Refuse to start if a route lacks its bucket declaration, or declares it wrongly.

    Raises:
        RuntimeError: A route has no declaration, or two; names a bucket that
            doesn't exist; says it needs a credential but isn't behind
            ``require_access``; a ``GET`` route is declared mutating, or any
            other method isn't (design "Decisions (2026-10-01)", item 2.3 and
            item 3.5).
    """
    from backend.access import require_access  # (access imports nothing from here)

    known = {GENERAL, POLL, TURNS, UPLOADS, ACCESS, HEALTHZ, PAGE}
    for route in iter_routes(app):
        if not _is_api_route(route):
            continue
        declared = declared_limits(route)
        where = f"{sorted(route.methods)} {route.path}"
        if declared is None:
            raise RuntimeError(f"route {where} doesn't declare its rate-limit buckets")
        if not declared.buckets or not set(declared.buckets) <= known:
            raise RuntimeError(f"route {where} names an unknown bucket: {declared.buckets}")
        if declared.credential and require_access not in route_dependencies(route):
            raise RuntimeError(f"route {where} needs a credential but lacks require_access")
        safe = route.methods <= _SAFE_METHODS
        if safe and declared.mutating:
            raise RuntimeError(f"route {where} is a GET route declared as mutating")
        if not safe and not declared.mutating:
            raise RuntimeError(f"route {where} changes state but isn't declared mutating")
        if declared.poll and (declared.mutating or declared.buckets != (POLL,)):
            raise RuntimeError(f"route {where} is declared as a poll but isn't a read of one")


# -- Charging -----------------------------------------------------------------------------


def charges_for(declared: RouteLimits | None, *, valid: bool, path: str) -> list[tuple[str, int]]:
    """What a request costs before it runs: ``[(bucket, tokens), ...]``.

    ``declared`` is the matched route's declaration; ``None`` (no route
    matched, or the method isn't allowed) is charged to ``general`` under
    ``/api/v1`` and to ``page`` elsewhere. ``valid`` is whether the request
    carries a valid credential, as ``check_credential`` decided.
    """
    if declared is None:
        return [(GENERAL, 1)] if path.startswith("/api/v1") else [(PAGE, 1)]
    if declared.credential and not valid:
        return [(GENERAL, 2)]
    return [(bucket, 1) for bucket in declared.buckets]


def after_response(
    declared: RouteLimits | None, *, valid: bool, status: int
) -> list[tuple[str, int]]:
    """What a request costs once its response is known (taken only down to zero)."""
    if declared is None:
        return []
    if declared.poll and valid and status != 200:
        return [(GENERAL, 1)]
    if status == 401 and not (declared.credential and not valid):
        # A wrong password on a route that takes no credential, which the
        # limiter couldn't judge before the handler ran (the access route).
        return [(GENERAL, 1)]
    return []


@dataclass(frozen=True)
class Decision:
    """The outcome of charging a request: allowed, or refused with a wait in seconds."""

    allowed: bool
    retry_after: int = 0


class _Bucket:
    __slots__ = ("stamp", "tokens")

    def __init__(self, tokens: float, stamp: float) -> None:
        self.tokens = tokens
        self.stamp = stamp


class RateLimiter:
    """Per-IP token buckets in memory. Thread-safe; time comes from an injected clock."""

    def __init__(self, specs: Mapping[str, BucketSpec], clock: Callable[[], float]) -> None:
        self._specs = dict(specs)
        self._clock = clock
        self._lock = threading.Lock()
        self._buckets: dict[tuple[str, str], _Bucket] = {}

    def charge(self, ip: str, charges: Sequence[tuple[str, int]]) -> Decision:
        """Charge a request, or refuse it and charge nothing.

        Every bucket named must hold at least one token. Then each is
        charged its cost, down to zero but no lower. Otherwise nothing is
        taken, and the wait is the longest any refusing bucket needs to hold
        a token again.
        """
        with self._lock:
            now = self._clock()
            states = [(name, cost, self._refilled(ip, name, now)) for name, cost in charges]
            wait = 0
            for name, _, bucket in states:
                if bucket.tokens < 1 - _EPSILON:
                    spec = self._specs[name]
                    wait = max(wait, math.ceil((1 - bucket.tokens) / spec.refill_per_s - _EPSILON))
            if wait:
                return Decision(False, max(wait, 1))
            for _, cost, bucket in states:
                bucket.tokens = max(bucket.tokens - cost, 0.0)
            return Decision(True)

    def charge_after(self, ip: str, charges: Sequence[tuple[str, int]]) -> None:
        """Take tokens for a response already sent: never refuses, never goes below zero."""
        if not charges:
            return
        with self._lock:
            now = self._clock()
            for name, cost in charges:
                bucket = self._refilled(ip, name, now)
                bucket.tokens = max(bucket.tokens - cost, 0.0)

    def tokens(self, ip: str, name: str) -> float:
        """The tokens ``ip`` has in ``name`` right now (for tests and diagnostics)."""
        with self._lock:
            return self._refilled(ip, name, self._clock()).tokens

    def prune(self) -> int:
        """Forget every bucket that has refilled completely. Returns how many."""
        with self._lock:
            return self._prune_locked(self._clock())

    def _refilled(self, ip: str, name: str, now: float) -> _Bucket:
        spec = self._specs[name]
        key = (name, ip)
        bucket = self._buckets.get(key)
        if bucket is None:
            if len(self._buckets) >= _PRUNE_ABOVE:
                self._prune_locked(now)
            bucket = self._buckets[key] = _Bucket(float(spec.burst), now)
            return bucket
        elapsed = max(now - bucket.stamp, 0.0)
        bucket.tokens = min(float(spec.burst), bucket.tokens + elapsed * spec.refill_per_s)
        bucket.stamp = now
        return bucket

    def _prune_locked(self, now: float) -> int:
        full = [
            key
            for key, bucket in self._buckets.items()
            if bucket.tokens + max(now - bucket.stamp, 0.0) * self._specs[key[0]].refill_per_s
            >= self._specs[key[0]].burst
        ]
        for key in full:
            del self._buckets[key]
        return len(full)


# -- The client's address -----------------------------------------------------------------


def client_ip(scope: Scope, trusted_proxy_hops: int) -> str:
    """The address a request is charged to.

    With ``trusted_proxy_hops == 0`` it is the socket's peer, and
    ``X-Forwarded-For`` is ignored, so a caller can't choose its own bucket.
    With ``N`` proxies in front of the app, each appends the address it saw
    to ``X-Forwarded-For``, so the client is the ``N``-th entry from the
    right; anything before that is the caller's to forge and is ignored. If
    the header has fewer than ``N`` valid entries, the socket's peer is used.
    """
    client = scope.get("client")
    peer = str(client[0]) if client else "unknown"
    if trusted_proxy_hops <= 0:
        return peer
    values = [
        value.decode("latin-1")
        for name, value in scope.get("headers", [])
        if name.lower() == b"x-forwarded-for"
    ]
    entries = [part.strip() for value in values for part in value.split(",")]
    if len(entries) < trusted_proxy_hops:
        return peer
    candidate = entries[-trusted_proxy_hops]
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return peer

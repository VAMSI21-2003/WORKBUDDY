"""Access — who may call the API: the credential check, the access cookie, and the Origin check.

Design "Decisions (2026-09-30)", item 1; "Decisions (2026-09-30, part 2)",
ask 4; and "Decisions (2026-10-01)", items 1 and 2.

**One function decides.** ``check_credential(request)`` says whether a request
carries a valid credential and which kind. The router-wide dependency
(``require_access``), the rate limiter and the Origin check all call it (the
gate middleware stores its answer on the request, so none of them can
disagree). A route never looks at a password, a header or a cookie itself, so
a real sign-in can replace the demo password by changing ``AccessControl``
only: how a credential is checked (``AccessControl.check``) and how one is
obtained (``AccessControl.exchange``).

**The credential today** is either

- the access cookie ``__Host-wb_access`` (the browser's), a random 256-bit
  token that ``POST /api/v1/access`` hands out in exchange for the demo
  password and that the server holds only as its SHA-256, with an absolute
  expiry; or
- ``Authorization: Bearer <demo password>``, kept for scripts and tests.

If an ``Authorization`` header is present only it is checked and the cookie
is ignored, so a wrong Bearer is a ``401`` even beside a valid cookie. Every
failure (no credential, a wrong Bearer, an unknown or expired cookie, another
scheme) is the same ``401``: nobody learns why. With no demo password set
(``WORKBUDDY_ENV=dev`` only) every request is let through and ``kind`` is
``None``.

**CSRF.** A cookie authenticates requests the browser sends by itself, so a
request that the cookie authenticates, and that isn't ``GET``, ``HEAD`` or
``OPTIONS``, must carry an ``Origin`` header equal to
``WORKBUDDY_PUBLIC_ORIGIN``. ``POST /api/v1/access`` takes no credential and
is checked too. A Bearer request isn't: a page on another origin can't add
the header without a CORS preflight.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Literal

from fastapi import Depends, Request, Security
from fastapi.responses import Response
from fastapi.security import APIKeyCookie, HTTPAuthorizationCredentials, HTTPBearer
from starlette.types import Scope

from backend.errors import ApiError, ErrorCode
from backend.settings import ENV_DEV, Settings

logger = logging.getLogger(__name__)

COOKIE_NAME = "__Host-wb_access"
REALM = 'Bearer realm="workbuddy-demo"'
CREDENTIAL_SCOPE_KEY = "workbuddy.credential"
_MAX_TOKEN_CHARS = 256

Kind = Literal["bearer", "cookie"]


@dataclass(frozen=True)
class CredentialResult:
    """Whether a request carries a valid credential, and which kind.

    ``kind`` is ``None`` when the request is invalid, and also when there is
    nothing to check (no demo password, ``dev`` only): then ``valid`` is true.
    """

    valid: bool
    kind: Kind | None = None


class TokenStore:
    """The live access tokens: in memory, bounded, each with an absolute expiry.

    The key is the token's SHA-256, so the store never holds a cookie's value.
    At most ``max_tokens`` are kept: when it is full, the token closest to
    expiry is evicted (its user sees the gate again). Expired tokens are
    dropped when looked up and by ``sweep``. A restart empties it.
    """

    def __init__(self, *, ttl_s: float, max_tokens: int, clock: Callable[[], float]) -> None:
        self._ttl_s = ttl_s
        self._max_tokens = max_tokens
        self._clock = clock
        self._lock = threading.Lock()
        self._expiry: dict[str, float] = {}

    def issue(self) -> str:
        """A new token (256 random bits), valid for the TTL from now."""
        token = secrets.token_urlsafe(32)
        with self._lock:
            now = self._clock()
            if len(self._expiry) >= self._max_tokens:
                self._drop_expired(now)
            while len(self._expiry) >= self._max_tokens:
                del self._expiry[min(self._expiry, key=self._expiry.__getitem__)]
            self._expiry[_digest(token)] = now + self._ttl_s
        return token

    def valid(self, token: str) -> bool:
        """Whether ``token`` was issued, and hasn't expired."""
        if not token or len(token) > _MAX_TOKEN_CHARS:
            return False
        key = _digest(token)
        with self._lock:
            expires = self._expiry.get(key)
            if expires is None:
                return False
            if expires <= self._clock():
                del self._expiry[key]
                return False
            return True

    def sweep(self) -> int:
        """Drop every expired token. Returns how many."""
        with self._lock:
            return self._drop_expired(self._clock())

    def __len__(self) -> int:
        with self._lock:
            return len(self._expiry)

    def _drop_expired(self, now: float) -> int:
        expired = [key for key, expires in self._expiry.items() if expires <= now]
        for key in expired:
            del self._expiry[key]
        return len(expired)


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8", "surrogatepass")).hexdigest()


class AccessControl:
    """The demo-password access scheme. Replace this class to replace the sign-in.

    ``check`` decides a request's credential; ``exchange`` turns what a
    sign-in form sends into a cookie token (or ``None``). Nothing else in
    the app knows how either works.
    """

    def __init__(self, settings: Settings, clock: Callable[[], float]) -> None:
        self._password = (
            settings.demo_password.encode("utf-8") if settings.demo_password is not None else None
        )
        self._open = self._password is None and settings.env == ENV_DEV
        self.tokens = TokenStore(
            ttl_s=settings.access_ttl_hours * 3600.0,
            max_tokens=settings.access_max_tokens,
            clock=clock,
        )

    @property
    def open(self) -> bool:
        """True when there is no password to check (``dev`` only): every request is let through."""
        return self._open

    def check(self, request: Request | Scope) -> CredentialResult:
        """Decide ``request``'s credential. If ``Authorization`` is present, only it counts."""
        if self._open:
            return CredentialResult(True, None)
        headers = request.headers if isinstance(request, Request) else Request(request).headers
        authorization = headers.getlist("authorization")
        if authorization:
            if len(authorization) == 1 and self._bearer_valid(authorization[0]):
                return CredentialResult(True, "bearer")
            return CredentialResult(False, None)
        cookies = request.cookies if isinstance(request, Request) else Request(request).cookies
        token = cookies.get(COOKIE_NAME)
        if token is not None and self.tokens.valid(token):
            return CredentialResult(True, "cookie")
        return CredentialResult(False, None)

    def exchange(self, password: str | None) -> str | None:
        """A new access token for ``password``, or ``None`` if it's wrong or missing.

        With no demo password set (``dev``), nothing is checked.
        """
        if self._open:
            return self.tokens.issue()
        if password is None:
            return None
        if not self._password_matches(password.encode("utf-8", "surrogatepass")):
            return None
        return self.tokens.issue()

    def _bearer_valid(self, header: str) -> bool:
        # ``Bearer`` and one space, then the credential exactly as sent: a
        # password may contain spaces, so nothing after the first one is trimmed.
        scheme, space, credential = header.partition(" ")
        if not space or scheme.lower() != "bearer":
            return False
        # ASGI hands header text over as latin-1: re-encoding gives back the
        # bytes the client sent, which are compared as UTF-8 with the password.
        return self._password_matches(credential.encode("latin-1", "replace"))

    def _password_matches(self, candidate: bytes) -> bool:
        assert self._password is not None
        return hmac.compare_digest(candidate, self._password)


def access_of(request: Request | Scope) -> AccessControl:
    app = request.app if isinstance(request, Request) else request["app"]
    return app.state.access  # type: ignore[no-any-return]


def check_credential(request: Request) -> CredentialResult:
    """THE credential check: the one function everything that cares calls.

    Returns the answer the gate middleware already computed for this request
    when there is one, so the limiter, the Origin check and the dependency
    agree by construction.
    """
    cached = request.scope.get(CREDENTIAL_SCOPE_KEY)
    if isinstance(cached, CredentialResult):
        return cached
    result = access_of(request).check(request)
    request.scope[CREDENTIAL_SCOPE_KEY] = result
    return result


def unauthorized() -> ApiError:
    """The one ``401``: the same header and body whatever went wrong."""
    return ApiError(ErrorCode.UNAUTHORIZED, headers={"WWW-Authenticate": REALM})


def origin_allowed(scope: Scope, settings: Settings) -> bool:
    """Whether the request's ``Origin`` is exactly the configured public origin.

    (Outside production, with no origin set, the same port on ``localhost``
    is accepted as well: ``Settings.accepted_origins``.)

    A missing header, ``null``, a second header or any other value is refused.
    Compared after lower-casing, so ``HTTPS://Demo.Example`` matches
    ``https://demo.example``; the header's value is never derived from
    ``Host``.
    """
    values = [v for k, v in scope.get("headers", []) if k.lower() == b"origin"]
    if len(values) != 1:
        return False
    return values[0].decode("latin-1").strip().lower() in settings.accepted_origins


def origin_check_needed(scope: Scope, result: CredentialResult) -> bool:
    """Whether the request must pass the Origin check: the cookie authenticated a state change."""
    return result.kind == "cookie" and scope.get("method") not in ("GET", "HEAD", "OPTIONS")


def forbidden_origin() -> ApiError:
    return ApiError(ErrorCode.FORBIDDEN_ORIGIN)


_bearer_scheme = HTTPBearer(auto_error=False, scheme_name="bearer")
_cookie_scheme = APIKeyCookie(name=COOKIE_NAME, auto_error=False, scheme_name="access_cookie")


async def require_access(
    request: Request,
    _bearer: Annotated[HTTPAuthorizationCredentials | None, Security(_bearer_scheme)] = None,
    _cookie: Annotated[str | None, Security(_cookie_scheme)] = None,
) -> CredentialResult:
    """The router-wide dependency: a valid credential, and the Origin check where it applies.

    It is on the whole ``/api/v1`` router (and on every HTML route the
    frontend adds), so a new route can't forget it. The two ``Security``
    parameters only document the schemes in ``openapi.json``; what decides is
    ``check_credential``.
    """
    result = check_credential(request)
    if not result.valid:
        raise unauthorized()
    if origin_check_needed(request.scope, result) and not origin_allowed(
        request.scope, request.app.state.settings
    ):
        raise forbidden_origin()
    return result


AccessDependency = Depends(require_access)


async def exchange_password(request: Request) -> Response:
    """``POST /api/v1/access``: the demo password, in a form, for the access cookie.

    ``application/x-www-form-urlencoded`` with one field, ``password``; any
    other content type is ``422``. Success is ``204`` with ``Set-Cookie``. A
    wrong or missing password is the one ``401``.
    """
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type != "application/x-www-form-urlencoded":
        raise ApiError(ErrorCode.INVALID_REQUEST)
    form = await request.form()
    passwords = form.getlist("password")
    if len(passwords) > 1 or any(not isinstance(p, str) for p in passwords):
        raise ApiError(ErrorCode.INVALID_REQUEST)
    token = access_of(request).exchange(passwords[0] if passwords else None)  # type: ignore[arg-type]
    if token is None:
        raise unauthorized()
    return Response(
        status_code=204,
        headers={"Set-Cookie": f"{COOKIE_NAME}={token}; Path=/; Secure; HttpOnly; SameSite=Strict"},
    )

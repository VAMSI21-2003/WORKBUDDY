"""OpenID Connect — signing a person in with Google (docs/design/google-sign-in.md).

The authorization-code flow with PKCE, done on the server:

1. ``PendingLogins.start`` makes ``state``, ``nonce`` and a PKCE verifier and remembers them for
   ``PENDING_TTL_S`` seconds, with a hash of a random *binder* that the browser keeps in a cookie.
2. ``OidcClient.authorization_url`` is where the browser is sent.
3. Google sends the browser back with ``code`` and ``state``. ``PendingLogins.take`` finds the
   pending login (once: it is gone after it is taken) only if the binder cookie is the one the
   same browser was given at the start, so a callback URL an attacker plants in a victim's browser
   (login CSRF) finds nothing.
4. ``OidcClient.exchange_code`` trades the code, with the PKCE verifier and the client secret, for
   an ID token; ``verify_id_token`` checks it.

``verify_id_token`` accepts a token only if all of these hold, and says only *which* failed to the
log, never to the person and never with any of the token's content:

- RS256, signed by a key in the issuer's published key set (cached; one refetch per
  ``JWKS_REFETCH_S`` for an unknown key id);
- ``iss`` is the issuer (for Google, either spelling Google uses), ``aud`` is our client id (and
  ``azp``, when present, is too), ``exp`` has not passed and ``iat`` is not in the future
  (``LEEWAY_S`` of clock difference allowed);
- ``nonce`` is the one made for this login;
- ``email_verified`` is ``true`` and ``email`` and ``sub`` are present.

Everything that reaches Google goes through ``self._http`` (an ``httpx.Client`` the tests replace
with a fake identity provider). Redirects are not followed.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt
from jwt.algorithms import RSAAlgorithm

from backend.settings import GOOGLE_ISSUER, Settings

logger = logging.getLogger(__name__)

CALLBACK_PATH = "/auth/google/callback"
PENDING_TTL_S = 600.0
MAX_PENDING = 5000
DISCOVERY_TTL_S = 3600.0
JWKS_TTL_S = 3600.0
JWKS_REFETCH_S = 60.0
LEEWAY_S = 60
HTTP_TIMEOUT_S = 10.0
MAX_TOKEN_CHARS = 8192
MAX_RESPONSE_BYTES = 256 * 1024
# The two spellings of Google's issuer that its ID tokens use.
_GOOGLE_ISSUERS = (GOOGLE_ISSUER, "accounts.google.com")


class SignInError(Exception):
    """A sign-in that didn't work. ``reason`` is a short code for the log, never shown, and never
    holds anything from a token or a response."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Identity:
    """What Google vouched for, and all that is kept: the subject id and the email."""

    subject: str
    email: str


@dataclass(frozen=True)
class Pending:
    nonce: str
    verifier: str


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def challenge_for(verifier: str) -> str:
    """The PKCE S256 challenge for ``verifier``."""
    return _b64url(hashlib.sha256(verifier.encode("ascii")).digest())


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", "surrogatepass")).hexdigest()


class PendingLogins:
    """Started sign-ins waiting for Google's answer: in memory, bounded, single use."""

    def __init__(self, clock: Callable[[], float]) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        # sha256(state) -> (nonce, verifier, sha256(binder), expires)
        self._pending: dict[str, tuple[str, str, str, float]] = {}

    def start(self) -> tuple[str, str, str, str]:
        """``(state, nonce, verifier, binder)`` for a new login."""
        state, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        verifier, binder = secrets.token_urlsafe(48), secrets.token_urlsafe(32)
        now = self._clock()
        with self._lock:
            if len(self._pending) >= MAX_PENDING:
                self._drop_expired(now)
            while len(self._pending) >= MAX_PENDING:
                del self._pending[min(self._pending, key=lambda k: self._pending[k][3])]
            self._pending[_hash(state)] = (nonce, verifier, _hash(binder), now + PENDING_TTL_S)
        return state, nonce, verifier, binder

    def take(self, state: str | None, binder: str | None) -> Pending | None:
        """The pending login for ``state``, removed, if ``binder`` is its browser's.

        A wrong or missing binder takes nothing (and removes nothing), so a stranger who learns a
        state can't use up the person's login. An expired entry is removed and not returned.
        """
        if not state or not binder or len(state) > 256 or len(binder) > 256:
            return None
        key = _hash(state)
        with self._lock:
            entry = self._pending.get(key)
            if entry is None:
                return None
            nonce, verifier, binder_hash, expires = entry
            if not hmac.compare_digest(binder_hash, _hash(binder)):
                return None
            del self._pending[key]
            if expires <= self._clock():
                return None
            return Pending(nonce=nonce, verifier=verifier)

    def __len__(self) -> int:
        with self._lock:
            return len(self._pending)

    def _drop_expired(self, now: float) -> None:
        for key in [k for k, v in self._pending.items() if v[3] <= now]:
            del self._pending[key]


class OidcClient:
    """Talks to the identity provider and checks what it says. One per app."""

    def __init__(
        self,
        settings: Settings,
        *,
        http: httpx.Client | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._settings = settings
        self._http = http if http is not None else httpx.Client(
            timeout=HTTP_TIMEOUT_S, follow_redirects=False
        )
        self._clock = clock
        self._lock = threading.Lock()
        self._discovery: tuple[dict[str, Any], float] | None = None
        self._keys: dict[str, Any] = {}
        self._keys_at: float | None = None
        self._refetch_at: float | None = None
        self.pending = PendingLogins(clock)

    @property
    def enabled(self) -> bool:
        return self._settings.google_enabled

    @property
    def redirect_uri(self) -> str:
        """Where Google sends the browser back: the public origin and a fixed path, nothing else."""
        return self._settings.public_origin + CALLBACK_PATH

    # -- Step 1 ---------------------------------------------------------------------------------

    def begin(self) -> tuple[str, str]:
        """Start a sign-in: ``(authorization url, binder)``. The binder goes in a cookie."""
        state, nonce, verifier, binder = self.pending.start()
        endpoint = self._endpoint("authorization_endpoint")
        query = urlencode(
            {
                "client_id": self._settings.google_client_id,
                "redirect_uri": self.redirect_uri,
                "response_type": "code",
                "scope": "openid email",
                "state": state,
                "nonce": nonce,
                "code_challenge": challenge_for(verifier),
                "code_challenge_method": "S256",
                "prompt": "select_account",
            }
        )
        return f"{endpoint}?{query}", binder

    # -- Step 4 ---------------------------------------------------------------------------------

    def exchange_code(self, code: str, verifier: str) -> str:
        """Trade the authorization ``code`` for an ID token (the raw JWT)."""
        endpoint = self._endpoint("token_endpoint")
        try:
            response = self._http.post(
                endpoint,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": self.redirect_uri,
                    "client_id": self._settings.google_client_id,
                    "client_secret": self._settings.google_client_secret,
                    "code_verifier": verifier,
                },
                headers={"Accept": "application/json"},
            )
        except httpx.HTTPError:
            raise SignInError("token_endpoint_unreachable") from None
        body = self._json(response, "token_endpoint")
        token = body.get("id_token")
        if not isinstance(token, str) or not token or len(token) > MAX_TOKEN_CHARS:
            raise SignInError("token_endpoint_no_id_token")
        return token

    def verify_id_token(self, token: str, nonce: str) -> Identity:
        """The person ``token`` vouches for, or ``SignInError`` (see the module docstring)."""
        if len(token) > MAX_TOKEN_CHARS:
            raise SignInError("token_too_long")
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError:
            raise SignInError("token_malformed") from None
        if header.get("alg") != "RS256":
            raise SignInError("token_algorithm")
        kid = header.get("kid")
        if not isinstance(kid, str) or not kid:
            raise SignInError("token_no_key_id")
        key = self._key_for(kid)
        try:
            claims = jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                audience=self._settings.google_client_id,
                issuer=list(self._accepted_issuers()),
                leeway=LEEWAY_S,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            )
        except jwt.ExpiredSignatureError:
            raise SignInError("token_expired") from None
        except jwt.InvalidAudienceError:
            raise SignInError("token_audience") from None
        except jwt.InvalidIssuerError:
            raise SignInError("token_issuer") from None
        except jwt.InvalidSignatureError:
            raise SignInError("token_signature") from None
        except jwt.PyJWTError:
            raise SignInError("token_invalid") from None
        return self._identity(claims, nonce)

    # -- Checks ---------------------------------------------------------------------------------

    def _accepted_issuers(self) -> tuple[str, ...]:
        issuer = self._settings.oidc_issuer
        return _GOOGLE_ISSUERS if issuer == GOOGLE_ISSUER else (issuer,)

    def _identity(self, claims: dict[str, Any], nonce: str) -> Identity:
        client_id = self._settings.google_client_id
        audience = claims.get("aud")
        authorized_party = claims.get("azp")
        if isinstance(audience, list) and len(audience) > 1 and authorized_party != client_id:
            raise SignInError("token_azp")
        if authorized_party is not None and authorized_party != client_id:
            raise SignInError("token_azp")
        sent = claims.get("nonce")
        if not isinstance(sent, str) or not hmac.compare_digest(
            sent.encode("utf-8"), nonce.encode("utf-8")
        ):
            raise SignInError("token_nonce")
        if claims.get("email_verified") is not True:
            raise SignInError("email_not_verified")
        subject, email = claims.get("sub"), claims.get("email")
        if not isinstance(subject, str) or not 0 < len(subject) <= 255:
            raise SignInError("token_no_subject")
        if not isinstance(email, str) or not 3 <= len(email) <= 254 or email.count("@") != 1:
            raise SignInError("token_no_email")
        return Identity(subject=subject, email=email.strip().lower())

    # -- Talking to the provider ----------------------------------------------------------------

    def _endpoint(self, name: str) -> str:
        value = self._discovery_document().get(name)
        if not isinstance(value, str) or not value.startswith(self._scheme()):
            raise SignInError("discovery_bad_endpoint")
        return value

    def _scheme(self) -> str:
        """``https://`` or ``http://``: the provider's endpoints must use the issuer's own."""
        return self._settings.oidc_issuer.partition("://")[0] + "://"

    def _discovery_document(self) -> dict[str, Any]:
        now = self._clock()
        with self._lock:
            if self._discovery is not None and self._discovery[1] > now:
                return self._discovery[0]
        issuer = self._settings.oidc_issuer
        try:
            response = self._http.get(f"{issuer}/.well-known/openid-configuration")
        except httpx.HTTPError:
            raise SignInError("discovery_unreachable") from None
        document = self._json(response, "discovery")
        if document.get("issuer") not in self._accepted_issuers():
            raise SignInError("discovery_issuer")
        with self._lock:
            self._discovery = (document, now + DISCOVERY_TTL_S)
        return document

    def _key_for(self, kid: str) -> Any:
        now = self._clock()
        with self._lock:
            stale = self._keys_at is None or now - self._keys_at >= JWKS_TTL_S
            key = None if stale else self._keys.get(kid)
            may_refetch = self._refetch_at is None or now - self._refetch_at >= JWKS_REFETCH_S
        if key is not None:
            return key
        if not stale and not may_refetch:
            raise SignInError("token_unknown_key")
        self._fetch_keys(now)
        with self._lock:
            key = self._keys.get(kid)
        if key is None:
            raise SignInError("token_unknown_key")
        return key

    def _fetch_keys(self, now: float) -> None:
        uri = self._discovery_document().get("jwks_uri")
        if not isinstance(uri, str) or not uri.startswith(self._scheme()):
            raise SignInError("discovery_bad_endpoint")
        with self._lock:
            self._refetch_at = now
        try:
            response = self._http.get(uri)
        except httpx.HTTPError:
            raise SignInError("jwks_unreachable") from None
        document = self._json(response, "jwks")
        keys: dict[str, Any] = {}
        entries = document.get("keys")
        if not isinstance(entries, list):
            raise SignInError("jwks_bad")
        for entry in entries:
            if not isinstance(entry, dict) or entry.get("kty") != "RSA":
                continue
            if entry.get("use") not in (None, "sig") or entry.get("alg") not in (None, "RS256"):
                continue
            kid = entry.get("kid")
            if not isinstance(kid, str) or not kid:
                continue
            try:
                keys[kid] = RSAAlgorithm.from_jwk(json.dumps(entry))
            except (jwt.PyJWTError, ValueError):
                continue
        with self._lock:
            self._keys, self._keys_at = keys, now

    @staticmethod
    def _json(response: httpx.Response, what: str) -> dict[str, Any]:
        if response.status_code != 200:
            raise SignInError(f"{what}_status")
        if len(response.content) > MAX_RESPONSE_BYTES:
            raise SignInError(f"{what}_too_large")
        try:
            body = response.json()
        except ValueError:
            raise SignInError(f"{what}_not_json") from None
        if not isinstance(body, dict):
            raise SignInError(f"{what}_not_json")
        return body

"""The fake identity provider's logic: accounts, one-time codes, PKCE, and signed tokens.

It does what Google does, closely enough for the app's checks to mean something: the tokens are
real RS256 JWTs signed with a real key that ``/jwks`` publishes, a code works once, PKCE is
verified, the client secret and the redirect address are checked. ``flaws`` make it do one thing
wrong on purpose, so a test can see the app refuse it.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

FLAWS = (
    "wrong_nonce",
    "wrong_audience",
    "wrong_issuer",
    "expired",
    "unverified_email",
    "email_not_a_boolean",
    "tampered_signature",
    "tampered_payload",
    "unknown_kid",
    "wrong_key",
    "no_kid",
    "no_email",
    "no_subject",
    "wrong_azp",
    "future_iat",
    "alg_none",
    "alg_hs256",
    "two_audiences",
)

_KEYS: dict[str, rsa.RSAPrivateKey] = {}


def _key(name: str) -> rsa.RSAPrivateKey:
    """A 2048-bit key, made once per process: generating one takes a moment."""
    if name not in _KEYS:
        _KEYS[name] = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return _KEYS[name]


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


@dataclass
class Account:
    email: str
    subject: str
    verified: bool = True


@dataclass
class _Code:
    client_id: str
    redirect_uri: str
    challenge: str
    nonce: str
    account: Account
    flaws: frozenset[str]
    used: bool = False


@dataclass
class FakeIdp:
    issuer: str = "https://idp.test"
    client_id: str = "workbuddy-client.apps.test"
    client_secret: str = "fake-client-secret-for-tests"
    redirect_uri: str = "https://demo.example/auth/google/callback"
    clock: Callable[[], float] = time.time
    kid: str = "fake-key-1"
    accounts: dict[str, Account] = field(default_factory=dict)
    # What the app asked of the provider, for the tests to look at.
    log: list[str] = field(default_factory=list)
    _codes: dict[str, _Code] = field(default_factory=dict, repr=False)
    # Tokens are valid this long after they are issued.
    token_lifetime_s: int = 3600

    def __post_init__(self) -> None:
        for name in ("alice", "bob"):
            email = f"{name}@example.test"
            self.accounts.setdefault(email, Account(email, f"sub-{name}"))

    # -- The user's side: what a browser does at the provider ----------------------------------

    def authorize(self, url: str, email: str, *flaws: str) -> str:
        """The URL Google would send the browser back to, after ``email`` agreed.

        ``url`` is the authorization URL the app sent the browser to. Checks it the way a
        provider would: client, redirect address, PKCE method, scope.
        """
        assert not set(flaws) - set(FLAWS), f"unknown flaw in {flaws}"
        parts = urlsplit(url)
        assert f"{parts.scheme}://{parts.netloc}{parts.path}" == f"{self.issuer}/authorize", url
        query = {k: v[0] for k, v in parse_qs(parts.query).items()}
        assert query["client_id"] == self.client_id
        assert query["redirect_uri"] == self.redirect_uri, query["redirect_uri"]
        assert query["response_type"] == "code"
        assert query["code_challenge_method"] == "S256"
        assert "openid" in query["scope"].split() and "email" in query["scope"].split()
        assert query.get("state") and query.get("nonce") and query.get("code_challenge")
        code = secrets.token_urlsafe(24)
        self._codes[code] = _Code(
            client_id=query["client_id"],
            redirect_uri=query["redirect_uri"],
            challenge=query["code_challenge"],
            nonce=query["nonce"],
            account=self.accounts[email],
            flaws=frozenset(flaws),
        )
        return f"{self.redirect_uri}?{urlencode({'code': code, 'state': query['state']})}"

    # -- The app's side: what the server asks the provider -------------------------------------

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=self.transport(), follow_redirects=False)

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.log.append(f"{request.method} {path}")
        if request.method == "GET" and path == "/.well-known/openid-configuration":
            return httpx.Response(200, json=self.discovery())
        if request.method == "GET" and path == "/jwks":
            return httpx.Response(200, json=self.jwks())
        if request.method == "POST" and path == "/token":
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            status, body = self.token(form)
            return httpx.Response(status, json=body)
        return httpx.Response(404, json={"error": "not_found"})

    def discovery(self) -> dict[str, Any]:
        return {
            "issuer": self.issuer,
            "authorization_endpoint": f"{self.issuer}/authorize",
            "token_endpoint": f"{self.issuer}/token",
            "jwks_uri": f"{self.issuer}/jwks",
            "response_types_supported": ["code"],
            "id_token_signing_alg_values_supported": ["RS256"],
            "code_challenge_methods_supported": ["S256"],
        }

    def jwks(self) -> dict[str, Any]:
        jwk = json.loads(RSAAlgorithm.to_jwk(_key("good").public_key()))
        return {"keys": [{**jwk, "kid": self.kid, "alg": "RS256", "use": "sig"}]}

    def token(self, form: dict[str, str]) -> tuple[int, dict[str, Any]]:
        """The token endpoint: one code, once, for the right client, address and PKCE verifier."""
        bad = (400, {"error": "invalid_grant"})
        if form.get("grant_type") != "authorization_code":
            return 400, {"error": "unsupported_grant_type"}
        if form.get("client_id") != self.client_id or not hmac.compare_digest(
            form.get("client_secret", ""), self.client_secret
        ):
            return 401, {"error": "invalid_client"}
        record = self._codes.get(form.get("code", ""))
        if record is None or record.used:
            return bad
        record.used = True
        if form.get("redirect_uri") != record.redirect_uri:
            return bad
        verifier = form.get("code_verifier", "")
        if _b64url(hashlib.sha256(verifier.encode("ascii")).digest()) != record.challenge:
            return bad
        return 200, {
            "access_token": "not-used-by-the-app",
            "token_type": "Bearer",
            "expires_in": self.token_lifetime_s,
            "id_token": self.id_token(record),
        }

    # -- Tokens ---------------------------------------------------------------------------------

    def claims(self, record: _Code) -> dict[str, Any]:
        now = int(self.clock())
        flaws = record.flaws
        claims: dict[str, Any] = {
            "iss": self.issuer,
            "aud": self.client_id,
            "azp": self.client_id,
            "sub": record.account.subject,
            "email": record.account.email,
            "email_verified": record.account.verified,
            "nonce": record.nonce,
            "iat": now,
            "exp": now + self.token_lifetime_s,
        }
        if "wrong_nonce" in flaws:
            claims["nonce"] = "not-the-nonce"
        if "wrong_audience" in flaws:
            claims["aud"] = "someone-elses-client.apps.test"
            claims["azp"] = "someone-elses-client.apps.test"
        if "wrong_issuer" in flaws:
            claims["iss"] = "https://evil.test"
        if "expired" in flaws:
            claims["iat"], claims["exp"] = now - 7200, now - 3600
        if "unverified_email" in flaws:
            claims["email_verified"] = False
        if "email_not_a_boolean" in flaws:
            claims["email_verified"] = "true"
        if "no_email" in flaws:
            del claims["email"]
        if "no_subject" in flaws:
            del claims["sub"]
        if "wrong_azp" in flaws:
            claims["azp"] = "someone-elses-client.apps.test"
        if "future_iat" in flaws:
            claims["iat"] = now + 7200
            claims["exp"] = now + 10800
        if "two_audiences" in flaws:
            claims["aud"] = [self.client_id, "another-client.apps.test"]
            del claims["azp"]
        return claims

    def id_token(self, record: _Code) -> str:
        flaws = record.flaws
        claims = self.claims(record)
        headers: dict[str, Any] = {"kid": self.kid}
        if "unknown_kid" in flaws:
            headers["kid"] = "some-other-key"
        if "no_kid" in flaws:
            del headers["kid"]
        signer = _key("wrong" if "wrong_key" in flaws else "good")
        pem = signer.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        if "alg_none" in flaws:
            return _unsigned(claims, {**headers, "alg": "none"})
        if "alg_hs256" in flaws:
            return _hmac_signed(claims, {**headers, "alg": "HS256"})
        token = jwt.encode(claims, pem, algorithm="RS256", headers=headers)
        header, payload, signature = token.split(".")
        if "tampered_signature" in flaws:
            flipped = "A" if signature[0] != "A" else "B"
            signature = flipped + signature[1:]
        if "tampered_payload" in flaws:
            # A different person's claims under the real signature.
            changed = {**claims, "sub": "sub-mallory", "email": "mallory@example.test"}
            payload = _b64url(json.dumps(changed, separators=(",", ":")).encode())
        return ".".join((header, payload, signature))


def _unsigned(claims: dict[str, Any], headers: dict[str, Any]) -> str:
    head = _b64url(json.dumps(headers, separators=(",", ":")).encode())
    body = _b64url(json.dumps(claims, separators=(",", ":")).encode())
    return f"{head}.{body}."


def _hmac_signed(claims: dict[str, Any], headers: dict[str, Any]) -> str:
    head = _b64url(json.dumps(headers, separators=(",", ":")).encode())
    body = _b64url(json.dumps(claims, separators=(",", ":")).encode())
    sig = hmac.new(b"anything", f"{head}.{body}".encode(), hashlib.sha256).digest()
    return f"{head}.{body}.{_b64url(sig)}"

"""Settings — every path and limit the API enforces, read once at startup.

Each value here is read by the code that enforces it *and* by the session
response's ``limits`` (``docs/design/backend-api-design.md``, "Decisions
(2026-09-30, part 2)", ask 2), so the two can't disagree.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

MIB = 1024 * 1024

# The repo root: the default data root is ``<repo>/workspace``, whatever the
# current working directory is.
_REPO_ROOT = Path(__file__).resolve().parent.parent

# 2 MiB (design "Decisions (2026-10-04)", item 3). Live run 9 measured a
# 5 MB file at 256 s for one step and 308 s for a two-step chain, past the
# 180 s deadline; the design's evidence rule ("Decisions (2026-09-30, part
# 2)", item 3) then sets 2 MB. WORKBUDDY_MAX_UPLOAD_BYTES overrides it.
DEFAULT_MAX_UPLOAD_BYTES = 2 * MIB
# The raw request body may exceed the file by the multipart framing (part
# headers, boundaries). Anything beyond this is cut off before parsing.
BODY_OVERHEAD_BYTES = 64 * 1024

# Owner decision (design "Decisions (2026-10-04)"): ``.xlsx`` only. This is
# the upload validator's allowlist itself, not a copy of it.
ACCEPTED_EXTENSIONS: tuple[str, ...] = (".xlsx",)

# The longest a plan may wait for its answer: an hour.
MAX_CONFIRMATION_TIMEOUT_S = 3600
# The longest a turn may work before its deadline: an hour. A larger value
# overflows ``threading.Timer``, whose thread then dies and leaves the turn
# with no deadline at all (PR-3 review, R2-L5).
MAX_TURN_DEADLINE_S = 3600

# ``WORKBUDDY_ENV``: "production" (binds 0.0.0.0, needs an https public
# origin), "dev" (may run with no password), or unset ("", binds 127.0.0.1
# and still needs a password).
ENV_DEV = "dev"
ENV_PRODUCTION = "production"
ENV_UNSET = ""
_ENVS = (ENV_DEV, ENV_PRODUCTION, ENV_UNSET)
# Where a non-production server is expected to be reached (design "Decisions
# (2026-10-01)", item 2). With no ``WORKBUDDY_PUBLIC_ORIGIN`` the origin is
# derived from the port the server is started on, and the same port on
# ``localhost`` is accepted as well (review L5 of backend-api-retention: a
# browser on ``http://localhost:8000`` otherwise gets ``403`` from the access
# route). An origin that is set explicitly is matched exactly, with no alias.
DEFAULT_PORT = 8000
LOOPBACK_HOST = "127.0.0.1"
LOCALHOST_NAME = "localhost"
ALL_INTERFACES_HOST = "0.0.0.0"  # only ever bound in production

# Read by PR-2 only. Since PR-3 every file-changing step asks the user, and
# approving without asking is a test setting that no variable turns on.
RETIRED_AUTO_APPROVE_VARIABLE = "WORKBUDDY_AUTO_APPROVE"

logger = logging.getLogger(__name__)


class SettingsError(ValueError):
    """A setting is missing or invalid. The app refuses to start."""


@dataclass(frozen=True)
class BucketSpec:
    """One per-IP token bucket: its burst, and how fast it refills (tokens per hour)."""

    burst: int
    per_hour: float

    @property
    def refill_per_s(self) -> float:
        return self.per_hour / 3600.0


# Design "Decisions (2026-09-30, part 2)", item 1, and "Decisions
# (2026-10-01)", item 3. ``general``: 60/min; ``poll``: 3/s; ``turns``:
# 10/hour; ``uploads`` and ``access``: 20/hour; ``healthz`` and ``page``: 1/s.
DEFAULT_RATE_LIMITS: dict[str, BucketSpec] = {
    "general": BucketSpec(burst=60, per_hour=3600),
    "poll": BucketSpec(burst=30, per_hour=10800),
    "turns": BucketSpec(burst=10, per_hour=10),
    "uploads": BucketSpec(burst=5, per_hour=20),
    "access": BucketSpec(burst=10, per_hour=20),
    "healthz": BucketSpec(burst=10, per_hour=3600),
    "page": BucketSpec(burst=30, per_hour=3600),
}


def default_rate_limits() -> dict[str, BucketSpec]:
    return dict(DEFAULT_RATE_LIMITS)


@dataclass(frozen=True)
class Settings:
    """The API's configuration.

    Attributes:
        data_dir: The data root. Made absolute at construction, because the
            skills' legacy default path is relative to the working directory.
        max_upload_bytes: The largest upload accepted, in bytes.
        max_message_chars: The longest turn message, after trimming.
        max_zip_entries: The most entries an ``.xlsx`` may have.
        max_zip_uncompressed_bytes: The largest total declared uncompressed
            size of an ``.xlsx``'s entries.
        turn_deadline_s: Wall-clock seconds a turn may run before it's
            finalized as failed, not counting the time its plan waits for
            the user's answer.
        confirmation_timeout_s: Seconds a turn's plan waits for the user's
            answer, at most ``MAX_CONFIRMATION_TIMEOUT_S``. No answer in time
            is a rejection.
        max_concurrent_turns: Turns that may be running at once, across all
            sessions. A turn counts until its worker thread exits.
        busy_retry_after_s: The ``Retry-After`` hint on ``429 server_busy``.
        auto_approve: A test setting: approve every file-changing step
            without asking. Off by default, and never read from the
            environment, so a server always asks the user (design Section
            8.3, PR-3). Only tests that aren't about confirmation turn it on.
        env: ``"production"``, ``"dev"`` or ``""`` (``WORKBUDDY_ENV``). The
            default here is ``"dev"``, which is what code that builds a
            ``Settings`` itself (the tests) gets; ``from_env`` defaults to
            ``""``, so a deployment that sets nothing needs a password.
        demo_password: The shared demo password. Required unless ``env`` is
            ``"dev"``; with none, every route is open (dev only). Never
            shown in ``repr``, logged, echoed or returned.
        public_origin: The one origin the demo page is served from
            (``WORKBUDDY_PUBLIC_ORIGIN``), normalized (lower case, no
            default port). Required, and ``https://``, in production;
            ``http://127.0.0.1:<port>`` elsewhere when not given (see ``port``).
        port: The port the server listens on (``python -m backend --port``).
            Only used to derive the default ``public_origin`` outside
            production; the same port on ``localhost`` is accepted too (L5).
        access_ttl_hours: How long an access cookie's token is valid, from
            issue; not renewed by use.
        access_max_tokens: The most live access tokens held at once.
        trusted_proxy_hops: How many reverse proxies stand in front of the
            app (``WORKBUDDY_TRUSTED_PROXY_HOPS``). ``0``: the client IP is
            the socket's, and ``X-Forwarded-For`` is ignored.
        cors_origins: Exact origins allowed to call the API cross-origin.
            Empty (the same-origin deployment) sends no CORS headers.
        rate_limits: Every per-IP token bucket's burst and refill.
        session_ttl_hours: A session idle for longer is removed by the
            retention sweep, and this is the ``retention_hours`` the session
            response advertises (the same value).
        sweep_interval_s: Seconds between sweeps.
        max_uploads_per_session: Uploads one session may hold.
        max_session_upload_bytes: Bytes of uploads one session may hold.
        max_turns_per_session: Turns one session may run.
        max_total_bytes: A cap on the bytes under the data root. At or over
            it, a new session or upload gets ``503 storage_full``; the sweep
            never deletes a live session to make room.
    """

    data_dir: Path
    max_upload_bytes: int = DEFAULT_MAX_UPLOAD_BYTES
    max_message_chars: int = 4000
    max_zip_entries: int = 1000
    max_zip_uncompressed_bytes: int = 200 * MIB
    turn_deadline_s: float = 180.0
    confirmation_timeout_s: float = 120.0
    max_concurrent_turns: int = 2
    busy_retry_after_s: int = 10
    auto_approve: bool = False
    env: str = ENV_DEV
    demo_password: str | None = field(default=None, repr=False)
    public_origin: str = ""
    port: int = DEFAULT_PORT
    access_ttl_hours: float = 12.0
    access_max_tokens: int = 10_000
    trusted_proxy_hops: int = 0
    cors_origins: tuple[str, ...] = ()
    rate_limits: dict[str, BucketSpec] = field(default_factory=default_rate_limits, compare=False)
    session_ttl_hours: float = 24.0
    sweep_interval_s: float = 300.0
    max_uploads_per_session: int = 20
    max_session_upload_bytes: int = 100 * MIB
    max_turns_per_session: int = 50
    max_total_bytes: int = 2 * 1024 * MIB
    # Set by ``_check_access``: whether ``public_origin`` was derived from ``port``.
    origin_is_derived: bool = field(default=False, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "data_dir", Path(self.data_dir).expanduser().resolve())
        for name in (
            "max_upload_bytes",
            "max_message_chars",
            "max_zip_entries",
            "max_zip_uncompressed_bytes",
            "max_concurrent_turns",
            "busy_retry_after_s",
            "access_max_tokens",
            "max_uploads_per_session",
            "max_session_upload_bytes",
            "max_turns_per_session",
            "max_total_bytes",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise SettingsError(f"{name} must be a positive integer")
        for name in (
            "turn_deadline_s",
            "confirmation_timeout_s",
            "access_ttl_hours",
            "session_ttl_hours",
            "sweep_interval_s",
        ):
            if not getattr(self, name) > 0:
                raise SettingsError(f"{name} must be positive")
        if self.turn_deadline_s > MAX_TURN_DEADLINE_S:
            raise SettingsError(f"turn_deadline_s must be at most {MAX_TURN_DEADLINE_S}")
        if self.confirmation_timeout_s > MAX_CONFIRMATION_TIMEOUT_S:
            # Far beyond any wait a person makes, and it keeps the wait inside
            # what a thread can wait for (review round 1, L7).
            raise SettingsError(
                f"confirmation_timeout_s must be at most {MAX_CONFIRMATION_TIMEOUT_S}"
            )
        self._check_access()
        self._check_rate_limits()

    def _check_access(self) -> None:
        """The startup checks (design "Decisions (2026-09-30)" item 1, "(2026-10-01)" item 2)."""
        if self.env not in _ENVS:
            raise SettingsError('env must be "production", "dev" or unset')
        if self.demo_password is not None and not self.demo_password:
            raise SettingsError("demo_password must not be empty")
        if self.demo_password is None and self.env != ENV_DEV:
            # A deployment can't silently be open.
            raise SettingsError(
                "WORKBUDDY_DEMO_PASSWORD is not set; set it, or set WORKBUDDY_ENV=dev"
            )
        port = self.port
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise SettingsError("port must be a port number")
        origin = self.public_origin
        if not origin:
            if self.env == ENV_PRODUCTION:
                raise SettingsError("WORKBUDDY_PUBLIC_ORIGIN is required in production")
            origin = f"http://{LOOPBACK_HOST}:{self.port}"
            object.__setattr__(self, "origin_is_derived", True)
        origin = normalize_origin(origin, "public_origin")
        if self.env == ENV_PRODUCTION and not origin.startswith("https://"):
            raise SettingsError("public_origin must be an https:// origin in production")
        object.__setattr__(self, "public_origin", origin)
        if (
            isinstance(self.trusted_proxy_hops, bool)
            or not isinstance(self.trusted_proxy_hops, int)
            or self.trusted_proxy_hops < 0
        ):
            raise SettingsError("trusted_proxy_hops must be a non-negative integer")
        origins = tuple(normalize_origin(o, "cors_origins") for o in self.cors_origins)
        object.__setattr__(self, "cors_origins", origins)

    def _check_rate_limits(self) -> None:
        if set(self.rate_limits) != set(DEFAULT_RATE_LIMITS):
            raise SettingsError("rate_limits must define exactly the buckets " + ", ".join(
                sorted(DEFAULT_RATE_LIMITS)
            ))
        for name, spec in self.rate_limits.items():
            if (
                isinstance(spec.burst, bool)
                or not isinstance(spec.burst, int)
                or spec.burst < 1
                or not spec.per_hour > 0
            ):
                raise SettingsError(f"rate limit {name!r} needs a burst of at least 1 and a refill")
        # Raising the concurrent-turn cap without raising the poll allowance
        # would bring back the conflict part 2 resolved (item 1).
        if self.rate_limits["poll"].refill_per_s < self.max_concurrent_turns:
            raise SettingsError(
                "the poll bucket must refill at least as fast (per second) as "
                "max_concurrent_turns"
            )

    @property
    def bind_host(self) -> str:
        """Where the server binds: every interface in production, else loopback only."""
        return ALL_INTERFACES_HOST if self.env == ENV_PRODUCTION else LOOPBACK_HOST

    @property
    def accepted_origins(self) -> tuple[str, ...]:
        """The ``Origin`` values the CSRF check accepts.

        ``public_origin`` exactly. When it was derived from the port (nothing
        set, outside production), the same port on ``localhost`` as well.
        """
        if not self.origin_is_derived:
            return (self.public_origin,)
        alias = normalize_origin(f"http://{LOCALHOST_NAME}:{self.port}", "port")
        return (self.public_origin, alias)

    @property
    def retention_hours(self) -> int | float:
        """What the session response advertises: the sweep's own TTL, as a whole number if it is."""
        ttl = self.session_ttl_hours
        return int(ttl) if float(ttl).is_integer() else float(ttl)

    @property
    def accepted_extensions(self) -> tuple[str, ...]:
        """The upload extensions accepted: ``.xlsx`` only (owner decision)."""
        return ACCEPTED_EXTENSIONS

    @property
    def max_body_bytes(self) -> int:
        """The cap on any request body, enforced on the raw stream."""
        return self.max_upload_bytes + BODY_OVERHEAD_BYTES

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def outputs_dir(self) -> Path:
        return self.data_dir / "outputs"

    @property
    def sessions_dir(self) -> Path:
        return self.data_dir / "sessions"

    @classmethod
    def from_env(
        cls, environ: Mapping[str, str] | None = None, *, port: int = DEFAULT_PORT
    ) -> Settings:
        """Read settings from ``WORKBUDDY_*`` environment variables.

        ``port`` is the port the server will listen on (``--port``); it only
        decides the default public origin outside production.

        Raises:
            SettingsError: A variable is set to something invalid.
        """
        env = os.environ if environ is None else environ
        if env.get(RETIRED_AUTO_APPROVE_VARIABLE):
            logger.warning(
                "%s is no longer read: every file-changing step asks for confirmation",
                RETIRED_AUTO_APPROVE_VARIABLE,
            )
        rate_limits = {
            bucket: BucketSpec(
                burst=_int(env, f"WORKBUDDY_RATE_{bucket.upper()}_BURST", spec.burst),
                per_hour=_number(env, f"WORKBUDDY_RATE_{bucket.upper()}_PER_HOUR", spec.per_hour),
            )
            for bucket, spec in DEFAULT_RATE_LIMITS.items()
        }
        defaults = cls.__dataclass_fields__
        return cls(
            data_dir=Path(env.get("WORKBUDDY_DATA_DIR") or _REPO_ROOT / "workspace"),
            max_upload_bytes=_int(env, "WORKBUDDY_MAX_UPLOAD_BYTES", DEFAULT_MAX_UPLOAD_BYTES),
            turn_deadline_s=float(_int(env, "WORKBUDDY_TURN_DEADLINE_S", 180)),
            confirmation_timeout_s=float(_int(env, "WORKBUDDY_CONFIRMATION_TIMEOUT_S", 120)),
            max_concurrent_turns=_int(env, "WORKBUDDY_MAX_CONCURRENT_TURNS", 2),
            busy_retry_after_s=_int(env, "WORKBUDDY_BUSY_RETRY_AFTER_S", 10),
            env=env.get("WORKBUDDY_ENV", ENV_UNSET),
            demo_password=env.get("WORKBUDDY_DEMO_PASSWORD") or None,
            public_origin=env.get("WORKBUDDY_PUBLIC_ORIGIN", ""),
            port=port,
            access_ttl_hours=_number(
                env, "WORKBUDDY_ACCESS_TTL_HOURS", defaults["access_ttl_hours"].default
            ),
            access_max_tokens=_int(
                env, "WORKBUDDY_ACCESS_MAX_TOKENS", defaults["access_max_tokens"].default
            ),
            trusted_proxy_hops=_int(env, "WORKBUDDY_TRUSTED_PROXY_HOPS", 0),
            cors_origins=tuple(
                o.strip() for o in env.get("WORKBUDDY_CORS_ORIGINS", "").split(",") if o.strip()
            ),
            rate_limits=rate_limits,
            session_ttl_hours=_number(
                env, "WORKBUDDY_SESSION_TTL_HOURS", defaults["session_ttl_hours"].default
            ),
            sweep_interval_s=_number(
                env, "WORKBUDDY_SWEEP_INTERVAL_S", defaults["sweep_interval_s"].default
            ),
            max_uploads_per_session=_int(
                env,
                "WORKBUDDY_MAX_UPLOADS_PER_SESSION",
                defaults["max_uploads_per_session"].default,
            ),
            max_session_upload_bytes=_int(
                env,
                "WORKBUDDY_MAX_SESSION_UPLOAD_BYTES",
                defaults["max_session_upload_bytes"].default,
            ),
            max_turns_per_session=_int(
                env, "WORKBUDDY_MAX_TURNS_PER_SESSION", defaults["max_turns_per_session"].default
            ),
            max_total_bytes=_int(
                env, "WORKBUDDY_MAX_TOTAL_BYTES", defaults["max_total_bytes"].default
            ),
        )


def normalize_origin(value: str, name: str) -> str:
    """``value`` as a lower-case ``scheme://host[:port]`` origin, without a default port.

    Raises:
        SettingsError: It isn't an origin: another scheme, no host, or a
            path, query, fragment or credentials (so ``*`` is refused too).
    """
    try:
        parts = urlsplit(value.strip())
        host = parts.hostname
        port = parts.port
    except ValueError:
        raise SettingsError(f"{name} must be an origin such as https://demo.example") from None
    if (
        parts.scheme.lower() not in ("http", "https")
        or not host
        or parts.path
        or parts.query
        or parts.fragment
        or parts.username is not None
        or parts.password is not None
        or value.strip().endswith(("/", "?", "#"))
    ):
        raise SettingsError(f"{name} must be an origin such as https://demo.example")
    scheme = parts.scheme.lower()
    host = host.lower()
    if ":" in host:
        host = f"[{host}]"
    default_port = 443 if scheme == "https" else 80
    suffix = f":{port}" if port is not None and port != default_port else ""
    return f"{scheme}://{host}{suffix}"


def _int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        raise SettingsError(f"{name} must be an integer") from None


def _number(env: Mapping[str, str], name: str, default: float) -> float:
    raw = env.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = float(raw)
    except ValueError:
        raise SettingsError(f"{name} must be a number") from None
    if value != value or value in (float("inf"), float("-inf")):
        raise SettingsError(f"{name} must be a finite number")
    return value

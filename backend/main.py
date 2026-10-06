"""The app factory: ``create_app(settings, engine_factory)``.

Production calls ``create_app()`` with no arguments: settings come from the
environment and the engine factory builds the real pipeline
(``build_engine`` with ``PersonalityVoice``) on an LLM provider made once at
startup. Tests pass their own settings (a temporary data root) and an engine
factory backed by a fake provider, and still run the real pipeline.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi

from backend.access import AccessControl
from backend.errors import file_too_large_message, install_error_handlers
from backend.gate import RequestGate
from backend.logging_filter import install_log_redaction
from backend.middleware import RequestGuardMiddleware, SecurityHeadersMiddleware
from backend.ratelimit import RateLimiter, check_route_declarations
from backend.retention import MaintenanceLoop, RetentionSweeper, StorageMeter
from backend.routes import access_router, health_router, router
from backend.schemas import ErrorBody
from backend.sessions import SessionRegistry
from backend.settings import ENV_PRODUCTION, Settings
from backend.store import SessionStore
from backend.turns import EngineFactory, TurnRunner
from backend.web import mount_web
from buddy.engine_factory import build_engine
from buddy.hands import ConfirmationProvider
from buddy.llm import ClaudeProvider, GroqProvider, LLMConfigurationError, LLMProvider
from buddy.voice import PersonalityVoice
from core.engine import Engine

logger = logging.getLogger(__name__)

API_VERSION = "0.1.0"


class LLMNotConfiguredError(RuntimeError):
    """No LLM provider could be built at startup, so a turn can't run."""


def build_llm_provider_from_env() -> LLMProvider | None:
    """The LLM provider, from ``GROQ_API_KEY`` (preferred) or ``ANTHROPIC_API_KEY``.

    The key is read only by the provider's own constructor and is never
    logged, echoed or returned. ``None`` when neither is set: the server
    still starts, ``/healthz`` says ``llm_configured: false``, and a turn
    fails with the fallback reply.
    """
    try:
        if os.environ.get("GROQ_API_KEY"):
            return GroqProvider()
        if os.environ.get("ANTHROPIC_API_KEY"):
            return ClaudeProvider()
    except LLMConfigurationError:
        logger.warning("An LLM key is set but the provider couldn't be configured")
    return None


def production_engine_factory(provider: LLMProvider | None) -> EngineFactory:
    """The real pipeline, with the per-turn confirmation provider (design Section 3.5)."""

    def factory(confirmation_provider: ConfirmationProvider) -> Engine:
        if provider is None:
            raise LLMNotConfiguredError("no LLM provider is configured")
        return build_engine(
            llm_provider=provider,
            speaker=PersonalityVoice(provider),
            confirmation_provider=confirmation_provider,
        )

    return factory


def create_app(
    settings: Settings | None = None,
    engine_factory: EngineFactory | None = None,
    *,
    llm_configured: bool | None = None,
    clock: Callable[[], float] = time.monotonic,
    wall_clock: Callable[[], float] = time.time,
) -> FastAPI:
    """Build the API.

    Args:
        settings: Defaults to ``Settings.from_env()``, which refuses to start
            without a demo password unless ``WORKBUDDY_ENV=dev``, and
            without an https ``WORKBUDDY_PUBLIC_ORIGIN`` in production.
        engine_factory: Defaults to the real pipeline on the provider from
            the environment. Tests pass one backed by a fake provider.
        llm_configured: What ``/healthz`` reports. Defaults to whether a
            provider was built, or ``True`` when ``engine_factory`` is given.
        clock: A monotonic clock, in seconds: the rate-limit buckets, the
            access tokens' expiry and the storage meter's cache. Tests pass
            one they advance.
        wall_clock: Epoch seconds, for session idleness (retention).

    Returns:
        The app. Serve it with one worker: sessions live in this process.

    Raises:
        SettingsError: A setting is missing or invalid.
        RuntimeError: A route lacks its rate-limit declaration (see
            ``backend.ratelimit``).
    """
    settings = settings if settings is not None else Settings.from_env()
    if engine_factory is None:
        provider = build_llm_provider_from_env()
        engine_factory = production_engine_factory(provider)
        configured = provider is not None
    else:
        configured = True
    if llm_configured is not None:
        configured = llm_configured

    install_log_redaction()
    if settings.auto_approve:
        logger.warning(
            "auto_approve (a test setting) is on: every file-changing step is approved "
            "without asking"
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        check_route_declarations(app)  # routes added after create_app are checked too
        app.state.maintenance.start()
        try:
            yield
        finally:
            # A plan still waiting when the server stops is rejected, never
            # approved (design Section 1.5).
            app.state.runner.shutdown()
            app.state.maintenance.stop()

    app = FastAPI(
        title="WorkBuddy API",
        version=API_VERSION,
        # The committed backend/openapi.json is the contract; the server doesn't
        # serve docs (design "Decisions (2026-09-30)", item 1).
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        responses={500: {"model": ErrorBody, "description": "Internal error"}},
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.llm_configured = configured
    for folder in (settings.uploads_dir, settings.outputs_dir, settings.sessions_dir):
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    registry = SessionRegistry(settings, SessionStore(settings), wall_clock)
    app.state.registry = registry
    app.state.runner = TurnRunner(settings, engine_factory, registry)
    app.state.access = AccessControl(settings, clock)
    app.state.limiter = RateLimiter(settings.rate_limits, clock)
    app.state.meter = StorageMeter(settings, clock)
    app.state.maintenance = MaintenanceLoop(
        settings,
        RetentionSweeper(settings, registry, wall_clock, app.state.meter),
        app.state.access,
        app.state.limiter,
    )

    install_error_handlers(app)
    app.include_router(health_router)
    app.include_router(access_router)
    app.include_router(router)
    mount_web(app)
    check_route_declarations(app)
    # Added innermost first. The security headers wrap everything, including
    # the guard's own 413 and 500 responses and the gate's 401, 403 and 429;
    # the gate (rate limits, credential, Origin) runs before the guard, so a
    # request refused for its body size has been charged like any other.
    app.add_middleware(
        RequestGuardMiddleware,
        max_body_bytes=settings.max_body_bytes,
        file_too_large_message=file_too_large_message(settings.max_upload_bytes),
    )
    app.add_middleware(
        RequestGate,
        settings=settings,
        limiter=app.state.limiter,
        access=app.state.access,
        routes_of=lambda: app,
    )
    app.add_middleware(SecurityHeadersMiddleware, hsts=settings.env == ENV_PRODUCTION)
    if settings.cors_origins:
        # Exact origins only, never ``*``. Retry-After is exposed so a page
        # on another origin can read it.
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.cors_origins),
            allow_methods=["GET", "POST"],
            allow_headers=["Authorization", "Content-Type"],
            expose_headers=["Retry-After"],
            allow_credentials=False,
            max_age=600,
        )
    app.openapi = lambda: _openapi_with_our_error_bodies(app)  # type: ignore[method-assign]
    return app


_ERROR_BODY_REF = "#/components/schemas/ErrorBody"


def _openapi_with_our_error_bodies(app: FastAPI) -> dict[str, Any]:
    """The OpenAPI schema, with FastAPI's own validation-error body replaced by ours.

    FastAPI documents ``HTTPValidationError`` for ``422`` on any route with
    parameters, but every validation error this app returns is an
    ``ErrorBody`` (``backend.errors.validation_error_handler``). The schema
    must describe the real bodies (design "Decisions (2026-09-30, part 2)",
    ask 3), so those entries are rewritten and the unused schemas dropped.
    """
    if app.openapi_schema is not None:
        return app.openapi_schema
    schema = get_openapi(
        title=app.title, version=app.version, routes=app.routes, description=app.description
    )
    for operations in schema.get("paths", {}).values():
        for operation in operations.values():
            response = operation.get("responses", {}).get("422")
            if response is not None and "HTTPValidationError" in json.dumps(response):
                operation["responses"]["422"] = {
                    "description": "Invalid request or file",
                    "content": {"application/json": {"schema": {"$ref": _ERROR_BODY_REF}}},
                }
    components = schema.get("components", {}).get("schemas", {})
    for unused in ("HTTPValidationError", "ValidationError"):
        components.pop(unused, None)
    app.openapi_schema = schema
    return schema

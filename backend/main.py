"""The app factory: ``create_app(settings, engine_factory)``.

Production calls ``create_app()`` with no arguments: settings come from the
environment and the engine factory builds the real pipeline
(``build_engine`` with ``PersonalityVoice``) on an LLM provider made once at
startup. Tests pass their own settings (a temporary data root) and an engine
factory backed by a fake provider, and still run the real pipeline.
"""

from __future__ import annotations

import logging
import os

from fastapi import FastAPI

from backend.errors import file_too_large_message, install_error_handlers
from backend.logging_filter import install_log_redaction
from backend.middleware import RequestGuardMiddleware, SecurityHeadersMiddleware
from backend.routes import health_router, router
from backend.schemas import ErrorBody
from backend.sessions import SessionRegistry
from backend.settings import Settings
from backend.turns import EngineFactory, TurnRunner
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
) -> FastAPI:
    """Build the API.

    Args:
        settings: Defaults to ``Settings.from_env()``.
        engine_factory: Defaults to the real pipeline on the provider from
            the environment. Tests pass one backed by a fake provider.
        llm_configured: What ``/healthz`` reports. Defaults to whether a
            provider was built, or ``True`` when ``engine_factory`` is given.

    Returns:
        The app. Serve it with one worker: sessions live in this process.
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
            "WORKBUDDY_AUTO_APPROVE is on: every file-changing step is approved without asking"
        )

    app = FastAPI(
        title="WorkBuddy API",
        version=API_VERSION,
        # The committed backend/openapi.json is the contract; the server doesn't
        # serve docs (design "Decisions (2026-09-30)", item 1).
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        responses={500: {"model": ErrorBody, "description": "Internal error"}},
    )
    app.state.settings = settings
    app.state.llm_configured = configured
    registry = SessionRegistry(settings)
    app.state.registry = registry
    app.state.runner = TurnRunner(settings, engine_factory, registry)

    install_error_handlers(app)
    app.include_router(health_router)
    app.include_router(router)
    # Added innermost first: the security headers wrap everything, including
    # the guard's own 413 and 500 responses.
    app.add_middleware(
        RequestGuardMiddleware,
        max_body_bytes=settings.max_body_bytes,
        file_too_large_message=file_too_large_message(settings.max_upload_bytes),
    )
    app.add_middleware(SecurityHeadersMiddleware)
    return app

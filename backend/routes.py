"""Routes — the HTTP endpoints (design Section 1)."""

from __future__ import annotations

from fastapi import APIRouter, Request

from backend.schemas import Health

health_router = APIRouter()


@health_router.get("/healthz", response_model=Health, tags=["health"])
def healthz(request: Request) -> Health:
    """Liveness, and whether an LLM provider is configured. Booleans only."""
    return Health(ok=True, llm_configured=bool(request.app.state.llm_configured))

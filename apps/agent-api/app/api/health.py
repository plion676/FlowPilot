from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter(tags=["health"])


@router.get("/api/health")
async def health(request: Request) -> dict[str, object]:
    settings = request.app.state.settings
    return {
        "status": "ok",
        "service": "agent-api",
        "environment": settings.app_env,
        "llm": "configured" if settings.llm_configured else "not_configured",
    }

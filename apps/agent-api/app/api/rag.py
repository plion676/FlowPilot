from __future__ import annotations

import hmac

from fastapi import APIRouter, Request

from app.core.errors import AppError
from app.tools.registry import SearchSopArguments

router = APIRouter(tags=["internal-rag"])


@router.post("/internal/knowledge/search")
async def search(payload: SearchSopArguments, request: Request):
    secret = request.app.state.settings.internal_service_token or ""
    supplied = request.headers.get("X-Internal-Service-Token", "")
    if len(secret) < 16 or not hmac.compare_digest(secret.encode(), supplied.encode()):
        raise AppError("UNAUTHORIZED", "内部服务身份无效。", 401)
    return await request.app.state.rag_service.search(payload.query, payload.top_k)

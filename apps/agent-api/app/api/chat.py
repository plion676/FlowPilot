from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Request

from app.core.errors import AppError
from app.models.chat import ChatRequest, ChatResponse
from app.services.chat import ChatService

router = APIRouter(tags=["chat"])


@router.post("/api/chat", response_model=ChatResponse)
async def chat(payload: ChatRequest, request: Request) -> ChatResponse:
    service: ChatService | None = getattr(request.app.state, "chat_service", None)
    if service is None:
        raise AppError(
            code="LLM_NOT_CONFIGURED",
            message=("未配置模型密钥；请设置 LLM_API_KEY 环境变量（本地可使用项目根目录 .env）。"),
            status_code=503,
        )
    return await service.reply(
        payload.message,
        request_id=UUID(request.state.request_id),
        role=request.headers.get("X-Demo-Role", "consultant"),
        actor_id="demo-consultant",
        skill_name=payload.skill_name,
    )

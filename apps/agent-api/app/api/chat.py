from __future__ import annotations

import asyncio
from uuid import UUID

from fastapi import APIRouter, Request

from app.core.errors import AppError
from app.models.chat import ChatRequest, ChatResponse
from app.services.chat import ChatService
from app.traces.recorder import TraceRecorder, safe_code

router = APIRouter(tags=["chat"])


@router.post("/api/chat", response_model=ChatResponse)
async def chat(payload: ChatRequest, request: Request) -> ChatResponse:
    service: ChatService | None = getattr(request.app.state, "chat_service", None)
    trace = None
    role = request.headers.get("X-Demo-Role", "consultant")
    settings = request.app.state.settings
    if settings.app_env == "development":
        trace = TraceRecorder(
            request.app.state.trace_client,
            request.state.request_id,
            role,
            "demo-consultant",
            service.model_name if service else "",
            secrets=[
                settings.llm_api_key,
                settings.internal_service_token,
                settings.mcp_call_secret,
            ],
        )
        await trace.start(payload.message)
        if trace.created:
            request.state.trace_id = trace.id
    try:
        result = await execute_chat(payload, request, service, role, trace)
        if trace:
            await trace.emit(
                "final_response",
                {
                    "content": result.message,
                    "skill_name": result.skill_name,
                    "task": result.task,
                    "citations": result.citations,
                },
            )
            await trace.finish("succeeded")
            result.trace_id = trace.id if trace.created else None
            result.trace_incomplete = trace.incomplete
        return result
    except asyncio.CancelledError:
        if trace:
            await asyncio.shield(trace.finish("cancelled", "REQUEST_CANCELLED"))
        raise
    except Exception as error:
        if trace:
            await trace.finish("failed", safe_code(error, "AGENT_FAILED"))
        raise


async def execute_chat(payload, request, service, role, trace):
    if service is None:
        raise AppError(
            code="LLM_NOT_CONFIGURED",
            message=("未配置模型密钥；请设置 LLM_API_KEY 环境变量（本地可使用项目根目录 .env）。"),
            status_code=503,
        )
    return await service.reply(
        payload.message,
        request_id=UUID(request.state.request_id),
        role=role,
        actor_id="demo-consultant",
        skill_name=payload.skill_name,
        trace=trace,
    )

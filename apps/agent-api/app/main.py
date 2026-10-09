from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from app.api.chat import router as chat_router
from app.api.health import router as health_router
from app.api.rag import router as rag_router
from app.api.tasks import router as tasks_router
from app.api.tool_management import router as management_router
from app.api.traces import router as traces_router
from app.clients.bindings import HttpBindingStore
from app.clients.endpoints import HttpEndpointStore
from app.clients.llm import LangChainChatClient
from app.clients.mcp_factory import McpClientFactory
from app.core.config import Settings
from app.core.errors import AppError, ErrorBody, ErrorResponse
from app.core.logging import configure_logging
from app.rag.service import RagService
from app.services.chat import ChatService
from app.services.tool_management import ToolManagementService
from app.tasks.client import TaskClient
from app.tasks.service import TaskService
from app.tasks.worker import TaskWorker
from app.tools.registry import default_tool_registry
from app.traces.client import TraceClient


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or Settings.from_env()
    configure_logging(resolved_settings.log_level)

    @asynccontextmanager
    async def lifespan(app):
        worker = None
        if (
            app.state.chat_service
            and resolved_settings.app_env == "development"
            and os.getenv("TASK_WORKER_ENABLED", "1") == "1"
        ):
            worker = asyncio.create_task(
                TaskWorker(
                    app.state.task_client, app.state.tool_management, app.state.chat_service._model
                ).run()
            )
        yield
        if worker:
            worker.cancel()
            with suppress(asyncio.CancelledError):
                await worker

    app = FastAPI(title="OpsPilot Agent API", version="0.1.0", lifespan=lifespan)
    app.state.settings = resolved_settings
    app.state.tool_registry = default_tool_registry()
    app.state.rag_service = RagService()
    bindings = HttpBindingStore(
        resolved_settings.business_base_url, resolved_settings.internal_service_token
    )
    app.state.tool_management = ToolManagementService(
        bindings,
        app.state.tool_registry,
        McpClientFactory(),
        HttpEndpointStore(bindings),
    )
    app.state.chat_service = None
    app.state.trace_client = TraceClient(
        resolved_settings.business_base_url, resolved_settings.internal_service_token
    )
    app.state.task_client = TaskClient(
        resolved_settings.business_base_url, resolved_settings.internal_service_token
    )
    app.state.task_service = TaskService(app.state.task_client, app.state.tool_management)
    if resolved_settings.llm_configured:
        chat_client = LangChainChatClient(resolved_settings)
        app.state.chat_service = ChatService(
            chat_client.chat_model,
            chat_client.model_name,
            app.state.tool_management,
            task_service=(
                app.state.task_service if resolved_settings.app_env == "development" else None
            ),
        )

    @app.middleware("http")
    async def request_context(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        supplied = request.headers.get("X-Request-ID")
        try:
            request_id = str(uuid.UUID(supplied)) if supplied else str(uuid.uuid4())
        except ValueError:
            request_id = str(uuid.uuid4())
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        if getattr(request.state, "trace_id", None):
            response.headers["X-Trace-ID"] = request.state.trace_id
        return response

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, error: AppError) -> JSONResponse:
        request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
        payload = ErrorResponse(
            error=ErrorBody(
                code=error.code,
                message=error.message,
                request_id=request_id,
                retryable=error.retryable,
            )
        )
        return JSONResponse(status_code=error.status_code, content=payload.model_dump())

    app.include_router(health_router)
    app.include_router(chat_router)
    app.include_router(management_router)
    app.include_router(rag_router)
    app.include_router(tasks_router)
    app.include_router(traces_router)
    return app


app = create_app()

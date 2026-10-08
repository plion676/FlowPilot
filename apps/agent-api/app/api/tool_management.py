from __future__ import annotations

from ipaddress import ip_address
from typing import Annotated
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Path, Request
from pydantic import BaseModel, ConfigDict, Field

from app.clients.bindings import AgentProfile, ToolRef
from app.core.errors import AppError
from app.services.tool_management import ToolManagementService


def local_management(request: Request) -> ToolManagementService:
    """Development-only surface, never exposed to the Agent's Tool catalog."""
    try:
        local = request.client is not None and ip_address(request.client.host).is_loopback
    except ValueError:
        local = False
    origin = request.headers.get("origin")
    if origin:
        parsed = urlparse(origin)
        local = (
            local
            and parsed.scheme in {"http", "https"}
            and parsed.hostname
            in {
                "127.0.0.1",
                "localhost",
                "::1",
            }
        )
    if request.app.state.settings.app_env != "development" or not local:
        raise AppError("MANAGEMENT_DISABLED", "工具管理仅在本机开发环境开放。", 403)
    return request.app.state.tool_management


router = APIRouter(prefix="/api/admin", tags=["tool-management"])
LocalManagement = Annotated[ToolManagementService, Depends(local_management)]
EndpointId = Annotated[str, Path(pattern=r"^[a-z][a-z0-9_]{0,31}$")]


class SaveBindings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    endpoint_ids: list[str] = Field(max_length=10)
    expected_endpoint_versions: dict[str, int]
    tools: list[ToolRef] = Field(max_length=50)
    expected_version: int = Field(ge=1)


@router.get("/tools")
async def tools(service: LocalManagement) -> dict:
    raise AppError("BINDING_SCHEMA_CHANGED", "请从 MCP 连接目录选择工具。", 409)


@router.get("/agents")
async def agents(request: Request, service: LocalManagement) -> dict:
    return {"agents": await service.bindings.list_agents(request.state.request_id)}


@router.put("/agents/{agent_id}/capabilities", response_model=AgentProfile)
async def save_bindings(
    agent_id: str,
    payload: SaveBindings,
    request: Request,
    service: LocalManagement,
) -> AgentProfile:
    return await service.save(agent_id, payload.model_dump(), request.state.request_id)


@router.put("/agents/{agent_id}/tools")
async def legacy_save(agent_id: str, service: LocalManagement):
    raise AppError("BINDING_SCHEMA_CHANGED", "旧工具配置缺少 Endpoint 来源，请刷新页面。", 409)


class EndpointPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,31}$")
    name: str = Field(min_length=1, max_length=120)
    role_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,79}$")
    url: str = Field(max_length=500)
    credential_profile: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    enabled: bool
    expected_version: int | None = Field(default=None, ge=1)


@router.get("/mcp-endpoints")
async def endpoints(request: Request, service: LocalManagement):
    data = await service.endpoints.list(request.state.request_id)
    data["credential_profiles"] = service.factory.configured_profiles()
    return data


@router.post("/mcp-endpoints")
async def create_endpoint(payload: EndpointPayload, request: Request, service: LocalManagement):
    if not payload.id:
        raise AppError("INVALID_ARGUMENTS", "新增连接必须填写部署 Endpoint ID。", 400)
    return await service.save_endpoint(
        payload.model_dump(exclude_none=True), request.state.request_id
    )


@router.put("/mcp-endpoints/{endpoint_id}")
async def update_endpoint(
    endpoint_id: EndpointId, payload: EndpointPayload, request: Request, service: LocalManagement
):
    if not payload.expected_version or (payload.id and payload.id != endpoint_id):
        raise AppError("INVALID_ARGUMENTS", "连接标识不可修改，更新须包含配置版本。", 400)
    return await service.save_endpoint(
        payload.model_dump(exclude_none=True), request.state.request_id, endpoint_id
    )


@router.post("/mcp-endpoints/{endpoint_id}/check")
async def check_endpoint(endpoint_id: EndpointId, request: Request, service: LocalManagement):
    return await service.check(endpoint_id, request.state.request_id)


@router.get("/mcp-endpoints/{endpoint_id}/tools")
async def endpoint_tools(endpoint_id: EndpointId, request: Request, service: LocalManagement):
    endpoint = await service.endpoints.get(endpoint_id, request.state.request_id)
    try:
        data = await service.endpoints.checked_tools(endpoint_id, request.state.request_id)
    except AppError as error:
        if error.code == "NOT_FOUND":
            return {"tools": [], "status": "unknown", "stale": True}
        raise
    return {
        **data,
        "stale": data["check"]["checked_execution_revision"] != endpoint.execution_revision,
        "status": data["check"]["status"],
    }

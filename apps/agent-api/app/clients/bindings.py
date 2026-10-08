"""Versioned Agent configuration owned by the existing Go/MySQL service."""

from __future__ import annotations

from typing import Any, Protocol
from urllib.parse import quote, urlparse

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.core.errors import AppError


class ToolRef(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    endpoint_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    tool_name: str


class AgentProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,79}$")
    name: str
    binding_schema_version: int = 2
    endpoint_ids: list[str] = Field(max_length=10)
    tools: list[ToolRef] = Field(max_length=50)
    endpoints: list[dict[str, Any]] = Field(default_factory=list, max_length=10)
    version: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_sources(self):
        keys = [(tool.endpoint_id, tool.tool_name) for tool in self.tools]
        if (
            self.binding_schema_version != 2
            or len(keys) != len(set(keys))
            or len(self.endpoint_ids) != len(set(self.endpoint_ids))
            or any(tool.endpoint_id not in self.endpoint_ids for tool in self.tools)
        ):
            raise ValueError("来源绑定无效")
        return self


class BindingStore(Protocol):
    async def list_agents(self, request_id: str) -> list[AgentProfile]: ...

    async def get(self, agent_id: str, request_id: str) -> AgentProfile: ...

    async def save(
        self, agent_id: str, payload: dict[str, Any], request_id: str
    ) -> AgentProfile: ...


class HttpBindingStore:
    def __init__(self, base_url: str | None, token: str | None) -> None:
        self._base_url = base_url
        self._token = token
        if base_url:
            parsed = urlparse(base_url)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.path not in {"", "/"}
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("BUSINESS_BASE_URL 必须为服务根地址")

    async def _request(
        self, method: str, path: str, request_id: str, payload: dict[str, Any] | None = None
    ) -> Any:
        if not self._base_url or not self._token or len(self._token) < 16:
            raise AppError(
                "BINDING_NOT_CONFIGURED",
                "请配置 BUSINESS_BASE_URL 与 INTERNAL_SERVICE_TOKEN 后连接工具管理服务。",
                503,
            )
        try:
            async with httpx.AsyncClient(base_url=self._base_url, timeout=5) as client:
                response = await client.request(
                    method,
                    path,
                    json=payload,
                    headers={
                        "X-Internal-Service-Token": self._token,
                        "X-Request-ID": request_id,
                    },
                )
            if response.status_code == 404:
                raise AppError("NOT_FOUND", "角色或 MCP 连接不存在。", 404)
            if response.status_code == 409:
                raise AppError("BINDING_CONFLICT", "配置已被修改，请刷新后重新选择。", 409)
            if response.status_code == 400:
                raise AppError(
                    "INVALID_ARGUMENTS", "来源未验证或配置参数无效，请检查连接与工具。", 400
                )
            if not response.is_success:
                raise AppError("DEPENDENCY_UNAVAILABLE", "工具绑定服务暂不可用。", 503, True)
            return response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise AppError("DEPENDENCY_UNAVAILABLE", "工具绑定服务暂不可用。", 503, True) from error

    async def list_agents(self, request_id: str) -> list[AgentProfile]:
        data = await self._request("GET", "/internal/agents", request_id)
        try:
            return [AgentProfile.model_validate(item) for item in data["agents"]]
        except (ValidationError, KeyError, TypeError) as error:
            raise AppError("INVALID_BINDING", "工具绑定服务返回了无效配置。", 503) from error

    async def get(self, agent_id: str, request_id: str) -> AgentProfile:
        data = await self._request(
            "GET", f"/internal/agents/{quote(agent_id, safe='')}/bindings", request_id
        )
        return self._profile(data)

    async def save(self, agent_id: str, payload: dict[str, Any], request_id: str) -> AgentProfile:
        data = await self._request(
            "PUT",
            f"/internal/agents/{quote(agent_id, safe='')}/bindings",
            request_id,
            payload,
        )
        return self._profile(data)

    @staticmethod
    def _profile(data: Any) -> AgentProfile:
        try:
            return AgentProfile.model_validate(data)
        except ValidationError as error:
            raise AppError("INVALID_BINDING", "工具绑定服务返回了无效配置。", 503) from error

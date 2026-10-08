"""Persistent MCP connection records; credentials never live in these models."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.clients.bindings import HttpBindingStore


class Endpoint(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    name: str = Field(min_length=1, max_length=120)
    role_id: str = Field(pattern=r"^(?:[a-z][a-z0-9_]{0,79})?$")
    url: str = Field(max_length=500)
    credential_profile: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    enabled: bool
    version: int = Field(ge=1)
    execution_revision: int = Field(ge=1)
    updated_at: str = ""


class HttpEndpointStore:
    def __init__(self, client: HttpBindingStore):
        self.client = client

    async def list(self, request_id: str) -> dict[str, Any]:
        data = await self.client._request("GET", "/internal/mcp-endpoints", request_id)
        data["endpoints"] = [Endpoint.model_validate(item) for item in data["endpoints"]]
        return data

    async def get(self, endpoint_id: str, request_id: str) -> Endpoint:
        data = await self.client._request(
            "GET", f"/internal/mcp-endpoints/{endpoint_id}", request_id
        )
        return Endpoint.model_validate(data)

    async def save(
        self, payload: dict[str, Any], request_id: str, endpoint_id: str | None = None
    ) -> Endpoint:
        path = "/internal/mcp-endpoints" + (f"/{endpoint_id}" if endpoint_id else "")
        data = await self.client._request(
            "PUT" if endpoint_id else "POST", path, request_id, payload
        )
        return Endpoint.model_validate(data)

    async def check_result(
        self, endpoint: Endpoint, tools: list[dict], error_code: str, request_id: str
    ):
        return await self.client._request(
            "PUT",
            f"/internal/mcp-endpoints/{endpoint.id}/check-result",
            request_id,
            {
                "execution_revision": endpoint.execution_revision,
                "status": "failed" if error_code else "ready",
                "error_code": error_code,
                "tools": tools,
            },
        )

    async def checked_tools(self, endpoint_id: str, request_id: str) -> dict:
        return await self.client._request(
            "GET", f"/internal/mcp-endpoints/{endpoint_id}/check", request_id
        )

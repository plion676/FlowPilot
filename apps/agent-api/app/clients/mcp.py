"""Signed, policy-bound official MCP client for one Tool call."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import time
from collections.abc import Mapping
from typing import Any, Protocol

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from app.core.errors import AppError
from app.models.policy import AllowedToolBinding

# This value is generated from the pinned MCP Zod Tool catalog. A schema change
# must update both sides deliberately; otherwise calls fail closed.
CONTRACT_FINGERPRINT = "d89d38bdd0109fb2cbaf9b02e97dfbcc7899d1555d361f916a40de6a9a5133ec"


class McpToolClient(Protocol):
    async def list_tools(self) -> list[dict[str, Any]]: ...

    async def call(
        self,
        binding: AllowedToolBinding,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        actor_id: str,
    ) -> dict[str, Any]: ...


def signed_headers(
    binding: AllowedToolBinding,
    tool_name: str,
    arguments: Mapping[str, Any],
    *,
    actor_id: str,
    secret: str,
    now: int | None = None,
) -> dict[str, str]:
    if len(secret) < 32:
        raise AppError("MCP_NOT_CONFIGURED", "MCP 内部签名密钥尚未配置。", 503)
    issued_at = int(time.time()) if now is None else now
    context = {
        "version": 2 if binding.endpoint_id else 1,
        "request_id": str(binding.request_id),
        "actor_id": actor_id,
        "role": binding.role,
        "route": binding.route,
        "selected_tool": binding.selected_tool,
        "skill_name": binding.skill_name,
        "binding_tools": sorted(binding.tool_names),
        "tool_name": tool_name,
        "arguments": dict(arguments),
        "contract_fingerprint": CONTRACT_FINGERPRINT,
        "issued_at": issued_at,
        "expires_at": issued_at + 30,
    }
    if binding.binding_version is not None:
        context["binding_version"] = binding.binding_version
    if binding.endpoint_id:
        context["endpoint_id"] = binding.endpoint_id
        context["endpoint_revision"] = binding.endpoint_revision
    payload = json.dumps(context, ensure_ascii=False, separators=(",", ":")).encode()
    encoded = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    signature = hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).hexdigest()
    return {"X-Opspilot-Context": encoded, "X-Opspilot-Signature": signature}


class OfficialMcpClient:
    def __init__(self, url: str, secret: str, *, endpoint_id: str | None = None) -> None:
        if not url.startswith(("http://", "https://")):
            raise ValueError("MCP_URL 必须是 HTTP(S) URL")
        self._url = url
        self._secret = secret
        self.endpoint_id = endpoint_id

    async def list_tools(self) -> list[dict[str, Any]]:
        try:
            async with (
                asyncio.timeout(12),
                httpx.AsyncClient(
                    timeout=5.0,
                    trust_env=False,
                    follow_redirects=False,
                    event_hooks={"response": [self._limit_response]},
                ) as http_client,
            ):
                async with streamable_http_client(self._url, http_client=http_client) as (
                    read,
                    write,
                    _,
                ):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        tools: list[dict[str, Any]] = []
                        cursor: str | None = None
                        seen: set[str] = set()
                        while True:
                            result = await session.list_tools(cursor=cursor)
                            tools.extend(tool.model_dump(by_alias=True) for tool in result.tools)
                            if len(tools) > 50 or len(seen) >= 10:
                                raise ValueError("MCP 目录超过上限")
                            cursor = result.nextCursor
                            if cursor is None:
                                return tools
                            if cursor in seen:
                                raise ValueError("MCP 工具目录分页重复")
                            seen.add(cursor)
        except Exception as error:
            raise AppError(
                "DEPENDENCY_UNAVAILABLE", "无法读取 MCP 工具目录，请检查 MCP 服务。", 503, True
            ) from error

    async def call(
        self,
        binding: AllowedToolBinding,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        actor_id: str,
    ) -> dict[str, Any]:
        headers = signed_headers(
            binding, tool_name, arguments, actor_id=actor_id, secret=self._secret
        )
        try:
            if self.endpoint_id and binding.endpoint_id != self.endpoint_id:
                raise AppError("FORBIDDEN_ENDPOINT", "工具来源不匹配。", 403)
            async with (
                asyncio.timeout(15),
                httpx.AsyncClient(
                    headers=headers,
                    timeout=5.0,
                    trust_env=False,
                    follow_redirects=False,
                    event_hooks={"response": [self._limit_response]},
                ) as http_client,
            ):
                async with streamable_http_client(self._url, http_client=http_client) as (
                    read,
                    write,
                    _,
                ):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        result = await session.call_tool(tool_name, arguments)
        except AppError:
            raise
        except Exception as error:
            raise AppError(
                "DEPENDENCY_UNAVAILABLE", "MCP 工具服务暂不可用。", 503, retryable=True
            ) from error
        if result.isError:
            code = (
                result.content[0].text
                if result.content and hasattr(result.content[0], "text")
                else ""
            )
            if code in {"SOP_INDEX_NOT_READY", "EMBEDDING_UNAVAILABLE", "INVALID_SOP_EVIDENCE"}:
                raise AppError(code, "SOP 检索尚不可用，请检查索引和本地模型。", 503)
            if code == "NOT_FOUND":
                raise AppError("NOT_FOUND", "未找到对应的模拟业务记录。", 404)
            if code == "BINDING_CHANGED":
                raise AppError("BINDING_CHANGED", "工具绑定已更新，请重新发送消息。", 409)
            if code == "ENDPOINT_DISABLED":
                raise AppError("ENDPOINT_DISABLED", "MCP 连接已停用。", 403)
            if code == "APPROVAL_REQUIRED":
                raise AppError("APPROVAL_REQUIRED", "该写操作需要人工审批。", 403)
            if code in {"FORBIDDEN_TOOL", "FORBIDDEN_SKILL", "INVALID_BINDING", "UNAUTHORIZED"}:
                raise AppError("FORBIDDEN_TOOL", "当前角色不允许执行该操作。", 403)
            raise AppError("DEPENDENCY_UNAVAILABLE", "工具调用未成功。", 503, retryable=True)
        if not isinstance(result.structuredContent, dict):
            raise AppError("INVALID_TOOL_RESULT", "工具返回了无效结果。", 503)
        return result.structuredContent

    @staticmethod
    async def _limit_response(response: httpx.Response) -> None:
        # The project's stateless server returns finite JSON/SSE responses.
        # Limit bytes before the SDK decodes; redirects are never followed.
        if response.is_redirect:
            raise ValueError("MCP 重定向被禁止")
        size = 0
        chunks = []
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > 262144:
                raise ValueError("MCP 响应超过上限")
            chunks.append(chunk)
        response._content = b"".join(chunks)

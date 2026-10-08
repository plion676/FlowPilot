"""Endpoint-aware manual capabilities and deterministic runtime authorization."""

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any
from uuid import UUID

from pydantic import ValidationError

from app.agent.skills import SkillRegistry
from app.clients.bindings import AgentProfile, BindingStore, ToolRef
from app.clients.endpoints import Endpoint
from app.clients.mcp import CONTRACT_FINGERPRINT
from app.core.errors import AppError
from app.models.policy import AllowedToolBinding
from app.tools.registry import ToolRegistry


def tool_alias(ref: ToolRef) -> str:
    prefix = re.sub(r"[^A-Za-z0-9_]", "_", ref.tool_name)[:28]
    digest = hashlib.sha256(
        json.dumps([ref.endpoint_id, ref.tool_name], separators=(",", ":")).encode()
    ).hexdigest()[:32]
    return f"{prefix}__{digest}"


@dataclass(frozen=True)
class RoleBindingSnapshot:
    request_id: UUID
    role: str
    binding_version: int
    tools: frozenset[ToolRef]
    endpoints: Mapping[str, Endpoint]


class ToolManagementService:
    def __init__(
        self, bindings: BindingStore, registry: ToolRegistry, factory: Any, endpoints: Any
    ):
        self.bindings, self.registry, self.factory, self.endpoints = (
            bindings,
            registry,
            factory,
            endpoints,
        )
        self.skills = SkillRegistry()

    async def catalog(self, endpoint_id: str, request_id: str) -> list[dict[str, Any]]:
        endpoint = await self.endpoints.get(endpoint_id, request_id)
        result, names = [], set()
        client = self.factory.for_endpoint(endpoint)
        for item in await client.list_tools():
            name = item["name"]
            if name in names:
                raise AppError("INVALID_TOOL_CATALOG", "MCP 工具目录包含重复名称。", 503)
            names.add(name)
            meta = item.get("_meta") or {}
            if meta.get("opspilot/endpointId") != endpoint.id:
                raise AppError("ENDPOINT_ID_MISMATCH", "登记的连接标识与 MCP 服务身份不一致。", 503)
            if not endpoint.role_id or meta.get("opspilot/roleId") != endpoint.role_id:
                raise AppError("ENDPOINT_ROLE_MISMATCH", "连接所属角色与 MCP 部署角色不一致。", 503)
            spec = self.registry.get(name)
            compatible = (
                spec is not None
                and meta.get("opspilot/contractFingerprint") == CONTRACT_FINGERPRINT
            )
            result.append(
                {
                    "name": name,
                    "tool_name": name,
                    "endpoint_id": endpoint.id,
                    "endpoint_name": endpoint.name,
                    "endpoint_role_id": endpoint.role_id,
                    "domain": name.split(".", 1)[0],
                    "description": (item.get("description") or name)[:1000],
                    "input_schema": item["inputSchema"],
                    "risk_level": spec.risk_level if spec else "unknown",
                    "bindable": compatible,
                    "available": bool(compatible and spec and spec.available),
                    "status": "contract_mismatch"
                    if not compatible
                    else "ready"
                    if spec and spec.available
                    else "not_ready",
                    "model_name": tool_alias(ToolRef(endpoint_id=endpoint.id, tool_name=name)),
                }
            )
        return sorted(result, key=lambda item: item["name"])

    async def check(self, endpoint_id: str, request_id: str) -> dict:
        endpoint = await self.endpoints.get(endpoint_id, request_id)
        tools = []
        try:
            tools = await self.catalog(endpoint_id, request_id)
            if any(not item["bindable"] for item in tools):
                raise AppError("INVALID_TOOL_CATALOG", "连接中的工具契约未通过验证。", 503)
        except AppError as error:
            await self.endpoints.check_result(endpoint, tools, error.code, request_id)
            raise
        await self.endpoints.check_result(endpoint, tools, "", request_id)
        return {
            "tools": tools,
            "execution_revision": endpoint.execution_revision,
            "status": "ready",
        }

    async def save_endpoint(self, payload: dict, request_id: str, endpoint_id: str | None = None):
        self.factory.validate(
            Endpoint(
                id=endpoint_id or payload.get("id", ""),
                version=1,
                execution_revision=1,
                **{
                    key: value
                    for key, value in payload.items()
                    if key not in {"id", "expected_version"}
                },
            )
        )
        return await self.endpoints.save(payload, request_id, endpoint_id)

    async def save(self, agent_id: str, payload: dict, request_id: str) -> AgentProfile:
        current = await self.bindings.get(agent_id, request_id)
        refs = [ToolRef.model_validate(ref) for ref in payload["tools"]]
        endpoint_ids = payload["endpoint_ids"]
        if (
            len(set(refs)) != len(refs)
            or len(set(endpoint_ids)) != len(endpoint_ids)
            or any(
                ref.endpoint_id not in endpoint_ids or ref.tool_name not in self.registry.names
                for ref in refs
            )
        ):
            raise AppError("INVALID_ARGUMENTS", "工具或 Endpoint 绑定重复、未知或不匹配。", 400)
        # New grants require current live evidence; revocation never requires MCP online.
        for endpoint_id in endpoint_ids:
            endpoint = await self.endpoints.get(endpoint_id, request_id)
            if endpoint.role_id != agent_id:
                raise AppError("FORBIDDEN_ENDPOINT", "不能绑定其他角色或未归属的 MCP 连接。", 403)
        added = set(refs) - set(current.tools)
        for endpoint_id in sorted({ref.endpoint_id for ref in added}):
            checked = await self.check(endpoint_id, request_id)
            valid = {item["name"] for item in checked["tools"] if item["bindable"]}
            if any(ref.tool_name not in valid for ref in added if ref.endpoint_id == endpoint_id):
                raise AppError("INVALID_ARGUMENTS", "新绑定工具不存在或契约不兼容。", 400)
        return await self.bindings.save(agent_id, payload, request_id)

    async def prepare(self, role: str, request_id: UUID) -> tuple[RoleBindingSnapshot, list[dict]]:
        try:
            profile = await self.bindings.get(role, str(request_id))
        except AppError as error:
            if error.code == "NOT_FOUND":
                raise AppError("UNKNOWN_AGENT", "当前 Agent 类别不存在。", 403) from error
            raise
        endpoints = {item["id"]: Endpoint.model_validate(item) for item in profile.endpoints}
        if any(endpoint.role_id != role for endpoint in endpoints.values()):
            raise AppError("FORBIDDEN_ENDPOINT", "角色与 MCP 连接归属不匹配，请修正绑定。", 403)
        if set(endpoints) != set(profile.endpoint_ids) or any(
            ref.tool_name not in self.registry.names for ref in profile.tools
        ):
            raise AppError("INVALID_BINDING", "当前来源绑定配置无效。", 503)
        snapshot = RoleBindingSnapshot(
            request_id, role, profile.version, frozenset(profile.tools), MappingProxyType(endpoints)
        )
        catalog = []
        for endpoint_id in sorted({ref.endpoint_id for ref in profile.tools}):
            expected = {ref.tool_name for ref in profile.tools if ref.endpoint_id == endpoint_id}
            try:
                items = await self.catalog(endpoint_id, str(request_id))
                by_name = {item["name"]: item for item in items}
                if not expected <= by_name.keys() or any(
                    not by_name[name]["bindable"] for name in expected
                ):
                    raise AppError("INVALID_TOOL_CATALOG", "已绑定工具缺失或契约变化。", 503)
                catalog.extend(by_name[name] for name in sorted(expected))
            except AppError as error:
                # Ordinary replies remain possible, but no old tools are exposed.
                catalog.extend(
                    {
                        "name": name,
                        "endpoint_id": endpoint_id,
                        "available": False,
                        "status": error.code,
                    }
                    for name in sorted(expected)
                )
        aliases = [item["model_name"] for item in catalog if item["available"]]
        if len(aliases) != len(set(aliases)):
            raise AppError("INVALID_BINDING", "模型工具别名冲突。", 503)
        return snapshot, catalog

    async def authorize_call(
        self,
        snapshot: RoleBindingSnapshot,
        ref: ToolRef,
        arguments: dict,
        *,
        skill_name: str | None = None,
    ) -> tuple[dict, AllowedToolBinding, Endpoint]:
        current = await self.bindings.get(snapshot.role, str(snapshot.request_id))
        if current.version != snapshot.binding_version or set(current.tools) != snapshot.tools:
            raise AppError("BINDING_CHANGED", "角色工具绑定已更新，请重新发送消息。", 409)
        if ref not in snapshot.tools:
            raise AppError("FORBIDDEN_TOOL", "当前角色未绑定这个来源的工具。", 403)
        skill = self.skills.resolve(snapshot.role, skill_name) if skill_name else None
        if skill and ref.tool_name not in skill.allowed_tools:
            raise AppError("FORBIDDEN_TOOL", "当前 Skill 不允许调用这个工具。", 403)
        endpoint = await self.endpoints.get(ref.endpoint_id, str(snapshot.request_id))
        if endpoint.role_id != snapshot.role:
            raise AppError("FORBIDDEN_ENDPOINT", "MCP 连接不属于当前角色。", 403)
        old = snapshot.endpoints[ref.endpoint_id]
        if endpoint.execution_revision != old.execution_revision:
            raise AppError("BINDING_CHANGED", "MCP 连接已变化，请重新发送消息。", 409)
        self.factory.for_endpoint(
            endpoint
        )  # Check target, credential scope and enabled state again.
        spec = self.registry.get(ref.tool_name)
        if spec is None:
            raise AppError("FORBIDDEN_TOOL", "工具未注册。", 403)
        if spec.risk_level == "approval_required":
            raise AppError("APPROVAL_REQUIRED", "写操作仍需要人工确认。", 403)
        if not spec.available:
            raise AppError("TOOL_NOT_READY", "工具尚未接通。", 503)
        try:
            validated = spec.validate(arguments)
        except (ValidationError, ValueError, TypeError) as error:
            raise AppError("INVALID_ARGUMENTS", "工具参数不符合要求。", 400) from error
        binding = AllowedToolBinding(
            request_id=snapshot.request_id,
            role=snapshot.role,
            route="agent",
            skill_name=skill_name,
            binding_version=snapshot.binding_version,
            endpoint_id=endpoint.id,
            endpoint_revision=endpoint.execution_revision,
            tool_names=frozenset(
                item.tool_name
                for item in current.tools
                if item.endpoint_id == endpoint.id
                and (not skill or item.tool_name in skill.allowed_tools)
            ),
        )
        return validated, binding, endpoint

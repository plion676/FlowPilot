from __future__ import annotations

import json
from typing import Any

from pydantic import TypeAdapter

from app.clients.bindings import AgentProfile, ToolRef
from app.clients.endpoints import Endpoint
from app.clients.mcp import CONTRACT_FINGERPRINT
from app.clients.mcp_factory import McpClientFactory
from app.core.errors import AppError
from app.tools.registry import default_tool_registry


class MemoryEndpoints:
    def __init__(self):
        self.items = {
            name: Endpoint(
                id=name,
                name=name.upper(),
                role_id="consultant",
                url="http://127.0.0.1:3100/mcp/consultant"
                + ("" if name == "business" else f"_{name}"),
                credential_profile="local",
                enabled=True,
                version=1,
                execution_revision=1,
            )
            for name in ["business", "crm", "mes"]
        }
        self.checks = {}

    async def list(self, request_id):
        return {
            "endpoints": list(self.items.values()),
            "checks": [item["check"] for item in self.checks.values()],
        }

    async def get(self, endpoint_id, request_id):
        if endpoint_id not in self.items:
            raise AppError("NOT_FOUND", "连接不存在。", 404)
        return self.items[endpoint_id]

    async def save(self, payload, request_id, endpoint_id=None):
        endpoint_id = endpoint_id or payload["id"]
        old = self.items.get(endpoint_id)
        if old and old.version != payload["expected_version"]:
            raise AppError("BINDING_CONFLICT", "配置冲突。", 409)
        values = {
            key: value for key, value in payload.items() if key not in {"expected_version", "id"}
        }
        revision = old.execution_revision if old else 1
        if old and any(
            values[key] != getattr(old, key)
            for key in ["role_id", "url", "enabled", "credential_profile"]
        ):
            revision += 1
        self.items[endpoint_id] = Endpoint(
            id=endpoint_id,
            version=old.version + 1 if old else 1,
            execution_revision=revision,
            **values,
        )
        return self.items[endpoint_id]

    async def check_result(self, endpoint, tools, error_code, request_id):
        if self.items[endpoint.id].execution_revision != endpoint.execution_revision:
            raise AppError("BINDING_CONFLICT", "连接已变化。", 409)
        self.checks[endpoint.id] = {
            "check": {
                "endpoint_id": endpoint.id,
                "checked_execution_revision": endpoint.execution_revision,
                "status": "failed" if error_code else "ready",
                "error_code": error_code,
                "checked_at": "2026-10-08T00:00:00Z",
            },
            "tools": tools,
        }

    async def checked_tools(self, endpoint_id, request_id):
        if endpoint_id not in self.checks:
            raise AppError("NOT_FOUND", "尚未检查。", 404)
        return self.checks[endpoint_id]


class MemoryBindings:
    def __init__(self, tools: list[str] | None = None, endpoints=None):
        self.endpoints = endpoints or MemoryEndpoints()
        self.profile = AgentProfile(
            id="consultant",
            name="运营顾问",
            version=1,
            endpoint_ids=["business"],
            tools=[
                ToolRef(endpoint_id="business", tool_name=name)
                for name in tools
                if tools is not None
            ]
            if tools is not None
            else [
                ToolRef(endpoint_id="business", tool_name=name)
                for name in ["crm.get_customer_overview", "crm.list_open_tickets"]
            ],
        )
        self.extra = {}

    async def list_agents(self, request_id):
        return [await self.get(name, request_id) for name in [self.profile.id, *self.extra]]

    async def get(self, agent_id, request_id):
        profile = self.profile if agent_id == self.profile.id else self.extra.get(agent_id)
        if profile is None:
            raise AppError("UNKNOWN_AGENT", "当前 Agent 类别不存在。", 403)
        return profile.model_copy(
            update={
                "endpoints": [
                    self.endpoints.items[name].model_dump() for name in profile.endpoint_ids
                ]
            },
            deep=True,
        )

    async def save(self, agent_id, payload, request_id):
        current = await self.get(agent_id, request_id)
        if payload["expected_version"] != current.version:
            raise AppError("BINDING_CONFLICT", "配置已被修改。", 409)
        profile = current.model_copy(
            update={
                "endpoint_ids": payload["endpoint_ids"],
                "tools": [ToolRef.model_validate(item) for item in payload["tools"]],
                "version": current.version + 1,
            }
        )
        if agent_id == self.profile.id:
            self.profile = profile
        else:
            self.extra[agent_id] = profile
        return await self.get(agent_id, request_id)


class FakeMcp:
    def __init__(self, endpoint_id="business"):
        self.endpoint_id = endpoint_id
        self.reported_id = endpoint_id
        self.reported_role = "consultant"
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.after_call = None
        self.fingerprint = CONTRACT_FINGERPRINT
        self.fail = False

    async def list_tools(self):
        if self.fail:
            raise AppError("DEPENDENCY_UNAVAILABLE", "模拟连接失败。", 503)
        registry = default_tool_registry()
        return [
            {
                "name": name,
                "description": f"模拟工具 {name}",
                "inputSchema": TypeAdapter(registry.get(name).input_schema).json_schema(),
                "_meta": {
                    "opspilot/contractFingerprint": self.fingerprint,
                    "opspilot/endpointId": self.reported_id,
                    "opspilot/roleId": self.reported_role,
                },
            }
            for name in sorted(registry.names)
            if self.endpoint_id == "business" or name.startswith(self.endpoint_id + ".")
        ]

    async def call(self, binding, tool_name, arguments, *, actor_id):
        assert binding.endpoint_id == self.endpoint_id
        self.calls.append((tool_name, arguments))
        if self.after_call:
            await self.after_call()
        if tool_name == "crm.get_customer_overview":
            return {
                "customer_code": arguments["customer_id"],
                "name": "模拟客户甲",
                "renewal_date": "2026-10-05",
                "risk_level": "high",
            }
        if tool_name == "mes.get_work_order_status":
            return {"work_order_code": "WO-1001", "status": "in_progress"}
        if tool_name == "knowledge.search_sop":
            from app.rag.corpus import Corpus

            chunk = next(chunk for chunk in Corpus().chunks.values() if "临近续费" in chunk.excerpt)
            return {
                "matches": [
                    {
                        key: getattr(chunk, key)
                        for key in ["document_id", "title", "chunk_id", "excerpt"]
                    }
                    | {"score": 0.8}
                ]
            }
        return {"customer_code": arguments["customer_id"], "tickets": []}


class FakeFactory(McpClientFactory):
    def __init__(self):
        targets = [endpoint.url for endpoint in MemoryEndpoints().items.values()]
        super().__init__(
            {
                "MCP_CALL_SECRET": "local-test-only-signing-secret-32-characters",
                "MCP_ALLOWED_TARGETS": json.dumps(targets),
                "MCP_CREDENTIAL_PROFILES": json.dumps(
                    {
                        "local": {
                            "secret_env": "MCP_CALL_SECRET",
                            "endpoint_ids": ["business", "crm", "mes"],
                            "targets": targets,
                        }
                    }
                ),
            }
        )
        self.clients = {name: FakeMcp(name) for name in ["business", "crm", "mes"]}

    def for_endpoint(self, endpoint):
        super().for_endpoint(endpoint)
        return self.clients[endpoint.id]

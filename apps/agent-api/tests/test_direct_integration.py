"""Opt-in real MCP -> Go -> MySQL; restores role capabilities with CAS."""

import os
from dataclasses import replace
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage

from app.clients.bindings import HttpBindingStore, ToolRef
from app.clients.endpoints import HttpEndpointStore
from app.clients.mcp_factory import McpClientFactory
from app.core.errors import AppError
from app.services.chat import ChatService
from app.services.tool_management import ToolManagementService, tool_alias
from app.tools.registry import default_tool_registry
from tests.test_api import ToolCallingFakeModel, tool_call


@pytest.mark.asyncio
async def test_real_binding_react_mcp_mysql_and_revocation():
    names = [
        "OPSPILOT_TEST_MCP_URL",
        "OPSPILOT_TEST_MCP_SECRET",
        "OPSPILOT_TEST_BUSINESS_URL",
        "OPSPILOT_TEST_SERVICE_TOKEN",
    ]
    if not all(os.getenv(name) for name in names):
        pytest.skip("Set OPSPILOT_TEST_MCP_URL/SECRET and OPSPILOT_TEST_BUSINESS_URL/SERVICE_TOKEN")
    bindings = HttpBindingStore(os.environ[names[2]], os.environ[names[3]])
    endpoints = HttpEndpointStore(bindings)
    env = dict(os.environ)
    env.update(MCP_URL=os.environ[names[0]], MCP_CALL_SECRET=os.environ[names[1]])
    factory = McpClientFactory(env)
    manager = ToolManagementService(bindings, default_tool_registry(), factory, endpoints)
    request_id = str(uuid4())
    original = await bindings.get("consultant", request_id)
    result = await manager.check("business", request_id)
    assert len(result["tools"]) == 5
    assert all(item["bindable"] for item in result["tools"])
    assert (await endpoints.get("business", request_id)).role_id == "consultant"

    async def save(refs, version, endpoint_ids=None):
        ids = sorted({ref.endpoint_id for ref in refs}) if endpoint_ids is None else endpoint_ids
        expected = {name: (await endpoints.get(name, request_id)).version for name in ids}
        return await manager.save(
            "consultant",
            {
                "endpoint_ids": ids,
                "tools": [ref.model_dump() for ref in refs],
                "expected_version": version,
                "expected_endpoint_versions": expected,
            },
            request_id,
        )

    refs = [
        ToolRef(endpoint_id="business", tool_name=name)
        for name in [
            "crm.get_customer_overview",
            "crm.list_open_tickets",
            "mes.get_work_order_status",
        ]
    ]
    saved = await save(refs, original.version)
    disabled = None
    try:
        model = ToolCallingFakeModel(
            responses=[
                tool_call(tool_alias(refs[0])),
                tool_call(tool_alias(refs[1])),
                tool_call(tool_alias(refs[2]), {"work_order_id": "WO-1001"}),
                AIMessage(content="同一角色连接的三个领域工具查询完成。"),
            ]
        )
        response = await ChatService(model, "fake-model", manager).reply(
            "查询", role="consultant", actor_id="test", request_id=uuid4()
        )
        assert len(response.tool_calls) == 3
        assert {call.endpoint_id for call in response.tool_calls} == {"business"}
        assert response.tool_calls[0].result["risk_level"] == "high"
        assert response.tool_calls[2].result["work_order_code"] == "WO-1001"
        snapshot, _ = await manager.prepare("consultant", uuid4())
        _, scoped, crm = await manager.authorize_call(snapshot, refs[0], {"customer_id": "C1001"})
        # Signed, but claiming another role: deployed MCP must reject independently.
        with pytest.raises(AppError) as rejected:
            await factory.for_endpoint(crm).call(
                replace(scoped, role="production"),
                "crm.get_customer_overview",
                {"customer_id": "C1001"},
                actor_id="test",
            )
        assert rejected.value.code == "FORBIDDEN_TOOL"
        payload = {
            key: value
            for key, value in crm.model_dump().items()
            if key in {"name", "role_id", "url", "credential_profile", "enabled"}
        }
        disabled = await manager.save_endpoint(
            {**payload, "enabled": False, "expected_version": crm.version}, request_id, "business"
        )
        # Bypass API gate deliberately to prove the MCP gate also denies old grants.
        with pytest.raises(AppError) as rejected:
            await factory.for_endpoint(crm).call(
                scoped, "crm.get_customer_overview", {"customer_id": "C1001"}, actor_id="test"
            )
        assert rejected.value.code == "ENDPOINT_DISABLED"
        restored = await manager.save_endpoint(
            {**payload, "expected_version": disabled.version}, request_id, "business"
        )
        disabled = None
        assert restored.execution_revision > crm.execution_revision
        with pytest.raises(AppError) as rejected:
            await factory.for_endpoint(restored).call(
                scoped, "crm.get_customer_overview", {"customer_id": "C1001"}, actor_id="test"
            )
        assert rejected.value.code == "BINDING_CHANGED"
        saved = await save([], saved.version, [])
        greeting = await ChatService(
            ToolCallingFakeModel(responses=[AIMessage(content="你好！")]), "fake-model", manager
        ).reply("你好", role="consultant", actor_id="test", request_id=uuid4())
        assert greeting.tool_calls == []
    finally:
        if disabled:
            await manager.save_endpoint(
                {**payload, "expected_version": disabled.version}, request_id, "business"
            )
        await save(original.tools, saved.version, original.endpoint_ids)

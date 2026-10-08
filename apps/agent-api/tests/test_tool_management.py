from dataclasses import replace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from app.clients.bindings import ToolRef
from app.clients.mcp_factory import McpClientFactory
from app.core.errors import AppError
from app.main import create_app
from app.services.chat import ChatService
from app.services.tool_management import ToolManagementService, tool_alias
from tests.fakes import FakeFactory, MemoryBindings, MemoryEndpoints
from tests.test_api import ToolCallingFakeModel, tool_call


def management_client(settings, *, environment="development", host="127.0.0.1"):
    app = create_app(replace(settings, app_env=environment))
    endpoints, factory = MemoryEndpoints(), FakeFactory()
    bindings = MemoryBindings(endpoints=endpoints)
    app.state.tool_management = ToolManagementService(
        bindings, app.state.tool_registry, factory, endpoints
    )
    return TestClient(app, client=(host, 50000)), factory, bindings, endpoints


def test_catalog_save_empty_bindings_and_conflict(settings):
    client, factory, _, _ = management_client(settings)
    assert len(client.get("/api/admin/mcp-endpoints").json()["endpoints"]) == 3
    assert client.get("/api/admin/mcp-endpoints/crm/tools").json()["status"] == "unknown"
    for endpoint_id, count in [("business", 5), ("crm", 2), ("mes", 1)]:
        result = client.post(f"/api/admin/mcp-endpoints/{endpoint_id}/check")
        assert result.status_code == 200, result.json()
        assert len(result.json()["tools"]) == count
    payload = {
        "endpoint_ids": ["mes"],
        "tools": [{"endpoint_id": "mes", "tool_name": "mes.get_work_order_status"}],
        "expected_version": 1,
        "expected_endpoint_versions": {"mes": 1},
    }
    result = client.put("/api/admin/agents/consultant/capabilities", json=payload)
    assert result.status_code == 200, result.json()
    assert result.json()["version"] == 2
    assert client.put("/api/admin/agents/consultant/capabilities", json=payload).status_code == 409
    factory.clients["mes"].fail = True
    result = client.put(
        "/api/admin/agents/consultant/capabilities",
        json={
            "endpoint_ids": [],
            "tools": [],
            "expected_version": 2,
            "expected_endpoint_versions": {},
        },
    )
    assert result.status_code == 200
    assert result.json()["tools"] == []
    assert client.put("/api/admin/agents/consultant/tools", json={}).status_code == 409


def test_rejects_cross_endpoint_unknown_and_incompatible(settings):
    client, factory, _, _ = management_client(settings)
    payload = {
        "endpoint_ids": ["crm"],
        "tools": [{"endpoint_id": "mes", "tool_name": "mes.get_work_order_status"}],
        "expected_version": 1,
        "expected_endpoint_versions": {"crm": 1},
    }
    assert client.put("/api/admin/agents/consultant/capabilities", json=payload).status_code == 400
    factory.clients["crm"].reported_id = "business"
    assert (
        client.post("/api/admin/mcp-endpoints/crm/check").json()["error"]["code"]
        == "ENDPOINT_ID_MISMATCH"
    )
    factory.clients["crm"].reported_id = "crm"
    factory.clients["crm"].fingerprint = "bad"
    assert (
        client.post("/api/admin/mcp-endpoints/crm/check").json()["error"]["code"]
        == "INVALID_TOOL_CATALOG"
    )
    checked = client.get("/api/admin/mcp-endpoints/crm/tools").json()
    assert checked["status"] == "failed"
    assert not any(item["bindable"] for item in checked["tools"])


@pytest.mark.asyncio
async def test_role_ownership_is_enforced_on_binding_prepare_and_call(settings):
    client, factory, bindings, endpoints = management_client(settings)
    manager = client.app.state.tool_management
    # Even an empty Tool set cannot grant another role's connection.
    endpoints.items["mes"] = endpoints.items["mes"].model_copy(update={"role_id": "production"})
    result = client.put(
        "/api/admin/agents/consultant/capabilities",
        json={
            "endpoint_ids": ["mes"],
            "tools": [],
            "expected_version": 1,
            "expected_endpoint_versions": {"mes": 1},
        },
    )
    assert result.status_code == 403
    assert result.json()["error"]["code"] == "FORBIDDEN_ENDPOINT"
    factory.clients["business"].reported_role = "production"
    result = client.post("/api/admin/mcp-endpoints/business/check")
    assert result.json()["error"]["code"] == "ENDPOINT_ROLE_MISMATCH"
    factory.clients["business"].reported_role = "consultant"
    snapshot, _ = await manager.prepare("consultant", uuid4())
    endpoints.items["business"] = endpoints.items["business"].model_copy(
        update={"role_id": "production"}
    )
    with pytest.raises(AppError) as rejected:
        await manager.authorize_call(snapshot, bindings.profile.tools[0], {"customer_id": "C1001"})
    assert rejected.value.code == "FORBIDDEN_ENDPOINT"
    with pytest.raises(AppError) as rejected:
        await manager.prepare("consultant", uuid4())
    assert rejected.value.code == "FORBIDDEN_ENDPOINT"


def test_management_is_local_development_only(settings):
    for kwargs in [{"environment": "production"}, {"host": "203.0.113.1"}]:
        client, _, _, _ = management_client(settings, **kwargs)
        assert client.get("/api/admin/mcp-endpoints").status_code == 403
    client, _, _, _ = management_client(settings)
    assert (
        client.post(
            "/api/admin/mcp-endpoints/crm/check", headers={"Origin": "https://example.com"}
        ).status_code
        == 403
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:3100/mcp",
        "http://169.254.169.254/latest",
        "http://user:pass@127.0.0.1:3100/mcp",
        "http://127.0.0.1:3100/mcp?key=x",
        "http://127.0.0.1:3307/mcp",
    ],
)
def test_forbidden_targets(url):
    endpoint = MemoryEndpoints().items["business"].model_copy(update={"url": url})
    with pytest.raises(AppError) as rejected:
        McpClientFactory().validate(endpoint)
    assert rejected.value.code == "INVALID_ENDPOINT_TARGET"


def test_credential_profile_cannot_read_model_key():
    with pytest.raises(ValueError):
        McpClientFactory(
            {
                "MCP_CREDENTIAL_PROFILES": (
                    '{"bad":{"secret_env":"LLM_API_KEY","endpoint_ids":["business"],"targets":[]}}'
                )
            }
        )


@pytest.mark.asyncio
async def test_same_name_tools_use_independent_sources_and_revoke(settings):
    client, factory, bindings, endpoints = management_client(settings)
    manager = client.app.state.tool_management
    refs = [
        ToolRef(endpoint_id=name, tool_name="crm.get_customer_overview")
        for name in ["business", "crm"]
    ]
    await manager.save(
        "consultant",
        {
            "endpoint_ids": ["business", "crm"],
            "tools": [ref.model_dump() for ref in refs],
            "expected_version": 1,
            "expected_endpoint_versions": {"business": 1, "crm": 1},
        },
        str(uuid4()),
    )
    model = ToolCallingFakeModel(
        responses=[
            tool_call(tool_alias(refs[0])),
            tool_call(tool_alias(refs[1])),
            AIMessage(content="两个来源均查询完成。"),
        ]
    )
    response = await ChatService(model, "fake", manager).reply(
        "查询", role="consultant", actor_id="test", request_id=uuid4()
    )
    assert {call.endpoint_id for call in response.tool_calls} == {"business", "crm"}
    assert len(factory.clients["business"].calls) == len(factory.clients["crm"].calls) == 1
    snapshot, _ = await manager.prepare("consultant", uuid4())
    endpoints.items["crm"] = endpoints.items["crm"].model_copy(
        update={"enabled": False, "execution_revision": 2}
    )
    with pytest.raises(AppError) as error:
        await manager.authorize_call(snapshot, refs[1], {"customer_id": "C1001"})
    assert error.value.code == "BINDING_CHANGED"
    factory.clients["business"].fail = True
    model = ToolCallingFakeModel(responses=[AIMessage(content="你好，没有可用业务连接。")])
    response = await ChatService(model, "fake", manager).reply(
        "你好", role="consultant", actor_id="test", request_id=uuid4()
    )
    assert response.tool_calls == []


@pytest.mark.asyncio
async def test_endpoint_revocation_stops_later_react_iteration(settings):
    client, factory, _, endpoints = management_client(settings)
    manager = client.app.state.tool_management

    async def disable():
        endpoint = endpoints.items["business"]
        endpoints.items["business"] = endpoint.model_copy(
            update={"enabled": False, "execution_revision": endpoint.execution_revision + 1}
        )

    factory.clients["business"].after_call = disable
    model = ToolCallingFakeModel(responses=[tool_call(), tool_call("crm__list_open_tickets")])
    with pytest.raises(AppError) as rejected:
        await ChatService(model, "fake", manager).reply(
            "查询基础信息与工单", role="consultant", actor_id="test", request_id=uuid4()
        )
    assert rejected.value.code == "BINDING_CHANGED"
    assert len(factory.clients["business"].calls) == 1


@pytest.mark.asyncio
async def test_mcp_response_limits_and_redirects():
    import httpx

    from app.clients.mcp import OfficialMcpClient

    for response in [
        httpx.Response(302, headers={"Location": "http://evil.example"}),
        httpx.Response(200, content=b"x" * 262145),
    ]:
        with pytest.raises(ValueError):
            await OfficialMcpClient._limit_response(response)
    response = httpx.Response(200, content=b'{"ok":true}')
    await OfficialMcpClient._limit_response(response)
    assert response.json() == {"ok": True}

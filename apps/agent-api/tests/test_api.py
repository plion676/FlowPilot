from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from openai import BadRequestError
from pydantic import Field

from app.clients.bindings import ToolRef
from app.services.chat import ChatService
from app.services.tool_management import ToolManagementService, tool_alias
from tests.fakes import FakeFactory, MemoryBindings, MemoryEndpoints


class ToolCallingFakeModel(FakeMessagesListChatModel):
    bound_names: list[str] = Field(default_factory=list)

    def bind_tools(self, tools: object, **kwargs: object) -> ToolCallingFakeModel:
        self.bound_names = [tool.name for tool in tools]
        return self


def tool_call(name="crm__get_customer_overview", arguments=None) -> AIMessage:
    if "__" in name and len(name.split("__")[-1]) != 32:
        name = tool_alias(ToolRef(endpoint_id="business", tool_name=name.replace("__", ".")))
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": name,
                "args": arguments if arguments is not None else {"customer_id": "C1001"},
                "id": f"call-{name}",
            }
        ],
    )


def install(client, responses=None, bound_tools=None):
    endpoints, factory = MemoryEndpoints(), FakeFactory()
    mcp, bindings = factory.clients["business"], MemoryBindings(bound_tools, endpoints)
    model = ToolCallingFakeModel(
        responses=responses or [tool_call(), AIMessage(content="查询完成。")]
    )
    manager = ToolManagementService(bindings, client.app.state.tool_registry, factory, endpoints)
    client.app.state.tool_management = manager
    client.app.state.chat_service = ChatService(model, "fake-model", manager)
    return mcp, bindings, model


def test_health_and_safe_configuration_error(client: TestClient) -> None:
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["llm"] == "not_configured"
    assert health.headers["X-Request-ID"]
    response = client.post("/api/chat", json={"message": "你好"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "LLM_NOT_CONFIGURED"
    assert "sk-" not in str(response.json())


def test_agent_receives_role_tools_and_returns_actual_observation(client: TestClient) -> None:
    mcp, _, model = install(client)
    response = client.post("/api/chat", json={"message": "查询客户 C1001 的基础信息"})
    assert response.status_code == 200, response.json()
    body = response.json()
    assert body["route"] == {"kind": "agent", "name": "crm.get_customer_overview"}
    assert body["data"]["risk_level"] == "high"
    assert body["binding_version"] == 1
    assert len(body["tool_calls"]) == 1
    assert set(model.bound_names) == {
        tool_alias(ToolRef(endpoint_id="business", tool_name=name))
        for name in ["crm.get_customer_overview", "crm.list_open_tickets"]
    }
    assert mcp.calls == [("crm.get_customer_overview", {"customer_id": "C1001"})]


@pytest.mark.parametrize("prompt", ["你好", "今天天气如何", "查询客户信息"])
@pytest.mark.parametrize("bound_tools", [[], ["crm.get_customer_overview"]])
def test_normal_reply_and_clarification_need_no_tool(client, prompt, bound_tools) -> None:
    mcp, _, _ = install(client, [AIMessage(content="自然回复或说明信息不足。")], bound_tools)
    response = client.post("/api/chat", json={"message": prompt})
    assert response.status_code == 200
    assert response.json()["message"] == "自然回复或说明信息不足。"
    assert response.json()["route"] == {"kind": "agent"}
    assert response.json()["data"] is None
    assert response.json()["tool_calls"] == []
    assert mcp.calls == []


def test_agent_can_combine_two_tools_without_pre_router(client) -> None:
    mcp, _, _ = install(
        client,
        [
            tool_call(),
            tool_call("crm__list_open_tickets"),
            AIMessage(content="查询完成。"),
        ],
    )
    response = client.post("/api/chat", json={"message": "查看 C1001 的基本信息和未关闭工单"})
    assert response.status_code == 200, response.json()
    assert len(response.json()["tool_calls"]) == 2
    assert len(mcp.calls) == 2


@pytest.mark.parametrize(
    ("name", "args", "code"),
    [
        ("mes__get_work_order_status", {"work_order_id": "WO-1001"}, "FORBIDDEN_TOOL"),
        ("crm__get_customer_overview", {"customer_id": "bad"}, "INVALID_ARGUMENTS"),
        (
            "crm__get_customer_overview",
            {"customer_id": "C1001", "sql": "anything"},
            "INVALID_ARGUMENTS",
        ),
    ],
)
def test_invalid_model_calls_do_not_reach_mcp(client, name, args, code) -> None:
    mcp, _, _ = install(client, [tool_call(name, args)])
    response = client.post("/api/chat", json={"message": "查询"})
    assert response.status_code in {400, 403}, response.json()
    assert response.json()["error"]["code"] == code
    assert mcp.calls == []


def test_bound_mes_is_executable_and_unready_tools_are_not_exposed(client) -> None:
    mcp, _, model = install(
        client,
        [
            tool_call("mes__get_work_order_status", {"work_order_id": "WO-1001"}),
            AIMessage(content="生产中。"),
        ],
        ["mes.get_work_order_status", "knowledge.search_sop", "workflow.create_followup_plan"],
    )
    response = client.post("/api/chat", json={"message": "查询 WO-1001"})
    assert response.status_code == 200, response.json()
    assert set(model.bound_names) == {
        tool_alias(ToolRef(endpoint_id="business", tool_name=name))
        for name in ["mes.get_work_order_status", "knowledge.search_sop"]
    }
    assert len(mcp.calls) == 1


def test_live_unbinding_stops_next_call_in_same_agent_loop(client) -> None:
    mcp, bindings, _ = install(client, [tool_call(), tool_call("crm__list_open_tickets")])

    async def revoke():
        await bindings.save(
            "consultant", {"endpoint_ids": [], "tools": [], "expected_version": 1}, "test-request"
        )

    mcp.after_call = revoke
    response = client.post("/api/chat", json={"message": "查询 C1001 的基础信息和工单"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "BINDING_CHANGED"
    assert len(mcp.calls) == 1


def test_unknown_role_does_not_reach_model_or_mcp(client) -> None:
    mcp, _, model = install(client)
    response = client.post("/api/chat", headers={"X-Demo-Role": "admin"}, json={"message": "你好"})
    assert response.status_code == 403
    assert model.i == 0
    assert mcp.calls == []


def test_provider_400_is_redacted(client, monkeypatch) -> None:
    install(client)

    async def reject(*args, **kwargs):
        raise BadRequestError(
            message="sensitive detail",
            response=httpx.Response(400, request=httpx.Request("POST", "https://api.deepseek.com")),
            body={"error": "sensitive detail"},
        )

    monkeypatch.setattr("app.services.chat.ReActAgentRunner.invoke", reject)
    response = client.post("/api/chat", json={"message": "你好"})
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "LLM_BAD_REQUEST"
    assert "sensitive detail" not in str(response.json())

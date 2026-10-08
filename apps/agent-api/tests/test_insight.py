import json
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage

from app.clients.bindings import ToolRef
from app.core.errors import AppError
from app.services.chat import ChatService
from app.services.tool_management import tool_alias
from tests.test_api import install, tool_call
from tests.test_rag import match_and_source

TOOLS = [
    "crm.get_customer_overview",
    "crm.list_open_tickets",
    "knowledge.search_sop",
    "mes.get_work_order_status",
]


def responses(*, activate=False, final=None):
    _, source = match_and_source()
    messages = [
        tool_call(),
        tool_call("crm__list_open_tickets"),
        tool_call("knowledge__search_sop", {"query": "高风险客户临近续费和未关闭工单", "top_k": 3}),
        AIMessage(
            content=final
            or json.dumps(
                {"answer": f"建议先核实工单与续费意愿。[[{source}]]", "sources": [source]},
                ensure_ascii=False,
            )
        ),
    ]
    if activate:
        messages.insert(0, tool_call("activate_skill", {"skill_name": "crm.customer_insight"}))
    return messages


@pytest.mark.parametrize("explicit", [True, False])
def test_same_react_supports_explicit_and_model_selected_skill(client, explicit):
    mcp, _, model = install(client, responses(activate=not explicit), TOOLS)
    payload = {"message": "结合 C1001 的风险和工单给出 SOP 建议"}
    if explicit:
        payload["skill_name"] = "crm.customer_insight"
    response = client.post("/api/chat", json=payload)
    assert response.status_code == 200, response.json()
    data = response.json()
    assert data["skill_name"] == "crm.customer_insight"
    assert data["route"]["kind"] == "agent"
    assert len(data["citations"]) == 1
    assert "[1]" in data["message"]
    assert len(mcp.calls) == 3
    assert len(model.bound_names) == 3
    assert (
        tool_alias(ToolRef(endpoint_id="business", tool_name="mes.get_work_order_status"))
        not in model.bound_names
    )


def test_simple_sop_search_bypasses_skill(client):
    install(client, responses()[2:], ["knowledge.search_sop"])
    response = client.post("/api/chat", json={"message": "查询高风险客户 SOP"})
    assert response.status_code == 200
    assert response.json()["skill_name"] is None
    assert len(response.json()["tool_calls"]) == 1
    assert len(response.json()["citations"]) == 1


def test_skill_never_grants_unbound_tools(client):
    install(client, responses(), ["crm.get_customer_overview"])
    response = client.post(
        "/api/chat", json={"message": "洞察", "skill_name": "crm.customer_insight"}
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "SKILL_TOOLS_UNAVAILABLE"


def test_skill_rejects_model_call_outside_minimal_set(client):
    mcp, _, _ = install(
        client, [tool_call("mes__get_work_order_status", {"work_order_id": "WO-1001"})], TOOLS
    )
    response = client.post(
        "/api/chat", json={"message": "洞察", "skill_name": "crm.customer_insight"}
    )
    assert response.status_code == 403
    assert mcp.calls == []


def test_forged_reference_is_not_returned_as_successful_advice(client):
    install(client, responses(final='{"answer":"凭空建议[[fake]]","sources":["fake"]}'), TOOLS)
    response = client.post(
        "/api/chat", json={"message": "洞察", "skill_name": "crm.customer_insight"}
    )
    assert response.status_code == 200
    assert "没有通过引用校验" in response.json()["message"]
    assert response.json()["citations"] == []
    assert "凭空建议" not in response.json()["message"]


@pytest.mark.asyncio
async def test_empty_or_offline_sop_only_returns_facts_and_limits(client):
    mcp, _, model = install(client, responses(), TOOLS)
    original_call = mcp.call

    async def empty(binding, name, arguments, *, actor_id):
        if name == "knowledge.search_sop":
            return {"matches": []}
        return await original_call(binding, name, arguments, actor_id=actor_id)

    mcp.call = empty
    result = client.post(
        "/api/chat", json={"message": "洞察", "skill_name": "crm.customer_insight"}
    )
    assert "没有可引用" in result.json()["message"]
    assert result.json()["citations"] == []

    async def offline(binding, name, arguments, *, actor_id):
        if name == "knowledge.search_sop":
            raise AppError("DEPENDENCY_UNAVAILABLE", "offline", 503)
        return await original_call(binding, name, arguments, actor_id=actor_id)

    mcp.call = offline
    manager = client.app.state.tool_management
    response = await ChatService(model, "fake", manager).reply(
        "洞察",
        role="consultant",
        actor_id="test",
        request_id=uuid4(),
        skill_name="crm.customer_insight",
    )
    assert "暂不可用" in response.message
    assert response.citations == []

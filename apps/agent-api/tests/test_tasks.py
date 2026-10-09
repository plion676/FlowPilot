import json
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage

from app.agent.skills import FOLLOWUP
from app.core.errors import AppError
from app.services.chat import ChatService
from app.tasks.service import TaskService
from app.tasks.worker import TaskWorker
from tests.test_api import install, tool_call
from tests.test_rag import match_and_source

TOOLS = [
    "crm.get_customer_overview",
    "crm.list_open_tickets",
    "knowledge.search_sop",
    "workflow.create_followup_plan",
]


class MemoryTaskClient:
    def __init__(self):
        self.calls = []

    async def request(self, method, path, payload=None, params=None):
        self.calls.append((path, payload))
        return {"task_id": str(uuid4()), "status": "created", "version": 1}


def test_followup_creation_still_uses_main_react_and_does_not_write_crm(client):
    mcp, _, model = install(
        client,
        [tool_call("activate_skill", {"skill_name": FOLLOWUP}), AIMessage(content="任务已安排。")],
        TOOLS,
    )
    store = MemoryTaskClient()
    service = TaskService(store, client.app.state.tool_management)
    client.app.state.chat_service = ChatService(
        model, "fake", client.app.state.tool_management, task_service=service
    )
    result = client.post("/api/chat", json={"message": "生成回访计划", "skill_name": FOLLOWUP})
    assert result.status_code == 200, result.json()
    assert result.json()["task"]["status"] == "created"
    assert "尚未创建正式" in result.json()["message"]
    assert mcp.calls == []
    assert not any("workflow" in name for name in model.bound_names)
    assert len(store.calls) == 1


@pytest.mark.asyncio
async def test_missing_workflow_binding_never_creates_task(client):
    install(client, [], TOOLS[:3])
    store = MemoryTaskClient()
    with pytest.raises(AppError) as rejected:
        await TaskService(store, client.app.state.tool_management).submit("consultant", "test")
    assert rejected.value.code == "SKILL_TOOLS_UNAVAILABLE"
    assert store.calls == []


def setup_worker(client, *, empty=False, invalid_commit=False):
    task = {
        "task_id": str(uuid4()),
        "window_start": "2026-09-30",
        "window_end": "2026-10-07",
        "role": "consultant",
        "actor_id": "test",
        "endpoint_id": "business",
        "status": "gathering",
    }
    proposal = {
        "operation": "propose",
        "task_id": task["task_id"],
        "window_start": task["window_start"],
        "window_end": task["window_end"],
    }
    _, source = match_and_source()
    responses = [tool_call("workflow__create_followup_plan", proposal)]
    if invalid_commit:
        responses = [
            tool_call(
                "workflow__create_followup_plan",
                {"operation": "commit", "task_id": task["task_id"]},
            )
        ]
    elif not empty:
        responses += [
            tool_call(),
            tool_call("crm__list_open_tickets"),
            tool_call("knowledge__search_sop", {"query": "续费风险跟进", "top_k": 3}),
        ]
    responses.append(
        AIMessage(
            content="没有候选。"
            if empty
            else json.dumps({"answer": f"核实续费意向与工单。[[{source}]]", "sources": [source]})
        )
    )
    mcp, _, model = install(client, responses, TOOLS)
    original = mcp.call

    async def call(binding, name, args, *, actor_id):
        if name == "workflow.create_followup_plan":
            mcp.calls.append((name, args))
            assert binding.task_grant is not None
            return {
                "task_id": task["task_id"],
                "candidates": []
                if empty
                else [
                    {"customer_code": "C1001", "risk_level": "high", "renewal_date": "2026-10-05"}
                ],
            }
        return await original(binding, name, args, actor_id=actor_id)

    mcp.call = call
    store = MemoryTaskClient()
    worker = TaskWorker(store, client.app.state.tool_management, model)
    grant = {"task_id": task["task_id"], "version": 2, "lease_token": "a" * 64}
    return worker, task, grant, mcp, store


@pytest.mark.asyncio
async def test_proposal_agent_four_tools_and_citations(client):
    worker, task, grant, mcp, store = setup_worker(client)
    await worker.process({"task": task, "grant": grant})
    assert len(mcp.calls) == 4
    result = store.calls[-1][1]
    assert result["error_code"] == ""
    assert result["proposal"]["citations"]
    assert "[1]" in result["proposal"]["analysis"]
    assert all(args.get("operation") != "commit" for _, args in mcp.calls)


@pytest.mark.asyncio
async def test_empty_proposal_and_model_commit_rejection(client):
    worker, task, grant, mcp, store = setup_worker(client, empty=True)
    await worker.process({"task": task, "grant": grant})
    assert store.calls[-1][1]["proposal"]["candidates"] == []
    worker, task, grant, mcp, store = setup_worker(client, invalid_commit=True)
    await worker.process({"task": task, "grant": grant})
    assert mcp.calls == []
    assert store.calls[-1][1]["error_code"] == "INVALID_ARGUMENTS"


def test_task_api_requires_local_demo_identity_and_strict_decision(client):
    result = client.post(
        f"/api/tasks/{uuid4()}/decision",
        json={
            "decision": "approve",
            "idempotency_key": str(uuid4()),
            "expected_version": 2,
            "role": "consultant",
        },
    )
    assert result.status_code == 422
    result = client.get("/api/tasks", headers={"Origin": "https://malicious.example"})
    assert result.status_code == 403

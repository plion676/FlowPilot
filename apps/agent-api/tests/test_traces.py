import asyncio
import json

import pytest
from langchain_core.messages import AIMessage
from langchain_core.tools import tool

from app.agent.react import ReActAgentRunner
from app.core.errors import AppError
from app.traces.recorder import Redactor, TraceRecorder, visible_content
from tests.test_api import ToolCallingFakeModel, install, tool_call
from tests.test_tool_management import management_client


class MemoryTraceClient:
    def __init__(self):
        self.runs = {}
        self.events = {}
        self.fail = False

    async def request(self, method, path="", payload=None, params=None):
        if self.fail:
            raise RuntimeError("private dependency diagnostic")
        if method == "POST" and path == "":
            run = {**payload, "status": "running", "last_sequence": 0}
            self.runs[run["trace_id"]] = run
            self.events[run["trace_id"]] = []
            return run
        if method == "GET" and not path:
            return {"traces": list(self.runs.values()), "has_more": False}
        trace_id = path.split("/")[1]
        run = self.runs.get(trace_id)
        identity = payload or params
        if not run or any(run[k] != identity[k] for k in ["role", "actor_id"]):
            raise AppError("NOT_FOUND", "Trace 不存在", 404)
        if method == "POST" and path.endswith("/events"):
            event = {**payload, "sequence": len(self.events[trace_id]) + 1}
            self.events[trace_id].append(event)
            run["last_sequence"] = event["sequence"]
            return event
        if method == "POST":
            run.update(payload)
            return run
        return {
            "trace": run,
            "events": [
                e
                for e in self.events[trace_id]
                if e["sequence"] > int(params.get("after_sequence", 0))
            ],
            "has_more": False,
        }


def trace_client(settings, responses=None):
    client, *_ = management_client(settings)
    repo = MemoryTraceClient()
    client.app.state.trace_client = repo
    install(client, responses)
    return client, repo


def test_chat_trace_actual_order_and_incremental_query(settings):
    client, repo = trace_client(settings)
    result = client.post("/api/chat", json={"message": "查询 C1001"})
    assert result.status_code == 200
    trace_id = result.json()["trace_id"]
    assert result.headers["X-Trace-ID"] == trace_id
    body = client.get(f"/api/traces/{trace_id}").json()
    events = body["events"]
    assert [e["kind"] for e in events] == [
        "user_message",
        "system_message",
        "model_start",
        "assistant_message",
        "tool_call",
        "tool_start",
        "tool_result",
        "system_message",
        "model_start",
        "assistant_message",
        "final_response",
    ]
    assert events[3]["payload"]["content"] == ""
    assert events[4]["payload"]["name"] == "crm.get_customer_overview"
    assert events[4]["call_id"] == events[6]["call_id"]
    assert events[6]["payload"]["content"]["risk_level"] == "high"
    assert body["trace"]["status"] == "succeeded"
    assert (
        client.get(f"/api/traces/{trace_id}?after_sequence=10").json()["events"][0]["kind"]
        == "final_response"
    )
    assert (
        client.get(
            f"/api/traces/{trace_id}", headers={"X-Demo-Role": "customer_service"}
        ).status_code
        == 404
    )
    assert len(repo.events[trace_id]) == 11


def test_greeting_failure_policy_denial_and_local_gate(settings):
    client, repo = trace_client(settings, [AIMessage(content="你好！")])
    response = client.post("/api/chat", json={"message": "你好"})
    events = repo.events[response.json()["trace_id"]]
    assert not any(e["kind"].startswith("tool_") for e in events)
    assert events[-1]["payload"]["content"] == "你好！"
    install(client, [tool_call(arguments={"customer_id": "bad"})])
    response = client.post("/api/chat", json={"message": "查询客户"})
    assert response.status_code == 400
    id_ = response.headers["X-Trace-ID"]
    events = repo.events[id_]
    assert events[-1]["kind"] == "tool_rejected"
    assert events[-1]["payload"]["executed"] is False
    assert not any(e["kind"] == "tool_start" for e in events)
    assert repo.runs[id_]["status"] == "failed"
    assert client.get("/api/traces", headers={"Origin": "https://evil.example"}).status_code == 403
    remote, *_ = management_client(settings, host="10.0.0.1")
    assert remote.get("/api/traces").status_code == 403
    production, *_ = management_client(settings, environment="production")
    assert production.get("/api/traces").status_code == 403


def test_trace_failure_does_not_replace_business_result(settings):
    client, repo = trace_client(settings)
    repo.fail = True
    result = client.post("/api/chat", json={"message": "查询 C1001"})
    assert result.status_code == 200
    assert result.json()["trace_id"] is None
    assert result.json()["trace_incomplete"] is True
    assert result.json()["data"]["customer_code"] == "C1001"


def test_skill_binding_changes_raw_answer_and_followup_task_link(settings):
    from tests.test_insight import TOOLS, responses

    client, repo = trace_client(settings)
    install(client, responses(activate=True), TOOLS)
    response = client.post("/api/chat", json={"message": "洞察 C1001"})
    assert response.status_code == 200
    events = repo.events[response.json()["trace_id"]]
    control = next(e for e in events if e["kind"] == "tool_call")
    assert control["payload"]["source"] == "local_control"
    sets = [e["payload"]["tools"] for e in events if e["kind"] == "model_start"]
    assert len(sets[0]) == 5 and len(sets[1]) == 3
    raw = [e for e in events if e["kind"] == "assistant_message"][-1]
    assert isinstance(raw["payload"]["content"], dict)
    assert events[-1]["payload"]["content"] == response.json()["message"]
    assert events[-1]["payload"]["content"] != raw["payload"]["content"]

    class TestTasks:
        async def submit(self, role, actor, request_id):
            return {"task_id": "11111111-1111-4111-8111-111111111111", "status": "queued"}

    install(
        client,
        [
            tool_call("activate_skill", {"skill_name": "crm.followup_workflow"}),
            AIMessage(content="已创建草案任务。"),
        ],
        TOOLS + ["workflow.create_followup_plan"],
    )
    client.app.state.chat_service._task_service = TestTasks()
    response = client.post("/api/chat", json={"message": "创建回访提案"})
    assert response.status_code == 200
    events = repo.events[response.json()["trace_id"]]
    assert events[-1]["payload"]["task"]["status"] == "queued"
    assert [e["payload"]["tools"] for e in events if e["kind"] == "model_start"][-1] == []
    assert all(e["payload"].get("name") != "workflow.create_followup_plan" for e in events)


def test_redaction_and_size_limits():
    redactor = Redactor(["known-private-value"])
    payload = redactor.payload(
        {
            "content": '{"lease_token":"private-lease","nested":{"api_key":"private-key"}}',
            "text": "Bearer private-bearer known-private-value token=private-text sk-abcdefgh12345",
            "headers": {"Authorization": "private"},
            "reasoning_content": "private reasoning",
        }
    )
    encoded = json.dumps(payload)
    assert "private" not in encoded
    assert "[REDACTED]" in encoded
    big = redactor.payload({"content": "中" * 10000})
    assert big["truncated"] is True
    assert len(json.dumps(big, ensure_ascii=False).encode()) < 16384
    assert (
        visible_content(
            [{"type": "reasoning", "text": "hidden"}, {"type": "text", "text": "visible"}]
        )
        == "visible"
    )


@pytest.mark.asyncio
async def test_parallel_completion_order_and_empty_assistant():
    @tool
    async def lookup(delay: float) -> str:
        """Read deterministic test observation."""
        await asyncio.sleep(delay)
        return str(delay)

    model = ToolCallingFakeModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "lookup", "args": {"delay": 0.05}, "id": "slow"},
                    {"name": "lookup", "args": {"delay": 0}, "id": "fast"},
                ],
            ),
            AIMessage(content="完成"),
        ]
    )
    repo = MemoryTraceClient()
    trace = TraceRecorder(repo, "request", "consultant", "actor", "fake")
    await trace.start("两个查询")
    result = await ReActAgentRunner(model, [lookup], trace=trace).invoke("两个查询")
    await trace.finish("succeeded")
    events = repo.events[trace.id]
    assert result == "完成"
    assert [e["call_id"] for e in events if e["kind"] == "tool_result"] == ["fast", "slow"]
    assert [e["call_id"] for e in events if e["kind"] == "tool_call"] == ["slow", "fast"]
    assert all(e["step"] == 1 for e in events if e["kind"].startswith("tool_"))


@pytest.mark.asyncio
async def test_model_and_tool_errors_and_capture_gap():
    class BrokenModel(ToolCallingFakeModel):
        async def _agenerate(self, *args, **kwargs):
            raise RuntimeError("must not expose raw provider diagnostics")

    repo = MemoryTraceClient()
    trace = TraceRecorder(repo, "request", "consultant", "actor", "fake")
    await trace.start("hello")
    with pytest.raises(RuntimeError):
        await ReActAgentRunner(
            BrokenModel(responses=[AIMessage(content="unused")]), [], trace=trace
        ).invoke("hello")
    assert repo.events[trace.id][-1]["kind"] == "model_error"
    assert "diagnostics" not in json.dumps(repo.events)

    @tool
    async def broken() -> str:
        """Test-only failure."""
        raise AppError("DEPENDENCY_UNAVAILABLE", "private tool diagnostic", 503)

    trace2 = TraceRecorder(repo, "request", "consultant", "actor", "fake")
    await trace2.start("tool")
    model = ToolCallingFakeModel(
        responses=[AIMessage(content="", tool_calls=[{"name": "broken", "args": {}, "id": "call"}])]
    )
    with pytest.raises(AppError):
        await ReActAgentRunner(model, [broken], trace=trace2).invoke("tool")
    assert repo.events[trace2.id][-1]["kind"] == "tool_error"
    assert "private tool diagnostic" not in json.dumps(repo.events)
    repo.fail = True
    await trace2.emit("assistant_message", {"content": "gap"})
    repo.fail = False
    await trace2.finish("failed", "DEPENDENCY_UNAVAILABLE")
    assert repo.runs[trace2.id]["incomplete"] is True

"""Opt-in fixed model -> actual ReAct/MCP/Go/MySQL. No binding or CRM writes."""

import os
from uuid import uuid4

import httpx
import pytest
from langchain_core.messages import AIMessage

from app.core.config import Settings
from app.main import create_app
from app.services.chat import ChatService
from tests.test_api import ToolCallingFakeModel, tool_call


@pytest.mark.asyncio
async def test_persisted_trace_real_mcp_mysql_and_fresh_api():
    if not os.getenv("OPSPILOT_TEST_BUSINESS_URL"):
        pytest.skip("Run scripts/dev.py verify-trace with local project services")
    settings = Settings.from_env()
    app = create_app(settings)
    manager = app.state.tool_management
    snapshot, catalog = await manager.prepare("consultant", uuid4())
    assert any(i["name"] == "crm.get_customer_overview" and i["available"] for i in catalog)
    original_version = snapshot.binding_version
    cases = [
        ([AIMessage(content="你好！")], "你好", "succeeded"),
        ([tool_call(), AIMessage(content="已依据工具查询到 C1001。")], "查询 C1001", "succeeded"),
        ([tool_call(arguments={"customer_id": "invalid"})], "无效参数测试", "failed"),
        ([tool_call(arguments={"customer_id": "C99999999"})], "不存在的模拟客户", "failed"),
    ]
    ids = []
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
    ) as client:
        for responses, message, status in cases:
            app.state.chat_service = ChatService(
                ToolCallingFakeModel(responses=responses), "fixed-trace-test", manager
            )
            response = await client.post("/api/chat", json={"message": message})
            assert (
                response.status_code == 200
                if status == "succeeded"
                else response.status_code >= 400
            )
            trace_id = response.headers["X-Trace-ID"]
            ids.append(trace_id)
            detail = (await client.get(f"/api/traces/{trace_id}")).json()
            assert detail["trace"]["status"] == status
            assert detail["trace"]["incomplete"] is False
            events = detail["events"]
            assert [e["sequence"] for e in events] == list(range(1, len(events) + 1))
            if message == "查询 C1001":
                observation = next(e for e in events if e["kind"] == "tool_result")
                assert observation["payload"]["content"]["customer_code"] == "C1001"
                assert observation["payload"]["endpoint_revision"] > 0
            elif message == "无效参数测试":
                assert events[-1]["kind"] == "tool_rejected"
                assert not any(e["kind"] == "tool_start" for e in events)
            elif status == "failed":
                assert events[-1]["kind"] == "tool_error"
            assert (
                await client.get(f"/api/traces/{trace_id}", headers={"X-Demo-Role": "production"})
            ).status_code == 404
    # A new API instance reads the persisted rows, not in-process chat state.
    fresh = create_app(settings)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fresh), base_url="http://127.0.0.1"
    ) as client:
        for id_ in ids:
            assert (await client.get(f"/api/traces/{id_}")).json()["events"]
    current, _ = await manager.prepare("consultant", uuid4())
    assert current.binding_version == original_version
    print("Persisted trace IDs:", ", ".join(ids))

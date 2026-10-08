"""Opt-in real ReAct -> MCP -> Go/MySQL + Qdrant; restores bindings with CAS."""

import json
import os
from dataclasses import replace
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage

from app.clients.bindings import HttpBindingStore, ToolRef
from app.clients.endpoints import HttpEndpointStore
from app.clients.llm import LangChainChatClient
from app.clients.mcp_factory import McpClientFactory
from app.core.config import Settings
from app.core.errors import AppError
from app.services.chat import ChatService
from app.services.tool_management import ToolManagementService, tool_alias
from app.tools.registry import default_tool_registry
from tests.test_api import ToolCallingFakeModel, tool_call


@pytest.mark.asyncio
@pytest.mark.parametrize("live_model", [False, True], ids=["fixed-model", "live-model"])
async def test_real_customer_insight_and_skill_scope(live_model, monkeypatch):
    names = [
        "OPSPILOT_TEST_MCP_URL",
        "OPSPILOT_TEST_MCP_SECRET",
        "OPSPILOT_TEST_BUSINESS_URL",
        "OPSPILOT_TEST_SERVICE_TOKEN",
    ]
    if not all(os.getenv(name) for name in names):
        pytest.skip("Run scripts/dev.py verify-insight against the local demo services")
    if live_model and os.getenv("OPSPILOT_TEST_LIVE_MODEL") != "1":
        pytest.skip("Set OPSPILOT_TEST_LIVE_MODEL=1 to make paid model requests")
    store = HttpBindingStore(os.environ[names[2]], os.environ[names[3]])
    endpoints = HttpEndpointStore(store)
    env = dict(os.environ)
    env.update(MCP_URL=os.environ[names[0]], MCP_CALL_SECRET=os.environ[names[1]])
    factory = McpClientFactory(env)
    manager = ToolManagementService(store, default_tool_registry(), factory, endpoints)
    request_id = str(uuid4())
    original = await store.get("consultant", request_id)
    await manager.check("business", request_id)

    async def save(refs, version, endpoint_ids):
        return await manager.save(
            "consultant",
            {
                "endpoint_ids": endpoint_ids,
                "tools": [ref.model_dump() for ref in refs],
                "expected_version": version,
                "expected_endpoint_versions": {
                    name: (await endpoints.get(name, request_id)).version for name in endpoint_ids
                },
            },
            request_id,
        )

    refs = [
        ToolRef(endpoint_id="business", tool_name=name)
        for name in [
            "crm.get_customer_overview",
            "crm.list_open_tickets",
            "knowledge.search_sop",
            "mes.get_work_order_status",
        ]
    ]
    saved = await save(refs, original.version, ["business"])
    try:
        snapshot, _ = await manager.prepare("consultant", uuid4())
        query = {"query": "高风险客户临近续费和未关闭工单", "top_k": 3}
        _, scoped, endpoint = await manager.authorize_call(
            snapshot, refs[2], query, skill_name="crm.customer_insight"
        )
        assert len(scoped.tool_names) == 3
        client = factory.for_endpoint(endpoint)
        retrieved = await client.call(scoped, "knowledge.search_sop", query, actor_id="test")
        assert retrieved["matches"]
        source = "business:" + retrieved["matches"][0]["chunk_id"]
        model = ToolCallingFakeModel(
            responses=[
                tool_call(tool_alias(refs[0])),
                tool_call(tool_alias(refs[1])),
                tool_call(tool_alias(refs[2]), query),
                AIMessage(
                    content=json.dumps(
                        {
                            "answer": f"建议核实客户风险、工单影响与续费意愿。[[{source}]]",
                            "sources": [source],
                        },
                        ensure_ascii=False,
                    )
                ),
            ]
        )
        model_name = "integration-fixed-model"
        raw_answers = []
        if live_model:
            from app.agent.citations import EvidenceLedger

            validate = EvidenceLedger.validate

            def capture(self, raw):
                raw_answers.append(raw)
                return validate(self, raw)

            monkeypatch.setattr(EvidenceLedger, "validate", capture)
            provider = LangChainChatClient(Settings.from_env())
            model, model_name = provider.chat_model, provider.model_name
        response = await ChatService(model, model_name, manager).reply(
            "结合 C1001 的工单、续费风险和 SOP 给出跟进建议",
            role="consultant",
            actor_id="test",
            request_id=uuid4(),
            skill_name="crm.customer_insight",
        )
        assert {call.name for call in response.tool_calls} == {ref.tool_name for ref in refs[:3]}
        overview = next(call for call in response.tool_calls if call.name == refs[0].tool_name)
        assert overview.result["risk_level"] == "high"
        assert response.citations, raw_answers
        from app.rag.corpus import Corpus

        corpus = Corpus()
        for citation in response.citations:
            assert citation["excerpt"] == corpus.chunks[citation["chunk_id"]].excerpt
        assert "[1]" in response.message
        with pytest.raises(AppError) as rejected:
            await client.call(
                replace(scoped, tool_names=frozenset(ref.tool_name for ref in refs)),
                "mes.get_work_order_status",
                {"work_order_id": "WO-1001"},
                actor_id="test",
            )
        assert rejected.value.code == "FORBIDDEN_TOOL"
        saved = await save(refs[:2], saved.version, ["business"])
        with pytest.raises(AppError) as rejected:
            await client.call(scoped, "knowledge.search_sop", query, actor_id="test")
        assert rejected.value.code == "BINDING_CHANGED"
    finally:
        await save(original.tools, saved.version, original.endpoint_ids)

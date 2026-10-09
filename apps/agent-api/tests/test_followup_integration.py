"""Opt-in real HTTP/ReAct/MCP/MySQL/Qdrant loop; approvals are test-human actions.

Run serially with TASK_WORKER_ENABLED=0. Leaves clearly simulated task/audit records,
restores exact original role bindings using CAS, and never contacts real customers.
"""

import json
import os
import subprocess
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from langchain_core.messages import AIMessage

from app.agent.skills import FOLLOWUP
from app.clients.bindings import HttpBindingStore, ToolRef
from app.clients.endpoints import HttpEndpointStore
from app.clients.mcp_factory import McpClientFactory
from app.core.errors import AppError
from app.services.tool_management import ToolManagementService, tool_alias
from app.tasks.client import TaskClient
from app.tasks.worker import TaskWorker
from app.tools.registry import default_tool_registry
from tests.test_api import ToolCallingFakeModel, tool_call


@pytest.mark.asyncio
async def test_real_followup_approval_commit_and_cancel():
    required = [
        "OPSPILOT_TEST_MCP_URL",
        "OPSPILOT_TEST_MCP_SECRET",
        "OPSPILOT_TEST_BUSINESS_URL",
        "OPSPILOT_TEST_SERVICE_TOKEN",
    ]
    if not all(os.getenv(name) for name in required):
        pytest.skip("Run scripts/dev.py verify-followup; Agent worker must be disabled")
    if os.getenv("TASK_WORKER_ENABLED") != "0":
        pytest.skip("Stop the background Worker and set TASK_WORKER_ENABLED=0")
    url, token = os.environ[required[2]], os.environ[required[3]]
    store = HttpBindingStore(url, token)
    endpoints = HttpEndpointStore(store)
    manager = ToolManagementService(store, default_tool_registry(), McpClientFactory(), endpoints)
    tasks = TaskClient(url, token)
    request = str(uuid4())
    original = await store.get("consultant", request)
    refs = [
        ToolRef(endpoint_id="business", tool_name=name)
        for name in [
            "crm.get_customer_overview",
            "crm.list_open_tickets",
            "knowledge.search_sop",
            "workflow.create_followup_plan",
        ]
    ]

    async def save(tools, version, sources):
        return await manager.save(
            "consultant",
            {
                "tools": [ref.model_dump() for ref in tools],
                "endpoint_ids": sources,
                "expected_version": version,
                "expected_endpoint_versions": {
                    name: (await endpoints.get(name, request)).version for name in sources
                },
            },
            request,
        )

    def plan_count(task_id):
        project = Path(__file__).resolve().parents[2] / "business-service"
        inspected = subprocess.run(
            ["go", "run", "./cmd/inspect-task", task_id],
            cwd=project,
            capture_output=True,
            text=True,
            check=False,
        )
        assert inspected.returncode == 0, "read-only MySQL plan verification failed"
        return json.loads(inspected.stdout)["plan_count"]

    saved = await save(refs, original.version, ["business"])
    ids = []
    try:
        async with httpx.AsyncClient(
            base_url="http://127.0.0.1:8000", trust_env=False, timeout=30
        ) as api:
            # A fixed model drives ReAct; all tool observations and persistence are real.
            payload = {"idempotency_key": str(uuid4())}
            created = await api.post("/api/tasks", json=payload)
            assert created.status_code == 200, "public task creation failed"
            task_id = created.json()["task_id"]
            ids.append(task_id)
            replay = await api.post("/api/tasks", json=payload)
            assert replay.json()["task_id"] == task_id
            claimed = await tasks.request("POST", "tasks/claim", {})
            assert claimed["task"]["task_id"] == task_id, (
                "Another worker/task is active; run serially"
            )
            task, grant = claimed["task"], claimed["grant"]
            snapshot, _ = await manager.prepare("consultant", uuid4())
            propose = {
                "operation": "propose",
                "task_id": task_id,
                "window_start": task["window_start"],
                "window_end": task["window_end"],
            }

            async def invoke(ref, args):
                args, binding, endpoint = await manager.authorize_call(
                    snapshot, ref, args, skill_name=FOLLOWUP, task_grant=grant
                )
                return await manager.factory.for_endpoint(endpoint).call(
                    binding, ref.tool_name, args, actor_id="demo-consultant"
                )

            candidates = (await invoke(refs[3], propose))["candidates"]
            assert candidates, (
                "Run scripts/dev.py seed-followup to add a current-date simulated customer"
            )
            with pytest.raises(AppError):
                await invoke(refs[3], {"operation": "commit", "task_id": task_id})
            assert plan_count(task_id) == 0
            query = {"query": "高风险客户临近续费和未关闭工单", "top_k": 3}
            evidence = await invoke(refs[2], query)
            assert evidence["matches"], "SOP index must be ready"
            source = "business:" + evidence["matches"][0]["chunk_id"]
            responses = [tool_call(tool_alias(refs[3]), propose)]
            for candidate in candidates:
                for ref in refs[:2]:
                    responses.append(
                        tool_call(tool_alias(ref), {"customer_id": candidate["customer_code"]})
                    )
            responses += [
                tool_call(tool_alias(refs[2]), query),
                AIMessage(
                    content=json.dumps(
                        {
                            "answer": f"建议核实客户工单影响和续费意向。[[{source}]]",
                            "sources": [source],
                        }
                    )
                ),
            ]
            worker = TaskWorker(tasks, manager, ToolCallingFakeModel(responses=responses))
            await worker.process(claimed)
            pending = (await api.get(f"/api/tasks/{task_id}")).json()
            assert pending["status"] == "pending_approval", "proposal did not reach approval"
            assert pending["proposal"]["citations"]
            assert plan_count(task_id) == 0
            public_text = json.dumps(pending)
            assert "lease_token" not in public_text and "lease_hash" not in public_text
            decision = {
                "decision": "approve",
                "expected_version": pending["version"],
                "idempotency_key": str(uuid4()),
            }
            approved = await api.post(f"/api/tasks/{task_id}/decision", json=decision)
            assert approved.status_code == 200
            duplicate = await api.post(f"/api/tasks/{task_id}/decision", json=decision)
            assert duplicate.json()["version"] == approved.json()["version"]
            await worker.process(await tasks.request("POST", "tasks/claim", {}))
            finished = (await api.get(f"/api/tasks/{task_id}")).json()
            assert finished["status"] == "completed", "approved commit failed"
            assert plan_count(task_id) == len(candidates) == finished["result"]["plan_count"]
            await api.post(f"/api/tasks/{task_id}/decision", json=decision)
            assert plan_count(task_id) == len(candidates)
            audit = (await api.get(f"/api/tasks/{task_id}/audit")).json()
            events = {event["event"] for event in audit["events"]}
            assert {"created", "approve", "crm_committed"} <= events
            assert "lease_token" not in json.dumps(audit)
            # Cancellation permanently revokes an already issued worker lease.
            cancelled = await api.post("/api/tasks", json={"idempotency_key": str(uuid4())})
            cancel_id = cancelled.json()["task_id"]
            ids.append(cancel_id)
            active = await tasks.request("POST", "tasks/claim", {})
            response = await api.post(
                f"/api/tasks/{cancel_id}/decision",
                json={
                    "decision": "cancel",
                    "expected_version": active["task"]["version"],
                    "idempotency_key": str(uuid4()),
                },
            )
            assert response.json()["status"] == "cancelled"
            with pytest.raises(AppError):
                await tasks.request("POST", "tasks/heartbeat", active["grant"])
            assert plan_count(cancel_id) == 0
    finally:
        for task_id in ids:
            current = await tasks.get(task_id, "consultant", "demo-consultant")
            if current["status"] in {"created", "gathering", "pending_approval", "approved"}:
                await tasks.decide(
                    task_id,
                    "consultant",
                    "demo-consultant",
                    {
                        "decision": "cancel",
                        "expected_version": current["version"],
                        "idempotency_key": str(uuid4()),
                    },
                )
        await save(original.tools, saved.version, original.endpoint_ids)

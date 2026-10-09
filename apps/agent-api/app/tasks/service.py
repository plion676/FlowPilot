from uuid import uuid4

from app.agent.skills import FOLLOWUP
from app.core.errors import AppError


class TaskService:
    def __init__(self, client, management):
        self.client, self.management = client, management

    async def submit(self, role, actor, key=None):
        snapshot, catalog = await self.management.prepare(role, uuid4())
        definition = self.management.skills.resolve(role, FOLLOWUP)
        # A workflow is intentionally pinned to one role-owned source, not a domain endpoint.
        endpoints = {
            item["endpoint_id"]
            for item in catalog
            if item["name"] == "workflow.create_followup_plan"
        }
        ready = [
            endpoint
            for endpoint in endpoints
            if set(definition.allowed_tools)
            <= {
                item["name"]
                for item in catalog
                if item["endpoint_id"] == endpoint and item["available"]
            }
        ]
        if len(ready) != 1:
            raise AppError(
                "SKILL_TOOLS_UNAVAILABLE", "请在一个本角色连接中绑定并接通四个回访工具。", 403
            )
        return await self.client.request(
            "POST",
            "tasks",
            {
                "role": role,
                "actor_id": actor,
                "idempotency_key": key or str(uuid4()),
                "endpoint_id": ready[0],
                "binding_version": snapshot.binding_version,
            },
        )

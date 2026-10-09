"""Fixed internal Task repository API; never exposed as arbitrary HTTP tools."""

import json
from uuid import uuid4

import httpx

from app.core.errors import AppError


class TaskClient:
    def __init__(self, url, token):
        self.url, self.token = url, token

    async def request(self, method, path, payload=None, params=None):
        if not self.url or not self.token or len(self.token) < 16:
            raise AppError("TASK_NOT_CONFIGURED", "任务存储尚未配置。", 503)
        try:
            async with httpx.AsyncClient(
                base_url=self.url, timeout=8, trust_env=False, follow_redirects=False
            ) as client:
                result = await client.request(
                    method,
                    "/internal/" + path,
                    json=payload,
                    params=params,
                    headers={"X-Internal-Service-Token": self.token, "X-Request-ID": str(uuid4())},
                )
                if len(result.content) > 262144:
                    raise ValueError("Task response too large")
                body = result.json()
                if not result.is_success:
                    error = body.get("error", {})
                    code = error.get("code", "DEPENDENCY_UNAVAILABLE")
                    allowed = {
                        "NOT_FOUND",
                        "INVALID_ARGUMENTS",
                        "TASK_CONFLICT",
                        "TASK_EXPIRED",
                        "APPROVAL_REQUIRED",
                        "BINDING_CHANGED",
                        "FORBIDDEN_TOOL",
                        "FORBIDDEN_SKILL",
                        "LEASE_LOST",
                        "TASK_LIMIT_REACHED",
                        "CUSTOMER_CHANGED",
                    }
                    if code not in allowed:
                        code = "DEPENDENCY_UNAVAILABLE"
                    raise AppError(
                        code, "任务操作失败，请检查状态、权限与有效期。", result.status_code
                    )
                return body
        except (httpx.HTTPError, ValueError, json.JSONDecodeError) as error:
            raise AppError("DEPENDENCY_UNAVAILABLE", "任务存储暂不可用。", 503, True) from error

    async def list(self, role, actor):
        return await self.request("GET", "tasks", params={"role": role, "actor_id": actor})

    async def get(self, task_id, role, actor, *, audit=False):
        return await self.request(
            "GET",
            f"tasks/{task_id}" + ("/audit" if audit else ""),
            params={"role": role, "actor_id": actor},
        )

    async def decide(self, task_id, role, actor, payload):
        return await self.request(
            "POST", f"tasks/{task_id}/decision", {**payload, "role": role, "actor_id": actor}
        )

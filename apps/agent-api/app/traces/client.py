from uuid import uuid4

import httpx

from app.core.errors import AppError


class TraceClient:
    def __init__(self, url, token):
        self.url, self.token = url, token

    async def request(self, method, path="", payload=None, params=None):
        if not self.url or not self.token:
            raise AppError("TRACE_UNAVAILABLE", "Trace 存储尚未配置。", 503)
        try:
            async with httpx.AsyncClient(
                base_url=self.url, timeout=2, trust_env=False, follow_redirects=False
            ) as client:
                response = await client.request(
                    method,
                    "/internal/traces" + path,
                    json=payload,
                    params=params,
                    headers={"X-Internal-Service-Token": self.token, "X-Request-ID": str(uuid4())},
                )
                if len(response.content) > 2_000_000:
                    raise ValueError("Trace response too large")
                if response.status_code == 404:
                    raise AppError("NOT_FOUND", "Trace 不存在。", 404)
                if not response.is_success:
                    raise ValueError("Trace repository rejected request")
                return response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise AppError("TRACE_UNAVAILABLE", "Trace 存储暂不可用。", 503, True) from error

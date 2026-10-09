"""Best-effort observer. Sanitization happens before any persistence call."""

import asyncio
import json
import logging
import os
import re
from uuid import uuid4

logger = logging.getLogger("opspilot.trace")
PRIVATE_KEY = re.compile(
    r"authorization|api.?key|secret|token|password|credential|signature|lease|grant|reasoning|headers|dsn",
    re.I,
)


def visible_content(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        # Provider reasoning blocks and opaque metadata are intentionally excluded.
        return "\n".join(
            block if isinstance(block, str) else str(block.get("text", ""))
            for block in content
            if isinstance(block, str) or isinstance(block, dict) and block.get("type") == "text"
        )
    return ""


class Redactor:
    def __init__(self, secrets=()):
        self.secrets = tuple(sorted({v for v in secrets if v}, key=len, reverse=True))

    def text(self, value):
        for secret in self.secrets:
            value = value.replace(secret, "[REDACTED]")
        value = re.sub(r"(?i)Bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]", value)
        value = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}\b", "[REDACTED]", value)
        value = re.sub(
            r"(?i)([\"']?(?:api[_-]?key|authorization|password|secret|token|lease[_-]?token|task[_-]?grant)[\"']?\s*[:=]\s*)(?:\"[^\"]*\"|'[^']*'|[^\s,;}]+)",
            r"\1[REDACTED]",
            value,
        )
        return value

    def clean(self, value, depth=0):
        if depth > 8:
            return {"truncated": True, "reason": "depth_limit"}
        if isinstance(value, dict):
            result = {
                self.text(str(k))[:100]: "[REDACTED]"
                if PRIVATE_KEY.search(str(k))
                else self.clean(v, depth + 1)
                for k, v in list(value.items())[:100]
            }
            if len(value) > 100:
                result["truncated"] = True
            return result
        if isinstance(value, list):
            result = [self.clean(v, depth + 1) for v in value[:100]]
            if len(value) > 100:
                result.append({"truncated": True, "reason": "item_limit"})
            return result
        if isinstance(value, str):
            # Tool observations commonly contain JSON encoded as a message string.
            try:
                parsed = json.loads(value)
            except (ValueError, TypeError):
                parsed = None
            if isinstance(parsed, (dict, list)):
                return self.clean(parsed, depth + 1)
            value = self.text(value)
            return value if len(value) <= 8000 else value[:8000] + "\n[TRUNCATED]"
        if value is None or isinstance(value, (bool, int, float)):
            return value
        return "[UNSUPPORTED]"

    def payload(self, value):
        cleaned = self.clean(value)
        encoded = json.dumps(cleaned, ensure_ascii=False)
        if len(encoded.encode()) > 12000:
            return {"truncated": True, "reason": "event_size_limit", "excerpt": encoded[:2500]}
        return cleaned


def safe_code(error, fallback):
    code = getattr(error, "code", fallback)
    return code if isinstance(code, str) and re.fullmatch(r"[A-Z0-9_]{1,80}", code) else fallback


class TraceRecorder:
    def __init__(self, client, request_id, role, actor, model, *, secrets=()):
        self.client = client
        self.id = str(uuid4())
        self.identity = {"role": role, "actor_id": actor}
        env_secrets = [v for k, v in os.environ.items() if PRIVATE_KEY.search(k)]
        self.redactor = Redactor([*env_secrets, *secrets])
        self.request_id, self.model = request_id, model
        self.created = False
        self.incomplete = False
        self.step = 0
        self.tools = {}
        self._lock = asyncio.Lock()
        self._stopped = False

    async def start(self, message):
        try:
            await self.client.request(
                "POST",
                payload={
                    **self.identity,
                    "trace_id": self.id,
                    "request_id": self.request_id,
                    "summary": self.redactor.text(message)[:100],
                    "model": self.redactor.text(self.model or "")[:80],
                },
            )
            self.created = True
        except Exception:
            self.incomplete = self._stopped = True
            logger.warning("trace_start_unavailable")
        await self.emit("user_message", {"content": message})

    async def emit(self, kind, payload, *, call_id="", step=None):
        async with self._lock:
            if self._stopped:
                return
            try:
                await self.client.request(
                    "POST",
                    f"/{self.id}/events",
                    {
                        **self.identity,
                        "event_id": str(uuid4()),
                        "kind": kind,
                        "step": self.step if step is None else step,
                        "call_id": self.redactor.text(call_id)[:128],
                        "payload": self.redactor.payload(payload),
                    },
                )
            except Exception:
                # Stop recording after a gap; never replay a model/tool to fill it.
                self.incomplete = self._stopped = True
                logger.warning("trace_capture_incomplete")

    def tool(self, call):
        return {
            **self.tools.get(call["name"], {"name": call["name"]}),
            "model_name": call["name"],
            "arguments": call.get("args", {}),
        }

    async def finish(self, status, error_code=""):
        if not self.created:
            return
        try:
            await self.client.request(
                "POST",
                f"/{self.id}/finish",
                {
                    **self.identity,
                    "status": status,
                    "incomplete": self.incomplete,
                    "error_code": error_code,
                },
            )
        except Exception:
            self.incomplete = True
            logger.warning("trace_finish_unavailable")

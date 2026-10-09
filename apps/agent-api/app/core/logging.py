"""Small structured logging setup with secret-field redaction."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from typing import Any

SENSITIVE_KEYS = frozenset(
    {
        "api_key",
        "authorization",
        "password",
        "token",
        "secret",
        "lease_token",
        "lease_hash",
        "task_grant",
    }
)


def redact(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): "[REDACTED]" if str(key).lower() in SENSITIVE_KEYS else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {"level": record.levelname, "logger": record.name, "message": record.getMessage()}
        extra = getattr(record, "safe_context", None)
        if extra is not None:
            payload["context"] = redact(extra)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

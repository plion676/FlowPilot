"""Audit write port plus a test/development sink."""

from __future__ import annotations

from typing import Protocol

from app.audit.models import AuditEvent


class AuditSink(Protocol):
    async def record(self, event: AuditEvent) -> None: ...


class InMemoryAuditSink:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    async def record(self, event: AuditEvent) -> None:
        self.events.append(event)

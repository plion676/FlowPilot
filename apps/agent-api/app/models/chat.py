from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    session_id: str | None = Field(default=None, max_length=128)
    skill_name: str | None = Field(default=None, max_length=80)


class ToolCallRecord(BaseModel):
    name: str
    arguments: dict[str, Any]
    result: dict[str, Any]
    endpoint_id: str | None = None
    endpoint_name: str | None = None
    endpoint_revision: int | None = None


class ChatResponse(BaseModel):
    trace_id: str | None = None
    trace_incomplete: bool = False
    request_id: str
    message: str
    model: str
    route: dict[str, str] | None = None
    data: dict[str, Any] | None = None
    citations: list[dict[str, Any]] = Field(default_factory=list)
    task: dict[str, Any] | None = None
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    binding_version: int | None = None
    skill_name: str | None = None

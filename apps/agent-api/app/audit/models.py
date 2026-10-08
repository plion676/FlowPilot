"""A deliberately narrow event shape that cannot contain prompts or headers."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AuditEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    request_id: UUID
    actor_id: str = Field(min_length=1, max_length=80)
    role: str = Field(min_length=1, max_length=80)
    event_type: Literal["route_selected", "route_clarification", "policy_denied"]
    outcome: Literal["allowed", "clarification", "denied"]
    reason_code: (
        Literal[
            "UNKNOWN_ROLE",
            "FORBIDDEN_TOOL",
            "FORBIDDEN_SKILL",
            "INVALID_ARGUMENTS",
            "APPROVAL_REQUIRED",
            "INVALID_BINDING",
            "INVALID_MODEL_PROPOSAL",
            "CLARIFICATION_REQUIRED",
        ]
        | None
    ) = None
    route_kind: Literal["direct_tool", "skill", "clarification"] | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

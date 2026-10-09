"""Immutable per-request authorization results."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

type RouteKind = Literal["direct_tool", "skill", "agent"]


@dataclass(frozen=True, slots=True)
class AllowedToolBinding:
    request_id: UUID
    role: str
    route: RouteKind
    tool_names: frozenset[str]
    selected_tool: str | None = None
    skill_name: str | None = None
    binding_version: int | None = None
    endpoint_id: str | None = None
    endpoint_revision: int | None = None
    task_grant: dict | None = None


@dataclass(frozen=True, slots=True)
class ApprovalGrant:
    """A grant already verified by the trusted Task service, never model input."""

    task_id: UUID
    task_version: int
    verified: bool

"""Immutable metadata and strict arguments for the five MVP Tools."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from types import MappingProxyType
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

type RiskLevel = Literal["read_only", "approval_required"]


class ToolArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class CustomerArguments(ToolArguments):
    customer_id: str = Field(pattern=r"^C[0-9]{4,}$")


class WorkOrderArguments(ToolArguments):
    work_order_id: str = Field(pattern=r"^WO-[0-9]{4,}$")


class SearchSopArguments(ToolArguments):
    query: str = Field(min_length=2, max_length=300)
    top_k: int = Field(default=5, ge=1, le=10)


class FollowupProposalArguments(ToolArguments):
    operation: Literal["propose"]
    task_id: UUID
    window_start: date
    window_end: date

    @model_validator(mode="after")
    def check_window(self) -> FollowupProposalArguments:
        if (self.window_end - self.window_start).days != 7:
            raise ValueError("回访窗口必须覆盖当天至第 7 个自然日")
        return self


class FollowupCommitArguments(ToolArguments):
    operation: Literal["commit"]
    task_id: UUID


FollowupArguments = Annotated[
    FollowupProposalArguments | FollowupCommitArguments,
    Field(discriminator="operation"),
]


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    risk_level: RiskLevel
    input_schema: Any
    mcp_name: str
    available: bool = True

    @property
    def model_name(self) -> str:
        """Provider-safe function name; never used as the policy/MCP identity."""
        return self.name.replace(".", "__")

    def validate(self, arguments: dict[str, Any]) -> dict[str, Any]:
        # MCP arguments arrive as JSON. Strict validation still accepts ISO dates
        # and UUIDs in JSON while rejecting Python-only objects and coercions.
        payload = json.dumps(arguments, allow_nan=False)
        parsed = TypeAdapter(self.input_schema).validate_json(payload)
        return parsed.model_dump(mode="json")


class ToolRegistry:
    def __init__(self, specs: tuple[ToolSpec, ...]) -> None:
        if len({spec.name for spec in specs}) != len(specs):
            raise ValueError("Tool 名称不能重复")
        if len({spec.mcp_name for spec in specs}) != len(specs):
            raise ValueError("MCP Tool 名称不能重复")
        model_names = [spec.model_name for spec in specs]
        if len(set(model_names)) != len(model_names) or any(
            re.fullmatch(r"[A-Za-z0-9_-]{1,128}", name) is None for name in model_names
        ):
            raise ValueError("模型 Tool 名称重复或不符合函数命名规则")
        self._specs = MappingProxyType({spec.name: spec for spec in specs})

    def get(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)

    @property
    def names(self) -> frozenset[str]:
        return frozenset(self._specs)


def default_tool_registry() -> ToolRegistry:
    return ToolRegistry(
        (
            ToolSpec(
                "crm.get_customer_overview",
                "read_only",
                CustomerArguments,
                "crm.get_customer_overview",
            ),
            ToolSpec(
                "crm.list_open_tickets",
                "read_only",
                CustomerArguments,
                "crm.list_open_tickets",
            ),
            ToolSpec(
                "mes.get_work_order_status",
                "read_only",
                WorkOrderArguments,
                "mes.get_work_order_status",
            ),
            ToolSpec(
                "knowledge.search_sop",
                "read_only",
                SearchSopArguments,
                "knowledge.search_sop",
            ),
            ToolSpec(
                "workflow.create_followup_plan",
                "approval_required",
                FollowupArguments,
                "workflow.create_followup_plan",
                available=True,
            ),
        )
    )

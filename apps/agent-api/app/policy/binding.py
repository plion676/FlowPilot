"""Resolve and enforce the minimum Tool set for one request."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import ValidationError

from app.models.policy import AllowedToolBinding, ApprovalGrant
from app.policy.engine import DenialCode, PolicyDenied, PolicyEngine
from app.tools.registry import ToolRegistry


class BindingResolver:
    def __init__(self, engine: PolicyEngine, registry: ToolRegistry) -> None:
        self._engine = engine
        self._registry = registry

    def direct(
        self,
        *,
        request_id: UUID,
        role: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> AllowedToolBinding:
        allowed = self._engine.direct_tools(role)
        if tool_name not in allowed:
            raise PolicyDenied(DenialCode.FORBIDDEN_TOOL)
        self._validate_arguments(tool_name, arguments)
        return AllowedToolBinding(
            request_id=request_id,
            role=role,
            route="direct_tool",
            tool_names=frozenset({tool_name}),
            selected_tool=tool_name,
        )

    def skill(self, *, request_id: UUID, role: str, skill_name: str) -> AllowedToolBinding:
        tools = self._engine.skill_tools(role, skill_name)
        if not tools:
            raise PolicyDenied(DenialCode.FORBIDDEN_SKILL)
        return AllowedToolBinding(
            request_id=request_id,
            role=role,
            route="skill",
            tool_names=tools,
            skill_name=skill_name,
        )

    def authorize_call(
        self,
        binding: AllowedToolBinding,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        approval: ApprovalGrant | None = None,
    ) -> dict[str, Any]:
        # Recheck the policy on every call, even if a caller retained a binding.
        if binding.route == "direct_tool":
            permitted = self._engine.direct_tools(binding.role)
            if binding.selected_tool != tool_name:
                raise PolicyDenied(DenialCode.INVALID_BINDING)
        elif binding.route == "skill" and binding.skill_name is not None:
            permitted = self._engine.skill_tools(binding.role, binding.skill_name)
        else:
            raise PolicyDenied(DenialCode.INVALID_BINDING)
        if tool_name not in binding.tool_names or tool_name not in permitted:
            raise PolicyDenied(DenialCode.FORBIDDEN_TOOL)
        validated = self._validate_arguments(tool_name, arguments)
        if tool_name == "workflow.create_followup_plan" and validated["operation"] == "commit":
            task_id = UUID(validated["task_id"])
            if approval is None or not approval.verified or approval.task_id != task_id:
                raise PolicyDenied(DenialCode.APPROVAL_REQUIRED)
        return validated

    def _validate_arguments(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        spec = self._registry.get(tool_name)
        if spec is None:
            raise PolicyDenied(DenialCode.FORBIDDEN_TOOL)
        try:
            return spec.validate(arguments)
        except (ValidationError, TypeError, ValueError) as error:
            raise PolicyDenied(DenialCode.INVALID_ARGUMENTS) from error

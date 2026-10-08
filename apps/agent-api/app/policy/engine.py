"""Role and Skill permissions independent of model output."""

from __future__ import annotations

from enum import StrEnum

from app.policy.config_models import PolicyConfig
from app.tools.registry import ToolRegistry


class DenialCode(StrEnum):
    UNKNOWN_ROLE = "UNKNOWN_ROLE"
    FORBIDDEN_TOOL = "FORBIDDEN_TOOL"
    FORBIDDEN_SKILL = "FORBIDDEN_SKILL"
    INVALID_ARGUMENTS = "INVALID_ARGUMENTS"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    INVALID_BINDING = "INVALID_BINDING"


class PolicyDenied(Exception):
    def __init__(self, code: DenialCode) -> None:
        self.code = code
        super().__init__(code.value)


class PolicyEngine:
    def __init__(self, config: PolicyConfig, registry: ToolRegistry) -> None:
        self._config = config
        self._registry = registry

    def direct_tools(self, role_name: str) -> frozenset[str]:
        role = self._config.roles.get(role_name)
        if role is None:
            raise PolicyDenied(DenialCode.UNKNOWN_ROLE)
        return frozenset(role.bound_tools) & frozenset(role.direct_tools) & self._registry.names

    def skill_tools(self, role_name: str, skill_name: str) -> frozenset[str]:
        role = self._config.roles.get(role_name)
        if role is None:
            raise PolicyDenied(DenialCode.UNKNOWN_ROLE)
        if skill_name not in role.allowed_skills:
            raise PolicyDenied(DenialCode.FORBIDDEN_SKILL)
        skill = self._config.skills.get(skill_name)
        if skill is None:
            raise PolicyDenied(DenialCode.FORBIDDEN_SKILL)
        return frozenset(role.bound_tools) & frozenset(skill.allowed_tools) & self._registry.names

    def allowed_skills(self, role_name: str) -> frozenset[str]:
        role = self._config.roles.get(role_name)
        if role is None:
            raise PolicyDenied(DenialCode.UNKNOWN_ROLE)
        return frozenset(role.allowed_skills) & frozenset(self._config.skills)

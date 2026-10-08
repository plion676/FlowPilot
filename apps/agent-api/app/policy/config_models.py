"""Versioned Role and Skill policy configuration, validated at startup."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.tools.registry import ToolRegistry


class PolicyConfigurationError(ValueError):
    """A policy file is malformed or refers to an unavailable capability."""


class StrictConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class RoleConfig(StrictConfig):
    bound_tools: tuple[str, ...] = Field(min_length=1)
    direct_tools: tuple[str, ...] = Field(default_factory=tuple)
    allowed_skills: tuple[str, ...] = Field(default_factory=tuple)

    @field_validator("bound_tools", "direct_tools", "allowed_skills", mode="before")
    @classmethod
    def tuple_from_yaml_list(cls, value: object) -> tuple[str, ...]:
        if not isinstance(value, list):
            raise ValueError("策略集合必须是列表")
        return tuple(value)


class SkillConfig(StrictConfig):
    allowed_tools: tuple[str, ...] = Field(min_length=1)
    risk_level: Literal["read_only", "approval_required"]

    @field_validator("allowed_tools", mode="before")
    @classmethod
    def tuple_from_yaml_list(cls, value: object) -> tuple[str, ...]:
        if not isinstance(value, list):
            raise ValueError("策略集合必须是列表")
        return tuple(value)


class RolesDocument(StrictConfig):
    version: Literal[1]
    roles: dict[str, RoleConfig] = Field(min_length=1)


class SkillsDocument(StrictConfig):
    version: Literal[1]
    skills: dict[str, SkillConfig] = Field(min_length=1)


class PolicyConfig(StrictConfig):
    roles: dict[str, RoleConfig]
    skills: dict[str, SkillConfig]
    version: int


def _read_yaml(path: Path) -> object:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise PolicyConfigurationError(f"无法读取策略配置：{path.name}") from error


def load_policy_config(directory: Path, registry: ToolRegistry) -> PolicyConfig:
    try:
        roles = RolesDocument.model_validate(_read_yaml(directory / "roles.yaml"))
        skills = SkillsDocument.model_validate(_read_yaml(directory / "skills.yaml"))
    except ValidationError as error:
        raise PolicyConfigurationError("策略配置结构或版本无效") from error
    if roles.version != skills.version:
        raise PolicyConfigurationError("Role 与 Skill 策略版本不一致")
    for skill_name, skill in skills.skills.items():
        if not skill_name or len(skill.allowed_tools) != len(set(skill.allowed_tools)):
            raise PolicyConfigurationError(f"Skill Tool 重复或名称无效：{skill_name}")
        for tool_name in skill.allowed_tools:
            spec = registry.get(tool_name)
            if spec is None:
                raise PolicyConfigurationError(f"Skill 引用了未注册的 Tool：{tool_name}")
            if spec.risk_level == "approval_required" and skill.risk_level != "approval_required":
                raise PolicyConfigurationError(f"Skill 风险等级过低：{skill_name}")
    for role_name, role in roles.roles.items():
        if not role_name or len(role.bound_tools) != len(set(role.bound_tools)):
            raise PolicyConfigurationError(f"Role 绑定 Tool 重复或名称无效：{role_name}")
        if len(role.direct_tools) != len(set(role.direct_tools)):
            raise PolicyConfigurationError(f"Role 直调 Tool 重复：{role_name}")
        if len(role.allowed_skills) != len(set(role.allowed_skills)):
            raise PolicyConfigurationError(f"Role 的 Skill 重复：{role_name}")
        for tool_name in role.bound_tools:
            if registry.get(tool_name) is None:
                raise PolicyConfigurationError(f"Role 绑定了未注册的 Tool：{tool_name}")
        for tool_name in role.direct_tools:
            spec = registry.get(tool_name)
            if spec is None or spec.risk_level != "read_only":
                raise PolicyConfigurationError(f"Role 直调 Tool 未注册或风险过高：{tool_name}")
            if tool_name not in role.bound_tools:
                raise PolicyConfigurationError(f"Role 直调 Tool 未包含在绑定工具集中：{tool_name}")
        for skill_name in role.allowed_skills:
            if skill_name not in skills.skills:
                raise PolicyConfigurationError(f"Role 引用了不存在的 Skill：{skill_name}")
            missing_tools = set(skills.skills[skill_name].allowed_tools) - set(role.bound_tools)
            if missing_tools:
                missing = ", ".join(sorted(missing_tools))
                raise PolicyConfigurationError(f"Role 启用的 Skill 缺少绑定 Tool：{missing}")
    return PolicyConfig(roles=roles.roles, skills=skills.skills, version=roles.version)

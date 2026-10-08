"""Skill definitions constrain capabilities; they never grant new Tool bindings."""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.core.errors import AppError
from app.policy.config_models import load_policy_config
from app.tools.registry import default_tool_registry

INSIGHT = "crm.customer_insight"


class SkillActivation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    skill_name: Literal["crm.customer_insight"]


class SkillRegistry:
    def __init__(self):
        self.policy = load_policy_config(
            Path(__file__).resolve().parents[2] / "config",
            default_tool_registry(),
        )

    def resolve(self, role: str, name: str):
        definition = self.policy.skills.get(name)
        role_definition = self.policy.roles.get(role)
        if not definition or not role_definition or name not in role_definition.allowed_skills:
            raise AppError("FORBIDDEN_SKILL", "当前角色无权启用这个 Skill。", 403)
        if name != INSIGHT or definition.risk_level != "read_only":
            raise AppError("SKILL_NOT_READY", "此 Skill 的审批流程尚未接通。", 503)
        return definition

    def ready(self, role: str, catalog: list[dict]) -> bool:
        try:
            definition = self.resolve(role, INSIGHT)
        except AppError:
            return False
        return set(definition.allowed_tools) <= {
            item["name"] for item in catalog if item["available"]
        }

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from app.policy.config_models import PolicyConfigurationError, load_policy_config
from app.policy.engine import PolicyEngine
from app.tools.registry import CustomerArguments, ToolRegistry, ToolSpec, default_tool_registry

CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"


def write_policy(directory: Path, roles: dict[str, object], skills: dict[str, object]) -> None:
    (directory / "roles.yaml").write_text(
        yaml.safe_dump({"version": 1, "roles": roles}), encoding="utf-8"
    )
    (directory / "skills.yaml").write_text(
        yaml.safe_dump({"version": 1, "skills": skills}), encoding="utf-8"
    )


def base_documents() -> tuple[dict[str, object], dict[str, object]]:
    return (
        {
            "consultant": {
                "bound_tools": ["crm.get_customer_overview"],
                "direct_tools": ["crm.get_customer_overview"],
                "allowed_skills": ["crm.customer_insight"],
            }
        },
        {
            "crm.customer_insight": {
                "allowed_tools": ["crm.get_customer_overview"],
                "risk_level": "read_only",
            }
        },
    )


def test_initial_policy_has_only_expected_consultant_permissions() -> None:
    config = load_policy_config(CONFIG_DIR, default_tool_registry())
    assert config.roles["consultant"].direct_tools == (
        "crm.get_customer_overview",
        "crm.list_open_tickets",
    )
    assert set(config.roles["consultant"].bound_tools) == {
        "crm.get_customer_overview",
        "crm.list_open_tickets",
        "knowledge.search_sop",
        "workflow.create_followup_plan",
    }
    assert "mes.get_work_order_status" not in config.roles["consultant"].direct_tools
    assert set(config.skills) == {"crm.customer_insight", "crm.followup_workflow"}
    assert len(default_tool_registry().names) == 5


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        ("unknown_skill", "不存在的 Skill"),
        ("duplicate_direct", "重复"),
        ("unknown_tool", "未注册"),
        ("write_direct", "风险过高"),
        ("understated_risk", "风险等级过低"),
        ("duplicate_bound", "绑定 Tool 重复"),
        ("unknown_bound", "绑定了未注册的 Tool"),
        ("direct_not_bound", "未包含在绑定工具集中"),
        ("skill_not_bound", "Skill 缺少绑定 Tool"),
    ],
)
def test_invalid_policy_fails_closed(tmp_path: Path, mutate: str, expected: str) -> None:
    roles, skills = base_documents()
    role = roles["consultant"]
    skill = skills["crm.customer_insight"]
    if mutate == "unknown_skill":
        role["allowed_skills"] = ["crm.unknown"]
    elif mutate == "duplicate_direct":
        role["direct_tools"] = ["crm.get_customer_overview"] * 2
    elif mutate == "unknown_tool":
        skill["allowed_tools"] = ["crm.unknown"]
    elif mutate == "write_direct":
        role["direct_tools"] = ["workflow.create_followup_plan"]
    elif mutate == "duplicate_bound":
        role["bound_tools"] = ["crm.get_customer_overview"] * 2
    elif mutate == "unknown_bound":
        role["bound_tools"] = ["crm.unknown"]
    elif mutate == "direct_not_bound":
        role["bound_tools"] = ["crm.list_open_tickets"]
    elif mutate == "skill_not_bound":
        role["direct_tools"] = []
        skill["allowed_tools"] = ["crm.list_open_tickets"]
    else:
        skill["allowed_tools"] = ["workflow.create_followup_plan"]
        role["bound_tools"] = ["crm.get_customer_overview", "workflow.create_followup_plan"]
    write_policy(tmp_path, roles, skills)
    with pytest.raises(PolicyConfigurationError, match=expected):
        load_policy_config(tmp_path, default_tool_registry())


def test_two_agent_categories_reuse_registry_with_manual_toolsets(tmp_path: Path) -> None:
    roles, skills = base_documents()
    roles["customer_service"] = {
        "bound_tools": ["crm.list_open_tickets"],
        "direct_tools": ["crm.list_open_tickets"],
        "allowed_skills": [],
    }
    write_policy(tmp_path, roles, skills)
    registry = default_tool_registry()
    policy = PolicyEngine(load_policy_config(tmp_path, registry), registry)

    assert policy.direct_tools("consultant") == {"crm.get_customer_overview"}
    assert policy.skill_tools("consultant", "crm.customer_insight") == {"crm.get_customer_overview"}
    assert policy.direct_tools("customer_service") == {"crm.list_open_tickets"}
    assert policy.allowed_skills("customer_service") == set()


def test_registry_rejects_duplicate_tool_names() -> None:
    spec = ToolSpec("crm.x", "read_only", CustomerArguments, "crm.x")
    with pytest.raises(ValueError, match="重复"):
        ToolRegistry((spec, spec))


def test_registry_arguments_reject_extra_fields() -> None:
    spec = default_tool_registry().get("crm.get_customer_overview")
    assert spec is not None
    with pytest.raises(ValidationError):
        spec.validate({"customer_id": "C1001", "sql": "DROP TABLE customers"})


def test_model_function_names_are_provider_safe_without_changing_mcp_names() -> None:
    registry = default_tool_registry()
    spec = registry.get("crm.get_customer_overview")
    assert spec is not None
    assert spec.model_name == "crm__get_customer_overview"
    assert spec.mcp_name == "crm.get_customer_overview"
    assert all("." not in registry.get(name).model_name for name in registry.names)


def test_registry_rejects_model_alias_collisions() -> None:
    with pytest.raises(ValueError, match="模型 Tool 名称重复"):
        ToolRegistry(
            (
                ToolSpec("crm.get", "read_only", CustomerArguments, "crm.get"),
                ToolSpec("crm__get", "read_only", CustomerArguments, "crm__get"),
            )
        )

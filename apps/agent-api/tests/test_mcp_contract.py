from __future__ import annotations

import json
from pathlib import Path

from app.clients.mcp import CONTRACT_FINGERPRINT
from app.policy.config_models import load_policy_config
from app.tools.registry import default_tool_registry


def test_mcp_policy_mirror_matches_agent_config() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    mirror = json.loads(
        (repo_root / "apps/mcp-server/config/policy.json").read_text(encoding="utf-8")
    )
    agent = load_policy_config(repo_root / "apps/agent-api/config", default_tool_registry())
    assert mirror["version"] == agent.version
    assert mirror["contract_fingerprint"] == CONTRACT_FINGERPRINT
    assert set(mirror["roles"]) == set(agent.roles)
    assert set(mirror["skills"]) == set(agent.skills)
    for name, role in agent.roles.items():
        assert mirror["roles"][name] == {
            "bound_tools": list(role.bound_tools),
            "direct_tools": list(role.direct_tools),
            "allowed_skills": list(role.allowed_skills),
        }
    for name, skill in agent.skills.items():
        assert mirror["skills"][name] == {
            "allowed_tools": list(skill.allowed_tools),
            "risk_level": skill.risk_level,
        }

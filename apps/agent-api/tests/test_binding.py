from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from app.models.policy import ApprovalGrant
from app.policy.binding import BindingResolver
from app.policy.config_models import load_policy_config
from app.policy.engine import DenialCode, PolicyDenied, PolicyEngine
from app.tools.registry import default_tool_registry


@pytest.fixture
def resolver() -> BindingResolver:
    registry = default_tool_registry()
    config = load_policy_config(Path(__file__).resolve().parents[1] / "config", registry)
    return BindingResolver(PolicyEngine(config, registry), registry)


def test_direct_binding_is_exactly_one_authorized_tool(resolver: BindingResolver) -> None:
    binding = resolver.direct(
        request_id=uuid4(),
        role="consultant",
        tool_name="crm.get_customer_overview",
        arguments={"customer_id": "C1001"},
    )
    assert binding.tool_names == frozenset({"crm.get_customer_overview"})
    with pytest.raises(PolicyDenied) as error:
        resolver.authorize_call(binding, "crm.list_open_tickets", {"customer_id": "C1001"})
    assert error.value.code is DenialCode.INVALID_BINDING


def test_skill_bindings_do_not_inherit_extra_tools(resolver: BindingResolver) -> None:
    insight = resolver.skill(
        request_id=uuid4(), role="consultant", skill_name="crm.customer_insight"
    )
    assert insight.tool_names == frozenset(
        {"crm.get_customer_overview", "crm.list_open_tickets", "knowledge.search_sop"}
    )
    workflow = resolver.skill(
        request_id=uuid4(), role="consultant", skill_name="crm.followup_workflow"
    )
    assert workflow.tool_names == insight.tool_names | {"workflow.create_followup_plan"}


@pytest.mark.parametrize(
    ("role", "tool", "code"),
    [
        ("unknown", "crm.get_customer_overview", DenialCode.UNKNOWN_ROLE),
        ("consultant", "mes.get_work_order_status", DenialCode.FORBIDDEN_TOOL),
        ("consultant", "knowledge.search_sop", DenialCode.FORBIDDEN_TOOL),
    ],
)
def test_direct_permission_denials(
    resolver: BindingResolver, role: str, tool: str, code: DenialCode
) -> None:
    with pytest.raises(PolicyDenied) as error:
        resolver.direct(request_id=uuid4(), role=role, tool_name=tool, arguments={})
    assert error.value.code is code


def test_invalid_arguments_do_not_produce_binding(resolver: BindingResolver) -> None:
    with pytest.raises(PolicyDenied) as error:
        resolver.direct(
            request_id=uuid4(),
            role="consultant",
            tool_name="crm.get_customer_overview",
            arguments={"customer_id": "../C1001"},
        )
    assert error.value.code is DenialCode.INVALID_ARGUMENTS


def test_commit_requires_trusted_matching_approval(resolver: BindingResolver) -> None:
    binding = resolver.skill(
        request_id=uuid4(), role="consultant", skill_name="crm.followup_workflow"
    )
    task_id = uuid4()
    arguments = {"operation": "commit", "task_id": str(task_id)}
    with pytest.raises(PolicyDenied) as error:
        resolver.authorize_call(binding, "workflow.create_followup_plan", arguments)
    assert error.value.code is DenialCode.APPROVAL_REQUIRED
    with pytest.raises(PolicyDenied):
        resolver.authorize_call(
            binding,
            "workflow.create_followup_plan",
            arguments,
            approval=ApprovalGrant(task_id=uuid4(), task_version=1, verified=True),
        )
    validated = resolver.authorize_call(
        binding,
        "workflow.create_followup_plan",
        arguments,
        approval=ApprovalGrant(task_id=task_id, task_version=1, verified=True),
    )
    assert validated["task_id"] == str(task_id)


def test_proposal_does_not_authorize_commit(resolver: BindingResolver) -> None:
    binding = resolver.skill(
        request_id=uuid4(), role="consultant", skill_name="crm.followup_workflow"
    )
    task_id = uuid4()
    validated = resolver.authorize_call(
        binding,
        "workflow.create_followup_plan",
        {
            "operation": "propose",
            "task_id": str(task_id),
            "window_start": "2026-09-30",
            "window_end": "2026-10-07",
        },
    )
    assert validated["operation"] == "propose"
    with pytest.raises(PolicyDenied) as error:
        resolver.authorize_call(
            binding,
            "workflow.create_followup_plan",
            {"operation": "commit", "task_id": str(task_id)},
        )
    assert error.value.code is DenialCode.APPROVAL_REQUIRED

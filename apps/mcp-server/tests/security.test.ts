import assert from "node:assert/strict";
import { createHmac } from "node:crypto";
import { test } from "node:test";
import {
  loadPolicy,
  signedExecutionGate,
  type AgentBinding,
} from "../src/security.js";

const secret = "local-test-only-signing-secret-32-characters";
const now = 1_780_000_000;
const policy = loadPolicy();

function headers(changes: Record<string, unknown> = {}, key = secret) {
  const context = {
    version: 1,
    request_id: "11111111-1111-4111-8111-111111111111",
    actor_id: "demo-consultant",
    role: "consultant",
    route: "direct_tool",
    selected_tool: "crm.get_customer_overview",
    skill_name: null,
    binding_tools: ["crm.get_customer_overview"],
    tool_name: "crm.get_customer_overview",
    arguments: { customer_id: "C1001" },
    contract_fingerprint: policy.contract_fingerprint,
    issued_at: now,
    expires_at: now + 30,
    ...(changes.route === "agent"
      ? { version: 2, endpoint_id: "business", endpoint_revision: 1 }
      : {}),
    ...changes,
  };
  const encoded = Buffer.from(JSON.stringify(context)).toString("base64url");
  return {
    context: encoded,
    signature: createHmac("sha256", key).update(encoded).digest("hex"),
  };
}

test("signed direct CRM call is allowed but MES and write tools stay denied", async () => {
  await signedExecutionGate(headers(), secret, policy, () => now).assertAllowed(
    "crm.get_customer_overview",
    { customer_id: "C1001" },
  );
  await assert.rejects(
    signedExecutionGate(
      headers({
        selected_tool: "mes.get_work_order_status",
        tool_name: "mes.get_work_order_status",
        binding_tools: ["mes.get_work_order_status"],
        arguments: { work_order_id: "WO-1001" },
      }),
      secret,
      policy,
      () => now,
    ).assertAllowed("mes.get_work_order_status", { work_order_id: "WO-1001" }),
    /FORBIDDEN_TOOL/,
  );
  await assert.rejects(
    signedExecutionGate(
      headers({
        route: "skill",
        selected_tool: null,
        skill_name: "crm.followup_workflow",
        binding_tools: [
          "crm.get_customer_overview",
          "crm.list_open_tickets",
          "knowledge.search_sop",
          "workflow.create_followup_plan",
        ],
        tool_name: "workflow.create_followup_plan",
        arguments: {
          operation: "commit",
          task_id: "11111111-1111-4111-8111-111111111111",
        },
      }),
      secret,
      policy,
      () => now,
    ).assertAllowed("workflow.create_followup_plan", {
      operation: "commit",
      task_id: "11111111-1111-4111-8111-111111111111",
    }),
    /APPROVAL_REQUIRED/,
  );
});

test("forged, expired, altered arguments and contract mismatches fail closed", async () => {
  const cases = [
    {
      signed: headers({}, "attacker-signing-secret-32-characters"),
      args: { customer_id: "C1001" },
      code: /UNAUTHORIZED/,
    },
    {
      signed: headers({ expires_at: now - 1 }),
      args: { customer_id: "C1001" },
      code: /UNAUTHORIZED/,
    },
    {
      signed: headers(),
      args: { customer_id: "C1002" },
      code: /INVALID_BINDING/,
    },
    {
      signed: headers({ contract_fingerprint: "0".repeat(64) }),
      args: { customer_id: "C1001" },
      code: /INVALID_BINDING/,
    },
    {
      signed: headers({ role: "administrator" }),
      args: { customer_id: "C1001" },
      code: /FORBIDDEN_TOOL/,
    },
    {
      signed: headers({
        binding_tools: [
          "crm.get_customer_overview",
          "mes.get_work_order_status",
        ],
      }),
      args: { customer_id: "C1001" },
      code: /FORBIDDEN_TOOL/,
    },
  ];
  for (const item of cases) {
    await assert.rejects(
      signedExecutionGate(item.signed, secret, policy, () => now).assertAllowed(
        "crm.get_customer_overview",
        item.args,
      ),
      item.code,
    );
  }
});

test("managed execution rereads bindings and rejects revoked or stale grants", async () => {
  let current: AgentBinding = {
    id: "consultant",
    version: 1,
    bound_tools: ["crm.get_customer_overview"],
    endpoint_id: "business",
    endpoint_revision: 1,
    enabled: true,
  };
  const context = headers({
    route: "agent",
    selected_tool: null,
    binding_version: 1,
  });
  const gate = signedExecutionGate(
    context,
    secret,
    policy,
    () => now,
    async () => current,
  );
  await gate.assertAllowed("crm.get_customer_overview", {
    customer_id: "C1001",
  });
  current = { ...current, version: 2, bound_tools: [] };
  await assert.rejects(
    gate.assertAllowed("crm.get_customer_overview", { customer_id: "C1001" }),
    /BINDING_CHANGED/,
  );
  await assert.rejects(
    signedExecutionGate(
      headers({ route: "agent", selected_tool: null, binding_version: 2 }),
      secret,
      policy,
      () => now,
      async () => current,
    ).assertAllowed("crm.get_customer_overview", { customer_id: "C1001" }),
    /FORBIDDEN_TOOL/,
  );
  await assert.rejects(
    signedExecutionGate(
      headers(),
      secret,
      policy,
      () => now,
      async () => current,
    ).assertAllowed("crm.get_customer_overview", { customer_id: "C1001" }),
    /INVALID_BINDING/,
  );
});

test("managed MES binding works; management never grants write approval", async () => {
  const current: AgentBinding = {
    id: "consultant",
    version: 2,
    bound_tools: ["mes.get_work_order_status", "workflow.create_followup_plan"],
    endpoint_id: "business",
    endpoint_revision: 1,
    enabled: true,
  };
  const args = { work_order_id: "WO-1001" };
  const signed = headers({
    route: "agent",
    selected_tool: null,
    binding_version: 2,
    binding_tools: current.bound_tools,
    tool_name: "mes.get_work_order_status",
    arguments: args,
  });
  await signedExecutionGate(
    signed,
    secret,
    policy,
    () => now,
    async () => current,
  ).assertAllowed("mes.get_work_order_status", args);
  const writeArgs = {
    operation: "propose",
    task_id: "11111111-1111-4111-8111-111111111111",
    window_start: "2026-10-08",
    window_end: "2026-10-15",
  };
  await assert.rejects(
    signedExecutionGate(
      headers({
        route: "agent",
        selected_tool: null,
        binding_version: 2,
        binding_tools: current.bound_tools,
        tool_name: "workflow.create_followup_plan",
        arguments: writeArgs,
      }),
      secret,
      policy,
      () => now,
      async () => current,
    ).assertAllowed("workflow.create_followup_plan", writeArgs),
    /APPROVAL_REQUIRED/,
  );
  await assert.rejects(
    signedExecutionGate(
      signed,
      secret,
      policy,
      () => now,
      async () => {
        throw new Error("DEPENDENCY_UNAVAILABLE");
      },
    ).assertAllowed("mes.get_work_order_status", args),
    /DEPENDENCY_UNAVAILABLE/,
  );
});

test("Endpoint identity, execution revision and v2 cannot be bypassed", async () => {
  const current: AgentBinding = {
    id: "consultant",
    version: 1,
    bound_tools: ["crm.get_customer_overview"],
    endpoint_id: "crm",
    endpoint_revision: 2,
    enabled: true,
  };
  const changes = {
    route: "agent",
    selected_tool: null,
    binding_version: 1,
    endpoint_id: "crm",
    endpoint_revision: 2,
  };
  const gate = (extra: Record<string, unknown> = {}, binding = current) =>
    signedExecutionGate(
      headers({ ...changes, ...extra }),
      secret,
      policy,
      () => now,
      async () => binding,
      "crm",
      "consultant",
    );
  await gate().assertAllowed("crm.get_customer_overview", {
    customer_id: "C1001",
  });
  await assert.rejects(
    gate({role: "production"}).assertAllowed("crm.get_customer_overview", {customer_id: "C1001"}),
    /FORBIDDEN_TOOL/,
  );
  for (const extra of [{ endpoint_id: "business" }, { version: 1 }])
    await assert.rejects(
      gate(extra).assertAllowed("crm.get_customer_overview", {
        customer_id: "C1001",
      }),
      /INVALID_BINDING/,
    );
  await assert.rejects(
    gate({ endpoint_revision: 1 }).assertAllowed("crm.get_customer_overview", {
      customer_id: "C1001",
    }),
    /BINDING_CHANGED/,
  );
  await assert.rejects(
    gate({}, { ...current, enabled: false }).assertAllowed(
      "crm.get_customer_overview",
      { customer_id: "C1001" },
    ),
    /ENDPOINT_DISABLED/,
  );
});

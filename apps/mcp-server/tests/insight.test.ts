import assert from "node:assert/strict";
import { createHmac } from "node:crypto";
import { test } from "node:test";
import { loadPolicy, signedExecutionGate } from "../src/security.js";

test("managed insight binding is a strict subset, never grants MES or unbound SOP", async () => {
  const policy = loadPolicy();
  const secret = "test-insight-signing-secret-at-least-32";
  const now = Math.floor(Date.now() / 1000);
  const all = [
    "crm.get_customer_overview",
    "crm.list_open_tickets",
    "knowledge.search_sop",
    "mes.get_work_order_status",
  ];
  const insight = all.slice(0, 3);
  function gate(
    tool: string,
    names: string[],
    bound = all,
    skill = "crm.customer_insight",
  ) {
    const context = Buffer.from(
      JSON.stringify({
        version: 2,
        request_id: "11111111-1111-4111-8111-111111111111",
        actor_id: "test",
        role: "consultant",
        route: "agent",
        selected_tool: null,
        skill_name: skill,
        binding_tools: names,
        binding_version: 1,
        endpoint_id: "business",
        endpoint_revision: 1,
        tool_name: tool,
        arguments: { query: "续费风险", top_k: 3 },
        contract_fingerprint: policy.contract_fingerprint,
        issued_at: now,
        expires_at: now + 30,
      }),
    ).toString("base64url");
    return signedExecutionGate(
      {
        context,
        signature: createHmac("sha256", secret).update(context).digest("hex"),
      },
      secret,
      policy,
      () => now,
      async () => ({
        id: "consultant",
        version: 1,
        endpoint_id: "business",
        endpoint_revision: 1,
        enabled: true,
        bound_tools: bound,
      }),
      "business",
      "consultant",
    );
  }
  const args = { query: "续费风险", top_k: 3 };
  await gate("knowledge.search_sop", insight).assertAllowed(
    "knowledge.search_sop",
    args,
  );
  await assert.rejects(
    gate("mes.get_work_order_status", all).assertAllowed(
      "mes.get_work_order_status",
      args,
    ),
    /FORBIDDEN_TOOL/,
  );
  await assert.rejects(
    gate("knowledge.search_sop", insight, all.slice(0, 2)).assertAllowed(
      "knowledge.search_sop",
      args,
    ),
    /FORBIDDEN_TOOL/,
  );
  await assert.rejects(
    gate(
      "knowledge.search_sop",
      insight,
      all,
      "crm.followup_workflow",
    ).assertAllowed("knowledge.search_sop", args),
    /FORBIDDEN_SKILL/,
  );
});

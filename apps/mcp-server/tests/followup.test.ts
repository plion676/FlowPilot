import assert from "node:assert/strict";
import { createHmac } from "node:crypto";
import { test } from "node:test";
import {
  loadPolicy,
  signedExecutionGate,
  type TaskGrantReader,
} from "../src/security.js";

test("workflow requires an independently verified live task grant and human commit approval", async () => {
  const policy = loadPolicy();
  const secret = "test-followup-signing-secret-at-least-32";
  const now = Math.floor(Date.now() / 1000);
  const id = "11111111-1111-4111-8111-111111111111";
  const args = { operation: "commit", task_id: id };
  const tools = policy.skills["crm.followup_workflow"].allowed_tools;
  let approved = false;
  const reader: TaskGrantReader = async (ctx, operation) => {
    assert.equal(ctx.task_grant?.task_id, id);
    assert.equal(operation, "commit");
    if (!approved) throw new Error("APPROVAL_REQUIRED");
  };
  function gate(withGrant = true, bindingTools = tools) {
    const payload = Buffer.from(
      JSON.stringify({
        version: 2,
        request_id: id,
        actor_id: "test",
        role: "consultant",
        route: "agent",
        selected_tool: null,
        skill_name: "crm.followup_workflow",
        binding_tools: bindingTools,
        binding_version: 1,
        endpoint_id: "business",
        endpoint_revision: 1,
        tool_name: "workflow.create_followup_plan",
        arguments: args,
        contract_fingerprint: policy.contract_fingerprint,
        issued_at: now,
        expires_at: now + 30,
        ...(withGrant
          ? {
              task_grant: {
                task_id: id,
                version: 3,
                lease_token: "a".repeat(64),
              },
            }
          : {}),
      }),
    ).toString("base64url");
    return signedExecutionGate(
      {
        context: payload,
        signature: createHmac("sha256", secret).update(payload).digest("hex"),
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
        bound_tools: [...tools, "mes.get_work_order_status"],
      }),
      "business",
      "consultant",
      reader,
    );
  }
  await assert.rejects(
    gate(false).assertAllowed("workflow.create_followup_plan", args),
    /APPROVAL_REQUIRED/,
  );
  await assert.rejects(
    gate().assertAllowed("workflow.create_followup_plan", args),
    /APPROVAL_REQUIRED/,
  );
  approved = true;
  await gate().assertAllowed("workflow.create_followup_plan", args);
  await assert.rejects(
    gate(true, [...tools, "mes.get_work_order_status"]).assertAllowed(
      "workflow.create_followup_plan",
      args,
    ),
    /FORBIDDEN_TOOL/,
  );
});

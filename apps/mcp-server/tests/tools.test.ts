import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { InMemoryTransport } from "@modelcontextprotocol/sdk/inMemory.js";
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import type { ToolBackend } from "../src/backend.js";
import { createMcpServer } from "../src/server.js";
import type { ExecutionGate } from "../src/security.js";
import { contractFingerprint, toolCatalog } from "../src/tools/registry.js";

const taskId = "11111111-1111-4111-8111-111111111111";
const calls: string[] = [];

const fakeBackend: ToolBackend = {
  async getCustomerOverview(input) {
    calls.push("customer");
    return { customer_code: input.customer_id, name: "模拟客户甲", renewal_date: "2026-10-07", risk_level: "high" };
  },
  async listOpenTickets(input) {
    calls.push("tickets");
    return { customer_code: input.customer_id, tickets: [{ ticket_id: "T1001", status: "open", summary: "模拟问题" }] };
  },
  async getWorkOrderStatus(input) {
    calls.push("work_order");
    return { work_order_code: input.work_order_id, status: "in_progress", updated_at: "2026-09-30T00:00:00Z" };
  },
  async searchSop() {
    calls.push("sop");
    return { matches: [{ document_id: "SOP-01", title: "模拟流程", chunk_id: "SOP-01-1", excerpt: "请核实工单。", score: 0.9 }] };
  },
  async proposeFollowupPlan(input) {
    calls.push("propose");
    return {
      task_id: input.task_id,
      operation: "propose",
      status: "pending_approval",
      candidates: [{ customer_code: "C1001", renewal_date: "2026-10-05", risk_level: "high" }],
    };
  },
};

const allowGate: ExecutionGate = { async assertAllowed(): Promise<void> {} };
const openServers: Array<{ client: Client; server: McpServer }> = [];

async function connect(backend?: ToolBackend, gate?: ExecutionGate): Promise<Client> {
  const server = createMcpServer(backend, gate);
  const client = new Client({ name: "opspilot-test", version: "0.1.0" });
  const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
  await server.connect(serverTransport);
  await client.connect(clientTransport);
  openServers.push({ client, server });
  return client;
}

afterEach(async () => {
  calls.length = 0;
  await Promise.all(openServers.splice(0).map(async ({ client, server }) => {
    await client.close();
    await server.close();
  }));
});

test("MCP client lists exactly the five independently registered domain tools", async () => {
  const client = await connect();
  const listed = await client.listTools();
  assert.deepEqual(listed.tools.map((tool) => tool.name).sort(), toolCatalog.map((tool) => tool.name).sort());
  assert.equal(listed.tools.length, 5);
  assert.ok(listed.tools.every((tool) => tool.inputSchema && tool.outputSchema));
  assert.match(contractFingerprint(), /^[a-f0-9]{64}$/);
});

test("production defaults reject execution even when a tool is visible", async () => {
  const client = await connect();
  const result = await client.callTool({ name: "crm.get_customer_overview", arguments: { customer_id: "C1001" } });
  assert.equal(result.isError, true);
  assert.equal(calls.length, 0);
});

test("independent read tools return their own structured result through SDK", async () => {
  const client = await connect(fakeBackend, allowGate);
  const cases = [
    ["crm.get_customer_overview", { customer_id: "C1001" }, "customer"],
    ["crm.list_open_tickets", { customer_id: "C1001" }, "tickets"],
    ["mes.get_work_order_status", { work_order_id: "WO-1001" }, "work_order"],
    ["knowledge.search_sop", { query: "工单", top_k: 3 }, "sop"],
  ] as const;
  for (const [name, args, expectedCall] of cases) {
    const result = await client.callTool({ name, arguments: args });
    assert.notEqual(result.isError, true, name);
    assert.ok(result.structuredContent, name);
    assert.equal(calls.at(-1), expectedCall);
  }
});

test("Zod rejects malformed and extra arguments before backend invocation", async () => {
  const client = await connect(fakeBackend, allowGate);
  const cases = [
    ["crm.get_customer_overview", { customer_id: "../C1001" }],
    ["crm.list_open_tickets", { customer_id: "C1001", sql: "DROP TABLE" }],
    ["mes.get_work_order_status", { work_order_id: "C1001" }],
    ["knowledge.search_sop", { query: "x", top_k: 99 }],
    ["workflow.create_followup_plan", { operation: "propose", task_id: taskId, window_start: "2026-09-30", window_end: "2026-10-09" }],
  ] as const;
  for (const [name, args] of cases) {
    const result = await client.callTool({ name, arguments: args });
    assert.equal(result.isError, true, name);
  }
  assert.equal(calls.length, 0);
});

test("proposal is only a pending task; commit is blocked until real approval exists", async () => {
  const client = await connect(fakeBackend, allowGate);
  const proposed = await client.callTool({
    name: "workflow.create_followup_plan",
    arguments: { operation: "propose", task_id: taskId, window_start: "2026-09-30", window_end: "2026-10-07" },
  });
  assert.equal(proposed.isError, undefined);
  assert.deepEqual(proposed.structuredContent, {
    task_id: taskId,
    operation: "propose",
    status: "pending_approval",
    candidates: [{ customer_code: "C1001", renewal_date: "2026-10-05", risk_level: "high" }],
  });
  const commit = await client.callTool({ name: "workflow.create_followup_plan", arguments: { operation: "commit", task_id: taskId } });
  assert.equal(commit.isError, true);
  assert.deepEqual(calls, ["propose"]);
});

test("invalid backend output does not leak as a successful Tool result", async () => {
  const client = await connect({ ...fakeBackend, async getCustomerOverview() { return { customer_id: "C1001" }; } }, allowGate);
  const result = await client.callTool({ name: "crm.get_customer_overview", arguments: { customer_id: "C1001" } });
  assert.equal(result.isError, true);
  assert.equal(result.structuredContent, undefined);
});

test("proposal backend cannot claim completion or omit candidate evidence", async () => {
  const client = await connect({
    ...fakeBackend,
    async proposeFollowupPlan() {
      return { task_id: taskId, operation: "propose", status: "completed" };
    },
  }, allowGate);
  const result = await client.callTool({
    name: "workflow.create_followup_plan",
    arguments: { operation: "propose", task_id: taskId, window_start: "2026-09-30", window_end: "2026-10-07" },
  });
  assert.equal(result.isError, true);
});

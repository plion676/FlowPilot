import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import type { ToolBackend } from "../backend.js";
import type { ExecutionGate } from "../security.js";
import { invokeTool } from "../tools/result.js";
import { customerInput, customerOutput, ticketsOutput } from "../tools/schemas.js";
import type { ToolDomain } from "../tools/types.js";

export const crmDomain: ToolDomain = {
  contracts: [
    { name: "crm.get_customer_overview", domain: "crm", riskLevel: "read_only", inputSchema: customerInput, outputSchema: customerOutput },
    { name: "crm.list_open_tickets", domain: "crm", riskLevel: "read_only", inputSchema: customerInput, outputSchema: ticketsOutput },
  ],
  register(server: McpServer, backend: ToolBackend, gate: ExecutionGate, metadata?: Record<string, unknown>): void {
    server.registerTool(
      "crm.get_customer_overview",
      {
        description: "查询模拟客户基础信息、续费日期与风险等级",
        _meta: metadata,
        inputSchema: customerInput,
        outputSchema: customerOutput,
        annotations: { readOnlyHint: true, openWorldHint: false },
      },
      async (input) => invokeTool("crm.get_customer_overview", gate, customerOutput, input, () => backend.getCustomerOverview(input)),
    );
    server.registerTool(
      "crm.list_open_tickets",
      {
        description: "查询指定模拟客户的未关闭工单",
        _meta: metadata,
        inputSchema: customerInput,
        outputSchema: ticketsOutput,
        annotations: { readOnlyHint: true, openWorldHint: false },
      },
      async (input) => invokeTool("crm.list_open_tickets", gate, ticketsOutput, input, () => backend.listOpenTickets(input)),
    );
  },
};

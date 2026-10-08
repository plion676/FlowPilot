import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import type { ToolBackend } from "../backend.js";
import type { ExecutionGate } from "../security.js";
import { invokeTool } from "../tools/result.js";
import { workOrderInput, workOrderOutput } from "../tools/schemas.js";
import type { ToolDomain } from "../tools/types.js";

export const mesDomain: ToolDomain = {
  contracts: [
    { name: "mes.get_work_order_status", domain: "mes", riskLevel: "read_only", inputSchema: workOrderInput, outputSchema: workOrderOutput },
  ],
  register(server: McpServer, backend: ToolBackend, gate: ExecutionGate, metadata?: Record<string, unknown>): void {
    server.registerTool(
      "mes.get_work_order_status",
      {
        description: "查询模拟生产或交付工单状态",
        _meta: metadata,
        inputSchema: workOrderInput,
        outputSchema: workOrderOutput,
        annotations: { readOnlyHint: true, openWorldHint: false },
      },
      async (input) => invokeTool("mes.get_work_order_status", gate, workOrderOutput, input, () => backend.getWorkOrderStatus(input)),
    );
  },
};

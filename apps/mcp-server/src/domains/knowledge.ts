import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import type { ToolBackend } from "../backend.js";
import type { ExecutionGate } from "../security.js";
import { invokeTool } from "../tools/result.js";
import { searchInput, searchOutput } from "../tools/schemas.js";
import type { ToolDomain } from "../tools/types.js";

export const knowledgeDomain: ToolDomain = {
  contracts: [
    { name: "knowledge.search_sop", domain: "knowledge", riskLevel: "read_only", inputSchema: searchInput, outputSchema: searchOutput },
  ],
  register(server: McpServer, backend: ToolBackend, gate: ExecutionGate, metadata?: Record<string, unknown>): void {
    server.registerTool(
      "knowledge.search_sop",
      {
        description: "检索模拟 SOP 文档，返回带文档与片段标识的来源",
        _meta: metadata,
        inputSchema: searchInput,
        outputSchema: searchOutput,
        annotations: { readOnlyHint: true, openWorldHint: false },
      },
      async (input) => invokeTool("knowledge.search_sop", gate, searchOutput, input, () => backend.searchSop(input)),
    );
  },
};

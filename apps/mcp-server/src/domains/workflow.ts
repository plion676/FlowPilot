import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import type { ToolBackend } from "../backend.js";
import type { ExecutionGate } from "../security.js";
import { invokeTool } from "../tools/result.js";
import {
  followupInput,
  followupOutput,
  proposalInput,
  validatedFollowupOutput,
} from "../tools/schemas.js";
import type { ToolDomain } from "../tools/types.js";

export const workflowDomain: ToolDomain = {
  contracts: [
    {
      name: "workflow.create_followup_plan",
      domain: "workflow",
      riskLevel: "approval_required",
      inputSchema: followupInput,
      outputSchema: followupOutput,
    },
  ],
  register(
    server: McpServer,
    backend: ToolBackend,
    gate: ExecutionGate,
    metadata?: Record<string, unknown>,
  ): void {
    server.registerTool(
      "workflow.create_followup_plan",
      {
        description:
          "创建高风险客户回访计划提案；提交操作必须由人工审批流程授权",
        _meta: metadata,
        inputSchema: followupInput,
        outputSchema: followupOutput,
        annotations: {
          readOnlyHint: false,
          destructiveHint: false,
          openWorldHint: false,
        },
      },
      async (input) => {
        if (input.operation === "commit") {
          if (!backend.commitFollowupPlan)
            return {
              content: [{ type: "text" as const, text: "APPROVAL_REQUIRED" }],
              isError: true,
            };
          return invokeTool(
            "workflow.create_followup_plan",
            gate,
            validatedFollowupOutput,
            input,
            () => backend.commitFollowupPlan!(input),
          );
        }
        return invokeTool(
          "workflow.create_followup_plan",
          gate,
          validatedFollowupOutput,
          input,
          () => backend.proposeFollowupPlan(proposalInput.parse(input)),
        );
      },
    );
  },
};

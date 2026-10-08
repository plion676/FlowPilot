import { createHash } from "node:crypto";
import { z } from "zod";
import { crmDomain } from "../domains/crm.js";
import { knowledgeDomain } from "../domains/knowledge.js";
import { mesDomain } from "../domains/mes.js";
import { workflowDomain } from "../domains/workflow.js";
import type { ToolContract, ToolDomain } from "./types.js";

export const domains: readonly ToolDomain[] = [crmDomain, mesDomain, knowledgeDomain, workflowDomain];
export const toolCatalog: readonly ToolContract[] = domains.flatMap((domain) => domain.contracts);

const names = toolCatalog.map((tool) => tool.name);
if (new Set(names).size !== names.length || names.length !== 5) {
  throw new Error("MCP Tool 目录必须恰好包含五个不重名工具");
}

// Stable metadata for the later Agent/MCP startup contract comparison.
export function contractFingerprint(): string {
  const contract = toolCatalog.map((tool) => ({
    name: tool.name,
    risk_level: tool.riskLevel,
    input_schema: z.toJSONSchema(tool.inputSchema),
    output_schema: z.toJSONSchema(tool.outputSchema),
  }));
  return createHash("sha256").update(JSON.stringify(contract)).digest("hex");
}

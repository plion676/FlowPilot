import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import type { z } from "zod";
import type { ToolBackend } from "../backend.js";
import type { ExecutionGate } from "../security.js";

export type RiskLevel = "read_only" | "approval_required";

export interface ToolContract {
  readonly name: string;
  readonly domain: "crm" | "mes" | "knowledge" | "workflow";
  readonly riskLevel: RiskLevel;
  readonly inputSchema: z.ZodType;
  readonly outputSchema: z.ZodType;
}

export interface ToolDomain {
  readonly contracts: readonly ToolContract[];
  register(server: McpServer, backend: ToolBackend, gate: ExecutionGate, metadata?: Record<string, unknown>): void;
}

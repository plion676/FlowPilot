import { z } from "zod";
import type { ExecutionGate } from "../security.js";

export async function invokeTool<T extends z.ZodType>(
  toolName: string,
  gate: ExecutionGate,
  outputSchema: T,
  input: Record<string, unknown>,
  action: () => Promise<unknown>,
) {
  try {
    await gate.assertAllowed(toolName, input);
    const output = outputSchema.parse(await action());
    return {
      content: [{ type: "text" as const, text: JSON.stringify(output) }],
      structuredContent: output as Record<string, unknown>,
    };
  } catch (error) {
    const code =
      error instanceof z.ZodError
        ? "INVALID_BACKEND_RESULT"
        : error instanceof Error &&
            /^(TASK_EXPIRED|TASK_CONFLICT|LEASE_LOST|CUSTOMER_CHANGED|CANDIDATE_LIMIT_REACHED|INVALID_ARGUMENTS|SOP_INDEX_NOT_READY|EMBEDDING_UNAVAILABLE|INVALID_SOP_EVIDENCE|ENDPOINT_DISABLED|TOOL_EXECUTION_UNAVAILABLE|BUSINESS_BACKEND_UNAVAILABLE|APPROVAL_REQUIRED|UNAUTHORIZED|INVALID_BINDING|BINDING_CHANGED|FORBIDDEN_TOOL|FORBIDDEN_SKILL|NOT_FOUND|DEPENDENCY_UNAVAILABLE)$/.test(
              error.message,
            )
          ? error.message
          : "TOOL_EXECUTION_FAILED";
    return { content: [{ type: "text" as const, text: code }], isError: true };
  }
}

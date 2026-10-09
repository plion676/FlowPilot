import { z } from "zod";
import type {
  BindingReader,
  TaskExecutionContext,
  TaskGrantReader,
} from "./security.js";

export interface ToolBackend {
  commitFollowupPlan?(input: {
    operation: "commit";
    task_id: string;
  }): Promise<unknown>;
  getCustomerOverview(input: { customer_id: string }): Promise<unknown>;
  listOpenTickets(input: { customer_id: string }): Promise<unknown>;
  getWorkOrderStatus(input: { work_order_id: string }): Promise<unknown>;
  searchSop(input: { query: string; top_k: number }): Promise<unknown>;
  proposeFollowupPlan(input: {
    operation: "propose";
    task_id: string;
    window_start: string;
    window_end: string;
  }): Promise<unknown>;
}

export function goTaskGrantReader(
  baseUrl: string,
  serviceToken: string,
): TaskGrantReader {
  return async (context, operation) => {
    if (!context.task_grant) throw new Error("APPROVAL_REQUIRED");
    await workflowPost(
      baseUrl,
      serviceToken,
      context,
      "internal/workflow/authorize",
      { operation },
    );
  };
}
async function workflowPost(
  baseUrl: string,
  token: string,
  context: TaskExecutionContext,
  path: string,
  extra: Record<string, unknown>,
) {
  let response: Response;
  try {
    response = await fetch(new URL(path, baseUrl), {
      method: "POST",
      redirect: "error",
      signal: AbortSignal.timeout(5000),
      headers: {
        "Content-Type": "application/json",
        "X-Internal-Service-Token": token,
        "X-Request-ID": context.request_id,
      },
      body: JSON.stringify({
        role: context.role,
        actor_id: context.actor_id,
        endpoint_id: context.endpoint_id,
        grant: context.task_grant,
        ...extra,
      }),
    });
  } catch {
    throw new Error("DEPENDENCY_UNAVAILABLE");
  }
  const body = (await response.json().catch(() => ({}))) as Record<
    string,
    unknown
  > & { error?: { code?: unknown } };
  if (!response.ok) {
    const code = body.error?.code;
    if (
      typeof code === "string" &&
      /^(APPROVAL_REQUIRED|TASK_EXPIRED|TASK_CONFLICT|LEASE_LOST|CUSTOMER_CHANGED|CANDIDATE_LIMIT_REACHED|BINDING_CHANGED|FORBIDDEN_TOOL|NOT_FOUND|INVALID_ARGUMENTS)$/.test(
        code,
      )
    )
      throw new Error(code);
    throw new Error("DEPENDENCY_UNAVAILABLE");
  }
  return body;
}

const bindingSchema = z.object({
  id: z.string().regex(/^[a-z][a-z0-9_]{0,79}$/),
  version: z.number().int().positive(),
  bound_tools: z
    .array(z.string())
    .max(5)
    .refine((names) => new Set(names).size === names.length),
  endpoint_id: z.string(),
  endpoint_revision: z.number().int().positive(),
  enabled: z.boolean(),
});

// Reads the authoritative store on every Tool call; never caches revocations.
export function goBindingReader(
  baseUrl: string,
  serviceToken: string,
  endpointId = "business",
): BindingReader {
  return async (agentId, requestId) => {
    try {
      const response = await fetch(
        new URL(
          `internal/agents/${encodeURIComponent(agentId)}/authorization?endpoint_id=${encodeURIComponent(endpointId)}`,
          baseUrl,
        ),
        {
          headers: {
            "X-Internal-Service-Token": serviceToken,
            "X-Request-ID": requestId,
          },
          signal: AbortSignal.timeout(5000),
        },
      );
      if (response.status === 404 || response.status === 400)
        throw new Error("FORBIDDEN_TOOL");
      if (!response.ok) throw new Error("DEPENDENCY_UNAVAILABLE");
      return bindingSchema.parse(await response.json());
    } catch (error) {
      if (error instanceof Error && error.message === "FORBIDDEN_TOOL")
        throw error;
      throw new Error("DEPENDENCY_UNAVAILABLE");
    }
  };
}

const unavailable = async (): Promise<never> => {
  throw new Error("BUSINESS_BACKEND_UNAVAILABLE");
};

// Only a test fake or a later, policy-protected internal REST adapter may replace this.
export const unavailableBackend: ToolBackend = {
  getCustomerOverview: unavailable,
  listOpenTickets: unavailable,
  getWorkOrderStatus: unavailable,
  searchSop: unavailable,
  proposeFollowupPlan: unavailable,
};

export function goReadBackend(
  baseUrl: string,
  serviceToken: string,
  requestId: string,
  ragBaseUrl = process.env.RAG_BASE_URL ?? "http://127.0.0.1:8000/",
  workflowContext?: TaskExecutionContext,
): ToolBackend {
  const base = new URL(baseUrl);
  if (
    !["http:", "https:"].includes(base.protocol) ||
    base.username ||
    base.password ||
    base.search ||
    base.hash ||
    base.pathname !== "/"
  ) {
    throw new Error("BUSINESS_BASE_URL 无效");
  }
  if (serviceToken.length < 16) throw new Error("INTERNAL_SERVICE_TOKEN 太短");
  const get = async (path: string): Promise<unknown> => {
    let response: Response;
    try {
      response = await fetch(new URL(path, base), {
        headers: {
          "X-Internal-Service-Token": serviceToken,
          "X-Request-ID": requestId,
        },
        signal: AbortSignal.timeout(5000),
      });
    } catch {
      throw new Error("DEPENDENCY_UNAVAILABLE");
    }
    if (response.status === 404) throw new Error("NOT_FOUND");
    if (!response.ok) throw new Error("DEPENDENCY_UNAVAILABLE");
    try {
      return await response.json();
    } catch {
      throw new Error("DEPENDENCY_UNAVAILABLE");
    }
  };
  return {
    getCustomerOverview: ({ customer_id }) =>
      get(`internal/crm/customers/${encodeURIComponent(customer_id)}`),
    listOpenTickets: ({ customer_id }) =>
      get(
        `internal/crm/customers/${encodeURIComponent(customer_id)}/tickets?status=open`,
      ),
    getWorkOrderStatus: ({ work_order_id }) =>
      get(`internal/mes/work-orders/${encodeURIComponent(work_order_id)}`),
    searchSop: async (input) => {
      let response: Response;
      try {
        const ragBase = new URL(ragBaseUrl);
        if (
          !["http:", "https:"].includes(ragBase.protocol) ||
          ragBase.username ||
          ragBase.password ||
          ragBase.search ||
          ragBase.hash ||
          ragBase.pathname !== "/"
        )
          throw new Error("INVALID_RAG_URL");
        response = await fetch(new URL("internal/knowledge/search", ragBase), {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-Internal-Service-Token": serviceToken,
            "X-Request-ID": requestId,
          },
          body: JSON.stringify(input),
          signal: AbortSignal.timeout(12000),
          redirect: "error",
        });
      } catch {
        throw new Error("DEPENDENCY_UNAVAILABLE");
      }
      if (!response.ok) {
        const body = (await response.json().catch(() => ({}))) as {
          error?: { code?: unknown };
        };
        const code = body.error?.code;
        if (
          typeof code === "string" &&
          [
            "SOP_INDEX_NOT_READY",
            "EMBEDDING_UNAVAILABLE",
            "INVALID_SOP_EVIDENCE",
          ].includes(code)
        )
          throw new Error(code);
        throw new Error("DEPENDENCY_UNAVAILABLE");
      }
      return response.json();
    },
    proposeFollowupPlan: async (input) => {
      if (!workflowContext?.task_grant) throw new Error("APPROVAL_REQUIRED");
      return workflowPost(
        baseUrl,
        serviceToken,
        workflowContext,
        "internal/workflow/followup",
        { input },
      );
    },
    commitFollowupPlan: async (input) => {
      if (!workflowContext?.task_grant) throw new Error("APPROVAL_REQUIRED");
      return workflowPost(
        baseUrl,
        serviceToken,
        workflowContext,
        "internal/workflow/followup",
        { input },
      );
    },
  };
}

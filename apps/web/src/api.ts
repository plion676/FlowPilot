export type Citation = {
  document_id: string;
  title: string;
  chunk_id: string;
  excerpt: string;
  score?: number;
  source_id?: string;
  endpoint_id?: string;
  version?: string;
  source_path?: string;
};

export type FollowupTask = {
  task_id: string;
  status: string;
  version: number;
  window_start: string;
  window_end: string;
  endpoint_id: string;
  error_code: string;
  expires_at: string;
  proposal: {
    candidates?: {
      customer_code: string;
      renewal_date: string;
      risk_level: string;
      action: string;
      due_date: string;
    }[];
    analysis?: string;
    citations?: Citation[];
  };
  result: { plan_count?: number; plan_ids?: string[] };
};
export const getTasks = async (signal?: AbortSignal): Promise<FollowupTask[]> => {
  const result = await managementTaskRequest<{ tasks: FollowupTask[] }>("", { signal });
  return Array.isArray(result.tasks) ? result.tasks : [];
};
export const taskDecision = (task: FollowupTask, decision: string, key: string) =>
  managementTaskRequest<FollowupTask>(`/${task.task_id}/decision`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ decision, expected_version: task.version, idempotency_key: key }),
  });
export const taskAudit = (id: string) =>
  managementTaskRequest<{
    events: { id: string; event: string; version: number; created_at: string }[];
  }>(`/${id}/audit`);
async function managementTaskRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api/tasks${path}`, {
    ...init,
    headers: { "X-Demo-Role": "consultant", ...init?.headers },
  });
  const payload = await response.json();
  if (!response.ok)
    throw new ApiRequestError(
      payload.error?.message ?? "任务服务暂不可用。",
      payload.error?.code ?? `HTTP_${response.status}`,
    );
  return payload as T;
}

export type ChatResponse = {
  trace_id?: string | null;
  trace_incomplete?: boolean;
  request_id: string;
  message: string;
  model: string;
  route: { kind: "agent" | "direct_tool" | "skill" | "clarification"; name?: string } | null;
  data: Record<string, unknown> | null;
  citations: Citation[];
  task: { task_id?: string; id?: string; status?: string } | null;
  tool_calls?: {
    name: string;
    arguments: Record<string, unknown>;
    result: Record<string, unknown>;
    endpoint_id?: string;
    endpoint_name?: string;
    endpoint_revision?: number;
  }[];
  binding_version?: number;
  skill_name?: string | null;
};

export type ToolRef = { endpoint_id: string; tool_name: string };
export type McpEndpoint = {
  id: string;
  name: string;
  role_id: string;
  url: string;
  credential_profile: string;
  enabled: boolean;
  version: number;
  execution_revision: number;
};
export type EndpointCheck = {
  endpoint_id: string;
  checked_execution_revision: number;
  status: string;
  error_code: string;
  checked_at: string;
};
export type EndpointDirectory = {
  endpoints: McpEndpoint[];
  checks: EndpointCheck[];
  credential_profiles: { id: string; configured: boolean }[];
};
export type AgentProfile = {
  id: string;
  name: string;
  version: number;
  endpoint_ids: string[];
  tools: ToolRef[];
  endpoints: McpEndpoint[];
};
export type ManagedTool = {
  endpoint_id: string;
  endpoint_name: string;
  endpoint_role_id: string;
  name: string;
  domain: string;
  description: string;
  input_schema: Record<string, unknown>;
  risk_level: "read_only" | "approval_required" | "unknown";
  bindable: boolean;
  available: boolean;
  status: "ready" | "not_ready" | "contract_mismatch";
};

export type HealthResponse = { status: string; llm: string };

export type TraceRun = {
  trace_id: string;
  request_id: string;
  role: string;
  summary: string;
  model: string;
  status: string;
  incomplete: boolean;
  error_code: string;
  last_sequence: number;
  created_at: string;
  updated_at: string;
  finished_at: string | null;
};
export type TraceEvent = {
  sequence: number;
  event_id: string;
  kind: string;
  step: number;
  call_id: string;
  payload: Record<string, unknown>;
  created_at: string;
};
async function traceRequest<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`/api/traces${path}`, {
    signal,
    headers: { "X-Demo-Role": "consultant" },
  });
  const result = await response.json();
  if (!response.ok)
    throw new ApiRequestError(
      result.error?.message ?? "Trace 暂不可用",
      result.error?.code ?? `HTTP_${response.status}`,
    );
  return result as T;
}
export const getTraces = (search: string, offset: number, signal?: AbortSignal) =>
  traceRequest<{ traces: TraceRun[]; has_more: boolean }>(
    `?search=${encodeURIComponent(search)}&offset=${offset}`,
    signal,
  );
export const getTrace = (id: string, after: number, signal?: AbortSignal) =>
  traceRequest<{ trace: TraceRun; events: TraceEvent[]; has_more: boolean }>(
    `/${encodeURIComponent(id)}?after_sequence=${after}`,
    signal,
  );

export class ApiRequestError extends Error {
  constructor(
    message: string,
    public readonly code: string,
    public readonly requestId?: string,
    public readonly retryable = false,
    public readonly traceId?: string,
  ) {
    super(message);
    this.name = "ApiRequestError";
  }
}

export async function sendChat(
  message: string,
  signal?: AbortSignal,
  skillName?: string | null,
): Promise<ChatResponse> {
  const response = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Demo-Role": "consultant" },
    body: JSON.stringify({ message, ...(skillName ? { skill_name: skillName } : {}) }),
    signal,
  });
  const payload: unknown = await response.json();
  if (!response.ok) {
    const error =
      typeof payload === "object" && payload !== null && "error" in payload
        ? (payload as { error: Record<string, unknown> }).error
        : {};
    throw new ApiRequestError(
      typeof error.message === "string" ? error.message : "请求未完成，请稍后重试。",
      typeof error.code === "string" ? error.code : `HTTP_${response.status}`,
      typeof error.request_id === "string" ? error.request_id : undefined,
      error.retryable === true,
      response.headers.get("X-Trace-ID") ?? undefined,
    );
  }
  if (
    typeof payload !== "object" ||
    payload === null ||
    !("request_id" in payload) ||
    !("message" in payload)
  ) {
    throw new ApiRequestError("服务返回了无效响应。", "INVALID_RESPONSE");
  }
  return payload as ChatResponse;
}

export async function getHealth(signal?: AbortSignal): Promise<HealthResponse> {
  const response = await fetch("/api/health", { signal });
  if (!response.ok) throw new Error("Agent API 不可用");
  return (await response.json()) as HealthResponse;
}

async function managementRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api/admin${path}`, init);
  const payload = await response.json();
  if (!response.ok) {
    throw new ApiRequestError(
      payload.error?.message ?? "工具配置请求失败，请稍后重试。",
      payload.error?.code ?? `HTTP_${response.status}`,
      payload.error?.request_id,
    );
  }
  return payload as T;
}

export const getEndpoints = (signal?: AbortSignal) =>
  managementRequest<EndpointDirectory>("/mcp-endpoints", { signal });
export const checkEndpoint = (id: string) =>
  managementRequest<{ tools: ManagedTool[]; status: string }>(
    `/mcp-endpoints/${encodeURIComponent(id)}/check`,
    { method: "POST" },
  );
export const getEndpointTools = (id: string, signal?: AbortSignal) =>
  managementRequest<{ tools: ManagedTool[]; stale: boolean; status: string }>(
    `/mcp-endpoints/${encodeURIComponent(id)}/tools`,
    { signal },
  );
export const saveEndpoint = (
  endpoint: Omit<McpEndpoint, "version" | "execution_revision">,
  version?: number,
) =>
  managementRequest<McpEndpoint>(
    `/mcp-endpoints${version ? `/${encodeURIComponent(endpoint.id)}` : ""}`,
    {
      method: version ? "PUT" : "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...endpoint, ...(version ? { expected_version: version } : {}) }),
    },
  );
export const getAgents = (signal?: AbortSignal) =>
  managementRequest<{ agents: AgentProfile[] }>("/agents", { signal });
export const saveAgentTools = (
  agent: AgentProfile,
  endpointIds: string[],
  tools: ToolRef[],
  endpoints: McpEndpoint[],
) =>
  managementRequest<AgentProfile>(`/agents/${encodeURIComponent(agent.id)}/capabilities`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      tools,
      endpoint_ids: endpointIds,
      expected_version: agent.version,
      expected_endpoint_versions: Object.fromEntries(
        endpoints
          .filter((endpoint) => endpointIds.includes(endpoint.id))
          .map((endpoint) => [endpoint.id, endpoint.version]),
      ),
    }),
  });

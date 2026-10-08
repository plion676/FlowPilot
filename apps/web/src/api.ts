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

export type ChatResponse = {
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

export class ApiRequestError extends Error {
  constructor(
    message: string,
    public readonly code: string,
    public readonly requestId?: string,
    public readonly retryable = false,
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

import { useEffect, useState } from "react";
import {
  getAgents,
  getEndpoints,
  getEndpointTools,
  checkEndpoint,
  saveAgentTools,
  type AgentProfile,
  type ManagedTool,
  type McpEndpoint,
  type ToolRef,
} from "./api";

const labels: Record<string, string> = {
  crm: "CRM · 客户服务",
  mes: "MES · 生产交付",
  knowledge: "知识库",
  workflow: "工作流",
};
const key = (ref: ToolRef) => `${ref.endpoint_id}/${ref.tool_name}`;
const equal = (a: string[], b: string[]) => [...a].sort().join("\n") === [...b].sort().join("\n");
const message = (error: unknown) =>
  error instanceof Error ? error.message : "加载失败，请检查服务。";
function fuzzy(text: string, query: string) {
  return query
    .toLowerCase()
    .trim()
    .split(/\s+/)
    .every((word) => {
      const haystack = text.toLowerCase();
      if (haystack.includes(word)) return true;
      let index = 0;
      for (const char of haystack) if (char === word[index]) index++;
      return index === word.length;
    });
}

export function ToolManager({ onDirtyChange }: { onDirtyChange?: (dirty: boolean) => void }) {
  const [agents, setAgents] = useState<AgentProfile[]>([]);
  const [endpoints, setEndpoints] = useState<McpEndpoint[]>([]);
  const [tools, setTools] = useState<ManagedTool[]>([]);
  const [agentId, setAgentId] = useState("consultant");
  const [sources, setSources] = useState<string[]>([]);
  const [selected, setSelected] = useState<ToolRef[]>([]);
  const [query, setQuery] = useState("");
  const [domain, setDomain] = useState("all");
  const [boundOnly, setBoundOnly] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [checking, setChecking] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [reload, setReload] = useState(0);
  const agent = agents.find((item) => item.id === agentId);
  const dirty =
    !!agent &&
    (!equal(sources, agent.endpoint_ids) || !equal(selected.map(key), agent.tools.map(key)));
  useEffect(() => {
    onDirtyChange?.(dirty);
    return () => onDirtyChange?.(false);
  }, [dirty, onDirtyChange]);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    void Promise.all([getEndpoints(controller.signal), getAgents(controller.signal)])
      .then(async ([directory, profiles]) => {
        const results = await Promise.allSettled(
          directory.endpoints.map(async (endpoint) => {
            const result = await getEndpointTools(endpoint.id, controller.signal);
            return result.tools.map((tool) => ({
              ...tool,
              bindable:
                tool.bindable && endpoint.enabled && !result.stale && result.status === "ready",
              available:
                tool.available && endpoint.enabled && !result.stale && result.status === "ready",
            }));
          }),
        );
        if (controller.signal.aborted) return;
        setEndpoints(directory.endpoints);
        setTools(results.flatMap((result) => (result.status === "fulfilled" ? result.value : [])));
        setAgents(profiles.agents);
        const current = profiles.agents.find((item) => item.id === agentId) ?? profiles.agents[0];
        setAgentId(current?.id ?? "consultant");
        setSources(current?.endpoint_ids ?? []);
        setSelected(current?.tools ?? []);
        setNotice("");
      })
      .catch((failure) => {
        if (!controller.signal.aborted) setError(message(failure));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [reload]);

  function toggleEndpoint(id: string) {
    if (sources.includes(id)) {
      if (
        selected.some((ref) => ref.endpoint_id === id) &&
        !window.confirm("移除这个 Endpoint 会一并取消其工具绑定，保存后生效。继续吗？")
      )
        return;
      setSources((current) => current.filter((value) => value !== id));
      setSelected((current) => current.filter((ref) => ref.endpoint_id !== id));
    } else setSources((current) => [...current, id]);
    setNotice("");
  }
  function toggle(ref: ToolRef) {
    setSelected((current) =>
      current.some((item) => key(item) === key(ref))
        ? current.filter((item) => key(item) !== key(ref))
        : [...current, ref],
    );
    setNotice("");
  }
  async function save() {
    if (!agent) return;
    setSaving(true);
    setError("");
    setNotice("");
    try {
      const saved = await saveAgentTools(agent, sources, selected, endpoints);
      setAgents((current) => current.map((item) => (item.id === saved.id ? saved : item)));
      setSelected(saved.tools);
      setSources(saved.endpoint_ids);
      setNotice("已保存，新的对话请求将使用这份连接与工具配置。");
    } catch (failure) {
      setError(message(failure));
    } finally {
      setSaving(false);
    }
  }
  async function check(id: string) {
    setChecking(id);
    setError("");
    try {
      const result = await checkEndpoint(id);
      setTools((current) => [
        ...current.filter((item) => item.endpoint_id !== id),
        ...result.tools,
      ]);
      setNotice("连接检查完成，工具目录已更新；绑定尚未自动改变。");
    } catch (failure) {
      setTools((current) =>
        current.map((item) =>
          item.endpoint_id === id ? { ...item, bindable: false, available: false } : item,
        ),
      );
      setError(message(failure));
    } finally {
      setChecking("");
    }
  }
  const visible = tools.filter(
    (tool) =>
      endpoints.some(
        (endpoint) => endpoint.id === tool.endpoint_id && endpoint.role_id === agentId,
      ) &&
      sources.includes(tool.endpoint_id) &&
      (domain === "all" || domain === tool.domain) &&
      (!boundOnly ||
        selected.some(
          (ref) => key(ref) === key({ endpoint_id: tool.endpoint_id, tool_name: tool.name }),
        )) &&
      fuzzy(
        `${tool.name} ${tool.description} ${tool.endpoint_name} ${tool.endpoint_role_id} ${labels[tool.domain] ?? tool.domain}`,
        query,
      ),
  );

  return (
    <div className="workspace-content tool-manager">
      <div className="welcome">
        <div>
          <span className="eyebrow">AGENT CAPABILITIES</span>
          <h1>
            角色工具绑定<span className="title-dot">.</span>
          </h1>
          <p>先选择当前角色的 MCP 连接，再按领域查找并勾选工具。</p>
        </div>
        <button
          className="secondary-button"
          disabled={loading || saving || !!checking}
          onClick={() => {
            if (!dirty || window.confirm("放弃未保存的绑定修改并刷新？"))
              setReload((value) => value + 1);
          }}
        >
          {dirty ? "放弃更改并刷新" : "刷新配置"}
        </button>
      </div>
      <div className="agent-selector">
        <div>
          <label htmlFor="managed-agent">当前 Agent</label>
          <select
            id="managed-agent"
            value={agentId}
            disabled={loading || saving || dirty}
            onChange={(event) => {
              const next = agents.find((item) => item.id === event.target.value);
              setAgentId(event.target.value);
              setSources(next?.endpoint_ids ?? []);
              setSelected(next?.tools ?? []);
              setNotice("");
            }}
          >
            {agents.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name} · {item.id}
              </option>
            ))}
          </select>
        </div>
        <p>连接授权与工具授权分开保存，ReAct 自主选择已获准的能力。</p>
        {agent && <span className="config-version">配置 v{agent.version}</span>}
      </div>
      {error && (
        <div className="management-error" role="alert">
          {error}
        </div>
      )}
      {notice && (
        <div className="management-notice" role="status">
          {notice}
        </div>
      )}
      <section className="endpoint-selection" aria-label="角色 MCP 连接">
        <span className="section-kicker">第一步 · 选择 Endpoint</span>
        <div className="endpoint-options">
          {endpoints
            .filter((endpoint) => endpoint.role_id === agentId)
            .map((endpoint) => (
              <div className="endpoint-option" key={endpoint.id}>
                <label>
                  <input
                    type="checkbox"
                    aria-label={`连接 ${endpoint.id}`}
                    checked={sources.includes(endpoint.id)}
                    disabled={
                      loading || saving || (!endpoint.enabled && !sources.includes(endpoint.id))
                    }
                    onChange={() => toggleEndpoint(endpoint.id)}
                  />
                  <span>
                    <strong>{endpoint.name}</strong>
                    <small>
                      {agent?.name} · {endpoint.id} · {endpoint.enabled ? "启用" : "已停用"}
                    </small>
                  </span>
                </label>
                <button
                  className="secondary-button"
                  disabled={!!checking || saving || !endpoint.enabled}
                  onClick={() => void check(endpoint.id)}
                >
                  {checking === endpoint.id ? "检查中…" : "检查工具"}
                </button>
              </div>
            ))}
        </div>
        {!endpoints.some((endpoint) => endpoint.role_id === agentId) && (
          <p>当前角色尚无 MCP 连接，请在“MCP 连接”中登记。</p>
        )}
      </section>
      <div className="tool-layout">
        <aside className="domain-panel" aria-label="工具领域">
          <span className="section-kicker">第二步 · 选择工具</span>
          <button className={domain === "all" ? "selected" : ""} onClick={() => setDomain("all")}>
            全部领域
          </button>
          {Object.entries(labels).map(([id, label]) => (
            <button
              key={id}
              className={domain === id ? "selected" : ""}
              onClick={() => setDomain(id)}
            >
              {label}
            </button>
          ))}
          <p>新增连接和工具不会自动授权。</p>
        </aside>
        <section className="tool-catalog" aria-label="MCP 工具目录">
          <div className="tool-search-row">
            <input
              type="search"
              aria-label="搜索工具"
              placeholder="搜索来源、工具名称、描述或领域…"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
            <label className="bound-filter">
              <input
                type="checkbox"
                checked={boundOnly}
                onChange={(event) => setBoundOnly(event.target.checked)}
              />
              仅看已选
            </label>
          </div>
          <div className="catalog-heading">
            <span>{loading ? "正在读取配置…" : `${visible.length} 个来源工具`}</span>
            <span>MCP · Endpoint 隔离</span>
          </div>
          {!loading && !visible.length && (
            <div className="tool-empty">没有匹配的工具。请先选择连接并检查目录，或更换搜索词。</div>
          )}
          {endpoints
            .filter((endpoint) => visible.some((tool) => tool.endpoint_id === endpoint.id))
            .map((endpoint) => (
              <section key={endpoint.id} className="endpoint-tool-group">
                <h2>
                  {endpoint.name}{" "}
                  <small>
                    {agent?.name} · {endpoint.id}
                  </small>
                </h2>
                {visible
                  .filter((tool) => tool.endpoint_id === endpoint.id)
                  .map((tool) => {
                    const ref = { endpoint_id: tool.endpoint_id, tool_name: tool.name };
                    const bound = selected.some((item) => key(item) === key(ref));
                    return (
                      <article key={key(ref)} className={`tool-card ${bound ? "is-bound" : ""}`}>
                        <label className="tool-card-label">
                          <input
                            type="checkbox"
                            aria-label={`绑定 ${tool.endpoint_id} / ${tool.name}`}
                            checked={bound}
                            disabled={saving || !!checking || (!tool.bindable && !bound)}
                            onChange={() => toggle(ref)}
                          />
                          <span>
                            <strong>{tool.description}</strong>
                            <code>{tool.name}</code>
                          </span>
                        </label>
                        <div className="tool-badges">
                          <span>{labels[tool.domain] ?? tool.domain}</span>
                          <span>
                            {tool.risk_level === "approval_required"
                              ? "需人工确认"
                              : tool.risk_level === "read_only"
                                ? "只读"
                                : "风险未核验"}
                          </span>
                          <span className={tool.available ? "ready-badge" : "not-ready-badge"}>
                            {!tool.bindable
                              ? "来源未验证/不可用"
                              : tool.available
                                ? "已接通"
                                : "尚未接通"}
                          </span>
                        </div>
                        <details className="tool-schema">
                          <summary>查看参数定义</summary>
                          <pre>{JSON.stringify(tool.input_schema, null, 2)}</pre>
                        </details>
                      </article>
                    );
                  })}
              </section>
            ))}
        </section>
        <aside className="binding-summary" aria-label="已选工具">
          <span className="section-kicker">当前配置</span>
          <h2>
            已选工具 <span>{selected.length}</span>
          </h2>
          <p>{sources.length} 个 Endpoint。保存后生效，撤权不会改变其他角色。</p>
          {selected.length ? (
            <ul>
              {selected.map((ref) => (
                <li key={key(ref)}>
                  <code>
                    {ref.endpoint_id}
                    <br />
                    {ref.tool_name}
                  </code>
                  <button
                    aria-label={`移除 ${ref.endpoint_id} / ${ref.tool_name}`}
                    disabled={saving}
                    onClick={() => toggle(ref)}
                  >
                    ×
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <div className="binding-empty">未选择工具，Agent 仍可进行普通对话。</div>
          )}
          <div className="binding-save">
            <span>{dirty ? "有未保存的修改" : "配置已同步"}</span>
            <button
              className="primary-button"
              disabled={!dirty || saving || loading || !!checking}
              onClick={() => void save()}
            >
              {saving ? "保存中…" : "保存工具绑定"}
            </button>
          </div>
          <small>本地开发配置 · 仅模拟业务数据</small>
        </aside>
      </div>
    </div>
  );
}

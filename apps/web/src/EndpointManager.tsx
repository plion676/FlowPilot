import { useEffect, useState } from "react";
import {
  checkEndpoint,
  getEndpoints,
  getAgents,
  saveEndpoint,
  type EndpointDirectory,
  type McpEndpoint,
  type AgentProfile,
} from "./api";

type Form = Omit<McpEndpoint, "version" | "execution_revision">;
const empty: Form = {
  id: "",
  name: "",
  role_id: "",
  url: "http://127.0.0.1:3100/mcp/consultant",
  credential_profile: "local",
  enabled: true,
};
const asForm = (endpoint: McpEndpoint): Form => ({
  id: endpoint.id,
  name: endpoint.name,
  role_id: endpoint.role_id,
  url: endpoint.url,
  credential_profile: endpoint.credential_profile,
  enabled: endpoint.enabled,
});
const message = (failure: unknown) =>
  failure instanceof Error ? failure.message : "请求失败，请检查服务。";

export function EndpointManager({ onDirtyChange }: { onDirtyChange?: (dirty: boolean) => void }) {
  const [directory, setDirectory] = useState<EndpointDirectory>({
    endpoints: [],
    checks: [],
    credential_profiles: [],
  });
  const [agents, setAgents] = useState<AgentProfile[]>([]);
  const [editing, setEditing] = useState<McpEndpoint>();
  const [form, setForm] = useState<Form>(empty);
  const [query, setQuery] = useState("");
  const [role, setRole] = useState("all");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [checking, setChecking] = useState("");
  const [loading, setLoading] = useState(true);
  const dirty = JSON.stringify(form) !== JSON.stringify(editing ? asForm(editing) : empty);
  useEffect(() => {
    onDirtyChange?.(dirty);
    return () => onDirtyChange?.(false);
  }, [dirty, onDirtyChange]);
  useEffect(() => {
    const controller = new AbortController();
    void Promise.all([getEndpoints(controller.signal), getAgents(controller.signal)])
      .then(([data, profiles]) => {
        if (!controller.signal.aborted) {
          setDirectory(data);
          setAgents(profiles.agents);
        }
      })
      .catch((failure) => {
        if (!controller.signal.aborted) setError(message(failure));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, []);
  function edit(endpoint?: McpEndpoint) {
    if (dirty && !window.confirm("放弃未保存的连接修改？")) return;
    setEditing(endpoint);
    setForm(endpoint ? asForm(endpoint) : empty);
    setError("");
    setNotice("");
  }
  async function save() {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      if (
        editing?.enabled &&
        !form.enabled &&
        !window.confirm(
          `停用会阻止 ${
            agents
              .filter((agent) => agent.endpoint_ids.includes(editing.id))
              .map((agent) => agent.name)
              .join("、") || "绑定角色"
          } 的后续调用，继续吗？`,
        )
      )
        return;
      const saved = await saveEndpoint(form, editing?.version);
      setDirectory((current) => ({
        ...current,
        endpoints: [...current.endpoints.filter((item) => item.id !== saved.id), saved],
      }));
      setEditing(saved);
      setForm(asForm(saved));
      setNotice("连接已保存。请检查连接以读取真实工具目录；不会自动绑定工具。");
    } catch (failure) {
      setError(message(failure));
    } finally {
      setBusy(false);
    }
  }
  async function runCheck(endpoint: McpEndpoint) {
    setChecking(endpoint.id);
    setError("");
    setNotice("");
    try {
      const result = await checkEndpoint(endpoint.id);
      setNotice(`${endpoint.name} 检查成功，发现 ${result.tools.length} 个工具。`);
    } catch (failure) {
      setError(message(failure));
    } finally {
      try {
        setDirectory(await getEndpoints());
      } catch {
        /* preserve previous directory */
      }
      setChecking("");
    }
  }
  const roleName = (id: string) =>
    agents.find((agent) => agent.id === id)?.name ?? (id || "历史连接 · 未归属");
  const visible = directory.endpoints.filter(
    (endpoint) =>
      (role === "all" || endpoint.role_id === role) &&
      `${endpoint.name} ${roleName(endpoint.role_id)} ${endpoint.role_id} ${endpoint.id} ${endpoint.url}`
        .toLowerCase()
        .includes(query.trim().toLowerCase()),
  );
  return (
    <div className="workspace-content tool-manager">
      <div className="welcome">
        <div>
          <span className="eyebrow">MCP CONNECTIONS</span>
          <h1>
            MCP 连接<span className="title-dot">.</span>
          </h1>
          <p>按角色管理 MCP 连接；CRM、MES 等领域只用于连接内的工具分组。</p>
        </div>
        <button className="secondary-button" disabled={busy || !!checking} onClick={() => edit()}>
          新增连接
        </button>
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
      <div className="endpoint-manager-layout">
        <section className="tool-catalog" aria-label="MCP 连接目录">
          <div className="tool-search-row">
            <input
              type="search"
              aria-label="搜索连接"
              placeholder="搜索角色、连接名称或地址…"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
            <select
              aria-label="连接所属角色筛选"
              value={role}
              onChange={(event) => setRole(event.target.value)}
            >
              <option value="all">全部角色</option>
              {agents.map((agent) => (
                <option key={agent.id} value={agent.id}>
                  {agent.name}
                </option>
              ))}
              <option value="">历史连接 · 未归属</option>
            </select>
          </div>
          {loading && <p>正在读取连接…</p>}
          {!loading && !visible.length && <div className="tool-empty">没有匹配的连接。</div>}
          {visible.map((endpoint) => {
            const check = directory.checks.find((item) => item.endpoint_id === endpoint.id);
            const stale = check?.checked_execution_revision !== endpoint.execution_revision;
            return (
              <article className="tool-card" key={endpoint.id}>
                <div className="endpoint-card-heading">
                  <strong>{endpoint.name}</strong>
                  <span className="config-version">v{endpoint.version}</span>
                </div>
                <code>
                  {endpoint.id} · {roleName(endpoint.role_id)}
                </code>
                <p className="endpoint-address">{endpoint.url}</p>
                <div className="tool-badges">
                  <span>{endpoint.enabled ? "启用" : "已停用"}</span>
                  <span
                    className={
                      !stale && check?.status === "ready" ? "ready-badge" : "not-ready-badge"
                    }
                  >
                    {stale
                      ? "尚未检查 / 检查已过期"
                      : check?.status === "ready"
                        ? "检查成功"
                        : `检查失败 ${check?.error_code ?? ""}`}
                  </span>
                </div>
                {check && <small>最近检查：{new Date(check.checked_at).toLocaleString()}</small>}
                <p className="tool-note">
                  绑定角色：
                  {agents
                    .filter((agent) => agent.endpoint_ids.includes(endpoint.id))
                    .map((agent) => agent.name)
                    .join("、") || "无"}
                </p>
                <div className="endpoint-actions">
                  <button
                    className="secondary-button"
                    disabled={busy || !!checking}
                    onClick={() => edit(endpoint)}
                  >
                    编辑 {endpoint.id}
                  </button>
                  <button
                    className="secondary-button"
                    disabled={busy || !!checking || !endpoint.enabled}
                    onClick={() => void runCheck(endpoint)}
                  >
                    {checking === endpoint.id ? "检查中…" : `检查 ${endpoint.id}`}
                  </button>
                </div>
              </article>
            );
          })}
        </section>
        <form
          className="endpoint-form binding-summary"
          aria-label="连接配置"
          onSubmit={(event) => {
            event.preventDefault();
            void save();
          }}
        >
          <span className="section-kicker">{editing ? "编辑连接" : "登记已部署的连接"}</span>
          <h2>{editing ? editing.name : "新增 Endpoint"}</h2>
          {(
            [
              ["id", "Endpoint ID"],
              ["name", "连接名称"],
              ["url", "连接地址"],
            ] as const
          ).map(([field, label]) => (
            <label key={field}>
              {label}
              <input
                aria-label={label}
                required
                disabled={busy || (field === "id" && !!editing)}
                value={form[field]}
                maxLength={field === "url" ? 500 : field === "id" ? 32 : 120}
                onChange={(event) =>
                  setForm((current) => ({ ...current, [field]: event.target.value }))
                }
              />
            </label>
          ))}
          <label>
            所属角色
            <select
              aria-label="所属角色"
              required
              value={form.role_id}
              disabled={busy}
              onChange={(event) =>
                setForm((current) => ({ ...current, role_id: event.target.value }))
              }
            >
              <option value="">请选择角色</option>
              {agents.map((agent) => (
                <option key={agent.id} value={agent.id}>
                  {agent.name} · {agent.id}
                </option>
              ))}
            </select>
          </label>
          <label>
            凭据引用
            <select
              aria-label="凭据引用"
              value={form.credential_profile}
              disabled={busy}
              onChange={(event) =>
                setForm((current) => ({ ...current, credential_profile: event.target.value }))
              }
            >
              {directory.credential_profiles.map((profile) => (
                <option key={profile.id} value={profile.id}>
                  {profile.id} · {profile.configured ? "已配置" : "未配置密钥"}
                </option>
              ))}
            </select>
          </label>
          <label className="bound-filter">
            <input
              type="checkbox"
              aria-label="启用连接"
              checked={form.enabled}
              disabled={busy}
              onChange={(event) =>
                setForm((current) => ({ ...current, enabled: event.target.checked }))
              }
            />
            启用连接
          </label>
          <p>
            Endpoint ID 必须与服务部署身份一致。地址须在服务端 IP 白名单内，凭据只通过环境变量配置。
          </p>
          <p>保存只登记连接，不启动 MCP 服务，也不自动授权工具。</p>
          <button
            className="primary-button"
            type="submit"
            disabled={busy || loading || !!checking || !dirty}
          >
            {busy ? "保存中…" : "保存连接"}
          </button>
        </form>
      </div>
    </div>
  );
}

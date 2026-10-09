import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { ToolManager } from "./ToolManager";
import { EndpointManager } from "./EndpointManager";
import { TaskPanel } from "./TaskPanel";
import { TracePage } from "./TracePage";
import {
  ApiRequestError,
  getHealth,
  sendChat,
  type ChatResponse,
  type HealthResponse,
} from "./api";

type IconName =
  | "grid"
  | "spark"
  | "search"
  | "ticket"
  | "chart"
  | "calendar"
  | "arrow"
  | "send"
  | "shield"
  | "document"
  | "clock"
  | "check"
  | "alert";

function Icon({ name, size = 20 }: { name: IconName; size?: number }) {
  const paths: Record<IconName, React.ReactNode> = {
    grid: (
      <>
        <rect x="3" y="3" width="7" height="7" rx="1.5" />
        <rect x="14" y="3" width="7" height="7" rx="1.5" />
        <rect x="3" y="14" width="7" height="7" rx="1.5" />
        <rect x="14" y="14" width="7" height="7" rx="1.5" />
      </>
    ),
    spark: (
      <>
        <path d="m12 2 1.9 6.1L20 10l-6.1 1.9L12 18l-1.9-6.1L4 10l6.1-1.9L12 2Z" />
        <path d="m19 17 .6 1.4L21 19l-1.4.6L19 21l-.6-1.4L17 19l1.4-.6L19 17Z" />
      </>
    ),
    search: (
      <>
        <circle cx="10.5" cy="10.5" r="6.5" />
        <path d="m15.5 15.5 5 5" />
      </>
    ),
    ticket: (
      <>
        <path d="M3 7a2 2 0 0 0 2-2h14a2 2 0 0 0 2 2v3a2 2 0 0 0 0 4v3a2 2 0 0 0-2 2H5a2 2 0 0 0-2-2v-3a2 2 0 0 0 0-4V7Z" />
        <path d="M12 5v2m0 4v2m0 4v2" />
      </>
    ),
    chart: (
      <>
        <path d="M4 19V5m0 14h16" />
        <path d="m7 15 4-4 3 2 5-6" />
      </>
    ),
    calendar: (
      <>
        <rect x="3" y="5" width="18" height="16" rx="2" />
        <path d="M7 3v4m10-4v4M3 10h18m-13 5h3" />
      </>
    ),
    arrow: (
      <>
        <path d="M4 12h16m-6-6 6 6-6 6" />
      </>
    ),
    send: (
      <>
        <path d="m21 3-8 18-3-7-7-3 18-8Z" />
        <path d="m10 14 5-5" />
      </>
    ),
    shield: (
      <>
        <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10Z" />
        <path d="m9 12 2 2 4-4" />
      </>
    ),
    document: (
      <>
        <path d="M6 2h8l5 5v13a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2Z" />
        <path d="M14 2v6h5M8 13h8m-8 4h6" />
      </>
    ),
    clock: (
      <>
        <circle cx="12" cy="12" r="9" />
        <path d="M12 7v5l3 2" />
      </>
    ),
    check: <path d="m5 12 4 4L19 6" />,
    alert: (
      <>
        <circle cx="12" cy="12" r="9" />
        <path d="M12 7v6m0 4h.01" />
      </>
    ),
  };
  return (
    <svg
      aria-hidden="true"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {paths[name]}
    </svg>
  );
}

const actions = [
  {
    id: "overview",
    number: "01",
    icon: "search" as const,
    title: "客户概览",
    description: "查询基础信息、续费日期与风险等级",
    prompt: "查询客户 C1001 的基础信息",
    available: true,
  },
  {
    id: "tickets",
    number: "02",
    icon: "ticket" as const,
    title: "开放工单",
    description: "查看指定客户尚未关闭的工单",
    prompt: "查询 C1001 的未关闭工单",
    available: true,
  },
  {
    id: "insight",
    number: "03",
    icon: "chart" as const,
    title: "客户洞察",
    description: "结合业务数据与 SOP 给出跟进建议",
    prompt: "结合 C1001 的工单、续费风险和 SOP，给出跟进建议",
    available: true,
  },
  {
    id: "followup",
    number: "04",
    icon: "calendar" as const,
    title: "回访计划",
    description: "筛选高风险客户，进入人工确认流程",
    prompt: "为未来 7 天到期且高风险的客户生成回访计划",
    available: true,
  },
];

type Exchange = {
  id: number;
  prompt: string;
  response?: ChatResponse;
  error?: ApiRequestError;
};

function routeLabel(response: ChatResponse) {
  if (response.route?.kind === "agent")
    return response.tool_calls?.length ? "工具调用 · ReAct" : "普通回复";
  if (response.route?.kind === "direct_tool") return "直接工具 · ReAct";
  if (response.route?.kind === "skill") return "Skill 编排";
  return "需要澄清";
}

function DataView({ response }: { response: ChatResponse }) {
  const data = response.data;
  if (!data) return null;
  if (response.route?.name === "crm.get_customer_overview") {
    return (
      <div className="data-card" aria-label="客户概览结果">
        <div className="data-card-heading">
          <span>客户概览</span>
          <span className="data-code">{String(data.customer_code ?? "—")}</span>
        </div>
        <div className="data-grid">
          <div>
            <span className="data-label">客户名称</span>
            <strong>{String(data.name ?? "—")}</strong>
          </div>
          <div>
            <span className="data-label">续费日期</span>
            <strong>{String(data.renewal_date ?? "—")}</strong>
          </div>
          <div>
            <span className="data-label">风险等级</span>
            <strong className={data.risk_level === "high" ? "risk-high" : ""}>
              {String(data.risk_level ?? "—")}
            </strong>
          </div>
        </div>
      </div>
    );
  }
  if (response.route?.name === "crm.list_open_tickets") {
    const tickets = Array.isArray(data.tickets) ? data.tickets : [];
    return (
      <div className="data-card" aria-label="开放工单结果">
        <div className="data-card-heading">
          <span>开放工单</span>
          <span className="data-code">
            {String(data.customer_code ?? "—")} · {tickets.length} 条
          </span>
        </div>
        {tickets.length === 0 ? (
          <p className="empty-data">当前没有未关闭工单。</p>
        ) : (
          <ul className="ticket-list">
            {tickets.map((ticket, index) => {
              const item = ticket as Record<string, unknown>;
              return (
                <li key={String(item.ticket_id ?? index)}>
                  <span className="ticket-id">{String(item.ticket_id ?? "—")}</span>
                  <span>{String(item.summary ?? "—")}</span>
                  <span className="ticket-status">未关闭</span>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    );
  }
  if (response.route?.name === "mes.get_work_order_status") {
    return (
      <div className="data-card" aria-label="生产工单结果">
        <div className="data-card-heading">
          <span>生产 / 交付工单</span>
          <span className="data-code">{String(data.work_order_code ?? "—")}</span>
        </div>
        <div className="data-grid">
          <div>
            <span className="data-label">当前状态</span>
            <strong>{String(data.status ?? "—")}</strong>
          </div>
          <div>
            <span className="data-label">更新时间</span>
            <strong>{String(data.updated_at ?? "—")}</strong>
          </div>
        </div>
      </div>
    );
  }
  return <p className="empty-data">工具已返回数据，当前界面尚无对应的专用展示卡片。</p>;
}

function EvidencePanel({
  latest,
  health,
}: {
  latest: ChatResponse | null;
  health: HealthResponse | null | "offline";
}) {
  const citations = latest?.citations ?? [];
  const task = latest?.task;
  return (
    <aside className="context-panel" id="evidence" aria-label="证据与任务">
      <div className="panel-top">
        <span className="panel-eyebrow">CURRENT CONTEXT</span>
        <h2>证据与流程</h2>
        <p>回答依据与任务进展，集中呈现。</p>
      </div>
      <section className="context-section">
        <div className="section-heading">
          <span className="section-icon">
            <Icon name="document" size={18} />
          </span>
          <h3>引用来源</h3>
          <span className="count-pill">{citations.length}</span>
        </div>
        {citations.length ? (
          citations.map((citation, index) => (
            <article
              className="citation-card"
              key={citation.source_id ?? `${citation.document_id}:${citation.chunk_id}`}
            >
              <span className="citation-id">
                [{index + 1}] {citation.document_id} · {citation.chunk_id}
              </span>
              <strong>{citation.title}</strong>
              {citation.version && (
                <small>
                  版本 {citation.version} · {citation.endpoint_id}
                </small>
              )}
              <blockquote>{citation.excerpt}</blockquote>
            </article>
          ))
        ) : (
          <div className="context-empty">
            <span className="empty-illustration">
              <Icon name="document" size={25} />
            </span>
            <strong>暂无引用来源</strong>
            <p>
              本轮尚无通过校验的 SOP 引用。客户洞察需先绑定 SOP 检索工具；没有依据时不会显示引用。
            </p>
          </div>
        )}
      </section>
      <section className="context-section task-section">
        <div className="section-heading">
          <span className="section-icon">
            <Icon name="clock" size={18} />
          </span>
          <h3>任务状态</h3>
        </div>
        {task ? (
          <div className="task-card">
            <span className="task-status">任务已提交</span>
            <strong>{task.task_id ?? task.id ?? "未提供任务编号"}</strong>
            <p>实时状态见下方任务列表，可审查提案、确认或取消。</p>
          </div>
        ) : (
          <div className="context-empty compact">
            <span className="empty-illustration">
              <Icon name="calendar" size={24} />
            </span>
            <strong>当前消息未创建任务</strong>
            <p>回访提案生成后，需要人工确认才会保存正式计划。</p>
          </div>
        )}
      </section>
      <TaskPanel refreshKey={task?.task_id} />
      <div className="panel-footer">
        <Icon name="shield" size={17} />
        <span>权限由 Agent API 与 MCP Server 双重校验</span>
      </div>
      <div className="panel-connection">
        <span className={`connection-dot ${health === "offline" ? "offline" : ""}`} />
        <span>
          {health === "offline"
            ? "Agent API 未连接"
            : health?.llm === "not_configured"
              ? "模型密钥尚未配置"
              : health
                ? "Agent API 已连接"
                : "正在检查连接"}
        </span>
      </div>
    </aside>
  );
}

export function App() {
  const initialTrace = window.location.hash.match(/^#trace\/([a-f0-9-]{36})$/i)?.[1] ?? null;
  const [page, setPage] = useState<"chat" | "tools" | "endpoints" | "traces">(
    window.location.hash.startsWith("#trace") ? "traces" : "chat",
  );
  const [selectedTrace, setSelectedTrace] = useState<string | null>(initialTrace);
  const [managementDirty, setManagementDirty] = useState(false);
  useEffect(() => {
    function onHashChange() {
      const hash = window.location.hash;
      const next = hash.startsWith("#trace") ? "traces" : hash === "#top" ? "chat" : null;
      if (!next) return;
      if (page !== next && managementDirty && !window.confirm("放弃管理页面未保存的修改？")) {
        window.history.replaceState(
          null,
          "",
          page === "traces" ? (selectedTrace ? `#trace/${selectedTrace}` : "#trace") : "#top",
        );
        return;
      }
      setPage(next);
      if (next === "traces")
        setSelectedTrace(hash.match(/^#trace\/([a-f0-9-]{36})$/i)?.[1] ?? null);
    }
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, [page, managementDirty, selectedTrace]);
  function navigate(next: "chat" | "tools" | "endpoints" | "traces") {
    if (page !== next && managementDirty && !window.confirm("放弃管理页面未保存的修改？")) return;
    setPage(next);
    window.history.replaceState(
      null,
      "",
      next === "traces" ? (selectedTrace ? `#trace/${selectedTrace}` : "#trace") : "#top",
    );
  }
  function openTrace(id: string) {
    if (managementDirty && !window.confirm("放弃管理页面未保存的修改？")) return;
    setSelectedTrace(id);
    setPage("traces");
    window.history.replaceState(null, "", `#trace/${id}`);
  }
  const [draft, setDraft] = useState("");
  const [draftSkill, setDraftSkill] = useState<string | null>(null);
  const [exchanges, setExchanges] = useState<Exchange[]>([]);
  const [pending, setPending] = useState(false);
  const [latest, setLatest] = useState<ChatResponse | null>(null);
  const [health, setHealth] = useState<HealthResponse | null | "offline">(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const idRef = useRef(0);
  const controllerRef = useRef<AbortController | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    getHealth(controller.signal)
      .then(setHealth)
      .catch(() => {
        if (!controller.signal.aborted) setHealth("offline");
      });
    return () => {
      controller.abort();
      controllerRef.current?.abort();
    };
  }, []);

  async function submit(value = draft) {
    const prompt = value.trim();
    if (!prompt || pending) return;
    const id = ++idRef.current;
    const controller = new AbortController();
    controllerRef.current = controller;
    setDraft("");
    setPending(true);
    setLatest(null);
    setExchanges((current) => [...current, { id, prompt }]);
    try {
      const response = await sendChat(prompt, controller.signal, draftSkill);
      setDraftSkill(null);
      setExchanges((current) =>
        current.map((item) => (item.id === id ? { ...item, response } : item)),
      );
      setLatest(response);
    } catch (error) {
      if (controller.signal.aborted) return;
      const apiError =
        error instanceof ApiRequestError
          ? error
          : new ApiRequestError("连接服务失败，请检查 Agent API 是否已启动。", "NETWORK_ERROR");
      setExchanges((current) =>
        current.map((item) => (item.id === id ? { ...item, error: apiError } : item)),
      );
    } finally {
      setPending(false);
      controllerRef.current = null;
    }
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void submit();
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      void submit();
    }
  }

  function selectAction(prompt: string, skillName: string | null = null) {
    setDraft(prompt);
    setDraftSkill(skillName);
    inputRef.current?.focus();
    document.getElementById("conversation")?.scrollIntoView({ behavior: "smooth" });
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <a className="brand" href="#top" aria-label="OpsPilot 首页">
          <span className="brand-mark">
            <span />
          </span>
          <span className="brand-copy">
            <strong>OpsPilot</strong>
            <small>OPERATIONS INTELLIGENCE</small>
          </span>
        </a>
        <div className="sidebar-divider" />
        <span className="sidebar-label">工作空间</span>
        <nav className="sidebar-nav" aria-label="主导航">
          <button
            className={`nav-item page-nav ${page === "chat" ? "active" : ""}`}
            onClick={() => navigate("chat")}
          >
            <Icon name="grid" size={19} />
            <span>工作台</span>
            {page === "chat" && <span className="nav-active-mark" />}
          </button>
          <button
            className={`nav-item page-nav ${page === "tools" ? "active" : ""}`}
            onClick={() => navigate("tools")}
          >
            <Icon name="shield" size={19} />
            <span>工具管理</span>
          </button>
          <button
            className={`nav-item page-nav ${page === "endpoints" ? "active" : ""}`}
            onClick={() => navigate("endpoints")}
          >
            <Icon name="grid" size={19} />
            <span>MCP 连接</span>
          </button>
          <button
            className={`nav-item page-nav ${page === "traces" ? "active" : ""}`}
            onClick={() => navigate("traces")}
          >
            <Icon name="chart" size={19} />
            <span>执行 Trace</span>
          </button>
          <a className="nav-item" href="#quick-actions" onClick={() => navigate("chat")}>
            <Icon name="spark" size={19} />
            <span>快捷操作</span>
          </a>
          <a className="nav-item" href="#evidence" onClick={() => navigate("chat")}>
            <Icon name="document" size={19} />
            <span>证据与任务</span>
          </a>
        </nav>
        <div className="sidebar-bottom">
          <div className="role-icon">C</div>
          <div>
            <strong>Consultant</strong>
            <span>本地演示角色</span>
          </div>
          <span className="role-chevron">⌄</span>
        </div>
      </aside>

      <main className="workspace" id="top">
        <header className="topbar">
          <div className="breadcrumb">
            工作空间 <span>/</span>{" "}
            <strong>
              {page === "chat"
                ? "运营工作台"
                : page === "tools"
                  ? "角色工具绑定"
                  : page === "traces"
                    ? "执行 Trace"
                    : "MCP 连接"}
            </strong>
          </div>
          <div className="topbar-right">
            <span className="environment-badge">
              <span /> LOCAL WORKSPACE
            </span>
            <span className="topbar-avatar">C</span>
          </div>
        </header>
        {page === "traces" ? (
          <TracePage selectedId={selectedTrace} onSelect={openTrace} />
        ) : page === "endpoints" ? (
          <EndpointManager onDirtyChange={setManagementDirty} />
        ) : page === "tools" ? (
          <ToolManager onDirtyChange={setManagementDirty} />
        ) : (
          <div className="workspace-content">
            <div className="welcome">
              <div>
                <span className="eyebrow">OPERATIONS, MADE CLEAR</span>
                <h1>
                  运营工作台<span className="title-dot">.</span>
                </h1>
                <p>把业务查询交给 Agent，让每一步都清晰可见、有据可查。</p>
              </div>
              <div className="date-stamp">
                <span>当前模式</span>
                <strong>模拟业务数据</strong>
                <small>READ-ONLY · MVP</small>
              </div>
            </div>

            <section className="hero-card" aria-label="能力概览">
              <div className="hero-glow" />
              <div className="hero-content">
                <span className="hero-kicker">
                  <Icon name="spark" size={17} /> YOUR AI OPERATIONS COPILOT
                </span>
                <h2>
                  从一个问题，
                  <br />
                  到清晰的业务答案。
                </h2>
                <p>先以已接通的客户查询开始。工具范围由角色绑定，执行经过双重权限校验。</p>
                <a href="#quick-actions" className="hero-link">
                  试试快捷查询 <Icon name="arrow" size={17} />
                </a>
              </div>
              <div className="hero-visual" aria-hidden="true">
                <div className="orbit orbit-outer" />
                <div className="orbit orbit-inner" />
                <div className="hero-core">
                  <Icon name="spark" size={35} />
                </div>
                <div className="orbit-node node-one">
                  <Icon name="search" size={18} />
                </div>
                <div className="orbit-node node-two">
                  <Icon name="shield" size={18} />
                </div>
                <div className="orbit-node node-three">
                  <Icon name="document" size={18} />
                </div>
              </div>
            </section>

            <section id="quick-actions" className="quick-section">
              <div className="section-title-row">
                <div>
                  <span className="section-kicker">GET STARTED</span>
                  <h2>快捷操作</h2>
                </div>
                <span className="section-hint">选择一个已接通的场景开始</span>
              </div>
              <div className="action-grid">
                {actions.map((action) => (
                  <button
                    type="button"
                    key={action.id}
                    className={`action-card ${action.available ? "" : "unavailable"}`}
                    onClick={() =>
                      action.available &&
                      selectAction(
                        action.prompt,
                        action.id === "insight"
                          ? "crm.customer_insight"
                          : action.id === "followup"
                            ? "crm.followup_workflow"
                            : null,
                      )
                    }
                    disabled={!action.available}
                    aria-label={`${action.title}${action.available ? "，填入提问" : "，尚未接通"}`}
                  >
                    <div className="action-top">
                      <span className="action-icon">
                        <Icon name={action.icon} size={21} />
                      </span>
                      <span className="action-number">{action.number}</span>
                    </div>
                    <strong>{action.title}</strong>
                    <p>{action.description}</p>
                    <span className={`action-foot ${action.available ? "ready" : "soon"}`}>
                      {action.available ? (
                        <>
                          已接通 <Icon name="arrow" size={15} />
                        </>
                      ) : (
                        <>
                          尚未接通 <Icon name="clock" size={14} />
                        </>
                      )}
                    </span>
                  </button>
                ))}
              </div>
            </section>

            <div className="lower-grid">
              <section className="conversation-panel" id="conversation" aria-label="聊天区域">
                <div className="conversation-heading">
                  <div>
                    <span className="conversation-mark">
                      <Icon name="spark" size={19} />
                    </span>
                    <div>
                      <h2>与 OpsPilot 对话</h2>
                      <p>自然语言查询 · ReAct 工具调用</p>
                    </div>
                  </div>
                  <span className="live-pill">
                    <span /> 只读模式
                  </span>
                </div>
                <div
                  className={`conversation-body ${exchanges.length === 0 ? "conversation-empty" : ""}`}
                  aria-live="polite"
                >
                  {exchanges.length === 0 ? (
                    <div className="chat-empty-state">
                      <div className="chat-empty-icon">
                        <Icon name="spark" size={28} />
                      </div>
                      <h3>你好，有什么可以帮你查询？</h3>
                      <p>
                        试着问我“查询客户 C1001 的基础信息”，
                        <br />
                        或从上方选择一个快捷操作。
                      </p>
                      <div className="chat-empty-tags">
                        <span>客户概览</span>
                        <span>开放工单</span>
                        <span>权限可控</span>
                      </div>
                    </div>
                  ) : (
                    exchanges.map((item) => (
                      <div className="exchange" key={item.id}>
                        <div className="chat-row user-row">
                          <div className="chat-avatar user-avatar">C</div>
                          <div className="chat-content">
                            <span className="speaker">你</span>
                            <div className="user-bubble">{item.prompt}</div>
                          </div>
                        </div>
                        {item.response ? (
                          <div className="chat-row agent-row">
                            <div className="chat-avatar agent-avatar">
                              <Icon name="spark" size={17} />
                            </div>
                            <div className="chat-content">
                              <div className="speaker-line">
                                <span className="speaker">OpsPilot</span>
                                <span className="route-pill">
                                  <Icon name="check" size={12} />
                                  {routeLabel(item.response)}
                                </span>
                              </div>
                              <div className="agent-answer">{item.response.message}</div>
                              {item.response.tool_calls?.length ? (
                                item.response.tool_calls.map((call, index) => (
                                  <div key={`${call.name}:${index}`}>
                                    {call.endpoint_id && (
                                      <p className="tool-source">
                                        来源：{call.endpoint_name ?? call.endpoint_id} ·{" "}
                                        {call.endpoint_id} · 修订 {call.endpoint_revision}
                                      </p>
                                    )}
                                    <DataView
                                      response={{
                                        ...item.response!,
                                        data: call.result,
                                        route: { kind: "agent", name: call.name },
                                      }}
                                    />
                                    <details className="tool-trace">
                                      <summary>调用记录 · {call.name}</summary>
                                      <pre>{JSON.stringify(call.arguments, null, 2)}</pre>
                                    </details>
                                  </div>
                                ))
                              ) : (
                                <DataView response={item.response} />
                              )}
                              <div className="response-meta">
                                请求 ID <code>{item.response.request_id}</code>
                              </div>
                              {item.response.trace_id && (
                                <button
                                  className="trace-link"
                                  onClick={() => openTrace(item.response!.trace_id!)}
                                >
                                  查看本轮 Trace →
                                </button>
                              )}
                              {item.response.trace_incomplete && (
                                <p className="trace-warning">
                                  Trace 采集不完整，业务响应不受影响。
                                </p>
                              )}
                            </div>
                          </div>
                        ) : item.error ? (
                          <div className="chat-row agent-row">
                            <div className="chat-avatar error-avatar">
                              <Icon name="alert" size={17} />
                            </div>
                            <div className="chat-content">
                              <span className="speaker">OpsPilot</span>
                              <div className="error-card">
                                <strong>请求未完成</strong>
                                <p>{item.error.message}</p>
                                <span>
                                  {item.error.code}
                                  {item.error.requestId ? ` · 请求 ID ${item.error.requestId}` : ""}
                                </span>
                              </div>
                              {item.error.traceId && (
                                <button
                                  className="trace-link"
                                  onClick={() => openTrace(item.error!.traceId!)}
                                >
                                  查看失败 Trace →
                                </button>
                              )}
                            </div>
                          </div>
                        ) : (
                          <div className="chat-row agent-row">
                            <div className="chat-avatar agent-avatar">
                              <Icon name="spark" size={17} />
                            </div>
                            <div className="chat-content">
                              <span className="speaker">OpsPilot</span>
                              <div className="thinking">
                                <i />
                                <i />
                                <i />
                                <span>正在处理你的请求…</span>
                              </div>
                            </div>
                          </div>
                        )}
                      </div>
                    ))
                  )}
                </div>
                <form className="composer" onSubmit={handleSubmit}>
                  {draftSkill && (
                    <div className="management-notice">
                      {draftSkill === "crm.followup_workflow"
                        ? "回访提案模式 · 保存正式计划需要人工确认"
                        : "客户洞察模式 · 需要绑定客户概览、开放工单与 SOP 检索"}
                      <button
                        type="button"
                        className="secondary-button"
                        onClick={() => setDraftSkill(null)}
                      >
                        {draftSkill === "crm.followup_workflow" ? "退出回访模式" : "退出洞察模式"}
                      </button>
                    </div>
                  )}
                  <label className="sr-only" htmlFor="chat-input">
                    输入业务问题
                  </label>
                  <textarea
                    id="chat-input"
                    ref={inputRef}
                    value={draft}
                    onChange={(event) => setDraft(event.target.value)}
                    onKeyDown={handleKeyDown}
                    placeholder="输入你的业务问题，例如：查询客户 C1001 的基础信息"
                    maxLength={4000}
                    rows={2}
                    disabled={pending}
                  />
                  <div className="composer-bottom">
                    <span>
                      <Icon name="shield" size={14} /> 仅调用当前角色允许的工具 · Enter 发送，Shift
                      + Enter 换行
                    </span>
                    <button type="submit" disabled={!draft.trim() || pending} aria-label="发送消息">
                      <Icon name="send" size={18} />
                    </button>
                  </div>
                </form>
              </section>
              <EvidencePanel latest={latest} health={health} />
            </div>
            <footer className="page-footer">
              <span>OpsPilot · 个人开源项目</span>
              <span>所有业务记录均为模拟数据</span>
            </footer>
          </div>
        )}
      </main>
    </div>
  );
}

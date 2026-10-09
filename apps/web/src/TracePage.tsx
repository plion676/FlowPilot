import { useEffect, useState } from "react";
import { getTrace, getTraces, type TraceEvent, type TraceRun } from "./api";
import "./TracePage.css";

const statusNames: Record<string, string> = {
  running: "运行中",
  succeeded: "已完成",
  failed: "失败",
  cancelled: "已取消",
  interrupted: "中断",
};
const eventNames: Record<string, string> = {
  user_message: "用户消息",
  system_message: "System message",
  model_start: "调用模型",
  assistant_message: "Assistant message",
  tool_call: "Tool call · 调用提议",
  tool_start: "Tool start · 开始执行",
  tool_result: "Tool result · 返回结果",
  tool_error: "Tool error · 执行失败",
  tool_rejected: "Tool rejected · 未执行",
  model_error: "模型调用失败",
  final_response: "最终展示回复",
};

function format(value: unknown) {
  return typeof value === "string" ? value : JSON.stringify(value, null, 2);
}
function truncated(value: unknown): boolean {
  if (typeof value === "string") return value.includes("[TRUNCATED]");
  if (!value || typeof value !== "object") return false;
  return Object.entries(value).some(([k, v]) => (k === "truncated" && v === true) || truncated(v));
}
function EventCard({ event }: { event: TraceEvent }) {
  const p = event.payload;
  const content = p.content;
  const error = event.kind.endsWith("error") || event.kind === "tool_rejected";
  const detail = <pre>{format(content ?? p)}</pre>;
  return (
    <li className={`trace-event ${error ? "trace-event-error" : ""}`}>
      <span className="trace-sequence">{String(event.sequence).padStart(2, "0")}</span>
      <article>
        <header>
          <strong>{eventNames[event.kind] ?? event.kind}</strong>
          <span>{event.step > 0 ? `模型步骤 ${event.step}` : "请求开始"}</span>
          {typeof p.duration_ms === "number" && <span>{p.duration_ms} ms</span>}
          <time>{new Date(event.created_at).toLocaleTimeString("zh-CN")}</time>
        </header>
        {p.name != null && (
          <h3>
            {String(p.name)} {p.source === "local_control" && <small>本地控制工具</small>}
          </h3>
        )}
        {p.endpoint_id != null && (
          <p className="trace-source">
            来源 {String(p.endpoint_name ?? p.endpoint_id)} · {String(p.endpoint_id)} · 修订{" "}
            {String(p.endpoint_revision ?? "—")}
          </p>
        )}
        {event.call_id && (
          <p className="trace-source">
            Call ID <code>{event.call_id}</code>
          </p>
        )}
        {p.model_name != null && (
          <details>
            <summary>模型工具别名</summary>
            <code>{String(p.model_name)}</code>
          </details>
        )}
        {error && (
          <p className="trace-warning">
            {String(p.error_code ?? "TOOL_FAILED")}
            {event.kind === "tool_rejected"
              ? " · 后端校验拒绝，工具未执行"
              : " · 不展示原始异常或密钥"}
          </p>
        )}
        {truncated(p) && <p className="trace-warning">内容已截断，以下并非完整记录。</p>}
        {event.kind === "system_message" ? (
          <details>
            <summary>展开系统消息（已脱敏）</summary>
            {detail}
          </details>
        ) : event.kind === "tool_result" ? (
          <details open>
            <summary>工具返回内容（已脱敏）</summary>
            {detail}
          </details>
        ) : content === "" ? (
          <p className="trace-empty-content">
            消息文本为空
            {event.kind === "assistant_message" ? "；工具提议单独显示在后续事件。" : "。"}
          </p>
        ) : (
          content != null && <div className="trace-message">{detail}</div>
        )}
        {p.arguments != null && (
          <details>
            <summary>参数（已脱敏）</summary>
            <pre>{format(p.arguments)}</pre>
          </details>
        )}
        {event.kind === "model_start" && (
          <details>
            <summary>本步骤允许的工具集合</summary>
            <pre>{format(p.tools)}</pre>
          </details>
        )}
        {p.task != null && (
          <details open>
            <summary>关联 Task（后台流程不属于本轮 Trace）</summary>
            <pre>{format(p.task)}</pre>
          </details>
        )}
        {p.citations != null && Array.isArray(p.citations) && p.citations.length > 0 && (
          <details>
            <summary>最终引用来源</summary>
            <pre>{format(p.citations)}</pre>
          </details>
        )}
        {p.truncated === true && p.excerpt != null && <pre>{format(p.excerpt)}</pre>}
        {event.kind === "tool_result" && <small>返回状态：{String(p.status ?? "unknown")}</small>}
      </article>
    </li>
  );
}

export function TracePage({
  selectedId,
  onSelect,
  initialSearch = "",
}: {
  selectedId: string | null;
  onSelect: (id: string) => void;
  initialSearch?: string;
}) {
  const [search, setSearch] = useState(initialSearch);
  const [offset, setOffset] = useState(0);
  const [runs, setRuns] = useState<TraceRun[]>([]);
  const [more, setMore] = useState(false);
  const [run, setRun] = useState<TraceRun | null>(null);
  const [events, setEvents] = useState<TraceEvent[]>([]);
  const [listError, setListError] = useState("");
  const [detailError, setDetailError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const result = await getTraces(search, offset, controller.signal);
        if (controller.signal.aborted) return;
        setRuns(result.traces);
        setMore(result.has_more);
        setListError("");
      } catch (error) {
        if (!controller.signal.aborted)
          setListError(error instanceof Error ? error.message : "Trace 列表读取失败");
      } finally {
        if (!controller.signal.aborted) {
          setLoading(false);
          timer = setTimeout(poll, 3000);
        }
      }
    }
    setLoading(true);
    void poll();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [search, offset]);

  useEffect(() => {
    setEvents([]);
    setRun(null);
    setDetailError("");
    if (!selectedId) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    let cursor = 0;
    async function poll() {
      try {
        const result = await getTrace(selectedId!, cursor, controller.signal);
        if (controller.signal.aborted) return;
        setRun(result.trace);
        setDetailError("");
        const newEvents = result.events.filter((e) => e.sequence > cursor);
        setEvents((current) => [...current, ...newEvents]);
        // Capture cursor outside the React state updater (which may be deferred).
        const incoming = result.events;
        if (incoming.length) cursor = incoming[incoming.length - 1].sequence;
        if (result.has_more || result.trace.status === "running")
          timer = setTimeout(poll, result.has_more ? 0 : 1000);
      } catch (error) {
        if (!controller.signal.aborted) {
          setDetailError(error instanceof Error ? error.message : "Trace 详情读取失败");
          timer = setTimeout(poll, 3000);
        }
      }
    }
    void poll();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [selectedId]);

  return (
    <div className="trace-page">
      <div className="trace-heading">
        <div>
          <span className="eyebrow">REACT OBSERVABILITY</span>
          <h1>
            执行 Trace<span>.</span>
          </h1>
          <p>按真实发生顺序查看消息、工具调用与结果。只读观测，不展示隐藏推理。</p>
        </div>
        <span className="trace-local">本机开发 · 已脱敏</span>
      </div>
      <div className="trace-layout">
        <section className="trace-list" aria-label="Trace 列表">
          <label>
            定位一次请求
            <input
              aria-label="搜索 Trace 或请求 ID"
              placeholder="输入 Trace / 请求 ID"
              maxLength={80}
              value={search}
              onChange={(e) => {
                setSearch(e.target.value);
                setOffset(0);
              }}
            />
          </label>
          {listError && (
            <p role="alert" className="trace-warning">
              {listError}
            </p>
          )}
          {loading && !runs.length && <p>正在读取…</p>}
          {!loading && !runs.length && !listError && (
            <p className="trace-placeholder">还没有匹配的 Trace。发送一条新消息后会自动记录。</p>
          )}
          {runs.map((item) => (
            <button
              key={item.trace_id}
              className={`trace-run ${selectedId === item.trace_id ? "selected" : ""}`}
              onClick={() => onSelect(item.trace_id)}
            >
              <div>
                <span className={`trace-status ${item.status}`}>
                  {statusNames[item.status] ?? item.status}
                </span>
                <time>{new Date(item.created_at).toLocaleString("zh-CN")}</time>
              </div>
              <strong>{item.summary || "（无摘要）"}</strong>
              <code>{item.request_id}</code>
              <small>
                {item.model || "模型未配置"} · {item.last_sequence} 个事件{" "}
                {item.incomplete ? "· 采集不完整" : ""}
              </small>
            </button>
          ))}
          <div className="trace-pagination">
            <button disabled={!offset} onClick={() => setOffset(Math.max(0, offset - 30))}>
              上一页
            </button>
            <span>{offset / 30 + 1}</span>
            <button disabled={!more} onClick={() => setOffset(offset + 30)}>
              下一页
            </button>
          </div>
        </section>
        <section className="trace-detail" aria-label="Trace 时间线">
          {!selectedId && (
            <div className="trace-placeholder">
              <h2>选择一次请求</h2>
              <p>Message → Tool call → Tool result → 下一次 Message</p>
              <p>并行工具按实际完成顺序展示，通过步骤和 Call ID 对应。</p>
            </div>
          )}
          {detailError && (
            <p role="alert" className="trace-warning">
              {detailError}
            </p>
          )}
          {selectedId && !run && !detailError && <p>正在读取时间线…</p>}
          {run && (
            <>
              <div className="trace-detail-heading">
                <h2>{statusNames[run.status] ?? run.status}</h2>
                <p>
                  Trace <code>{run.trace_id}</code>
                </p>
                <p>
                  请求 <code>{run.request_id}</code> · {run.role} · {run.model}
                </p>
              </div>
              {run.incomplete && (
                <p className="trace-warning">
                  采集不完整：不代表没有发生其他消息或业务操作；不会自动重放。
                </p>
              )}
              {run.error_code && <p className="trace-warning">请求错误：{run.error_code}</p>}
              {run.status === "running" && (
                <p className="trace-live">● 正在运行，每秒更新已保存事件</p>
              )}
              <ol className="trace-timeline">
                {events.map((e) => (
                  <EventCard key={e.event_id} event={e} />
                ))}
              </ol>
              {run.status === "interrupted" && (
                <p className="trace-warning">
                  15 分钟未更新，已标记中断；不能据此推断工具是否执行成功。
                </p>
              )}
            </>
          )}
        </section>
      </div>
    </div>
  );
}

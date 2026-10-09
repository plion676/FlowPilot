import { useEffect, useRef, useState } from "react";
import { getTasks, taskAudit, taskDecision, type FollowupTask } from "./api";
import "./tasks.css";

const labels: Record<string, string> = {
  created: "等待执行",
  gathering: "生成提案中",
  pending_approval: "等待人工确认",
  approved: "已批准，等待提交",
  committing: "正在提交",
  completed: "已保存正式计划",
  completed_empty: "无符合条件客户",
  failed: "执行失败",
  rejected: "已拒绝",
  cancelled: "已取消",
  expired: "已过期",
};

export function TaskPanel({ refreshKey }: { refreshKey?: string }) {
  const [tasks, setTasks] = useState<FollowupTask[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [audits, setAudits] = useState<
    Record<string, Awaited<ReturnType<typeof taskAudit>>["events"]>
  >({});
  const keys = useRef<Record<string, string>>({});
  useEffect(() => {
    const controller = new AbortController();
    let running = false;
    async function refresh() {
      if (running) return;
      running = true;
      try {
        const data = await getTasks(controller.signal);
        if (!controller.signal.aborted) {
          setTasks(data);
          setError("");
        }
      } catch (e) {
        if (!controller.signal.aborted)
          setError(e instanceof Error ? e.message : "任务状态暂不可用。");
      } finally {
        running = false;
      }
    }
    void refresh();
    const interval = setInterval(() => void refresh(), 1500);
    return () => {
      controller.abort();
      clearInterval(interval);
    };
  }, [refreshKey]);

  async function decide(task: FollowupTask, decision: string) {
    if (
      decision === "approve" &&
      !window.confirm(
        `批准并保存 ${task.proposal.candidates?.length ?? 0} 位模拟客户的回访计划？这不会实际联系客户。`,
      )
    )
      return;
    setBusy(task.task_id);
    setError("");
    const scope = `${task.task_id}:${task.version}:${decision}`;
    const key = (keys.current[scope] ??= crypto.randomUUID());
    try {
      await taskDecision(task, decision, key);
      setTasks(await getTasks());
      delete keys.current[scope];
    } catch (e) {
      setError(e instanceof Error ? e.message : "确认未完成，请重试。");
    } finally {
      setBusy("");
    }
  }
  async function audit(id: string) {
    try {
      const result = await taskAudit(id);
      setAudits((old) => ({ ...old, [id]: result.events }));
    } catch (e) {
      setError(e instanceof Error ? e.message : "审计暂不可用。");
    }
  }
  return (
    <section className="persistent-tasks" aria-label="回访任务列表">
      <h3>持久化回访任务</h3>
      <p>只保存模拟 CRM 记录，不实际联系客户。</p>
      {error && <p role="alert">{error}</p>}
      {!tasks.length && !error && <p>暂无回访任务，使用上方“回访计划”开始。</p>}
      {tasks.map((task) => (
        <article className="followup-task" key={task.task_id} data-task-id={task.task_id}>
          <strong>{labels[task.status] ?? task.status}</strong>
          <small>
            {task.task_id} · 版本 {task.version}
          </small>
          <p>
            窗口：{task.window_start} 至 {task.window_end}（含两端）
          </p>
          {task.error_code && <p role="alert">原因：{task.error_code}</p>}
          {task.proposal?.candidates?.map((candidate) => (
            <div className="task-candidate" key={candidate.customer_code}>
              <b>{candidate.customer_code}</b> · 高风险 · {candidate.renewal_date} 到期
              <p>
                {candidate.action} · 计划日期 {candidate.due_date}
              </p>
            </div>
          ))}
          {task.proposal?.analysis && (
            <details className="task-analysis">
              <summary>查看提案分析（审批前生成）</summary>
              <p>{task.proposal.analysis}</p>
            </details>
          )}
          {task.proposal?.citations?.map((source, index) => (
            <details key={source.source_id ?? source.chunk_id}>
              <summary>
                [{index + 1}] {source.title} · {source.document_id}
              </summary>
              <blockquote>{source.excerpt}</blockquote>
            </details>
          ))}
          {task.status === "completed" && (
            <p>
              已保存 {task.result.plan_count} 份正式计划。{task.result.plan_ids?.join("、")}
            </p>
          )}
          <div className="task-actions">
            {task.status === "pending_approval" && (
              <>
                <button disabled={!!busy} onClick={() => void decide(task, "approve")}>
                  批准并保存
                </button>
                <button disabled={!!busy} onClick={() => void decide(task, "reject")}>
                  拒绝提案
                </button>
              </>
            )}
            {["created", "gathering", "pending_approval", "approved"].includes(task.status) && (
              <button disabled={!!busy} onClick={() => void decide(task, "cancel")}>
                取消任务
              </button>
            )}
            <button onClick={() => void audit(task.task_id)}>查看审计</button>
          </div>
          {audits[task.task_id] && (
            <ol className="task-audit">
              {audits[task.task_id].map((event) => (
                <li key={event.id}>
                  {event.event} · v{event.version} ·{" "}
                  {new Date(event.created_at).toLocaleString("zh-CN")}
                </li>
              ))}
            </ol>
          )}
        </article>
      ))}
    </section>
  );
}

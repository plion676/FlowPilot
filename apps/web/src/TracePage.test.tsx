import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { TracePage } from "./TracePage";
import { sendChat, ApiRequestError } from "./api";

const id = "11111111-1111-4111-8111-111111111111";
const run = {
  trace_id: id,
  request_id: id,
  role: "consultant",
  summary: "查询 C1001",
  model: "fake",
  status: "succeeded",
  incomplete: false,
  error_code: "",
  last_sequence: 4,
  created_at: "2026-10-09T00:00:00Z",
  updated_at: "2026-10-09T00:00:00Z",
  finished_at: "2026-10-09T00:00:01Z",
};
const events = [
  {
    sequence: 1,
    event_id: "1",
    kind: "user_message",
    step: 0,
    call_id: "",
    payload: { content: "查询 C1001" },
    created_at: run.created_at,
  },
  {
    sequence: 2,
    event_id: "2",
    kind: "assistant_message",
    step: 1,
    call_id: "",
    payload: { content: "" },
    created_at: run.created_at,
  },
  {
    sequence: 3,
    event_id: "3",
    kind: "tool_call",
    step: 1,
    call_id: "call-1",
    payload: {
      name: "crm.get_customer_overview",
      endpoint_id: "business",
      endpoint_revision: 6,
      model_name: "lookup_alias",
      arguments: { customer_id: "C1001" },
    },
    created_at: run.created_at,
  },
  {
    sequence: 4,
    event_id: "4",
    kind: "tool_result",
    step: 1,
    call_id: "call-1",
    payload: {
      name: "crm.get_customer_overview",
      content: { risk_level: "high" },
      status: "success",
    },
    created_at: run.created_at,
  },
];

it("lists persisted traces and renders actual message/call/result order including empty assistant", async () => {
  const select = vi.fn();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) =>
      Response.json(
        String(input).includes("after_sequence")
          ? { trace: run, events, has_more: false }
          : { traces: [run], has_more: false },
      ),
    ),
  );
  const { container } = render(<TracePage selectedId={id} onSelect={select} />);
  await screen.findByText("Tool result · 返回结果");
  expect(screen.getByText(/消息文本为空/)).toBeInTheDocument();
  expect(container.querySelectorAll(".trace-event")).toHaveLength(4);
  expect(
    [...container.querySelectorAll(".trace-event header strong")].map((x) => x.textContent),
  ).toEqual(["用户消息", "Assistant message", "Tool call · 调用提议", "Tool result · 返回结果"]);
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: /查询 C1001/ }));
  expect(select).toHaveBeenCalledWith(id);
  await user.type(screen.getByRole("textbox", { name: "搜索 Trace 或请求 ID" }), "abc");
  await waitFor(() =>
    expect(fetch).toHaveBeenCalledWith(expect.stringContaining("search=abc"), expect.anything()),
  );
});

it("reads all event pages without dropping events or refetching terminal runs", async () => {
  const fetched = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (!url.includes("after_sequence")) return Response.json({ traces: [run], has_more: false });
    return Response.json(
      url.endsWith("after_sequence=0")
        ? { trace: run, events: events.slice(0, 2), has_more: true }
        : { trace: run, events: events.slice(2), has_more: false },
    );
  });
  vi.stubGlobal("fetch", fetched);
  const { container } = render(<TracePage selectedId={id} onSelect={() => {}} />);
  await screen.findByText("Tool result · 返回结果");
  expect(container.querySelectorAll(".trace-event")).toHaveLength(4);
  expect(fetched.mock.calls.some(([url]) => String(url).endsWith("after_sequence=2"))).toBe(true);
});

it("shows incomplete, truncated and rejected records without suggesting execution", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) =>
      Response.json(
        String(input).includes("after_sequence")
          ? {
              trace: { ...run, status: "failed", incomplete: true, error_code: "FORBIDDEN_TOOL" },
              events: [
                {
                  ...events[2],
                  kind: "tool_rejected",
                  payload: {
                    error_code: "FORBIDDEN_TOOL",
                    executed: false,
                    truncated: true,
                    excerpt: "[REDACTED]",
                  },
                },
              ],
              has_more: false,
            }
          : { traces: [], has_more: false },
      ),
    ),
  );
  render(<TracePage selectedId={id} onSelect={() => {}} />);
  await screen.findByText("Tool rejected · 未执行");
  expect(screen.getByText(/后端校验拒绝，工具未执行/)).toBeInTheDocument();
  expect(screen.getByText(/以下并非完整记录/)).toBeInTheDocument();
  expect(screen.getByText(/不会自动重放/)).toBeInTheDocument();
});

it("preserves a failed chat's Trace header for navigation", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      Response.json(
        { error: { code: "AGENT_UNAVAILABLE", message: "执行失败", request_id: id } },
        { status: 503, headers: { "X-Trace-ID": id } },
      ),
    ),
  );
  try {
    await sendChat("查询");
    throw new Error("should fail");
  } catch (error) {
    expect(error).toBeInstanceOf(ApiRequestError);
    expect((error as ApiRequestError).traceId).toBe(id);
  }
});

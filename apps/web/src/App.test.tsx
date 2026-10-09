import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { App } from "./App";

const overview = {
  request_id: "11111111-1111-4111-8111-111111111111",
  message: "已查询到客户 C1001。",
  model: "fake-model",
  route: { kind: "direct_tool", name: "crm.get_customer_overview" },
  data: {
    customer_code: "C1001",
    name: "模拟客户甲",
    renewal_date: "2026-10-05",
    risk_level: "high",
  },
  citations: [],
  task: null,
};

function mockApi(chatPayload: unknown = overview, chatStatus = 200) {
  const mocked = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
    if (String(input) === "/api/health") {
      return Response.json({ status: "ok", llm: "configured" });
    }
    if (String(input) === "/api/tasks") {
      return Response.json({ tasks: [] });
    }
    return Response.json(chatPayload, { status: chatStatus });
  });
  vi.stubGlobal("fetch", mocked);
  return mocked;
}

describe("OpsPilot current frontend slice", () => {
  it("opens a Trace deep link after the app is already mounted", async () => {
    window.history.replaceState(null, "", "#top");
    const id = "11111111-1111-4111-8111-111111111111";
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const path = String(input);
        if (path.startsWith("/api/traces/"))
          return Response.json({
            trace: {
              trace_id: id,
              request_id: id,
              status: "succeeded",
              role: "consultant",
              model: "fake",
              last_sequence: 0,
            },
            events: [],
            has_more: false,
          });
        if (path.startsWith("/api/traces")) return Response.json({ traces: [], has_more: false });
        return Response.json(
          path === "/api/health" ? { status: "ok", llm: "configured" } : { tasks: [] },
        );
      }),
    );
    render(<App />);
    window.location.hash = `#trace/${id}`;
    await screen.findByRole("heading", { name: "执行 Trace." });
    await screen.findByRole("heading", { name: "已完成" });
    expect(screen.getByLabelText("Trace 时间线")).toHaveTextContent(id);
    window.history.replaceState(null, "", "#top");
  });
  it("explicitly requests insight after selecting its shortcut", async () => {
    const fetched = mockApi({ ...overview, skill_name: "crm.customer_insight" });
    const user = userEvent.setup();
    render(<App />);
    await user.click(screen.getByRole("button", { name: "客户洞察，填入提问" }));
    expect(screen.getByRole("button", { name: "退出洞察模式" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "发送消息" }));
    await screen.findByText("模拟客户甲");
    const request = fetched.mock.calls.find(([path]) => path === "/api/chat");
    expect(JSON.parse(request![1]!.body as string).skill_name).toBe("crm.customer_insight");
  });
  it("shows only connected quick actions and sends a typed direct query", async () => {
    const fetchMock = mockApi();
    const user = userEvent.setup();
    render(<App />);
    expect(screen.getByRole("button", { name: "客户洞察，填入提问" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "回访计划，填入提问" })).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "客户概览，填入提问" }));
    expect(screen.getByRole("textbox", { name: "输入业务问题" })).toHaveValue(
      "查询客户 C1001 的基础信息",
    );
    await user.click(screen.getByRole("button", { name: "发送消息" }));
    expect(await screen.findByText("模拟客户甲")).toBeInTheDocument();
    expect(screen.getByText("直接工具 · ReAct")).toBeInTheDocument();
    expect(screen.getByText("11111111-1111-4111-8111-111111111111")).toBeInTheDocument();
    const chatCall = fetchMock.mock.calls.find(([path]) => path === "/api/chat");
    expect(chatCall).toBeDefined();
    expect(JSON.parse(chatCall![1]?.body as string)).toEqual({
      message: "查询客户 C1001 的基础信息",
    });
    expect(chatCall![1]?.headers).toMatchObject({ "X-Demo-Role": "consultant" });
  });

  it("shows server errors and request id without inventing a business result", async () => {
    mockApi(
      {
        error: {
          code: "LLM_NOT_CONFIGURED",
          message: "模型密钥尚未配置。",
          request_id: "test-request",
          retryable: false,
        },
      },
      503,
    );
    const user = userEvent.setup();
    render(<App />);
    await user.type(
      screen.getByRole("textbox", { name: "输入业务问题" }),
      "查询客户 C1001 的基础信息",
    );
    await user.click(screen.getByRole("button", { name: "发送消息" }));
    expect(await screen.findByText("模型密钥尚未配置。")).toBeInTheDocument();
    expect(screen.getByText(/LLM_NOT_CONFIGURED · 请求 ID test-request/)).toBeInTheDocument();
    expect(screen.queryByLabelText("客户概览结果")).not.toBeInTheDocument();
  });

  it("renders citations and Task state only if the backend supplied them", async () => {
    mockApi({
      ...overview,
      citations: [
        {
          document_id: "SOP-01",
          title: "模拟跟进规范",
          chunk_id: "SOP-01-1",
          excerpt: "先核实未关闭工单。",
          source_id: "business:SOP-01-1",
          endpoint_id: "business",
          version: "1.0",
        },
      ],
      task: { task_id: "task-01", status: "pending_approval" },
    });
    const user = userEvent.setup();
    render(<App />);
    await user.click(screen.getByRole("button", { name: "开放工单，填入提问" }));
    await user.click(screen.getByRole("button", { name: "发送消息" }));
    expect(await screen.findByText("模拟跟进规范")).toBeInTheDocument();
    expect(screen.getByText("先核实未关闭工单。")).toBeInTheDocument();
    expect(screen.getByText(/版本 1\.0/)).toBeInTheDocument();
    expect(screen.getByText(/版本 1\.0 · business/)).toBeInTheDocument();
    expect(screen.getByText("任务已提交")).toBeInTheDocument();
    expect(screen.queryByText("pending_approval")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /批准|拒绝|取消/ })).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("task-01")).toBeInTheDocument());
  });
});

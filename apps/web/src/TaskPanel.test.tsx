import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { it, expect, vi } from "vitest";
import { TaskPanel } from "./TaskPanel";

it("polls proposal, requires human confirmation and submits versioned idempotent decision", async () => {
  let state = "pending_approval";
  const task = {
    task_id: "11111111-1111-4111-8111-111111111111",
    status: state,
    version: 3,
    window_start: "2026-10-09",
    window_end: "2026-10-16",
    proposal: {
      candidates: [
        {
          customer_code: "C1001",
          risk_level: "high",
          renewal_date: "2026-10-12",
          action: "核实续费意向",
          due_date: "2026-10-09",
        },
      ],
      analysis: "建议核实工单[1]",
      citations: [
        {
          document_id: "SOP-CRM-001",
          title: "模拟跟进规范",
          chunk_id: "test",
          excerpt: "先核实客户反馈。",
        },
      ],
    },
    result: {},
  };
  const fetched = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
    if (String(input).endsWith("/decision")) {
      state = "approved";
      return Response.json({ ...task, status: state });
    }
    if (String(input).endsWith("/audit"))
      return Response.json({
        events: [{ id: "a", event: "approve", version: 4, created_at: "2026-10-09T00:00:00Z" }],
      });
    return Response.json({ tasks: [{ ...task, status: state }] });
  });
  vi.stubGlobal("fetch", fetched);
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  const user = userEvent.setup();
  render(<TaskPanel />);
  await screen.findByText("C1001");
  await user.click(screen.getByRole("button", { name: "批准并保存" }));
  expect(fetched.mock.calls.some(([path]) => String(path).endsWith("/decision"))).toBe(false);
  confirm.mockReturnValue(true);
  await user.click(screen.getByRole("button", { name: "批准并保存" }));
  await screen.findByText("已批准，等待提交");
  const request = fetched.mock.calls.find(([path]) => String(path).endsWith("/decision"));
  expect(JSON.parse(request![1]!.body as string)).toMatchObject({
    decision: "approve",
    expected_version: 3,
    idempotency_key: expect.any(String),
  });
  await user.click(screen.getByRole("button", { name: "查看审计" }));
  await waitFor(() => expect(screen.getByText(/approve · v4/)).toBeInTheDocument());
});

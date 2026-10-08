import { z } from "zod";

export const customerInput = z.strictObject({
  customer_id: z.string().regex(/^C[0-9]{4,}$/),
});

export const workOrderInput = z.strictObject({
  work_order_id: z.string().regex(/^WO-[0-9]{4,}$/),
});

export const searchInput = z.strictObject({
  query: z.string().min(2).max(300),
  top_k: z.number().int().min(1).max(10).default(5),
});

export const customerOutput = z.strictObject({
  customer_code: z.string(),
  name: z.string(),
  renewal_date: z.string(),
  risk_level: z.enum(["low", "medium", "high"]),
});

export const ticketsOutput = z.strictObject({
  customer_code: z.string(),
  tickets: z.array(z.strictObject({
    ticket_id: z.string(),
    status: z.literal("open"),
    summary: z.string(),
  })),
});

export const workOrderOutput = z.strictObject({
  work_order_code: z.string(),
  status: z.string(),
  updated_at: z.string(),
});

export const searchOutput = z.strictObject({
  matches: z.array(z.strictObject({
    document_id: z.string(),
    title: z.string(),
    chunk_id: z.string(),
    excerpt: z.string(),
    score: z.number().min(0).max(1),
  })),
});

const sevenDayWindow = (input: { window_start: string; window_end: string }) => {
  const start = Date.parse(`${input.window_start}T00:00:00Z`);
  const end = Date.parse(`${input.window_end}T00:00:00Z`);
  return Number.isFinite(start) && Number.isFinite(end) && end - start === 7 * 86_400_000;
};

export const proposalInput = z.strictObject({
  operation: z.literal("propose"),
  task_id: z.uuid(),
  window_start: z.iso.date(),
  window_end: z.iso.date(),
}).refine(sevenDayWindow, "回访窗口必须覆盖当天至第 7 个自然日");

export const commitInput = z.strictObject({
  operation: z.literal("commit"),
  task_id: z.uuid(),
});

export const followupInput = z.union([proposalInput, commitInput]);

export const followupOutput = z.strictObject({
  task_id: z.uuid(),
  operation: z.enum(["propose", "commit"]),
  status: z.enum(["pending_approval", "completed"]),
  candidates: z.array(z.strictObject({
    customer_code: z.string().regex(/^C[0-9]{4,}$/),
    renewal_date: z.iso.date(),
    risk_level: z.literal("high"),
  })).optional(),
  plan_count: z.number().int().nonnegative().optional(),
});

export const validatedFollowupOutput = followupOutput.superRefine((value, context) => {
  if (value.operation === "propose" && (value.status !== "pending_approval" || !value.candidates || value.plan_count !== undefined)) {
    context.addIssue({ code: "custom", message: "提案输出必须包含待审批状态与候选名单" });
  }
  if (value.operation === "commit" && (value.status !== "completed" || value.plan_count === undefined || value.candidates !== undefined)) {
    context.addIssue({ code: "custom", message: "提交输出必须包含已完成状态与计划数量" });
  }
});

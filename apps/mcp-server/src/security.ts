import { createHmac, timingSafeEqual } from "node:crypto";
import { readFileSync } from "node:fs";
import { z } from "zod";
import { contractFingerprint, toolCatalog } from "./tools/registry.js";

export interface ExecutionGate {
  assertAllowed(
    toolName: string,
    arguments_: Record<string, unknown>,
  ): Promise<void>;
}

export const denyAllGate: ExecutionGate = {
  async assertAllowed(): Promise<void> {
    throw new Error("TOOL_EXECUTION_UNAVAILABLE");
  },
};

const roleSchema = z.strictObject({
  bound_tools: z.array(z.string()),
  direct_tools: z.array(z.string()),
  allowed_skills: z.array(z.string()),
});
const skillSchema = z.strictObject({
  allowed_tools: z.array(z.string()),
  risk_level: z.enum(["read_only", "approval_required"]),
});
const policySchema = z.strictObject({
  version: z.literal(1),
  contract_fingerprint: z.string().regex(/^[a-f0-9]{64}$/),
  roles: z.record(z.string(), roleSchema),
  skills: z.record(z.string(), skillSchema),
});
const contextSchema = z.strictObject({
  version: z.union([z.literal(1), z.literal(2)]),
  endpoint_id: z.string().optional(),
  endpoint_revision: z.number().int().positive().optional(),
  request_id: z.uuid(),
  actor_id: z.string().min(1).max(80),
  role: z.string(),
  route: z.enum(["direct_tool", "skill", "agent"]),
  binding_version: z.number().int().positive().optional(),
  selected_tool: z.string().nullable(),
  skill_name: z.string().nullable(),
  binding_tools: z.array(z.string()),
  tool_name: z.string(),
  arguments: z.record(z.string(), z.unknown()),
  contract_fingerprint: z.string(),
  issued_at: z.number().int(),
  expires_at: z.number().int(),
  task_grant: z
    .strictObject({
      task_id: z.uuid(),
      version: z.number().int().positive(),
      lease_token: z.string().regex(/^[a-f0-9]{64}$/),
    })
    .optional(),
});
export type TaskExecutionContext = z.infer<typeof contextSchema>;
export type TaskGrantReader = (
  context: TaskExecutionContext,
  operation: string,
) => Promise<void>;

type Policy = z.infer<typeof policySchema>;

export interface AgentBinding {
  id: string;
  version: number;
  bound_tools: string[];
  endpoint_id: string;
  endpoint_revision: number;
  enabled: boolean;
}
export type BindingReader = (
  agentId: string,
  requestId: string,
) => Promise<AgentBinding>;

export function loadPolicy(): Policy {
  const policy = policySchema.parse(
    JSON.parse(
      readFileSync(new URL("../config/policy.json", import.meta.url), "utf8"),
    ),
  );
  if (policy.contract_fingerprint !== contractFingerprint())
    throw new Error("POLICY_CONTRACT_MISMATCH");
  const catalog = new Set(toolCatalog.map((tool) => tool.name));
  for (const skill of Object.values(policy.skills)) {
    if (
      new Set(skill.allowed_tools).size !== skill.allowed_tools.length ||
      skill.allowed_tools.some((name) => !catalog.has(name))
    )
      throw new Error("POLICY_INVALID_SKILL");
    if (
      skill.risk_level === "read_only" &&
      skill.allowed_tools.some(
        (name) =>
          toolCatalog.find((tool) => tool.name === name)?.riskLevel !==
          "read_only",
      )
    )
      throw new Error("POLICY_INVALID_SKILL");
  }
  for (const role of Object.values(policy.roles)) {
    if (
      new Set(role.bound_tools).size !== role.bound_tools.length ||
      role.bound_tools.some((name) => !catalog.has(name))
    )
      throw new Error("POLICY_UNKNOWN_TOOL");
    if (role.direct_tools.some((name) => !role.bound_tools.includes(name)))
      throw new Error("POLICY_INVALID_ROLE");
    if (
      role.allowed_skills.some(
        (name) =>
          !policy.skills[name] ||
          policy.skills[name].allowed_tools.some(
            (tool) => !role.bound_tools.includes(tool),
          ),
      )
    )
      throw new Error("POLICY_INVALID_ROLE");
  }
  return policy;
}

const canonical = (value: unknown): string => {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value !== null && typeof value === "object") {
    return `{${Object.entries(value as Record<string, unknown>)
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([key, item]) => `${JSON.stringify(key)}:${canonical(item)}`)
      .join(",")}}`;
  }
  return JSON.stringify(value);
};

export function signedExecutionGate(
  headers: { context?: string; signature?: string },
  secret: string,
  policy: Policy,
  now: () => number = () => Math.floor(Date.now() / 1000),
  readBinding?: BindingReader,
  ownEndpointId = "business",
  ownRoleId?: string,
  readTaskGrant?: TaskGrantReader,
): ExecutionGate {
  return {
    async assertAllowed(toolName, arguments_): Promise<void> {
      const encoded = headers.context;
      const signature = headers.signature;
      if (
        !encoded ||
        !signature ||
        !/^[a-f0-9]{64}$/.test(signature) ||
        secret.length < 32
      )
        throw new Error("UNAUTHORIZED");
      const expected = createHmac("sha256", secret).update(encoded).digest();
      if (!timingSafeEqual(expected, Buffer.from(signature, "hex")))
        throw new Error("UNAUTHORIZED");
      let context: z.infer<typeof contextSchema>;
      try {
        context = contextSchema.parse(
          JSON.parse(Buffer.from(encoded, "base64url").toString("utf8")),
        );
      } catch {
        throw new Error("UNAUTHORIZED");
      }
      const instant = now();
      if (
        context.issued_at > instant + 5 ||
        context.expires_at < instant ||
        context.expires_at - context.issued_at > 60
      )
        throw new Error("UNAUTHORIZED");
      if (
        context.contract_fingerprint !== policy.contract_fingerprint ||
        context.tool_name !== toolName
      )
        throw new Error("INVALID_BINDING");
      if (canonical(context.arguments) !== canonical(arguments_))
        throw new Error("INVALID_BINDING");
      // Deployed servers always supply readBinding. Static legacy routes must
      // not bypass a tool removed through the management page.
      if (readBinding) {
        if (ownRoleId && context.role !== ownRoleId)
          throw new Error("FORBIDDEN_TOOL");
        if (
          context.version !== 2 ||
          context.endpoint_id !== ownEndpointId ||
          !context.endpoint_revision
        )
          throw new Error("INVALID_BINDING");
        if (context.route !== "agent" || context.selected_tool !== null)
          throw new Error("INVALID_BINDING");
        const binding = await readBinding(context.role, context.request_id);
        const contract = toolCatalog.find((tool) => tool.name === toolName);
        let allowed = binding.bound_tools;
        if (context.skill_name !== null) {
          const skill = policy.skills[context.skill_name];
          if (
            !skill ||
            !["crm.customer_insight", "crm.followup_workflow"].includes(
              context.skill_name,
            ) ||
            !policy.roles[context.role]?.allowed_skills.includes(
              context.skill_name,
            )
          )
            throw new Error("FORBIDDEN_SKILL");
          if (context.skill_name === "crm.followup_workflow") {
            if (!context.task_grant || !readTaskGrant)
              throw new Error("APPROVAL_REQUIRED");
            if (
              toolName === "workflow.create_followup_plan" &&
              context.task_grant.task_id !== arguments_.task_id
            )
              throw new Error("INVALID_BINDING");
            await readTaskGrant(
              context,
              toolName === "workflow.create_followup_plan" &&
                arguments_.operation === "commit"
                ? "commit"
                : "propose",
            );
          } else if (skill.risk_level !== "read_only")
            throw new Error("APPROVAL_REQUIRED");
          allowed = allowed.filter((name) =>
            skill.allowed_tools.includes(name),
          );
        }
        if (!binding.enabled) throw new Error("ENDPOINT_DISABLED");
        if (
          binding.endpoint_id !== ownEndpointId ||
          binding.endpoint_revision !== context.endpoint_revision
        )
          throw new Error("BINDING_CHANGED");
        if (
          binding.id !== context.role ||
          binding.version !== context.binding_version
        )
          throw new Error("BINDING_CHANGED");
        if (
          canonical([...allowed].sort()) !==
            canonical([...context.binding_tools].sort()) ||
          !allowed.includes(toolName) ||
          !contract
        )
          throw new Error("FORBIDDEN_TOOL");
        if (
          contract.riskLevel !== "read_only" &&
          context.skill_name !== "crm.followup_workflow"
        )
          throw new Error("APPROVAL_REQUIRED");
        return;
      }
      if (context.route === "agent")
        throw new Error("TOOL_EXECUTION_UNAVAILABLE");
      const role = policy.roles[context.role];
      const contract = toolCatalog.find((tool) => tool.name === toolName);
      if (!role || !contract) throw new Error("FORBIDDEN_TOOL");
      let allowed: string[];
      if (context.route === "direct_tool") {
        if (context.skill_name !== null || context.selected_tool !== toolName)
          throw new Error("INVALID_BINDING");
        allowed = role.bound_tools.filter(
          (name) => role.direct_tools.includes(name) && name === toolName,
        );
      } else {
        const skill = context.skill_name
          ? policy.skills[context.skill_name]
          : undefined;
        if (
          !skill ||
          !role.allowed_skills.includes(context.skill_name!) ||
          context.selected_tool !== null
        )
          throw new Error("FORBIDDEN_SKILL");
        allowed = role.bound_tools.filter((name) =>
          skill.allowed_tools.includes(name),
        );
        if (
          skill.risk_level === "read_only" &&
          contract.riskLevel !== "read_only"
        )
          throw new Error("FORBIDDEN_TOOL");
      }
      if (
        canonical([...allowed].sort()) !==
          canonical([...context.binding_tools].sort()) ||
        !allowed.includes(toolName)
      )
        throw new Error("FORBIDDEN_TOOL");
      // The write path needs a separate Task approval proof; keep it unavailable here.
      if (contract.riskLevel !== "read_only")
        throw new Error("APPROVAL_REQUIRED");
    },
  };
}

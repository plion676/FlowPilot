import { z } from "zod";

const endpointSchema = z.strictObject({
  id: z.string().regex(/^[a-z][a-z0-9_]{0,31}$/),
  role_id: z.string().regex(/^[a-z][a-z0-9_]{0,79}$/),
  path: z.string().regex(/^\/mcp(?:\/[a-z][a-z0-9_-]*)?$/),
  domains: z
    .array(z.enum(["crm", "mes", "knowledge", "workflow"]))
    .min(1)
    .max(4),
  secret_env: z
    .string()
    .regex(/^(MCP_CALL_SECRET|OPSPILOT_MCP_SECRET_[A-Z0-9_]+)$/)
    .optional(),
});
export type EndpointDefinition = z.infer<typeof endpointSchema>;
export function loadEndpoints(
  source = process.env.MCP_ENDPOINT_DEFINITIONS,
): EndpointDefinition[] {
  const endpoints = z
    .array(endpointSchema)
    .min(1)
    .max(10)
    .parse(
      source
        ? JSON.parse(source)
        : [
            {
              id: "business",
              role_id: "consultant",
              path: "/mcp/consultant",
              domains: ["crm", "mes", "knowledge", "workflow"],
            },
          ],
    );
  if (
    new Set(endpoints.map((e) => e.id)).size !== endpoints.length ||
    new Set(endpoints.map((e) => e.path)).size !== endpoints.length ||
    endpoints.some((e) => new Set(e.domains).size !== e.domains.length)
  )
    throw new Error("MCP Endpoint 定义重复");
  return endpoints;
}

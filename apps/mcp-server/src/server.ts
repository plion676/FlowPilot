import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { unavailableBackend, type ToolBackend } from "./backend.js";
import { denyAllGate, type ExecutionGate } from "./security.js";
import { contractFingerprint, domains } from "./tools/registry.js";
import type { EndpointDefinition } from "./endpoints.js";

export function createMcpServer(
  backend: ToolBackend = unavailableBackend,
  gate: ExecutionGate = denyAllGate,
  endpoint: EndpointDefinition = {
    id: "business",
    role_id: "consultant",
    path: "/mcp/consultant",
    domains: ["crm", "mes", "knowledge", "workflow"],
  },
): McpServer {
  const server = new McpServer({ name: "opspilot-mcp", version: "0.1.0" });
  for (const domain of domains.filter((domain) =>
    domain.contracts.every((tool) => endpoint.domains.includes(tool.domain)),
  ))
    domain.register(server, backend, gate, {
      "opspilot/contractFingerprint": contractFingerprint(),
      "opspilot/endpointId": endpoint.id,
      "opspilot/roleId": endpoint.role_id,
    });
  return server;
}

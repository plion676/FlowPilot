import { createServer } from "node:http";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import {
  goBindingReader,
  goReadBackend,
  unavailableBackend,
} from "./backend.js";
import { denyAllGate, loadPolicy, signedExecutionGate } from "./security.js";
import { createMcpServer } from "./server.js";
import { loadEndpoints } from "./endpoints.js";

const port = Number(process.env.MCP_PORT ?? "3100");
if (!Number.isInteger(port) || port < 1 || port > 65535)
  throw new Error("MCP_PORT 无效");
const host = process.env.MCP_HOST ?? "127.0.0.1";
if (host !== "127.0.0.1" && host !== "0.0.0.0")
  throw new Error("MCP_HOST 无效");
const policy = loadPolicy();
const secret = process.env.MCP_CALL_SECRET ?? "";
const businessUrl = process.env.BUSINESS_BASE_URL ?? "";
const serviceToken = process.env.INTERNAL_SERVICE_TOKEN ?? "";
const executionEnabled =
  secret.length >= 32 && businessUrl !== "" && serviceToken.length >= 16;
const endpoints = loadEndpoints();

const httpServer = createServer(async (request, response) => {
  const origin = request.headers.origin;
  if (origin) {
    let valid = false;
    try {
      const parsed = new URL(origin);
      valid =
        ["http:", "https:"].includes(parsed.protocol) &&
        ["127.0.0.1", "localhost", "[::1]"].includes(parsed.hostname);
    } catch {
      /* deny malformed Origin */
    }
    if (!valid) {
      response.writeHead(403);
      response.end();
      return;
    }
  }
  const pathname = new URL(request.url ?? "/", "http://localhost").pathname;
  if (pathname === "/health" && request.method === "GET") {
    response.writeHead(200, { "content-type": "application/json" });
    response.end(
      JSON.stringify({
        status: "ok",
        tool_execution: executionEnabled ? "read_only" : "disabled",
      }),
    );
    return;
  }
  const endpoint = endpoints.find((item) => item.path === pathname);
  if (!endpoint) {
    response.writeHead(404);
    response.end();
    return;
  }

  const context = request.headers["x-opspilot-context"];
  const signature = request.headers["x-opspilot-signature"];
  const endpointSecret = endpoint.secret_env
    ? (process.env[endpoint.secret_env] ?? "")
    : secret;
  const endpointEnabled =
    endpointSecret.length >= 32 &&
    businessUrl !== "" &&
    serviceToken.length >= 16;
  const gate = endpointEnabled
    ? signedExecutionGate(
        {
          context: typeof context === "string" ? context : undefined,
          signature: typeof signature === "string" ? signature : undefined,
        },
        endpointSecret,
        policy,
        undefined,
        goBindingReader(businessUrl, serviceToken, endpoint.id),
        endpoint.id,
        endpoint.role_id,
      )
    : denyAllGate;
  let requestId = "00000000-0000-4000-8000-000000000000";
  if (typeof context === "string") {
    try {
      const parsed = JSON.parse(
        Buffer.from(context, "base64url").toString("utf8"),
      ) as { request_id?: unknown };
      if (
        typeof parsed.request_id === "string" &&
        /^[a-f0-9-]{36}$/i.test(parsed.request_id)
      )
        requestId = parsed.request_id;
    } catch {
      /* Signed gate rejects invalid context before backend use. */
    }
  }
  const backend = endpointEnabled
    ? goReadBackend(businessUrl, serviceToken, requestId)
    : unavailableBackend;
  const server = createMcpServer(backend, gate, endpoint);
  const transport = new StreamableHTTPServerTransport({
    sessionIdGenerator: undefined,
  });
  try {
    await server.connect(transport);
    await transport.handleRequest(request, response);
  } catch (error) {
    console.error("MCP_REQUEST_FAILED", { endpoint_id: endpoint.id });
    if (!response.headersSent) response.writeHead(500);
    if (!response.writableEnded) response.end();
  } finally {
    await transport.close();
    await server.close();
  }
});

httpServer.listen(port, host, () => {
  console.info(`OpsPilot MCP listening on http://${host}:${port} (role-scoped endpoints)`);
});

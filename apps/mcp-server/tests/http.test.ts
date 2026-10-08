import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { test } from "node:test";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";

test("Streamable HTTP client can initialize, list tools, and observes closed execution gate", async () => {
  const port = 20_000 + Math.floor(Math.random() * 20_000);
  const child = spawn(process.execPath, ["dist/index.js"], {
    cwd: new URL("..", import.meta.url).pathname,
    env: { ...process.env, MCP_PORT: String(port) },
    stdio: ["ignore", "pipe", "pipe"],
  });
  const output: string[] = [];
  child.stderr.on("data", (chunk: Buffer) => output.push(chunk.toString()));
  child.stdout.on("data", (chunk: Buffer) => output.push(chunk.toString()));
  const client = new Client({ name: "opspilot-http-test", version: "0.1.0" });
  try {
    let ready = false;
    for (let attempt = 0; attempt < 40; attempt += 1) {
      if (child.exitCode !== null)
        throw new Error(`MCP server exited: ${output.join("")}`);
      try {
        const response = await fetch(`http://127.0.0.1:${port}/health`);
        ready = response.ok;
        if (ready) break;
      } catch {
        /* server not listening yet */
      }
      await new Promise((resolve) => setTimeout(resolve, 50));
    }
    assert.equal(
      ready,
      true,
      `server did not become ready: ${output.join("")}`,
    );
    await client.connect(
      new StreamableHTTPClientTransport(
        new URL(`http://127.0.0.1:${port}/mcp/consultant`),
      ),
    );
    const listed = await client.listTools();
    assert.equal(listed.tools.length, 5);
    assert.ok(listed.tools.every((tool) => tool._meta?.["opspilot/roleId"] === "consultant" && tool._meta?.["opspilot/endpointId"] === "business"));
    for (const legacy of ["/mcp", "/mcp/crm", "/mcp/mes"])
      assert.equal((await fetch(`http://127.0.0.1:${port}${legacy}`)).status, 404);
    assert.equal(
      (
        await fetch(`http://127.0.0.1:${port}/mcp/consultant`, {
          headers: { Origin: "https://evil.example" },
        })
      ).status,
      403,
    );
    const result = await client.callTool({
      name: "crm.get_customer_overview",
      arguments: { customer_id: "C1001" },
    });
    assert.equal(result.isError, true);
  } finally {
    await client.close();
    child.kill("SIGTERM");
    if (child.exitCode === null) await once(child, "exit");
  }
});

import assert from "node:assert/strict";
import { test } from "node:test";
import { loadEndpoints } from "../src/endpoints.js";

test("Endpoint identity belongs to a role; Tool modules remain reusable", () => {
  const definitions = loadEndpoints(JSON.stringify([
    {id: "consultant", role_id: "consultant", path: "/mcp/consultant", domains: ["crm", "mes"]},
    {id: "support", role_id: "support", path: "/mcp/support", domains: ["crm", "knowledge"]},
  ]));
  assert.equal(definitions.length, 2);
  assert.ok(definitions.every((endpoint) => endpoint.domains.includes("crm")));
  assert.notEqual(definitions[0].role_id, definitions[1].role_id);
});

test("Legacy domain-only definitions cannot silently become role endpoints", () => {
  assert.throws(() => loadEndpoints(JSON.stringify([{id: "crm", path: "/mcp/crm", domains: ["crm"]}])));
  assert.throws(() => loadEndpoints(JSON.stringify([{id: "x", role_id: "", path: "/mcp/x", domains: ["crm"]}])));
});

// Optional browser smoke test. Start an isolated Chrome with remote debugging
// on 9225 first; all business services must be running locally.
import assert from "node:assert/strict";
import { writeFile } from "node:fs/promises";

const tabs = await (await fetch("http://127.0.0.1:9225/json/list")).json();
const tab = tabs.find((item) => item.type === "page");
const socket = new WebSocket(tab.webSocketDebuggerUrl);
await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
let sequence = 0;
const pending = new Map();
const errors = [];
socket.onmessage = ({ data }) => {
  const message = JSON.parse(data);
  if (message.method === "Runtime.exceptionThrown") errors.push(message.params.exceptionDetails.text);
  const item = pending.get(message.id);
  if (item) {
    pending.delete(message.id);
    if (message.error) item.reject(new Error(message.error.message));
    else item.resolve(message.result);
  }
};
function call(method, params = {}) {
  const id = ++sequence;
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    socket.send(JSON.stringify({ id, method, params }));
  });
}
async function evaluate(expression) {
  const result = await call("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
  if (result.exceptionDetails) throw new Error(result.exceptionDetails.text);
  return result.result.value;
}
async function waitFor(expression, attempts = 100) {
  for (let attempt = 0; attempt < attempts; attempt++) {
    if (await evaluate(expression)) return;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(`Timed out: ${expression}`);
}
async function screenshot(path) {
  const result = await call("Page.captureScreenshot", { format: "png", captureBeyondViewport: true });
  await writeFile(path, Buffer.from(result.data, "base64"));
}
try {
  await call("Page.enable");
  await call("Runtime.enable");
  await call("Emulation.setDeviceMetricsOverride", { width: 1440, height: 1100, deviceScaleFactor: 1, mobile: false });
  await call("Page.navigate", { url: "http://127.0.0.1:5173/" });
  await waitFor("[...document.querySelectorAll('button')].some(x => x.textContent === '工具管理')");
  await evaluate("[...document.querySelectorAll('button')].find(x => x.textContent === 'MCP 连接').click()");
  await waitFor("document.querySelectorAll('.endpoint-actions').length > 0");
  await evaluate(`(() => {
    const select = document.querySelector('select[aria-label="连接所属角色筛选"]');
    select.value = 'consultant';
    select.dispatchEvent(new Event('change', { bubbles: true }));
  })()`);
  await waitFor("document.querySelectorAll('.endpoint-actions').length === 1");
  assert.match(await evaluate("document.querySelector('.endpoint-address').textContent"), /\/mcp\/consultant$/);
  assert.equal(await evaluate("document.querySelector('input[aria-label=\"连接分类名称\"]')"), null);
  assert.equal(await evaluate("document.querySelectorAll('.management-error').length"), 0);
  await screenshot("/tmp/opspilot-endpoints-desktop.png");
  await evaluate("[...document.querySelectorAll('button')].find(x => x.textContent === '工具管理').click()");
  await waitFor("document.querySelectorAll('.tool-card').length === 5");
  assert.equal(await evaluate("document.querySelectorAll('.endpoint-option').length"), 1);
  assert.equal(await evaluate("document.querySelector('input[aria-label=\"连接 crm\"]')"), null);
  assert.equal(await evaluate("document.querySelectorAll('.management-error').length"), 0);
  await screenshot("/tmp/opspilot-tool-management-desktop.png");

  const selector = "input[aria-label='绑定 business / mes.get_work_order_status']";
  const original = await evaluate(`document.querySelector(${JSON.stringify(selector)}).checked`);
  async function toggleAndSave() {
    await evaluate(`document.querySelector(${JSON.stringify(selector)}).click()`);
    await waitFor("!document.querySelector('.binding-save button').disabled");
    await evaluate("document.querySelector('.binding-save button').click()");
    await waitFor("document.querySelector('.management-notice') !== null && document.querySelector('.binding-save button').disabled");
  }
  let changed = false;
  try {
    await toggleAndSave();
    changed = true;
    const current = await (await fetch("http://127.0.0.1:8000/api/admin/agents")).json();
    assert.equal(current.agents[0].tools.some(tool => tool.endpoint_id === "business" && tool.tool_name === "mes.get_work_order_status"), !original);
  } finally {
    if (changed) await toggleAndSave();
  }
  await evaluate(`(() => {
    const input = document.querySelector('input[type=search]');
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, '生产');
    input.dispatchEvent(new Event('input', { bubbles: true }));
  })()`);
  await waitFor("document.querySelectorAll('.tool-card').length === 1");
  assert.match(await evaluate("document.querySelector('.tool-card').textContent"), /mes.get_work_order_status/);
  // Explicit opt-in: this sends synthetic data to the configured model provider.
  if (process.env.OPSPILOT_SMOKE_INSIGHT === "1") {
    await evaluate(`(() => {
      const input = document.querySelector('input[type=search]');
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, '');
      input.dispatchEvent(new Event('input', { bubbles: true }));
    })()`);
    await waitFor("document.querySelectorAll('.tool-card').length === 5");
    const sopSelector = "input[aria-label='绑定 business / knowledge.search_sop']";
    const sopOriginal = await evaluate(`document.querySelector(${JSON.stringify(sopSelector)}).checked`);
    async function toggleSopAndSave() {
      await evaluate(`document.querySelector(${JSON.stringify(sopSelector)}).click()`);
      await waitFor("!document.querySelector('.binding-save button').disabled");
      await evaluate("document.querySelector('.binding-save button').click()");
      await waitFor("document.querySelector('.binding-save button').disabled && document.querySelector('.management-notice') !== null");
    }
    let sopChanged = false;
    try {
      if (!sopOriginal) { await toggleSopAndSave(); sopChanged = true; }
      await evaluate("[...document.querySelectorAll('button')].find(x => x.textContent === '工作台').click()");
      await evaluate("document.querySelector('button[aria-label=\"客户洞察，填入提问\"]').click()");
      await evaluate("document.querySelector('button[aria-label=\"发送消息\"]').click()");
      await waitFor("!!(document.querySelector('.agent-answer') || document.querySelector('.error-card'))", 900);
      assert.ok(await evaluate("document.querySelectorAll('.citation-card').length > 0"), "insight must show validated citations");
      assert.match(await evaluate("document.querySelector('.agent-answer').textContent"), /\[1\]/);
      assert.match(await evaluate("document.querySelector('.citation-card').textContent"), /版本 1\.0/);
      await screenshot("/tmp/opspilot-insight-desktop.png");
      await call("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
      await screenshot("/tmp/opspilot-insight-mobile.png");
      assert.ok(await evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), "insight mobile overflow");
      await call("Emulation.setDeviceMetricsOverride", { width: 1440, height: 1100, deviceScaleFactor: 1, mobile: false });
    } finally {
      await evaluate("[...document.querySelectorAll('button')].find(x => x.textContent === '工具管理').click()");
      await waitFor("document.querySelectorAll('.tool-card').length === 5");
      if (sopChanged) await toggleSopAndSave();
    }
    await evaluate(`(() => {
      const input = document.querySelector('input[type=search]');
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, '生产');
      input.dispatchEvent(new Event('input', { bubbles: true }));
    })()`);
    await waitFor("document.querySelectorAll('.tool-card').length === 1");
  }
  await call("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
  await screenshot("/tmp/opspilot-tool-management-mobile.png");
  assert.ok(await evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), "mobile horizontal overflow");
  await evaluate("[...document.querySelectorAll('button')].find(x => x.textContent === 'MCP 连接').click()");
  await waitFor("document.querySelectorAll('.endpoint-actions').length > 0");
  await evaluate(`(() => {
    const select = document.querySelector('select[aria-label="连接所属角色筛选"]');
    select.value = 'consultant';
    select.dispatchEvent(new Event('change', { bubbles: true }));
  })()`);
  await waitFor("document.querySelectorAll('.endpoint-actions').length === 1");
  await screenshot("/tmp/opspilot-endpoints-mobile.png");
  assert.ok(await evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), "Endpoint mobile horizontal overflow");
  assert.deepEqual(errors, []);
  console.log("Browser smoke passed: role-owned Endpoint directory, domain Tool search, bind/save/restore, mobile layout; no JS exceptions.");
} finally {
  socket.close();
}

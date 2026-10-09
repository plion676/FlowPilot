// Requires an isolated Chrome on 9225. Opt-in live mode uses the configured
// model and explicitly approves ONE simulated proposal through the actual UI.
import assert from "node:assert/strict";
import { writeFile } from "node:fs/promises";

const tabs = await (await fetch("http://127.0.0.1:9225/json/list")).json();
const tab = tabs.find((item) => item.type === "page");
assert.ok(tab, "Start an isolated Chrome debugging session first");
const socket = new WebSocket(tab.webSocketDebuggerUrl);
await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
let sequence = 0;
const pending = new Map();
const errors = [];
let confirmation = false;
socket.onmessage = ({ data }) => {
  const message = JSON.parse(data);
  if (message.method === "Runtime.exceptionThrown") errors.push(message.params.exceptionDetails.text);
  if (message.method === "Page.javascriptDialogOpening") {
    confirmation = true;
    void call("Page.handleJavaScriptDialog", { accept: true });
  }
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
async function waitFor(expression, attempts = 200) {
  for (let attempt = 0; attempt < attempts; attempt++) {
    if (await evaluate(expression)) return;
    await new Promise(resolve => setTimeout(resolve, 200));
  }
  throw new Error(`Timed out: ${expression}`);
}
async function screenshot(path) {
  const result = await call("Page.captureScreenshot", { format: "png", captureBeyondViewport: true });
  await writeFile(path, Buffer.from(result.data, "base64"));
}
let createdId;
try {
  await call("Page.enable");
  await call("Runtime.enable");
  await call("Emulation.setDeviceMetricsOverride", { width:1440, height:1100, deviceScaleFactor:1, mobile:false });
  const before = new Set((await (await fetch("http://127.0.0.1:8000/api/tasks")).json()).tasks.map(x => x.task_id));
  await call("Page.navigate", { url:"http://127.0.0.1:5173/" });
  await waitFor("!!document.querySelector('.persistent-tasks')");
  if (process.env.OPSPILOT_SMOKE_FOLLOWUP === "1") {
    await evaluate(`document.querySelector('button[aria-label="回访计划，填入提问"]').click()`);
    await waitFor(`!![...document.querySelectorAll('button')].find(x => x.textContent === '退出回访模式')`);
    await evaluate(`document.querySelector('button[aria-label="发送消息"]').click()`);
    for (let attempt = 0; attempt < 900; attempt++) {
      const tasks = (await (await fetch("http://127.0.0.1:8000/api/tasks")).json()).tasks;
      const task = tasks.find(x => !before.has(x.task_id));
      if (task) {
        createdId = task.task_id;
        assert.notEqual(task.status, "failed", `Live proposal failed: ${task.error_code}`);
        if (task.status === "pending_approval") {
          assert.ok(task.proposal.citations.length);
          assert.ok(task.proposal.candidates.length);
          break;
        }
      }
      if (attempt === 899) throw new Error("Live proposal timed out");
      await new Promise(resolve => setTimeout(resolve, 200));
    }
    const selector = `.followup-task[data-task-id='${createdId}']`;
    await waitFor(`document.querySelector(${JSON.stringify(selector)})?.textContent.includes('等待人工确认')`);
    await evaluate(`document.querySelector(${JSON.stringify(selector)}).querySelector('details').open = true`);
    await screenshot("/tmp/opspilot-followup-pending.png");
    await evaluate(`[...document.querySelector(${JSON.stringify(selector)}).querySelectorAll('button')].find(x => x.textContent === '批准并保存').click()`);
    await waitFor(`document.querySelector(${JSON.stringify(selector)})?.textContent.includes('已保存正式计划')`);
    assert.ok(confirmation, "Approval must open the real confirmation dialog");
    await evaluate(`[...document.querySelector(${JSON.stringify(selector)}).querySelectorAll('button')].find(x => x.textContent === '查看审计').click()`);
    await waitFor(`document.querySelector(${JSON.stringify(selector)})?.textContent.includes('crm_committed')`);
    const complete = await (await fetch(`http://127.0.0.1:8000/api/tasks/${createdId}`)).json();
    assert.equal(complete.status, "completed");
    assert.ok(complete.result.plan_count > 0);
    console.log(JSON.stringify({ task_id:createdId, status:complete.status, plan_count:complete.result.plan_count }));
  }
  await screenshot("/tmp/opspilot-followup-desktop.png");
  await call("Emulation.setDeviceMetricsOverride", { width:390, height:844, deviceScaleFactor:1, mobile:true });
  await screenshot("/tmp/opspilot-followup-mobile.png");
  assert.ok(await evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), "mobile overflow");
  assert.deepEqual(errors, []);
  console.log("Followup UI smoke passed");
} finally {
  // Leave completed records/audits; revoke only this test's unfinished task.
  if (createdId) {
    const task = await (await fetch(`http://127.0.0.1:8000/api/tasks/${createdId}`)).json();
    if (["created", "gathering", "pending_approval", "approved"].includes(task.status)) {
      await fetch(`http://127.0.0.1:8000/api/tasks/${createdId}/decision`, { method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({decision:"cancel", expected_version:task.version, idempotency_key:crypto.randomUUID()}) });
    }
  }
  socket.close();
}

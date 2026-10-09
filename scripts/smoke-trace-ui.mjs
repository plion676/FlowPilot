// Uses an isolated Chrome debug instance on 9225; never changes tool bindings.
import assert from "node:assert/strict";
import { writeFile } from "node:fs/promises";

const tabs = await (await fetch("http://127.0.0.1:9225/json/list")).json();
const socket = new WebSocket(tabs.find(t => t.type === "page").webSocketDebuggerUrl);
await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
let sequence = 0;
const pending = new Map();
const errors = [];
socket.onmessage = ({ data }) => {
  const message = JSON.parse(data);
  if (message.method === "Runtime.exceptionThrown") errors.push(message.params.exceptionDetails.text);
  const item = pending.get(message.id);
  if (item) { pending.delete(message.id); message.error ? item.reject(new Error(message.error.message)) : item.resolve(message.result); }
};
function call(method, params = {}) {
  const id = ++sequence;
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => { pending.delete(id); reject(new Error(`CDP timeout: ${method}`)); },10000);
    pending.set(id, {resolve: value => {clearTimeout(timer);resolve(value);},reject: error=>{clearTimeout(timer);reject(error);}});
    socket.send(JSON.stringify({id,method,params}));
  });
}
async function evaluate(expression) {
  const result = await call("Runtime.evaluate", {expression, returnByValue:true, awaitPromise:true});
  if (result.exceptionDetails) throw new Error(result.exceptionDetails.text);
  return result.result.value;
}
async function waitFor(expression, attempts=200) {
  for (let i=0;i<attempts;i++) {
    if (await evaluate(expression)) return;
    await new Promise(resolve=>setTimeout(resolve,100));
  }
  throw new Error(`Timed out: ${expression}`);
}
try {
  await call("Page.enable");await call("Runtime.enable");
  await call("Emulation.setDeviceMetricsOverride", {width:1440,height:1100,deviceScaleFactor:1,mobile:false});
  await call("Page.navigate", {url:"http://127.0.0.1:5173/#trace"});
  await waitFor("document.querySelectorAll('.trace-run').length>0");
  const runs = await (await fetch("http://127.0.0.1:8000/api/traces")).json();
  const query = runs.traces.find(r => r.status === "succeeded" && r.summary === "查询 C1001");
  assert.ok(query,"Run scripts/dev.py verify-trace first");
  await call("Page.navigate", {url:`http://127.0.0.1:5173/#trace/${query.trace_id}`});
  await waitFor("document.querySelectorAll('.trace-event').length === 11");
  console.log("Trace detail loaded with 11 events");
  const labels = await evaluate("[...document.querySelectorAll('.trace-event header strong')].map(x=>x.textContent)");
  assert.ok(labels.indexOf("Tool call · 调用提议") < labels.indexOf("Tool result · 返回结果"));
  assert.match(await evaluate("document.querySelector('.trace-detail').textContent"), /crm.get_customer_overview/);
  assert.match(await evaluate("document.querySelector('.trace-detail').textContent"), /修订 \d+/);
  await call("Page.reload");
  await waitFor("document.querySelectorAll('.trace-event').length === 11");
  console.log("Trace refresh passed");
  try {
    const shot = await call("Page.captureScreenshot", {format:"png",captureBeyondViewport:false,fromSurface:false});
    await writeFile("/tmp/opspilot-trace-desktop.png",Buffer.from(shot.data,"base64"));
  } catch { console.log("Screenshot unavailable; DOM assertions remain active"); }
  await call("Emulation.setDeviceMetricsOverride", {width:390,height:844,deviceScaleFactor:1,mobile:true});
  assert.equal(await evaluate("document.documentElement.scrollWidth > innerWidth"),false,"mobile overflow");
  if (process.env.OPSPILOT_SMOKE_TRACE_LIVE === "1") {
    await call("Emulation.setDeviceMetricsOverride", {width:1440,height:1100,deviceScaleFactor:1,mobile:false});
    await evaluate("[...document.querySelectorAll('button')].find(x=>x.textContent==='工作台').click()");
    await waitFor("document.querySelector('textarea')!==null");
    await evaluate(`(() => {const input=document.querySelector('textarea');Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(input,'查询客户 C1001 的基础信息');input.dispatchEvent(new Event('input',{bubbles:true}));})()`);
    await waitFor("!document.querySelector('button[aria-label=\"发送消息\"]').disabled");
    await evaluate("document.querySelector('button[aria-label=\"发送消息\"]').click()");
    await waitFor("document.querySelector('.trace-link') !== null",600);
    await evaluate("document.querySelector('.trace-link').click()");
    await waitFor("document.querySelectorAll('.trace-event').length >= 11");
    assert.match(await evaluate("document.querySelector('.trace-detail').textContent"),/C1001/);
  }
  assert.deepEqual(errors,[]);
  console.log("Trace browser smoke passed: chronological events, refresh, mobile layout" + (process.env.OPSPILOT_SMOKE_TRACE_LIVE === "1" ? ", live DeepSeek chat jump" : ""));
} finally {socket.close();}

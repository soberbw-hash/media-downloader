/* 扩展同步限流回归：15 秒内丢弃 → 改成「到点补跑」。
 *
 * 老 bug：queueSync 的抖动合并里，两次同步要隔 15 秒，但窗口内那波 cookie
 * 变化直接 return 吞掉——用户刚打开页面触发一次同步，紧接着登录成功，
 * 登录态就丢了，界面上一直「未登录」。
 *
 * 用虚拟时钟真跑一遍 background.js 的事件处理，不用装扩展：
 *   node tests/test_ext_throttle.mjs
 */
import fs from "node:fs";
import vm from "node:vm";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const SRC = fs.readFileSync(
  path.join(ROOT, "browser-extension", "background.js"), "utf-8");

/* ---------- 虚拟时钟：setTimeout 由我们手动推进 ---------- */
let now = 1700000000000;
let nextId = 1;
let timers = [];
function fakeSetTimeout(fn, ms) {
  const id = nextId++;
  timers.push({ id, fn, at: now + (ms || 0) });
  return id;
}
function fakeClearTimeout(id) {
  timers = timers.filter(t => t.id !== id);
}
async function advance(ms) {
  const target = now + ms;
  for (;;) {
    timers.sort((a, b) => a.at - b.at);
    const t = timers.find(x => x.at <= target);
    if (!t) break;
    timers = timers.filter(x => x.id !== t.id);
    now = t.at;
    await t.fn();            // runSync 是 async 的，等它跑完
  }
  now = target;
}

/* ---------- mock chrome / fetch ---------- */
const fakeCookies = {
  "xiaohongshu.com": [{
    name: "web_session", value: "fake_throttle", domain: "xiaohongshu.com",
    path: "/", secure: true, httpOnly: true, expirationDate: 1999999999
  }]
};
const store = {};
const listeners = {};        // 收集 addListener 注册进来的处理函数
function listen(name) {
  return { addListener(fn) { listeners[name] = fn; } };
}
const chrome = {
  cookies: {
    getAll: async ({ domain }) => fakeCookies[domain] || [],
    onChanged: listen("cookies.onChanged")
  },
  tabs: { onUpdated: listen("tabs.onUpdated") },
  storage: { local: {
    get: async (keys) => {
      const out = {};
      for (const k of (Array.isArray(keys) ? keys : [keys])) out[k] = store[k];
      return out;
    },
    set: async (o) => Object.assign(store, o)
  } },
  runtime: {
    onInstalled: listen("onInstalled"),
    onStartup: listen("onStartup"),
    onMessage: listen("onMessage")
  },
  alarms: {
    create() {}, clear() {}, onAlarm: listen("alarms.onAlarm")
  }
};

let extPosts = 0;            // 真正把登录态送出去的次数
const fetchMock = async (url, opts = {}) => {
  const u = String(url);
  if (u.includes("/api/login/extension") && opts.method === "POST") {
    extPosts += 1;
    return { ok: true, json: async () => ({ ok: true, msg: "同步完成", results: [{}] }) };
  }
  if (u.includes("/api/login/state")) {
    return { ok: true, json: async () => ({ sites: [{ id: "xiaohongshu" }] }) };
  }
  return { ok: false, json: async () => ({}) };
};

const ctx = vm.createContext({
  chrome, fetch: fetchMock, AbortController,
  setTimeout: fakeSetTimeout, clearTimeout: fakeClearTimeout,
  console, Date: { now: () => now },
  JSON, Array, String, Object
});
vm.runInContext(SRC, ctx, { filename: "background.js" });

function assert(cond, msg) {
  if (!cond) {
    console.error("✗ " + msg);
    process.exit(1);
  }
  console.log("✓ " + msg);
}

/* ---------- 场景 ---------- */

// 1. 首次同步不被限流
vm.runInContext("queueSync()", ctx);
await advance(4000);
assert(extPosts === 1, "首次 cookie 变化：4 秒抖动后立刻同步");

// 2. 15 秒窗口内的第二波变化：以前直接吞掉，现在必须补跑
vm.runInContext("queueSync()", ctx);
await advance(4000);           // 抖动到点，触发 runSync，但还在窗口内
assert(extPosts === 1, "窗口内没立刻重发（限流仍在）");
await advance(11000);          // 越过 15 秒窗口，补跑定时器到点
assert(extPosts === 2, "窗口内的变化没有丢：到点补跑了一次");

// 3. 连续抖动只发一次：4 秒内来三连击，只该同步一次
extPosts = 0;
vm.runInContext("queueSync()", ctx);
await advance(1000);
vm.runInContext("queueSync()", ctx);
await advance(1000);
vm.runInContext("queueSync()", ctx);
await advance(3000);           // 距最后一次 4 秒
await advance(15000);          // 等限流窗口过完，确保没有多余补跑
assert(extPosts === 1, "4 秒内三连击合并成一次同步");

// 4. cookies.onChanged 走的是同一条路（真登录的那一刻）
extPosts = 0;
listeners["cookies.onChanged"]({
  cookie: { domain: ".xiaohongshu.com", name: "web_session", value: "x" }
});
await advance(4000);
await advance(15000);
assert(extPosts === 1, "cookies.onChanged 触发的登录态也送到了");

// 5. 升级扩展不许把用户关掉的自动同步重新打开
await listeners["onInstalled"]({ reason: "install" });
assert(store.auto === true, "首次安装默认打开自动同步");
store.auto = false;
await listeners["onInstalled"]({ reason: "update" });
assert(store.auto === false, "升级时保留用户手动关掉的选择");

console.log("ALL PASS");

/* 用 mock 的 chrome API 真跑一遍扩展的 syncAll()，打到本机正在运行的下载器。
 * 不用真装扩展就能验证「扩展 → 本机服务」整条链路。
 *   node tests/test_extension_sync.mjs
 */
import fs from "node:fs";
import vm from "node:vm";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const SRC = fs.readFileSync(
  path.join(ROOT, "browser-extension", "background.js"), "utf-8");

const fakeCookies = {
  "suno.com": [{
    name: "__session", value: "fake_ext_e2e", domain: "suno.com",
    path: "/", secure: true, httpOnly: true, expirationDate: 1999999999
  }],
  "bilibili.com": [{
    name: "SESSDATA", value: "fake_ext_e2e_bili", domain: "bilibili.com",
    path: "/", secure: true, httpOnly: true, expirationDate: 1999999999
  }]
};

const store = {};
const noopListener = { addListener() {} };
const chrome = {
  cookies: { getAll: async ({ domain }) => fakeCookies[domain] || [] },
  storage: { local: {
    get: async (keys) => {
      const out = {};
      for (const k of (Array.isArray(keys) ? keys : [keys])) out[k] = store[k];
      return out;
    },
    set: async (o) => Object.assign(store, o)
  } },
  runtime: {
    onInstalled: noopListener, onStartup: noopListener, onMessage: noopListener
  },
  alarms: { create() {}, clear() {}, onAlarm: noopListener }
};

const ctx = vm.createContext({
  chrome, fetch, AbortController, setTimeout, clearTimeout,
  console, Date, JSON, Array, String, Object
});
vm.runInContext(SRC, ctx, { filename: "background.js" });

const out = await vm.runInContext("syncAll()", ctx);
console.log("同步结果:", JSON.stringify(out));
if (!out.ok) {
  console.error("✗ 扩展没同步成功：" + (out.msg || ""));
  process.exit(1);
}
if (!out.results || out.results.length < 2) {
  console.error("✗ 只同步了少于 2 个平台，端口探测或 collect 有问题");
  process.exit(1);
}
console.log("✓ 扩展 JS → 本机服务 全链路通过（含 HttpOnly cookie）");

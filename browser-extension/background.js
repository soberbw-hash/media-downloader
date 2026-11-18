/* 音视频下载器 · 登录态同步
 *
 * 跑在你自己日常的浏览器里，所以 chrome.cookies 读到的就是你已登录的账号
 * （HttpOnly 也能读，这是网页里的 JS 做不到的）。只往本机 127.0.0.1 发，
 * 不上传、不落盘到别处。服务端口是 8765~8785 里实际在跑的那个。
 */

const EXT_VERSION = "1.1.0";

const PLATFORMS = [
  { id: "xiaohongshu",   domains: ["xiaohongshu.com"] },
  { id: "instagram",     domains: ["instagram.com"] },
  { id: "douyin",        domains: ["douyin.com"] },
  { id: "kuaishou",      domains: ["kuaishou.com"] },
  { id: "bilibili",      domains: ["bilibili.com"] },
  { id: "weibo",         domains: ["weibo.com", "weibo.cn"] },
  { id: "music_netease", domains: ["music.163.com"] },
  { id: "music_qqmusic", domains: ["qq.com"] },
  { id: "suno",          domains: ["suno.com", "suno.ai"] },
  /* 网盘：cookie 名各站不同，但都很长一串；只在本机存，用来取分享直链 */
  { id: "baidu",         domains: ["baidu.com"] },
  { id: "quark",         domains: ["quark.cn"] },
  { id: "uc",            domains: ["uc.cn"] },
  { id: "xunlei",        domains: ["xunlei.com"] },
  { id: "123pan",        domains: ["123pan.com", "123865.com", "123684.com", "123pan.cn"] },
  { id: "pan115",        domains: ["115.com", "115cdn.com"] },
  { id: "c139",          domains: ["139.com"] },
  { id: "guangya",       domains: ["guangyapan.com", "guangyapan.cn"] }
];

const PORT_MIN = 8765;
const PORT_MAX = 8785;
const ALARM = "vdl-sync";
const MAX_PER_SITE = 400;

async function probe(port) {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), 500);
  try {
    const r = await fetch(`http://127.0.0.1:${port}/api/login/state`, {
      signal: ctl.signal, cache: "no-store"
    });
    if (!r.ok) return false;
    const d = await r.json();
    return !!(d && Array.isArray(d.sites) && d.sites.length);
  } catch (e) {
    return false;
  } finally {
    clearTimeout(timer);
  }
}

async function findPort() {
  for (let p = PORT_MIN; p <= PORT_MAX; p++) {
    if (await probe(p)) return p;
  }
  return 0;
}

async function collect() {
  const items = [];
  for (const s of PLATFORMS) {
    const seen = new Set();
    const list = [];
    for (const dom of s.domains) {
      // 按域取一次、再按站点 URL 取一次：百度这类登录态挂在 .baidu.com 上，
      // 单靠 domain 查询在个别版本上会漏，两种查法合起来最稳
      for (const q of [{ domain: dom }, { url: `https://${dom}/` }]) {
        try {
          const cs = await chrome.cookies.getAll(q);
          for (const c of cs || []) {
            const key = c.name + "|" + c.domain + "|" + c.path;
            if (seen.has(key)) continue;
            seen.add(key);
            list.push({
              name: c.name,
              value: c.value,
              domain: c.domain,
              path: c.path,
              secure: !!c.secure,
              expirationDate: c.expirationDate || 0
            });
          }
        } catch (e) {
          // 某个站点没授权 / 没登录，跳过就行，别让整批失败
        }
      }
    }
    if (!list.length) continue;
    items.push({ platform: s.id, cookies: list.slice(0, MAX_PER_SITE) });
  }
  return items;
}

async function syncAll() {
  const port = await findPort();
  if (!port) {
    const out = { ok: false, msg: "没找到正在运行的下载器，先把它打开", results: [] };
    await chrome.storage.local.set({ lastSync: Date.now(), lastMsg: out.msg });
    return out;
  }
  const items = await collect();
  let out;
  try {
    const r = await fetch(`http://127.0.0.1:${port}/api/login/extension`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-VDL-Ext": "1" },
      body: JSON.stringify({
        ext_version: EXT_VERSION,
        platforms: PLATFORMS.map(p => p.id),
        items
      })
    });
    out = await r.json();
  } catch (e) {
    out = { ok: false, msg: "连不上下载器：" + String(e), results: [] };
  }
  await chrome.storage.local.set({
    lastSync: Date.now(),
    lastMsg: out.msg || (out.ok ? "同步完成" : "同步失败")
  });
  return out;
}

function schedule() {
  chrome.alarms.create(ALARM, { delayInMinutes: 1, periodInMinutes: 15 });
}
function unschedule() {
  chrome.alarms.clear(ALARM);
}

/* 默认就开着自动同步：装上即生效，不用先点一次图标。
   用户明确关掉过才关（存的是 false）。 */
async function autoOn() {
  const d = await chrome.storage.local.get("auto");
  return d.auto !== false;
}

chrome.runtime.onInstalled.addListener(async () => {
  // 只在从没设过时才给默认值，升级时不覆盖用户手动关掉的选择
  const d = await chrome.storage.local.get("auto");
  if (d.auto === undefined) await chrome.storage.local.set({ auto: true });
  schedule();
  syncAll();                    // 装完立刻送一次登录态
});
chrome.runtime.onStartup.addListener(async () => {
  if (await autoOn()) {
    schedule();
    syncAll();
  }
});
chrome.alarms.onAlarm.addListener(a => {
  if (a.name === ALARM) syncAll();
});

/* 登录完就自动送过来：不用用户回来点「同步」，也不用等 15 分钟的定时器。
   登录成功的那一刻 cookie 会变，站点页面加载完也会触发；合并 4 秒内的抖动，
   两次同步至少隔 15 秒，免得频繁扫 cookie——但这波变化不能丢，到点补跑。 */
let syncTimer = null;
let lastSyncAt = 0;

function isWatched(domain) {
  const d = (domain || "").toLowerCase().replace(/^\./, "");
  return PLATFORMS.some(p => p.domains.some(x => d === x || d.endsWith("." + x)));
}

function queueSync() {
  if (syncTimer) clearTimeout(syncTimer);
  syncTimer = setTimeout(runSync, 4000);
}

async function runSync() {
  syncTimer = null;
  if (!(await autoOn())) return;
  const wait = lastSyncAt ? (15000 - (Date.now() - lastSyncAt)) : 0;
  if (wait > 0) {
    // 刚同步过没多久：到点再补一次，别把这波 cookie 变化吞掉
    syncTimer = setTimeout(runSync, wait);
    return;
  }
  lastSyncAt = Date.now();
  await syncAll();
}

chrome.cookies.onChanged.addListener(info => {
  if (info && info.cookie && isWatched(info.cookie.domain)) queueSync();
});
chrome.tabs.onUpdated.addListener((tabId, change, tab) => {
  if (change.status !== "complete" || !tab || !tab.url) return;
  try {
    if (isWatched(new URL(tab.url).hostname)) queueSync();
  } catch (e) { /* 不是网页地址，忽略 */ }
});

chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  if (msg && msg.type === "sync") {
    syncAll().then(reply).catch(e => reply({ ok: false, msg: String(e) }));
    return true;                    // 保持异步通道
  }
  if (msg && msg.type === "auto") {
    if (msg.on) schedule(); else unschedule();
    chrome.storage.local.set({ auto: !!msg.on });
    reply({ ok: true });
  }
});

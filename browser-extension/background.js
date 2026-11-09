/* 音视频下载器 · 登录态同步
 *
 * 跑在你自己日常的浏览器里，所以 chrome.cookies 读到的就是你已登录的账号
 * （HttpOnly 也能读，这是网页里的 JS 做不到的）。只往本机 127.0.0.1 发，
 * 不上传、不落盘到别处。服务端口是 8765~8785 里实际在跑的那个。
 */

const PLATFORMS = [
  { id: "xiaohongshu",   domain: "xiaohongshu.com" },
  { id: "instagram",     domain: "instagram.com" },
  { id: "douyin",        domain: "douyin.com" },
  { id: "kuaishou",      domain: "kuaishou.com" },
  { id: "bilibili",      domain: "bilibili.com" },
  { id: "weibo",         domain: "weibo.com" },
  { id: "music_netease", domain: "music.163.com" },
  { id: "music_qqmusic", domain: "qq.com" },
  { id: "suno",          domain: "suno.com" }
];

const PORT_MIN = 8765;
const PORT_MAX = 8785;
const ALARM = "vdl-sync";
const MAX_PER_SITE = 300;

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
    try {
      const cs = await chrome.cookies.getAll({ domain: s.domain });
      if (!cs || !cs.length) continue;
      items.push({
        platform: s.id,
        cookies: cs.slice(0, MAX_PER_SITE).map(c => ({
          name: c.name,
          value: c.value,
          domain: c.domain,
          path: c.path,
          secure: !!c.secure,
          expirationDate: c.expirationDate || 0
        }))
      });
    } catch (e) {
      // 某个站点没授权 / 没登录，跳过就行，别让整批失败
    }
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
  if (!items.length) {
    const out = { ok: false, msg: "浏览器里还没读到任何站点 cookie，先去网站登一次", results: [] };
    await chrome.storage.local.set({ lastSync: Date.now(), lastMsg: out.msg });
    return out;
  }
  let out;
  try {
    const r = await fetch(`http://127.0.0.1:${port}/api/login/extension`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-VDL-Ext": "1" },
      body: JSON.stringify({ items })
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
  await chrome.storage.local.set({ auto: true });
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

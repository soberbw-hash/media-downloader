const $ = (s) => document.querySelector(s);

/* ---------- 平台识别（按域名精确匹配，和后端一致） ---------- */
const HOSTS = [
  [["douyin.com", "iesdouyin.com"], "抖音"],
  [["bilibili.com", "b23.tv"], "B站"],
  [["xiaohongshu.com", "xhslink.com", "xhslink.cn"], "小红书"],
  [["kuaishou.com", "gif.kuaishou.com", "kwai.com", "chenzhongtech.com"], "快手"],
  [["weibo.com", "weibo.cn", "t.cn"], "微博"],
  [["ixigua.com"], "西瓜视频"],
  [["acfun.cn"], "AcFun"],
  [["xinpianchang.com"], "新片场"],
  [["tiktok.com"], "TikTok"],
  [["instagram.com", "instagr.am"], "Instagram"],
  [["youtube.com", "youtu.be"], "YouTube"],
  [["twitter.com", "x.com", "t.co"], "X"],
  [["vimeo.com"], "Vimeo"],
  [["twitch.tv"], "Twitch"],
  [["reddit.com", "redditmedia.com", "redd.it"], "Reddit"],
  [["channels.weixin.qq.com", "weixin.qq.com"], "视频号"],
  // 音乐：和视频同一个框，识别出来直接走音乐通道
  [["music.163.com"], "网易云"],
  [["y.qq.com", "i.y.qq.com", "c.y.qq.com"], "QQ音乐"],
  [["kugou.com", "m.kugou.com"], "酷狗音乐"],
  [["suno.com", "suno.ai"], "Suno"],
];
const ALL_HOSTS = HOSTS.flatMap((h) => h[0]).sort((a, b) => b.length - a.length);

/* 只吃 ASCII，避免把紧随其后的中文一起吞进来 */
const URL_RE = /https?:\/\/[A-Za-z0-9\-._~:/?#[\]@!$&'()*+,;=%]+/gi;
const BARE_RE = new RegExp(
  "(?<![A-Za-z0-9@._%/-])((?:[A-Za-z0-9-]+\\.)*(?:" +
  ALL_HOSTS.map((h) => h.replace(/\./g, "\\.")).join("|") +
  ")(?::\\d+)?(?:/[A-Za-z0-9\\-._~:/?#[\\]@!$&'()*+,;=%]*)?)",
  "gi"
);

let cfg = {};
let autoPaste = localStorage.getItem("autoPaste") !== "0";
let supportRendered = false;
let multiBatch = false;

function toast(msg, ms = 2400) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("on");
  clearTimeout(t._h);
  t._h = setTimeout(() => t.classList.remove("on"), ms);
}

function cleanUrl(u) {
  u = u.trim().replace(/[.,;:!?)\]}'"<>，。、；：！？）】》」』…–—]+$/, "");
  if (!/^https?:\/\//i.test(u)) u = "https://" + u;
  return u;
}

function urlsIn(text) {
  text = text || "";
  const spans = [];
  const hits = [];
  let m;
  URL_RE.lastIndex = 0;
  while ((m = URL_RE.exec(text))) {
    spans.push([m.index, m.index + m[0].length]);
    hits.push([m.index, cleanUrl(m[0])]);
  }
  BARE_RE.lastIndex = 0;
  while ((m = BARE_RE.exec(text))) {
    const at = m.index + (m[0].length - m[1].length);
    if (spans.some(([a, b]) => at >= a && at < b)) continue;
    hits.push([at, cleanUrl(m[1])]);
  }
  hits.sort((a, b) => a[0] - b[0]);
  const out = [];
  hits.forEach(([, u]) => { if (!out.includes(u)) out.push(u); });
  return out;
}

function platformOf(url) {
  let h = "";
  try { h = new URL(url).hostname.toLowerCase(); } catch (e) { return null; }
  for (const [list, name] of HOSTS) {
    for (const d of list) if (h === d || h.endsWith("." + d)) return name;
  }
  // 域名不认识不代表下不了：m3u8 / mp4 这种直链单独一条路，
  // 后端也是同一个判断（core.detect_platform → hls.looks_like）
  let path = "";
  try { const u = new URL(url); path = (u.pathname + u.search).toLowerCase(); } catch (e) {}
  if (/\.(m3u8|m3u|mpd|mp4|m4v|mov|mkv|webm|flv|ts|m4s|m4a|mp3|aac|flac|wav|ogg|opus)(\?|#|$)/.test(path)
      || /(^|[?&])(format=m3u|format=mpd)/.test(path)) return "直链 / HLS";
  return null;
}

// 音乐链接再细分一下：歌单会展开成几十首，贴之前就该知道
function musicKind(url) {
  let x;
  try { x = new URL(url); } catch (e) { return null; }
  const h = x.hostname.toLowerCase();
  if (!/(^|\.)music\.163\.com$|(^|\.)y\.qq\.com$|(^|\.)kugou\.com$/.test(h)) return null;
  const s = (x.pathname + x.search + x.hash).toLowerCase();
  if (/playlist|playsquare|albumdetail|\/album|disstid/.test(s)) return "歌单";
  return "单曲";
}

function renderDetected(urls) {
  const box = $("#detected");
  box.innerHTML = "";
  if (!urls || !urls.length) {
    const el = document.createElement("span");
    el.className = "ghost";
    const typed = ((typeof input !== "undefined" && input.value) || "").trim();
    el.textContent = typed
      ? "没有链接，回车按歌名搜"
      : (window.matchMedia("(hover: none)").matches
        ? "粘贴链接即可"
        : (autoPaste ? "Ctrl+V 粘贴" : "Ctrl+V 粘贴后点开始"));
    box.appendChild(el);
    return;
  }
  const count = {};
  urls.forEach((u) => {
    const mk = musicKind(u);
    const n = mk ? `${platformOf(u) || "音乐"} · ${mk}` : (platformOf(u) || "未知");
    count[n] = (count[n] || 0) + 1;
  });
  Object.keys(count).forEach((n) => {
    const el = document.createElement("span");
    el.className = n === "未知" ? "pill dim" : "pill";
    el.textContent = count[n] > 1 ? `${n} ×${count[n]}` : n;
    box.appendChild(el);
  });
  const el = document.createElement("span");
  el.className = "pill total";
  el.textContent = `共 ${urls.length} 条`;
  box.appendChild(el);
}

function fmtSize(b) {
  if (!b) return "";
  if (b > 1048576) return (b / 1048576).toFixed(1) + " MB";
  return (b / 1024).toFixed(0) + " KB";
}

function fmtDur(s) {
  if (!s) return "";
  const m = Math.floor(s / 60);
  const r = s % 60;
  return m ? `${m}分${r}秒` : `${r}秒`;
}

function esc(s) {
  return String(s || "").replace(/[&<>"]/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]
  ));
}

const STATUS_TEXT = {
  queued: "排队中",
  running: "下载中",
  done: "已完成",
  error: "失败",
  canceled: "已取消",
};

const ICON_ALIAS = {
  weixin: "wechatchannels",
  music_netease: "netease",
  music_qqmusic: "qqmusic",
};

/* 平台图标：品牌色矢量图形，加载失败也不影响布局 */
function iconHTML(pid) {
  const key = ICON_ALIAS[pid] || pid || "unknown";
  const ic = (typeof ICONS !== "undefined" && (ICONS[key] || ICONS.unknown)) || null;
  if (!ic) return `<span class="fav"><span class="sr">${esc(pid || "")}</span></span>`;
  return `<span class="fav" style="color:${ic.c}">`
    + `<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">${ic.svg}</svg></span>`;
}

function makeCard(t) {
  const el = document.createElement("div");
  el.className = "task";
  el.dataset.id = t.id;
  el.innerHTML = `
    <div class="thumb">${iconHTML(t.platform)}</div>
    <div class="body">
      <div class="row1">
        <span class="ptag">${esc(t.platform_name)}</span>
        <span class="title"></span>
      </div>
      <div class="meta"></div>
      <div class="bar"><i></i></div>
      <div class="status"><span class="txt"></span></div>
    </div>
    <div class="acts">
      <button class="open" title="打开所在文件夹">打开</button>
      <button class="retry" title="重试">重试</button>
      <button class="cancel" title="取消">取消</button>
    </div>
    <div class="gallery"></div>`;
  el.querySelector(".open").onclick = () => post("/api/open", { id: t.id }).then((r) => {
    if (!r.ok) toast(r.error || "文件还没下载好");
  });
  el.querySelector(".retry").onclick = () => post("/api/retry", { id: t.id });
  el.querySelector(".cancel").onclick = () => post("/api/cancel", { id: t.id });
  return el;
}

/* 后端有些错误是「人话（原始异常）」的格式，卡片上只留人话，原始信息塞进 tooltip */
function stripParen(s) {
  const m = (s || "").match(/^(.*)（([\s\S]+)）$/);
  if (m && m[1].trim().length >= 6) return [m[1].trim(), m[2].trim()];
  return [(s || "").trim(), ""];
}

/* 失败时往往没解析出标题，用「域名/path」顶替，总比两个字「失败」有信息 */
function shortUrl(u) {
  try {
    const x = new URL(u);
    if (x.hostname.indexOf("127.0.0.1") === 0) return u;
    let p = x.pathname;
    try { p = decodeURIComponent(p); } catch (e) {}
    p = p.replace(/\/+$/, "");
    if (p.length > 34) p = p.slice(0, 34) + "…";
    return x.hostname.replace(/^www\./, "") + p;
  } catch (e) {
    return u || "";
  }
}

function updateCard(el, t) {
  el.hidden = !passFilter(t);
  el.classList.toggle("done", t.status === "done");
  el.classList.toggle("running", t.status === "running" || t.status === "queued");
  el.classList.toggle("canceled", t.status === "canceled");
  el.classList.toggle("error", t.status === "error");

  const title = el.querySelector(".title");
  const text = t.title || (t.status === "error" ? shortUrl(t.url) : (t.stage || t.url));
  if (title.textContent !== text) title.textContent = text;
  title.title = t.status === "done" ? "点开所在文件夹" : (t.title || t.url);
  title.onclick = t.status === "done"
    ? () => post("/api/open", { id: t.id }).then((r) => {
        if (!r.ok) toast(r.error || "文件还没下载好");
      })
    : null;

  const meta = [];
  if (t.batch && multiBatch) meta.push(`第 ${t.batch} 批`);
  if (t.author) meta.push(t.author);
  if (t.duration) meta.push(fmtDur(t.duration));
  if (t.kind === "images" && t.count) meta.push(`图集 ${t.count} 张`);
  // 标题本身就是「域名/path」时，下面再写一遍域名纯属重复
  if (t.url && t.title) {
    try { meta.push(new URL(t.url).hostname.replace(/^www\./, "")); } catch (e) {}
  }
  const mt = meta.join(" · ");
  const metaEl = el.querySelector(".meta");
  if (metaEl.textContent !== mt) metaEl.textContent = mt;

  const bar = el.querySelector(".bar i");
  const pct = t.status === "error" ? 100 : t.percent || 0;
  bar.style.width = pct + "%";
  if (t.status === "done") bar.style.width = "100%";

  const st = el.querySelector(".status");
  let line = "";
  if (t.status === "error") {
    // 后端已经翻成人话的（err_title/err_tip）优先，认不出来的才剥括号、显示原始异常
    let head = t.err_title || "";
    let detail = t.error || "";
    const tip = t.err_tip || "";
    if (!head) {
      const cut = stripParen(detail);
      head = cut[0] || "失败";
      detail = cut[1] || "";
    }
    const full = (tip ? "建议：" + tip + "\n\n" : "")
      + (detail ? (detail === head ? "" : "详情：" + detail) : "");
    line = `<span class="err-msg" title="${esc(full || head)}">${esc(head)}`
      + (tip ? `<em>${esc(tip)}</em>` : "") + "</span>";
  } else if (t.status === "canceled") {
    line = "已取消";
  } else if (t.status === "done") {
    line = `已完成${t.size ? " · " + fmtSize(t.size) : ""}`;
  } else {
    line = `${t.stage || STATUS_TEXT[t.status]}${t.percent ? " " + t.percent.toFixed(1) + "%" : ""}`;
    if (t.speed) line += ` · ${t.speed.toFixed(1)} MB/s`;
    if (t.size) line += ` · ${fmtSize(t.size)}`;
  }
  if (st.innerHTML !== line) st.innerHTML = line;

  el.querySelector(".retry").style.display =
    t.status === "error" || t.status === "canceled" ? "" : "none";
  el.querySelector(".cancel").style.display =
    t.status === "queued" || t.status === "running" ? "" : "none";
  el.querySelector(".open").style.display = t.status === "done" ? "" : "none";

  const fav = el.querySelector(".fav");
  // 图集没有封面，就拿第一张当缩略图
  const cover = t.thumb ||
    (t.kind === "images" && t.count ? "/api/img?t=" + encodeURIComponent(t.id) + "&i=0" : "");
  if (cover) {
    const th = el.querySelector(".thumb");
    if (th.dataset.src !== cover) {
      th.dataset.src = cover;
      const img = new Image();
      img.onload = () => {
        th.style.backgroundImage = `url("${cover}")`;
        if (fav) fav.style.display = "none";   // 有真实封面就盖住图标
      };
      img.src = cover;
    }
  }

  updateGallery(el, t);
}

/* ---------- 图集：缩略图网格 / 勾选 / 打包 ---------- */
const selMap = new Map();     // taskId -> Set(index)

function getSel(id) {
  let s = selMap.get(id);
  if (!s) { s = new Set(); selMap.set(id, s); }
  return s;
}

function updateGallery(el, t) {
  const box = el.querySelector(".gallery");
  if (!box) return;
  const imgs = (t.kind === "images" && t.images) ? t.images : [];
  if (!imgs.length) {
    if (box.dataset.sig) {
      box.dataset.sig = "";
      box.innerHTML = "";
      box.classList.remove("on");
    }
    return;
  }
  const sig = t.id + "|" + imgs.length;
  if (box.dataset.sig !== sig) {
    box.dataset.sig = sig;
    buildGallery(el, box, t, imgs);
    box.classList.add("on");
  }
  syncSel(el, t, imgs.length);
}

function buildGallery(el, box, t, imgs) {
  box.innerHTML = "";
  const grid = document.createElement("div");
  grid.className = "grid";

  imgs.forEach((im, i) => {
    const cell = document.createElement("div");
    cell.className = "cell";
    cell.dataset.i = i;

    const img = document.createElement("img");
    img.loading = "lazy";
    img.alt = "";
    img.src = "/api/img?t=" + encodeURIComponent(t.id) + "&i=" + i;

    const tick = document.createElement("span");
    tick.className = "tick";

    const save = document.createElement("button");
    save.className = "cell-save";
    save.title = "保存这一张到下载目录";
    save.textContent = "↓";
    save.onclick = (ev) => {
      ev.stopPropagation();
      save.textContent = "…";
      post("/api/img/save", { id: t.id, i }).then((r) => {
        save.textContent = "↓";
        toast(r.ok ? "已保存 " + r.path.split(/[\\/]/).pop() : (r.error || "保存失败"));
      }).catch(() => { save.textContent = "↓"; });
    };

    cell.onclick = () => {
      const s = getSel(t.id);
      if (s.has(i)) s.delete(i); else s.add(i);
      syncSel(el, t, imgs.length);
    };

    cell.appendChild(img);
    cell.appendChild(tick);
    cell.appendChild(save);
    grid.appendChild(cell);
  });
  box.appendChild(grid);

  const tools = document.createElement("div");
  tools.className = "gtools";
  tools.innerHTML =
    '<button class="g-all">全选</button>' +
    '<button class="g-inv">反选</button>' +
    '<button class="g-zip">打包选中</button>' +
    '<button class="g-zipall">全部 ZIP</button>' +
    '<span class="g-tip"></span>';
  box.appendChild(tools);

  tools.querySelector(".g-all").onclick = () => {
    const s = getSel(t.id);
    imgs.forEach((_, i) => s.add(i));
    syncSel(el, t, imgs.length);
  };

  tools.querySelector(".g-inv").onclick = () => {
    const old = getSel(t.id);
    const s = new Set();
    imgs.forEach((_, i) => { if (!old.has(i)) s.add(i); });
    selMap.set(t.id, s);
    syncSel(el, t, imgs.length);
  };

  const doZip = async (btn, items, label) => {
    btn.disabled = true;
    btn.textContent = "打包中…";
    try {
      const r = await post("/api/zip", { id: t.id, items });
      if (r.ok) {
        toast("已打包 " + r.count + " 张：" + r.path.split(/[\\/]/).pop());
      } else {
        toast(r.error || "打包失败");
      }
    } catch (e) {
      toast("打包失败");
    }
    btn.disabled = false;
    btn.textContent = label;
  };

  tools.querySelector(".g-zip").onclick = (ev) => {
    const s = getSel(t.id);
    if (!s.size) { toast("先点几张图选中，再打包"); return; }
    doZip(ev.target, Array.from(s).sort((a, b) => a - b), "打包选中");
  };

  tools.querySelector(".g-zipall").onclick = (ev) => {
    doZip(ev.target, [], "全部 ZIP");
  };
}

function syncSel(el, t, n) {
  const s = getSel(t.id);
  el.querySelectorAll(".cell").forEach((c) => {
    c.classList.toggle("sel", s.has(Number(c.dataset.i)));
  });
  const zip = el.querySelector(".g-zip");
  if (zip) {
    zip.textContent = s.size ? "打包选中 (" + s.size + ")" : "打包选中";
    zip.classList.toggle("primary", s.size > 0);
  }
  const tip = el.querySelector(".g-tip");
  if (tip) tip.textContent = n + " 张 · 点图勾选，↓ 存单张";
}

/* ---------- 任务筛选：全部 / 进行中 / 失败 / 已完成 ---------- */
let taskFilter = localStorage.getItem("taskFilter") || "all";

function passFilter(t) {
  if (taskFilter === "all") return true;
  if (taskFilter === "live") return t.status === "queued" || t.status === "running";
  return t.status === taskFilter;
}

function setFilter(f) {
  taskFilter = f;
  localStorage.setItem("taskFilter", f);
  document.querySelectorAll("#filters .flt").forEach((b) => {
    b.classList.toggle("on", b.dataset.f === f);
  });
  refreshLast();
}

function renderStats(tasks) {
  const c = { all: tasks.length, live: 0, done: 0, error: 0 };
  tasks.forEach((t) => {
    if (t.status === "queued" || t.status === "running") c.live++;
    else if (c[t.status] !== undefined) c[t.status]++;
  });
  const live = c.live;
  const ca = $("#cancelAll");
  const cb = $("#clearBtn");
  const lhRight = document.querySelector(".list-head .lh-right");
  const filters = $("#filters");
  if (lhRight) lhRight.style.display = tasks.length ? "" : "none";
  if (filters) filters.hidden = !tasks.length;
  if (ca) ca.disabled = live === 0;
  if (cb) cb.disabled = c.done + tasks.filter((t) => t.status === "canceled").length === 0;
  const rf = $("#retryFailed");
  if (rf) {
    rf.hidden = c.error === 0;
    rf.textContent = `重试失败 ${c.error}`;
  }

  document.querySelectorAll("#filters .flt").forEach((b) => {
    const n = c[b.dataset.f] || 0;
    b.dataset.n = String(n);
    b.classList.toggle("zero", n === 0);
  });

  syncTitle(tasks, c);
}

/* ---------- 窗口标题：窗口最小化时，这是唯一能看到的进度 ---------- */
let liveBefore = 0;

function syncTitle(tasks, c) {
  let t = "音视频下载器";
  if (c.live > 0) {
    t = `↓ ${c.done}/${c.done + c.live} · 音视频下载器`;
  } else if (c.error > 0) {
    t = `⚠ ${c.error} 项失败 · 音视频下载器`;
  } else if (liveBefore > 0 && c.done) {
    // 刚刚全部跑完，闪一下「完成」，免得用户一直盯着等
    t = `✓ ${c.done} 项已完成`;
    clearTimeout(syncTitle._t);
    syncTitle._t = setTimeout(() => { document.title = "音视频下载器"; }, 5000);
  }
  liveBefore = c.live;
  if (document.title !== t) document.title = t;
}

let lastTasks = [];

function render(tasks) {
  lastTasks = tasks;
  const box = $("#tasks");
  renderStats(tasks);
  const bs = new Set(tasks.map((t) => t.batch).filter(Boolean));
  multiBatch = bs.size > 1;      // 只有一批时不显示「第 N 批」

  if (!tasks.length) {
    const blk = $("#tasksBlock");
    if (blk) blk.hidden = true;      // 没任务就整块收起来，不留空白也不带出滚动条
    if (box) box.innerHTML = "";
    selMap.clear();
    return;
  }
  const blk = $("#tasksBlock");
  if (blk) blk.hidden = false;

  const ids = new Set(tasks.map((t) => t.id));
  box.querySelectorAll(".task").forEach((el) => {
    if (!ids.has(el.dataset.id)) el.remove();
  });
  for (const k of selMap.keys()) if (!ids.has(k)) selMap.delete(k);

  tasks.forEach((t, i) => {
    let el = box.querySelector(`.task[data-id="${t.id}"]`);
    if (!el) {
      el = makeCard(t);
      const ref = box.children[i];
      if (ref) box.insertBefore(el, ref);
      else box.appendChild(el);
    }
    updateCard(el, t);
  });

  // 筛到一条都不剩时，给个提示，免得看起来像卡了
  const shown = tasks.filter(passFilter).length;
  if (!shown) {
    let tip = box.querySelector(".filtered-out");
    if (!tip) {
      tip = document.createElement("div");
      tip.className = "empty filtered-out";
      tip.innerHTML = '<span class="e-main">这个分类下没有任务</span>'
        + '<span class="e-sub">点「全部」看所有任务</span>';
      box.appendChild(tip);
    }
  } else {
    const tip = box.querySelector(".filtered-out");
    if (tip) tip.remove();
  }
}

function refreshLast() { if (lastTasks.length) render(lastTasks); }

document.querySelectorAll("#filters .flt").forEach((b) => {
  b.addEventListener("click", () => setFilter(b.dataset.f));
});

if ($("#retryFailed")) {
  $("#retryFailed").onclick = async () => {
    const btn = $("#retryFailed");
    btn.disabled = true;
    try {
      const r = await post("/api/retry_failed", {});
      toast(r.count ? `已重新排队 ${r.count} 项` : "没有失败的");
      poll();
    } catch (e) {
      toast("连接失败");
    } finally {
      btn.disabled = false;
    }
  };
}

async function post(url, body) {
  const r = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  return r.json();
}

let pollGap = 2500;

async function poll() {
  try {
    const r = await fetch("/api/state");
    const d = await r.json();
    const tasks = d.tasks || [];
    applyMode(d.mode);
    render(tasks);
    cfg = d.config || {};
    renderSupport(d.platforms || []);
    // 有活在跑就勤快点，全闲着没必要每 0.8 秒问一次
    const busy = tasks.some((t) => t.status === "running" || t.status === "queued");
    pollGap = busy ? 800 : 2500;
  } catch (e) {
    // 服务已关闭
    pollGap = 2500;
  }
}

/* ---------- 独立窗口模式：关窗即退出软件 ---------- */
let modeDone = false;
function applyMode(mode) {
  if (modeDone) return;
  modeDone = true;
  if (mode !== "app") return;
  document.body.classList.add("app-mode");
  // 右上角不再放「退出」——关窗本来就会退出，那个按钮只是"没退干净"时的兜底，
  // 放到设置抽屉最下面，不占地方
  const g = $("#quitGrp");
  if (g) g.hidden = false;
  // 告诉服务端"页面已经加载好了"，用于区分刷新和关窗
  post("/api/alive", {}).catch(() => {});
  // 关掉窗口 -> 服务一起退出
  window.addEventListener("pagehide", () => {
    try { navigator.sendBeacon("/api/quit"); } catch (e) {}
  });
}

function renderSupport(list) {
  if (supportRendered) return;
  supportRendered = true;
  const box = $("#support");
  if (!box) return;
  box.innerHTML = "";
  list.forEach((p) => {
    const el = document.createElement("span");
    el.className = "tag";
    if (p.level === "needs-api") {
      el.className = "tag warn";
      el.title = "要填解析接口，或直接粘视频直链";
    } else if (p.level === "cookie") {
      el.className = "tag warn";
      el.title = "需要登录 cookies 文件";
    } else if (p.level === "experimental") {
      el.className = "tag warn";
      el.title = "实验性支持，部分链接可能失败";
    } else if (p.level === "unsupported") {
      el.className = "tag no";
      el.title = "无公开接口，无法支持";
    } else {
      el.title = "开箱可用";
    }
    el.textContent = p.name;
    box.appendChild(el);
  });
}

/* ---------- 提交：一次粘贴 = 一批 ---------- */
let inflight = 0;

async function submitBlocks(blocks, localUrls) {
  await ensureDir();          // 第一次用时先问一句存哪儿
  inflight++;
  try {
    const r = await post("/api/add", { blocks });
    const added = r.added || [];
    const skipped = r.skipped || [];
    // 正在看「失败」列表时新任务加进来会看不见，先切回「全部」
    if (added.length && taskFilter !== "all") setFilter("all");
    if (added.length) {
      const no = r.batches && r.batches.length ? r.batches[0].no : 0;
      if (added.length === 1) {
        toast(`已加入第 ${no} 批 · ${added[0].platform_name}`);
      } else {
        toast(`第 ${no} 批 · 已加入 ${added.length} 条`);
      }
    }
    if (skipped.length) {
      const first = skipped[0];
      const msg = skipped.length === 1
        ? `跳过：${first.reason}`
        : `跳过 ${skipped.length} 条（${first.reason}）`;
      setTimeout(() => toast(msg, 3800), added.length ? 1000 : 0);
    }
    if (!added.length && !skipped.length) toast("没认出链接");
    poll();
  } catch (e) {
    toast("连接失败，服务可能已经关了");
  } finally {
    inflight--;
    $("#submit").disabled = inflight > 0;
  }
}

function flashComposer() {
  const c = $("#composer");
  c.classList.remove("pulse");
  void c.offsetWidth;
  c.classList.add("pulse");
  setTimeout(() => c.classList.remove("pulse"), 620);
}

async function submitBox() {
  const text = $("#input").value.trim();
  if (!text) { toast("贴个链接，或者打歌名搜歌"); return; }
  const urls = urlsIn(text);
  if (!urls.length) { toast("没找到链接，搜歌请切到「搜歌」"); return; }
  $("#input").value = "";
  $("#input").style.height = "auto";
  renderDetected([]);
  flashComposer();
  await submitBlocks([{ text }], urls);
}

/* ---------- 粘贴感知 ---------- */
const input = $("#input");
const composer = $("#composer");

input.addEventListener("paste", (e) => {
  const cd = e.clipboardData || window.clipboardData;
  const text = cd ? cd.getData("text") : "";
  if (!text) return;
  const urls = urlsIn(text);
  if (!urls.length) return;           // 不是链接：正常插入文字
  if (!autoPaste) {                   // 手动模式：先落进输入框，等用户点按钮
    setTimeout(() => { renderDetected(urlsIn(input.value)); autoGrow(); }, 30);
    return;
  }
  e.preventDefault();                 // 自动模式：不落输入框，直接成批下载
  flashComposer();
  submitBlocks([{ text }], urls);
});

input.addEventListener("input", () => {
  autoGrow();
  renderDetected(urlsIn(input.value));
});

function autoGrow() {
  input.style.height = "auto";
  input.style.height = Math.min(input.scrollHeight, 260) + "px";
}

input.addEventListener("keydown", (e) => {
  if (e.key !== "Enter" || e.isComposing) return;   // 输入法选词时的回车不算
  if (e.shiftKey) return;                            // Shift+Enter 换行
  e.preventDefault();
  submitBox();
});

/* 拖文字进来也认 */
["dragenter", "dragover"].forEach((t) => {
  composer.addEventListener(t, (e) => { e.preventDefault(); composer.classList.add("drag"); });
});
["dragleave", "drop"].forEach((t) => {
  composer.addEventListener(t, (e) => { e.preventDefault(); composer.classList.remove("drag"); });
});
composer.addEventListener("drop", (e) => {
  const text = e.dataTransfer ? e.dataTransfer.getData("text") : "";
  if (!text) return;
  const urls = urlsIn(text);
  if (!urls.length) return;
  if (autoPaste) { flashComposer(); submitBlocks([{ text }], urls); }
  else { input.value = (input.value ? input.value + "\n" : "") + text; autoGrow(); renderDetected(urlsIn(input.value)); }
});

$("#submit").onclick = submitBox;

/* 焦点在别处（比如刚点过卡片）时直接 Ctrl+V 也认 */
document.addEventListener("paste", (e) => {
  const el = e.target;
  const tag = (el && el.tagName ? el.tagName : "").toLowerCase();
  // 输入框里粘贴就正常粘贴（设置里的目录、接口地址都要能填）
  if (tag === "input" || tag === "textarea" || (el && el.isContentEditable)) return;
  const cd = e.clipboardData || window.clipboardData;
  const text = cd ? cd.getData("text") : "";
  if (!text) return;
  const urls = urlsIn(text);
  if (!urls.length) return;
  if (autoPaste) {
    e.preventDefault();
    flashComposer();
    submitBlocks([{ text }], urls);
  } else {
    input.value = (input.value ? input.value + "\n" : "") + text;
    autoGrow();
    renderDetected(urlsIn(input.value));
    input.focus();
  }
});

$("#autoPaste").addEventListener("change", (e) => {
  autoPaste = e.target.checked;
  localStorage.setItem("autoPaste", autoPaste ? "1" : "0");
  renderDetected(urlsIn(input.value));
  toast(autoPaste ? "粘贴后自动开始下载" : "粘贴后点「开始下载」");
});

/* ---------- 账号登录 ---------- */
let loginSig = "";

async function loadLogins(force) {
  try {
    const r = await fetch("/api/login/state");
    const d = await r.json();
    const sig = JSON.stringify([d.sites, d.status]);
    if (sig === loginSig && !force) return;
    loginSig = sig;
    renderLogins(d.sites || [], d.status || {});
  } catch (e) {}
}

function renderLogins(sites, status) {
  const box = $("#logins");
  if (!box || !sites || !sites.length) return;   // 还没拉到，先别把空列表画上去
  const busy = status.state === "opening" || status.state === "waiting";
  box.innerHTML = "";

  // 按「必须登 / 登了更好」分区，用户一眼就知道哪些能跳过
  const groups = [
    { t: "必须登录", sub: "不登录拿不到地址", list: sites.filter((s) => s.required) },
    { t: "可选登录", sub: "不登也能下，登了更稳", list: sites.filter((s) => !s.required) },
  ];

  groups.forEach((g) => {
    if (!g.list.length) return;
    const sec = document.createElement("section");
    sec.className = "lg-grp";
    const h = document.createElement("div");
    h.className = "lg-t";
    h.innerHTML = `${esc(g.t)}<span>${esc(g.sub)}</span>`;
    sec.appendChild(h);
    const list = document.createElement("div");
    list.className = "lg-list";
    g.list.forEach((s) => list.appendChild(loginRow(s, status, busy)));
    sec.appendChild(list);
    box.appendChild(sec);
  });

  const on = sites.filter((s) => s.logged_in).length;
  const must = sites.filter((s) => s.required).length;
  const sum = $("#acctSummary");
  if (sum) {
    sum.textContent = on
      ? `已登录 ${on} / ${sites.length} 个平台`
      : `${must} 个平台必须登录，其余可选`;
  }
  const num = $("#acctNum");
  if (num) num.innerHTML = `${on}<span>/${sites.length}</span>`;

  renderLoginBar(status, busy);
}

function loginRow(s, status, busy) {
  const row = document.createElement("div");
  row.className = "login-row";
  const running = busy && status.platform === s.id;
  const stCls = running ? "st run" : (s.logged_in ? "st ok" : "st");
  const stText = running ? "等待中" : (s.logged_in ? "已登录" : "未登录");
  row.innerHTML = iconHTML(s.id)
    + `<span class="nm">${esc(s.name)}</span>`
    + `<span class="${stCls}">${stText}</span>`
    + `<button type="button">${s.logged_in ? "重登" : "登录"}</button>`;
  row.title = s.tip || "";
  const btn = row.querySelector("button");
  btn.disabled = busy;
  btn.onclick = () => startLogin(s.id);
  return row;
}

function renderLoginBar(status, busy) {
  const bar = $("#loginBar");
  if (!bar) return;
  bar.innerHTML = "";
  bar.classList.remove("on");
  const show = (text, withCancel) => {
    bar.classList.add("on");
    const sp = document.createElement("span");
    sp.textContent = text;
    bar.appendChild(sp);
    if (withCancel) {
      const b = document.createElement("button");
      b.textContent = "取消";
      b.onclick = async () => { await post("/api/login/cancel", {}); loadLogins(true); };
      bar.appendChild(b);
    }
  };
  if (busy) {
    show(status.msg || "请在浏览器窗口里完成登录", true);
  } else if (["done", "error", "timeout", "closed"].includes(status.state)) {
    show(status.msg || "", false);
  }
}

async function startLogin(pid) {
  toast("正在打开登录窗口…");
  const r = await post("/api/login/start", { platform: pid });
  if (!r.ok) toast(r.error || "打不开登录窗口");
  loadLogins(true);
}

/* ---------- 下载目录 ---------- */
let dirPromise = null;
let dirResolve = null;

async function openPicker(btn, label) {
  if (btn) {
    btn.disabled = true;
    btn.textContent = "对话框已打开…";
  }
  let picked = "";
  try {
    const r = await post("/api/pickdir", { path: cfg.out_dir });
    if (r && r.ok && r.path) picked = r.path;
    else if (r && !r.canceled) toast(r.error || "对话框没打开，可以直接填路径");
  } catch (e) {
    toast("对话框没打开，可以直接填路径");
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = label;
    }
  }
  return picked;
}

async function saveDir(path) {
  const r = await post("/api/config", { out_dir: path });
  if (r && r.ok) { cfg = r.config; return true; }
  if (r && r.error) toast(r.error, 3600);
  return false;
}

async function ensureDir() {
  if (!cfg || !cfg.out_dir) {
    try { await poll(); } catch (e) {}   // 配置还没拉下来，先等一次
  }
  if (cfg && cfg.out_dir_set) return true;
  if (dirPromise) return dirPromise;     // 已经在问了
  $("#fdPath").textContent = (cfg && cfg.out_dir) || "默认目录";
  $("#firstDir").classList.add("on");
  dirPromise = new Promise((res) => { dirResolve = res; });
  return dirPromise;
}

function closeFirstDir() {
  $("#firstDir").classList.remove("on");
  const fn = dirResolve;
  dirResolve = null;
  dirPromise = null;
  if (fn) fn(true);
}

$("#fdDefault").onclick = async () => {
  if (!(await saveDir(cfg.out_dir))) { toast("保存失败"); return; }
  toast("下载目录：" + cfg.out_dir, 3200);
  closeFirstDir();
};

$("#fdPick").onclick = async () => {
  const p = await openPicker($("#fdPick"), "选择文件夹");
  if (!p) return;
  if (!(await saveDir(p))) { toast("保存失败"); return; }
  $("#fdPath").textContent = cfg.out_dir;
  toast("下载目录：" + cfg.out_dir, 3200);
  closeFirstDir();
};

/* ---------- 设置 ---------- */
function openDrawer() {
  closeAllDrawers();
  $("#mask").classList.add("on");
  $("#drawer").classList.add("on");
  $("#cfgOut").value = cfg.out_dir || "";
  $("#cfgQuality").value = cfg.quality || "best";
  $("#cfgProxy").value = cfg.proxy || "";
  $("#cfgCookies").value = cfg.cookies_file || "";
  $("#cfgSph").value = cfg.sph_api || "";
  $("#cfgConc").value = String(cfg.concurrency || 3);
  $("#cfgNameTpl").value = cfg.name_tpl || "";
  syncNameUI();
  $("#cfgMusicDir").value = cfg.music_dir || "";
  $("#cfgCover").checked = !!cfg.save_cover;
  $("#cfgSubs").checked = !!cfg.down_subs;
  loadLogins(true);          // 只为刷新「已登录 n/8」这行摘要
}

function openAcct() {
  closeAllDrawers();
  $("#mask").classList.add("on");
  $("#acctDrawer").classList.add("on");
  loadLogins(true);
}

function closeAllDrawers() {
  ["#drawer", "#acctDrawer", "#histDrawer"].forEach((s) => {
    const el = $(s);
    if (el) el.classList.remove("on");
  });
  $("#mask").classList.remove("on");
}

$("#openDir").onclick = async () => {
  const r = await post("/api/open", {});
  if (!r.ok) toast(r.error || "打不开");
};

$("#openSet").onclick = openDrawer;
$("#closeSet").onclick = closeAllDrawers;
$("#openAcct").onclick = openAcct;
$("#closeAcct").onclick = closeAllDrawers;
$("#mask").onclick = closeAllDrawers;

// 高级设置默认收起，记住上次展开状态
const advBox = $("#advBox");
if (advBox) {
  advBox.open = localStorage.getItem("advOpen") === "1";
  advBox.addEventListener("toggle", () => {
    localStorage.setItem("advOpen", advBox.open ? "1" : "0");
    // 展开后滚到底，三项高级设置一次看全，不用再手动拖
    if (advBox.open) {
      const body = advBox.closest(".drawer-body");
      if (body) {
        setTimeout(() => body.scrollTo({ top: body.scrollHeight, behavior: "smooth" }), 60);
      }
    }
  });
}

/* ---------- 文件命名：下拉预设 + 可点标签 ---------- */
const NAME_PRESETS = {
  "": "",
  "{date}_{platform}_{author}_{title}": "{date}_{platform}_{author}_{title}",
  "{platform}_{author}_{title}": "{platform}_{author}_{title}",
  "{author}_{title}": "{author}_{title}",
  "{date}_{title}": "{date}_{title}",
};

const TOKEN_CN = {
  title: "标题", author: "作者", platform: "平台",
  date: "日期", time: "时间", id: "ID", kind: "类型",
};

function nameExample(tpl) {
  if (!tpl) return "";
  const fake = { title: "视频标题", author: "作者", platform: "抖音",
                 date: "20260101", time: "120000", id: "1234", kind: "视频" };
  let out = tpl;
  Object.keys(fake).forEach((k) => { out = out.split("{" + k + "}").join(fake[k]); });
  return out + ".mp4";
}

function syncNameUI(fromSelect) {
  const pre = $("#cfgNamePre");
  const inp = $("#cfgNameTpl");
  const edit = $("#nameEdit");
  const hint = $("#nameHint");
  if (!pre || !inp) return;
  const v = inp.value.trim();
  if (!fromSelect) {
    // 输入框变化时同步下拉；但只要用户选过模板/自定义，编辑区就别收，
    // 不然拼到一半清个空，整个编辑器就消失了
    if (!v) {
      if (pre.value && pre.value !== "__custom__") pre.value = "__custom__";
    } else if (Object.prototype.hasOwnProperty.call(NAME_PRESETS, v)) {
      pre.value = v;
    } else {
      pre.value = "__custom__";
    }
  }
  edit.hidden = pre.value === "";
  const ex = nameExample(v);
  hint.textContent = ex
    ? "示例：" + ex
    : "留空用原标题。选「自定义」后，点标签往里加，用下划线连接。";
}

if ($("#cfgNamePre")) {
  $("#cfgNamePre").addEventListener("change", () => {
    const v = $("#cfgNamePre").value;
    const inp = $("#cfgNameTpl");
    if (v !== "__custom__") inp.value = v;   // 预设 = 直接填模板；自定义 = 保留现内容
    syncNameUI(true);
    if (v !== "") inp.focus();
  });
  $("#cfgNameTpl").addEventListener("input", syncNameUI);
  $("#nameToks").addEventListener("click", (e) => {
    const b = e.target.closest(".tok");
    if (!b) return;
    const inp = $("#cfgNameTpl");
    const token = b.dataset.t || "";
    const pos = inp.selectionStart != null ? inp.selectionStart : inp.value.length;
    const before = inp.value.slice(0, pos);
    const after = inp.value.slice(inp.selectionEnd != null ? inp.selectionEnd : pos);
    // 光标在中间且两边都有内容时，自动补个下划线连起来
    let mid = token;
    if (before && !/[_.\-]$/.test(before)) mid = "_" + mid;
    inp.value = before + mid + after;
    const caret = (before + mid).length;
    inp.focus();
    try { inp.setSelectionRange(caret, caret); } catch (e) {}
    syncNameUI();
  });
}

/* ---------- 版本与更新 ---------- */
async function checkUpdate(manual) {
  const msg = $("#updMsg");
  if (manual && msg) msg.textContent = "正在检查…";
  try {
    const r = await fetch("/api/check_update").then((x) => x.json());
    if (r.current) {
      const cv = $("#curVer");
      if (cv) cv.textContent = "v" + r.current;
    }
    if (!r.ok) {
      if (msg) msg.textContent = manual ? (r.error || "检查失败，稍后再试") : "启动时自动检查新版";
      return;
    }
    if (msg) msg.textContent = "当前版本 v" + r.current + (r.has_update ? "" : "，已是最新");
    if (r.has_update) {
      if (msg) {
        msg.innerHTML = "发现新版 v" + esc(r.latest) +
          " · <a href=\"" + esc(r.url) + "\" target=\"_blank\" rel=\"noreferrer\">去下载</a>";
      }
      toast("发现新版本 v" + r.latest + "，打开设置可更新", 3600);
    } else if (manual) {
      toast("已是最新版 v" + r.current);
    }
  } catch (e) {
    if (msg) msg.textContent = manual ? "检查失败，网络不通" : "启动时自动检查新版";
  }
}
if ($("#checkUpd")) $("#checkUpd").onclick = () => checkUpdate(true);

$("#pickOut").onclick = async () => {
  const p = await openPicker($("#pickOut"), "浏览");
  if (!p) return;
  $("#cfgOut").value = p;
  if (await saveDir(p)) toast("下载目录已改成 " + p, 3200);
};

$("#quitApp").onclick = async () => {
  const btn = $("#quitApp");
  btn.textContent = "正在退出…";
  btn.disabled = true;
  try { await post("/api/quit_now", {}); } catch (e) {}
  // 服务端会关掉，这里给个兜底提示
  setTimeout(() => {
    try { window.close(); } catch (e) {}
    setTimeout(() => {
      document.body.innerHTML =
        '<div style="display:flex;height:100vh;align-items:center;justify-content:center;' +
        'font:15px/1.7 system-ui;color:#6b7280;text-align:center">' +
        "已退出，窗口不会自己关的话，点右上角的 × 就行</div>";
    }, 300);
  }, 900);
};

$("#saveCfg").onclick = async () => {
  const r = await post("/api/config", {
    out_dir: $("#cfgOut").value.trim(),
    quality: $("#cfgQuality").value,
    proxy: $("#cfgProxy").value.trim(),
    cookies_file: $("#cfgCookies").value.trim(),
    sph_api: $("#cfgSph").value.trim(),
    concurrency: parseInt($("#cfgConc").value, 10) || 3,
    name_tpl: ($("#cfgNamePre").value === "" ? "" : $("#cfgNameTpl").value.trim()),
    music_dir: $("#cfgMusicDir").value.trim(),
    save_cover: $("#cfgCover").checked,
    down_subs: $("#cfgSubs").checked,
  });
  if (r.ok) { cfg = r.config; toast("已保存"); closeAllDrawers(); }
  else toast(r.error || "保存失败", 3600);
};

$("#clearBtn").onclick = async () => { await post("/api/clear", {}); poll(); };

$("#cancelAll").onclick = async () => {
  const r = await post("/api/cancel_all", {});
  toast(r.count ? `已取消 ${r.count} 条` : "没有正在跑的任务");
  poll();
};

const touchOnly = window.matchMedia("(hover: none)").matches;
if (window.innerWidth < 620) {
  input.setAttribute("placeholder", touchOnly ? "长按粘贴链接，自动开始下载" : "贴链接，贴完就下");
}
// 打开软件就能直接 Ctrl+V，不用先点输入框（触屏不自动聚焦，免得键盘弹出来）
if (!touchOnly) setTimeout(() => input.focus(), 80);

$("#autoPaste").checked = autoPaste;
renderDetected([]);
autoGrow();
poll();
// 窗口最小化 / 切到后台时不刷，省点 CPU；间隔跟着任务状态变（见 poll 里的 pollGap）
let pollTimer = 0;
(function schedulePoll() {
  clearTimeout(pollTimer);
  pollTimer = setTimeout(async () => {
    if (!document.hidden) await poll();
    schedulePoll();
  }, pollGap);
})();
document.addEventListener("visibilitychange", () => { if (!document.hidden) poll(); });
setFilter(taskFilter);
setTimeout(() => checkUpdate(false), 1200);   // 启动时静默检查新版
// 只有账号面板开着的时候才轮询登录态，设置面板不用每秒问
setInterval(() => {
  if ($("#acctDrawer").classList.contains("on")) loadLogins();
}, 1500);

/* ================= 链接下载 / 搜歌 切换 ================= */
let tabMode = localStorage.getItem("tabMode") === "music" ? "music" : "video";

function applyTab(m) {
  tabMode = m;
  localStorage.setItem("tabMode", m);
  document.querySelectorAll("#tabs .tab").forEach((b) => {
    const on = b.dataset.tab === m;
    b.classList.toggle("on", on);
    b.setAttribute("aria-selected", on ? "true" : "false");
  });
  const box = $("#composer");
  const mus = $("#musicPanel");
  if (box) box.hidden = m !== "video";
  if (mus) mus.hidden = m !== "music";
  if (m === "music") {
    setTimeout(() => { const q = $("#mQuery"); if (q) q.focus(); }, 60);
  } else if (!touchOnly) {
    input.focus();
  }
}

document.querySelectorAll("#tabs .tab").forEach((b) => {
  b.addEventListener("click", () => applyTab(b.dataset.tab));
});

/* ================= 搜歌：一次问三个平台 ================= */
let mPlat = localStorage.getItem("mPlat") || "all";
let mItems = [];
let mSel = new Set();
let searching = false;

const PLAT_NAME = { all: "全网", netease: "网易云", qqmusic: "QQ音乐", kugou: "酷狗" };

function applyPlat(p) {
  mPlat = p;
  localStorage.setItem("mPlat", p);
  document.querySelectorAll("#mPlats .plat").forEach((b) => {
    b.classList.toggle("on", b.dataset.p === p);
  });
}

document.querySelectorAll("#mPlats .plat").forEach((b) => {
  b.onclick = () => {
    applyPlat(b.dataset.p);
    const q = $("#mQuery").value.trim();
    if (q) musicSearch(q);      // 换平台直接重搜
  };
});
applyPlat(mPlat);

function musicRow(s, i) {
  const on = mSel.has(i);
  const cov = s.cover
    ? '<img class="cov" src="' + esc(s.cover) + '" alt="" loading="lazy" onerror="this.style.visibility=\'hidden\'">'
    : '<span class="cov" data-p="' + esc(s.platform || "") + '"></span>';
  const sub = esc(s.artist || "未知歌手") + (s.album ? " · " + esc(s.album) : "");
  const src = s.platform
    ? '<span class="psrc" data-p="' + esc(s.platform) + '">' +
      esc(PLAT_NAME[s.platform] || s.platform) + "</span>"
    : "";
  return '<div class="msong' + (on ? " on" : "") + '">' +
    '<input type="checkbox" class="mpick" data-i="' + i + '"' +
    (on ? " checked" : "") + ' aria-label="勾选这首歌">' + cov +
    '<div class="meta"><div class="t">' + esc(s.title || "未知曲目") + "</div>" +
    '<div class="a">' + src + sub + "</div></div>" +
    '<span class="d">' + (s.duration ? fmtDur(s.duration) : "") + "</span>" +
    '<button class="dl" data-i="' + i + '" title="下载" aria-label="下载这首歌">' +
    '<svg viewBox="0 0 24 24" width="13" height="13" aria-hidden="true">' +
    '<path d="M12 3v11m0 0l-4.2-4.2M12 14l4.2-4.2M5 19h14" fill="none" stroke="currentColor"' +
    ' stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>' +
    "</button></div>";
}

function syncMusicSel() {
  const total = mItems.length;
  const n = mSel.size;
  const bar = $("#mBar");
  if (bar) bar.hidden = !total;
  const all = $("#mAll");
  if (all) {
    all.checked = n > 0 && n === total;
    all.indeterminate = n > 0 && n < total;
  }
  const cnt = $("#mCount");
  if (cnt) cnt.textContent = n ? `已选 ${n} / ${total} 首` : `共 ${total} 首`;
  const b = $("#mBatch");
  if (b) {
    b.disabled = n === 0;
    b.textContent = n ? `下载所选（${n}）` : "下载所选";
  }
  // 列表超出高度才在底部做渐隐，否则最后一行会显得发虚
  const box = $("#mResults");
  if (box) box.classList.toggle("scrollable", box.scrollHeight > box.clientHeight + 2);
}

function renderMusic() {
  const box = $("#mResults");
  if (!mItems.length) {
    box.innerHTML = '<div class="mempty">没搜到，换个关键词试试</div>';
    syncMusicSel();
    return;
  }
  box.innerHTML = mItems.map(musicRow).join("");
  box.scrollTop = 0;                 // 换一批结果要从头看，别继承上次的滚动位置
  box.querySelectorAll(".dl").forEach((b) => {
    b.onclick = () => addMusic([mItems[+b.dataset.i]], b);
  });
  box.querySelectorAll(".mpick").forEach((cb) => {
    cb.onchange = () => {
      const i = +cb.dataset.i;
      if (cb.checked) mSel.add(i); else mSel.delete(i);
      cb.closest(".msong").classList.toggle("on", cb.checked);
      syncMusicSel();
    };
  });
  syncMusicSel();
}

async function musicSearch(q) {
  q = (q || $("#mQuery").value || "").trim();
  if (!q) { $("#mQuery").focus(); return; }
  $("#mResults").innerHTML = "";
  $("#mBar").hidden = true;
  $("#mHint").textContent = `${PLAT_NAME[mPlat] || "全网"} 搜索「${q}」…`;
  mSel.clear();
  searching = true;
  $("#mGo").disabled = true;
  try {
    const r = await post("/api/music/search", { platform: mPlat, q, limit: 24 });
    if (!r.ok) { $("#mHint").textContent = r.error || "搜索失败"; return; }
    mItems = r.items || [];
    renderMusic();
    // 有结果时不再叠一句「找到 N 首」，下面的「共 N 首」已经说了
    $("#mHint").textContent = mItems.length ? "" : "没搜到，换个关键词或换个平台";
  } catch (e) {
    $("#mHint").textContent = "搜索失败，服务可能已经关了";
  } finally {
    searching = false;
    $("#mGo").disabled = false;
  }
}

async function addMusic(list, btn) {
  if (!list.length) return;
  await ensureDir();
  const old = btn ? btn.textContent : "";
  if (btn) { btn.disabled = true; btn.textContent = "…"; }
  try {
    const r = await post("/api/music/add", { items: list });
    if (r.ok) {
      toast("已加入 " + r.count + " 首");
      if (taskFilter !== "all") setFilter("all");   // 否则音乐标签下看不到新任务
    } else {
      toast(r.error || "加入失败");
    }
    poll();
  } catch (e) {
    toast("连接失败");
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = old; }
  }
}

if ($("#mGo")) $("#mGo").onclick = () => musicSearch();
if ($("#mQuery")) {
  $("#mQuery").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.isComposing) { e.preventDefault(); musicSearch(); }
  });
}

if ($("#mAll")) {
  $("#mAll").onchange = () => {
    if ($("#mAll").checked) mItems.forEach((_, i) => mSel.add(i));
    else mSel.clear();
    renderMusic();
  };
}

if ($("#mBatch")) {
  $("#mBatch").onclick = async () => {
    const list = [...mSel].sort((a, b) => a - b).map((i) => mItems[i]).filter(Boolean);
    if (!list.length) return;
    await addMusic(list, $("#mBatch"));
    mSel.clear();
    renderMusic();
  };
}

/* ================= 历史 ================= */
let histItems = [];

function histRow(x, i) {
  const cov = x.thumb
    ? '<img class="cov" src="' + esc(x.thumb) + '" alt="" loading="lazy" onerror="this.style.visibility=\'hidden\'">'
    : '<span class="cov" data-p="' + esc(s.platform || "") + '"></span>';
  const sub = [x.platform_name || "", x.author || "", fmtSize(x.size || 0)]
    .filter(Boolean).join(" · ");
  return '<div class="hitem" data-i="' + i + '">' + cov +
    '<div class="hmeta"><div class="ht">' + esc(x.title || x.url || "未命名") + "</div>" +
    '<div class="hs">' + esc(sub) + "</div></div></div>";
}

function renderHistory() {
  const box = $("#histList");
  if (!histItems.length) {
    box.innerHTML = '<div class="mempty">还没有下载记录</div>';
    return;
  }
  box.innerHTML = histItems.map(histRow).join("");
  box.querySelectorAll(".hitem").forEach((el) => {
    el.onclick = async () => {
      const x = histItems[+el.dataset.i];
      const r = await post("/api/open", { path: x.path });
      if (!r.ok) toast(r.error || "文件已经不在了");
    };
  });
}

async function loadHistory(q) {
  try {
    const r = await fetch("/api/history?q=" + encodeURIComponent(q || ""))
      .then((x) => x.json());
    histItems = r.items || [];
    renderHistory();
  } catch (e) {
    $("#histList").innerHTML = '<div class="mempty">读不到历史</div>';
  }
}

function openHist() {
  closeAllDrawers();
  $("#mask").classList.add("on");
  $("#histDrawer").classList.add("on");
  loadHistory($("#histQ").value.trim());
}

if ($("#openHist")) $("#openHist").onclick = openHist;
if ($("#closeHist")) $("#closeHist").onclick = closeAllDrawers;
if ($("#histQ")) {
  let ht = 0;
  $("#histQ").addEventListener("input", () => {
    clearTimeout(ht);
    ht = setTimeout(() => loadHistory($("#histQ").value.trim()), 220);
  });
}

if ($("#exportCsv")) {
  $("#exportCsv").onclick = async () => {
    const r = await fetch("/api/export").then((x) => x.json());
    if (r.ok) toast("已导出 " + r.count + " 条到下载目录", 3200);
    else toast(r.error || "导出失败");
  };
}

if ($("#clearHist")) {
  $("#clearHist").onclick = async () => {
    if (!window.confirm("清空所有下载记录？已经下好的文件不会被删。")) return;
    await post("/api/history_clear", {});
    loadHistory("");
  };
}

/* 遮罩和 Esc 要能关掉两个抽屉 */
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeAllDrawers();
});

applyTab(tabMode);

if ($("#pickMusic")) {
  $("#pickMusic").onclick = async () => {
    const p = await openPicker($("#pickMusic"), "浏览");
    if (!p) return;
    $("#cfgMusicDir").value = p;
    toast("点底部「保存」生效");
  };
}

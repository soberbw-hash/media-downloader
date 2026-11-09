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
let pushKey = "";          // 「一键收网页」书签带的钥匙，跟着服务走
let autoPaste = localStorage.getItem("autoPaste") !== "0";
let supportRendered = false;

function toast(msg, ms = 2400, action) {
  const t = $("#toast");
  t.classList.remove("has-act");
  t.textContent = msg;
  if (action && action.label && typeof action.fn === "function") {
    t.classList.add("has-act");
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = action.label;
    b.onclick = () => { t.classList.remove("on"); action.fn(); };
    t.appendChild(b);
  }
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
  // 软件自己的地址（/push 书签回链）不是素材，别把它变成一条必然失败的下载任务
  hits.forEach(([, u]) => { if (!isSelfUrl(u) && !out.includes(u)) out.push(u); });
  return out;
}

function isSelfUrl(u) {
  try {
    const x = new URL(u);
    const h = x.hostname.toLowerCase();
    const port = +x.port;
    if (h !== "127.0.0.1" && h !== "localhost" && h !== "[::1]") return false;
    return x.pathname === "/push" || (port >= 8765 && port <= 8785);
  } catch (e) { return false; }
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
  // 字节下完、正在合并 / 收尾：别再挂个不动的「100.0%」像卡死，
  // 只留一句在做什么，进度条换成流动状态。
  const wrapping = t.status === "running" &&
    (pct >= 99.5 || /处理|合并|整理|收尾/.test(t.stage || ""));
  bar.parentElement.classList.toggle("wrapping", wrapping);

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
    line = `${t.stage || STATUS_TEXT[t.status]}${t.percent && !wrapping ? " " + t.percent.toFixed(1) + "%" : ""}`;
    if (t.speed && !wrapping) line += ` · ${t.speed.toFixed(1)} MB/s`;
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
    notifyDone(c.done);
  }
  liveBefore = c.live;
  if (document.title !== t) document.title = t;
}

/* 下完了主动说一声，并给一步到文件夹的入口；没授权系统通知就不去讨权限 */
function notifyDone(n) {
  try {
    if (typeof Notification !== "undefined" && Notification.permission === "granted") {
      const nt = new Notification("音视频下载器", { body: `${n} 项已下好` });
      nt.onclick = () => {
        window.focus();
        const b = $("#openDir");
        if (b) b.click();
      };
    }
  } catch (e) {}
  toast(`${n} 项已下好`, 5600, {
    label: "打开文件夹",
    fn: () => { const b = $("#openDir"); if (b) b.click(); },
  });
}

let lastTasks = [];

function render(tasks) {
  lastTasks = tasks;
  const box = $("#tasks");
  renderStats(tasks);

  if (!tasks.length) {
    const blk = $("#tasksBlock");
    if (blk) blk.hidden = true;      // 没任务就整块收起来，不留空白也不带出滚动条
    if (box) box.innerHTML = "";
    selMap.clear();
    syncMusicRows(tasks);
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
  syncMusicRows(tasks);   // 搜索结果行里同步显示下载进度
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
    renderUpdateStatus(d.update);
    if (d.push_key && d.push_key !== pushKey) {
      pushKey = d.push_key;
      renderPushBookmark();
    }
    // 版本号先填本地的，别等联网检查时先闪一个「v—」
    if (d.version) {
      const cv = $("#curVer");
      if (cv && (cv.textContent === "v—" || !cv.textContent)) cv.textContent = "v" + d.version;
    }
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

/* 支持哪些站：摆在粘贴框下面一行小字。
   只列真能下的 —— 要自己搭接口才有戏的（视频号）和明确不支持的，不在这儿画饼。 */
const PLAT_LEVEL_TIP = {
  cookie: "要登录一下才能下",
  experimental: "能下，个别链接可能失败",
};
/* 一行放得下的才列出来，剩下的用「等 N 个」带过；鼠标停在整个行上能看到全部 */
const PLAT_ORDER = ["douyin", "bilibili", "xiaohongshu", "kuaishou", "weibo",
  "ixigua", "youtube", "twitter", "tiktok", "instagram", "netease", "qqmusic", "suno"];
const PLAT_SHOW_MAX = 13;

function renderSupport(list) {
  if (supportRendered) return;
  supportRendered = true;
  const box = $("#support");
  if (!box) return;
  const usable = list.filter((p) => p.level !== "unsupported" && p.level !== "needs-api");
  const rank = (p) => {
    const i = PLAT_ORDER.indexOf(p.id);
    return i < 0 ? PLAT_ORDER.length : i;
  };
  const sorted = usable.slice().sort((a, b) => rank(a) - rank(b));
  const shown = sorted.slice(0, PLAT_SHOW_MAX);
  const rest = sorted.length - shown.length;

  box.innerHTML = "";
  shown.forEach((p, i) => {
    if (i) {
      const sep = document.createElement("span");
      sep.className = "pl-sep";
      sep.textContent = "·";
      box.appendChild(sep);
    }
    const el = document.createElement("span");
    el.className = "pl-item";
    el.textContent = p.name;
    el.title = PLAT_LEVEL_TIP[p.level] || "粘链接就能下";
    box.appendChild(el);
  });
  if (rest > 0) {
    const more = document.createElement("span");
    more.className = "pl-more";
    more.textContent = "共 " + sorted.length + " 个网站";
    more.title = sorted.map((p) => p.name).join("、");
    box.appendChild(more);
  }
  const line = $("#platLine");
  if (line) line.hidden = !usable.length;
}

/* ---------- 提交：一次粘贴 = 一批 ---------- */
let inflight = 0;

async function submitBlocks(blocks, localUrls) {
  // 粘贴、拖放、全局 Ctrl+V、剪贴板自动收都走这儿，统一一道闸防重复提交
  if (inflight > 0) return;
  inflight++;
  try {
    await ensureDir();
    const r = await post("/api/add", { blocks });
    const added = r.added || [];
    const skipped = r.skipped || [];
    // 正在看「失败」列表时新任务加进来会看不见，先切回「全部」
    if (added.length && taskFilter !== "all") setFilter("all");
    if (added.length) {
      if (added.length === 1) {
        toast(`已加入 · ${added[0].platform_name}`);
      } else {
        toast(`已加入 ${added.length} 条`);
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

/* 在搜索框里粘了一条链接：别当歌名搜，直接切回「链接下载」下掉 */
const mQuery = $("#mQuery");
if (mQuery) {
  mQuery.addEventListener("paste", (e) => {
    const cd = e.clipboardData || window.clipboardData;
    const text = cd ? cd.getData("text") : "";
    const urls = urlsIn(text);
    if (!urls.length) return;
    e.preventDefault();
    applyTab("video");
    if (autoPaste) {
      flashComposer();
      submitBlocks([{ text }], urls);
      toast("认出是链接，已直接开始下载");
    } else {
      input.value = (input.value ? input.value + "\n" : "") + text;
      autoGrow();
      renderDetected(urlsIn(input.value));
      toast("认出是链接，点「开始下载」就行");
    }
  });
}

$("#autoPaste").addEventListener("change", (e) => {
  autoPaste = e.target.checked;
  localStorage.setItem("autoPaste", autoPaste ? "1" : "0");
  renderDetected(urlsIn(input.value));
  toast(autoPaste ? "粘贴后自动开始下载" : "粘贴后点「开始下载」");
});

/* ---------- 网页书签 + 剪贴板（收进高级设置，用不上就看不见） ---------- */
let bookmarklet = "";
function renderPushBookmark() {
  if (!pushKey) return;
  const base = location.origin + "/push?k=" + encodeURIComponent(pushKey) + "&url=";
  bookmarklet = "javascript:(function(){window.open('" + base +
    "'+encodeURIComponent(location.href),'_blank');})()";
}

const copyBm = $("#btnCopyBookmark");
if (copyBm) copyBm.onclick = async () => {
  if (!bookmarklet) { toast("还没连上服务，稍等一下再点"); return; }
  try {
    await navigator.clipboard.writeText(bookmarklet);
    toast("已复制 · 新建个书签，把地址粘进去就能用", 4200);
  } catch (e) {
    window.prompt("复制这段，新建书签时粘到地址里：", bookmarklet);
  }
};

let lastClip = "";
async function grabClipboard(manual) {
  if (!manual && !autoPaste) return;          // 尊重「粘贴即下载」开关
  if (!navigator.clipboard || !navigator.clipboard.readText) {
    if (manual) toast("浏览器不让读剪贴板，直接 Ctrl+V 粘贴就行");
    return;
  }
  try {
    const text = (await navigator.clipboard.readText()) || "";
    if (!text) { if (manual) toast("剪贴板里没东西"); return; }
    const urls = urlsIn(text);
    if (!urls.length) { if (manual) toast("剪贴板里没有能下的链接"); return; }
    if (!manual && text === lastClip) return;  // 同一条只自动收一次
    lastClip = text;
    submitBlocks([{ text }], urls);
    toast("剪贴板里的链接已加入下载");
  } catch (e) {
    // 浏览器没给剪贴板权限就安安静静当没这功能，原来的 Ctrl+V 照常用
    if (manual) toast("读不到剪贴板，直接 Ctrl+V 粘贴就行");
  }
}

$("#btnGrabClip").onclick = () => grabClipboard(true);
window.addEventListener("focus", () => {
  if (!document.hidden) setTimeout(() => grabClipboard(false), 150);
});
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) grabClipboard(false);
});

/* ---------- 账号登录 ---------- */
let loginSig = "";
let defaultBrowser = null;
let extInfo = null;
let lastLogged = null;

async function loadLogins(force) {
  try {
    const r = await fetch("/api/login/state");
    const d = await r.json();
    defaultBrowser = d.default_browser || null;
    extInfo = d.extension || null;
    const list = d.sites || [];
    // 扩展把登录态送进来时，界面自己变「已登录」；再给一句人话确认，
    // 用户不用一直盯着面板等。
    if (lastLogged) {
      const added = list.filter((s) => s.logged_in && !lastLogged.includes(s.id));
      if (added.length) toast(`${added.map((s) => s.name).join("、")} 已登录，可以下了`);
    }
    lastLogged = list.filter((s) => s.logged_in).map((s) => s.id);
    const sig = JSON.stringify([list, d.status, defaultBrowser, extInfo]);
    if (sig === loginSig && !force) return;
    loginSig = sig;
    renderLogins(list, d.status || {});
  } catch (e) {}
}

function dailyName() {
  return (defaultBrowser && defaultBrowser.name) || "默认浏览器";
}

function renderLogins(sites, status) {
  const box = $("#logins");
  if (!box || !sites || !sites.length) return;   // 还没拉到，先别把空列表画上去
  const busy = status.state === "opening" || status.state === "waiting";
  box.innerHTML = "";

  // 按「必须登 / 登了更好」分区，用户一眼就知道哪些能跳过
  const groups = [
    { t: "必须登录", list: sites.filter((s) => s.required) },
    { t: "可选登录", list: sites.filter((s) => !s.required) },
  ];

  groups.forEach((g) => {
    if (!g.list.length) return;
    const sec = document.createElement("section");
    sec.className = "lg-grp";
    const h = document.createElement("div");
    h.className = "lg-t";
    h.textContent = g.t;
    const n = document.createElement("i");
    n.textContent = g.list.length;
    h.appendChild(n);
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
      : `只有 ${must} 个平台要登录`;
  }
  const num = $("#acctNum");
  if (num) num.innerHTML = `${on}<span>/${sites.length}</span>`;
  const prog = $("#acctProg");
  if (prog) {
    const pct = sites.length ? Math.round(on * 100 / sites.length) : 0;
    prog.style.width = pct + "%";
    prog.classList.toggle("full", on >= sites.length && sites.length > 0);
  }
  renderExt();
  renderLoginBar(status, busy);
}

/* 一键装小助手：真正读登录态的是扩展，这边只负责把安装压成一个按钮 */
async function setupExt() {
  const btn = $("#btnExtSetup");
  if (btn) btn.disabled = true;
  const r = await post("/api/login/setup_ext", {});
  if (btn) btn.disabled = false;
  if (!r.ok) { toast(r.error || "没打开，稍后再试一次"); return; }
  toast(r.msg || "文件夹已选中");
  setTimeout(() => loadLogins(true), 3000);
}

function renderExt() {
  const card = $("#extCard");
  if (!card) return;
  const on = !!(extInfo && extInfo.synced);
  const badge = $("#extBadge");
  if (badge) {
    badge.textContent = on ? "已连上" : "第一次要装";
    badge.className = on ? "ext-badge on" : "ext-badge";
  }
  const btn = $("#btnExtSetup");
  if (btn) {
    btn.textContent = on ? "重新检查" : "选中扩展文件夹";
    btn.onclick = on ? () => { loadLogins(true); toast("已重新检查"); } : setupExt;
  }
  const how = $("#extHow");
  if (how) how.hidden = on;      // 装好就别再占着位置
  card.classList.toggle("missing", !!(extInfo && !extInfo.exists));
}

function loginRow(s, status, busy) {
  const row = document.createElement("div");
  row.className = "login-row";
  const running = busy && status.platform === s.id;
  const acc = s.account || {};
  // cookie 文件在，但账号接口说失效了 → 老实显示「已失效」，别骗人
  const dead = !!(acc.error && String(acc.error).indexOf("失效") >= 0);
  const on = !dead && (!!s.logged_in || !!acc.ok);
  const stCls = running ? "st run" : (dead ? "st bad" : (on ? "st ok" : "st"));
  const stText = running ? "等待中" : (dead ? "已失效" : (on ? "已登录" : "未登录"));
  // 账号名和会员徽标跟在平台名后面：一行读完，不再多占一行把列表撑高
  const who = on && acc.ok && acc.name ? `<i class="acc">${esc(acc.name)}</i>` : "";
  const vip = on && acc.ok && acc.vip ? `<em class="vip">${esc(acc.detail || "VIP")}</em>` : "";
  const out = s.logged_in || acc.ok
    ? `<button type="button" class="ghost" data-act="out">退出</button>` : "";
  row.innerHTML = iconHTML(s.id)
    + `<span class="nm">${esc(s.name)}${who}${vip}</span>`
    + `<span class="${stCls}">${stText}</span>`
    + `<span class="acts">`
    + `<button type="button" data-act="login" title="用${esc(dailyName())}打开">打开</button>`
    + `${out}</span>`;
  row.title = acc.ok && acc.error ? acc.error : (s.tip || "");

  const lb = row.querySelector('[data-act="login"]');
  lb.disabled = busy;
  lb.onclick = () => startLogin(s.id);
  const ob = row.querySelector('[data-act="out"]');
  if (ob) {
    ob.disabled = busy;
    ob.onclick = () => logoutSite(s);
  }
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
    show(status.msg || "请在浏览器里完成登录", true);
  } else if (status.state === "opened") {
    const synced = !!(extInfo && extInfo.synced);
    show(synced ? "网页已打开 · 登录完就好" : "网页已打开", false);
    if (!synced) {
      const b = document.createElement("button");
      b.className = "go";
      b.textContent = "一键装小助手";
      b.onclick = () => setupExt();
      bar.appendChild(b);
    }
  } else if (["done", "error", "timeout", "closed"].includes(status.state)) {
    show(status.msg || "", false);
  }
}

async function startLogin(pid) {
  const r = await post("/api/login/start", { platform: pid });
  toast(r.ok ? (r.msg || `已在 ${dailyName()} 打开`)
             : (r.error || "打不开浏览器"));
  loadLogins(true);
}

async function logoutSite(s) {
  const r = await post("/api/login/logout", { platform: s.id });
  toast(r.ok ? `已退出 ${s.name}` : (r.msg || "退出失败"));
  loadLogins(true);
}

/* 脚本在 body 末尾加载，DOM 已经就绪，直接绑 */
if (typeof document !== "undefined") {
  const _es = $("#btnExtSetup");
  if (_es) _es.onclick = setupExt;

  // 教程图在抽屉里太小，点一下铺满屏幕看
  const _box = $("#shotBox"), _img = $("#shotImg");
  if (_box && _img) {
    document.querySelectorAll(".gpic").forEach((el) => {
      el.addEventListener("click", () => {
        // 小图是局部特写，点开换成整页，能看出它在页面哪个角落
        _img.src = el.getAttribute("data-full") || el.getAttribute("src") || "";
        _box.hidden = false;
      });
    });
    const closeShot = () => { _box.hidden = true; _img.removeAttribute("src"); };
    _box.addEventListener("click", closeShot);
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && !_box.hidden) closeShot();
    });
  }

  // 图取不回来时别留一个破图标 + 一行灰字，直接说清楚怎么办
  document.querySelectorAll("#extHow img.gpic").forEach((el) => {
    el.addEventListener("error", () => {
      const holder = el.parentNode;
      if (!holder) return;
      const tip = document.createElement("div");
      tip.className = "gt";
      tip.innerHTML = '<span class="gn">!</span>'
        + "<span>图没加载出来，把软件完全退出再打开一次就好。</span>";
      el.remove();
      holder.appendChild(tip);
    });
  });
}

/* ---------- 下载目录 ---------- */
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

/* 默认目录直接开下，不再拦一个「存哪儿」的弹窗打断粘贴。
   只在第一次悄悄说一句文件去哪了，想换随时去设置里点「浏览」。 */
async function ensureDir() {
  if (!cfg || !cfg.out_dir) {
    try { await poll(); } catch (e) {}   // 配置还没拉下来，先等一次
  }
  if (!ensureDir._told && cfg && !cfg.out_dir_set) {
    ensureDir._told = true;
    toast(`文件会存到 ${cfg.out_dir || "默认文件夹"} · 想改去设置`, 4200);
  }
  return true;
}

/* ---------- 设置 ---------- */
/* 常用页 / 高级页同一张卡片里换，卡片高度是死的，所以不会跳 */
function setPane(adv) {
  const main = $("#setMain"), ex = $("#setAdv");
  if (!main || !ex) return;
  adv = !!adv;
  main.hidden = adv;
  ex.hidden = !adv;
  if ($("#setBack")) $("#setBack").hidden = !adv;
  if ($("#setTitle")) $("#setTitle").textContent = adv ? "高级" : "设置";
  if ($("#openAdv")) $("#openAdv").hidden = adv;
  const body = $("#drawer .drawer-body");
  if (body) body.scrollTop = 0;   // 每页都从头看
}

function openDrawer() {
  closeAllDrawers(true);
  $("#mask").classList.add("on");
  $("#drawer").classList.add("on");
  setPane(false);
  $("#cfgOut").value = cfg.out_dir || "";
  $("#cfgQuality").value = cfg.quality || "best";
  if ($("#cfgProxy")) $("#cfgProxy").value = cfg.proxy || "";
  if ($("#cfgCookies")) $("#cfgCookies").value = cfg.cookies_file || "";
  $("#cfgConc").value = String(cfg.concurrency || 3);
  if ($("#cfgNameTpl")) $("#cfgNameTpl").value = cfg.name_tpl || "";
  syncNameUI();
  $("#cfgCover").checked = !!cfg.save_cover;
  $("#cfgSubs").checked = !!cfg.down_subs;
  $("#cfgDanmaku").checked = !!cfg.save_danmaku;   // 默认关
  $("#cfgDesc").checked = !!cfg.save_desc;         // 默认关
  loadLogins(true);          // 只为刷新「已登录 n/8」这行摘要
}

function openAcct() {
  closeAllDrawers(true);
  $("#mask").classList.add("on");
  $("#acctDrawer").classList.add("on");
  loadLogins(true);
}

/* instant=true 用在「弹窗换弹窗」时：旧的直接消失，别和新弹窗在同一位置叠一下 */
function closeAllDrawers(instant) {
  instant = instant === true;
  ["#drawer", "#acctDrawer", "#histDrawer"].forEach((s) => {
    const el = $(s);
    if (!el) return;
    if (instant && el.classList.contains("on")) {
      el.classList.add("instant");
      el.classList.remove("on");
      // 等两帧让无动画状态生效，再恢复过渡，下次开合照样有动画
      requestAnimationFrame(() => requestAnimationFrame(() => el.classList.remove("instant")));
    } else {
      el.classList.remove("on");
    }
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

// 弹窗满屏铺着，点卡片外面的空白等于点遮罩，一样关掉
["#drawer", "#acctDrawer", "#histDrawer"].forEach((s) => {
  const el = $(s);
  if (!el) return;
  el.addEventListener("mousedown", (e) => {
    if (e.target === el) closeAllDrawers();
  });
});

// 少用的东西收进「高级选项」那一页：点进去、点左上角箭号回来
if ($("#openAdv")) $("#openAdv").onclick = () => setPane(true);
if ($("#setBack")) $("#setBack").onclick = () => setPane(false);

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

/* 「自定义」不摆在常用页的下拉里：模板和预设对不上时，这里补一个选项兜住，
   平时看不见它，也不会被「自定义…」这种字眼劝退。 */
function customNameOpt(pre) {
  let opt = pre.querySelector('option[value="__custom__"]');
  if (!opt) {
    opt = document.createElement("option");
    opt.value = "__custom__";
    opt.textContent = "自定义";
    pre.appendChild(opt);
  }
  return opt;
}

function dropCustomNameOpt(pre) {
  const opt = pre.querySelector('option[value="__custom__"]');
  if (opt) opt.remove();
}

/* 高级页改了模板 → 常用页的下拉跟着走；常用页选了预设 → 模板跟着走 */
function syncNameUI() {
  const pre = $("#cfgNamePre");
  const inp = $("#cfgNameTpl");
  const hint = $("#nameHint");
  if (!pre || !inp) return;
  const v = (inp.value || "").trim();
  if (!v || Object.prototype.hasOwnProperty.call(NAME_PRESETS, v)) {
    pre.value = v;
    dropCustomNameOpt(pre);
  } else {
    customNameOpt(pre).selected = true;
  }
  if (hint) {
    const ex = nameExample(v);
    hint.textContent = ex
      ? "示例：" + ex
      : "留空就用视频原标题。点上面的标签往里加，中间用下划线连起来。";
  }
}

if ($("#cfgNamePre")) {
  $("#cfgNamePre").addEventListener("change", () => {
    const pre = $("#cfgNamePre");
    const inp = $("#cfgNameTpl");
    if (pre.value !== "__custom__") {
      inp.value = pre.value;    // 选预设 = 直接换成那套模板
      dropCustomNameOpt(pre);
    }
    syncNameUI();
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
/* 发现新版不用用户管：后台下好 → 等手头任务结束 → 自动替换重启。
   只有「没系统代理」或「更新失败要手动下包」时才把 VPN 提示露出来。 */
let updToastShown = false;

function renderUpdateStatus(u) {
  if (!u) return;
  const msg = $("#updMsg"), bar = $("#updBar"), tip = $("#updTip"), btn = $("#checkUpd");
  const state = u.state || "idle";
  if (u.current && $("#curVer")) $("#curVer").textContent = "v" + u.current;
  if (msg) {
    if (state === "idle") msg.textContent = u.detail || "";
    else if (state === "checking") msg.textContent = "正在检查…";
    else if (state === "downloading") msg.textContent = "正在下载新版 " + Math.round(u.percent || 0) + "%";
    else if (state === "waiting")
      msg.innerHTML = "新版已下好 · <a href=\"#\" id=\"updNow\">立刻更新</a>";
    else if (state === "applying") msg.textContent = "正在更新，马上回来…";
    else if (state === "done") msg.textContent = u.detail || "已更新好";
    else msg.innerHTML = (u.detail || "更新没成") +
      " · <a href=\"" + esc(u.manual_url || "") + "\" target=\"_blank\" rel=\"noreferrer\">下载安装包</a>";
  }
  if (bar) {
    const active = ["downloading", "waiting", "applying"].indexOf(state) >= 0;
    bar.hidden = !active;
    const fill = bar.querySelector("i");
    if (fill) fill.style.width = (state === "downloading" ? (u.percent || 0) : 100) + "%";
  }
  // 手动去 GitHub 下整包时一律提醒；自动下载只在没走代理时提醒，别常驻占位置
  const needTip = (state === "downloading" && u.proxy_hint) ||
    state === "error" || state === "manual";
  if (tip) tip.hidden = !needTip;
  if (btn) btn.textContent = ["downloading", "waiting", "applying"].indexOf(state) >= 0
    ? "正在更新" : "检查更新";
  const now = $("#updNow");
  if (now) now.onclick = async (e) => {
    e.preventDefault();
    await post("/api/update/apply", {});
  };
  if (state === "applying" && !updToastShown) {
    updToastShown = true;
    toast("新版装好了，正在重启…", 3000);
  }
}

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
      if (msg) msg.textContent = manual ? (r.error || "检查失败，稍后再试") : "";
      if (manual) toast(r.error || "检查失败，稍后再试", 3200);
      return;
    }
    if (!r.has_update) {
      if (msg) msg.textContent = "已是最新版";
      const bar = $("#updBar"), tip = $("#updTip");
      if (bar) bar.hidden = true;
      if (tip) tip.hidden = true;
      if (manual) toast("已是最新版 v" + r.current);
      return;
    }
    if (r.auto) {
      if (msg) msg.textContent = "发现新版 v" + r.latest + "，正在更新…";
      await post("/api/update/start", {});
      const s = await fetch("/api/update/status").then((x) => x.json());
      renderUpdateStatus(s.update);
    } else {
      if (msg) msg.innerHTML = "发现新版 v" + esc(r.latest) +
        " · <a href=\"" + esc(r.url) + "\" target=\"_blank\" rel=\"noreferrer\">下载安装包</a>";
      const tip = $("#updTip");
      if (tip) tip.hidden = false;
      toast("发现新版本 v" + r.latest, 3200);
    }
  } catch (e) {
    if (msg) msg.textContent = manual ? "检查失败，网络不通" : "";
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

/* ---------- 设置：改一下就存一下，不用记着点「完成」 ---------- */
function collectCfg() {
  const out = {
    out_dir: $("#cfgOut").value.trim(),
    quality: $("#cfgQuality").value,
    concurrency: parseInt($("#cfgConc").value, 10) || 3,
    name_tpl: ($("#cfgNamePre").value === "" ? "" : $("#cfgNameTpl").value.trim()),
    save_cover: $("#cfgCover").checked,
    down_subs: $("#cfgSubs").checked,
    save_danmaku: $("#cfgDanmaku").checked,
    save_desc: $("#cfgDesc").checked,
  };
  // 高级页里才有的项：没渲染就不发，别把已有配置读成空
  if ($("#cfgProxy")) out.proxy = $("#cfgProxy").value.trim();
  if ($("#cfgCookies")) out.cookies_file = $("#cfgCookies").value.trim();
  return out;
}

async function pushCfg(patch) {
  try {
    const r = await post("/api/config", patch);
    if (r.ok) cfg = r.config;
    else if (r.error) toast(r.error, 3200);
  } catch (e) { /* 服务没起来时别刷错误，下次改动会再试 */ }
}

const cfgTimers = {};
function cfgLater(key, patch, wait = 420) {
  clearTimeout(cfgTimers[key]);
  cfgTimers[key] = setTimeout(() => pushCfg(patch), wait);
}
function nameTplNow() {
  return { name_tpl: $("#cfgNamePre").value === "" ? "" : $("#cfgNameTpl").value.trim() };
}

// 下拉 / 开关：改完立刻生效
[["#cfgQuality", () => ({ quality: $("#cfgQuality").value })],
 ["#cfgConc", () => ({ concurrency: parseInt($("#cfgConc").value, 10) || 3 })],
 ["#cfgCover", () => ({ save_cover: $("#cfgCover").checked })],
 ["#cfgSubs", () => ({ down_subs: $("#cfgSubs").checked })],
 ["#cfgDanmaku", () => ({ save_danmaku: $("#cfgDanmaku").checked })],
 ["#cfgDesc", () => ({ save_desc: $("#cfgDesc").checked })],
].forEach(([sel, get]) => {
  const el = $(sel);
  if (el) el.addEventListener("change", () => pushCfg(get()));
});

// 文本框：停手一会儿再存，别把打了一半的路径建成文件夹
["#cfgProxy", "#cfgCookies"].forEach((sel) => {
  const el = $(sel);
  if (!el) return;
  const key = { "#cfgProxy": "proxy", "#cfgCookies": "cookies_file" }[sel];
  el.addEventListener("input", () => cfgLater(key, { [key]: el.value.trim() }, 600));
});
const outEl = $("#cfgOut");
if (outEl) outEl.addEventListener("change", () => pushCfg({ out_dir: outEl.value.trim() }));
const tplEl = $("#cfgNameTpl");
if (tplEl) tplEl.addEventListener("input", () => cfgLater("name_tpl", nameTplNow(), 600));
const preEl = $("#cfgNamePre");
if (preEl) preEl.addEventListener("change", () => pushCfg(nameTplNow()));

$("#saveCfg").onclick = async () => {
  Object.keys(cfgTimers).forEach((k) => clearTimeout(cfgTimers[k]));
  const r = await post("/api/config", collectCfg());
  if (r.ok) { cfg = r.config; closeAllDrawers(); }
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
  // 搜索页的任务区要贴底显示（不能被搜索结果挤没），用 body 上的类来区分
  document.body.classList.toggle("tab-music", m === "music");
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

/* 搜索结果 ↔ 下载任务的对应关系。
   key = 平台|歌曲ID，这样换一批结果、同一个歌再搜一次，状态都认得。
   mTask 里存 { tid, t }：t 是最近一次看到的任务快照，
   任务被「清空已完成」清走后，靠它还能继续显示「已下好」。 */
const mTask = new Map();
const mNote = new Map();     // 一次性提示，比如「这条以前下过了」

function mkey(s) {
  return (s && s.platform ? s.platform : "") + "|" + (s && s.id ? s.id : "");
}

/* 音乐的链接模板要跟后端一致，才能拿链接反查任务 */
const MUSIC_URL_TPL = {
  netease: "https://music.163.com/song?id=%s",
  qqmusic: "https://y.qq.com/n/ryqq/songDetail/%s",
  kugou: "https://www.kugou.com/song/#hash=%s",
};
function musicUrlOf(s) {
  const tpl = s ? MUSIC_URL_TPL[s.platform] : "";
  return (tpl && s.id) ? tpl.replace("%s", s.id) : "";
}

const PLAT_NAME = { all: "全网", netease: "网易云", qqmusic: "QQ音乐", kugou: "酷狗", bili: "B站", youtube: "YouTube" };
const MUSIC_PLATS = ["netease", "qqmusic", "kugou"];   // 能搜歌、也能试听的平台
// 记住上次在搜歌 / 找素材 / 找电影，别每次进来都先点一下「视频」
let mMode = (() => {
  const v = localStorage.getItem("mMode");
  return (v === "music" || v === "video" || v === "movie") ? v : "music";
})();
let vPlat = "all";        // all 或某个来源 id

/* 聚合搜索：来源清单、资源站入口都由后端下发；
   慢源在后台跑完后，前端拿 token 分批把结果补进来。 */
let vSources = [];
let vSites = [];
let vCustomSites = [];
let vCustomSources = [];
let vToken = "";
let vPulls = 0;
let vPullTimer = null;
let vLastQuery = "";

function applyPlat(p) {
  mPlat = p;
  localStorage.setItem("mPlat", p);
  document.querySelectorAll("#mPlats .plat").forEach((b) => {
    b.classList.toggle("on", b.dataset.p === p);
  });
}

// 三个来源的引导语：没关键词时告诉用户该搜什么
const SOURCE_GUIDE = {
  music: "输入歌名或歌手开始搜",
  video: "输入关键词找视频素材（解说、运镜、混剪…）",
  movie: "输入片名开始搜",
};

// 把上一轮的结果整个清掉。
// 换来源、换平台、清空搜索框时都要走这儿：
// 留着旧的计数和旧的列表，界面就会说「共 24 条」而下面空空，
// 或者列表里还是别的来源的东西。
function resetResults() {
  mItems = [];
  mSel.clear();
  syncMusicSel();
  const box = $("#mResults");
  if (box) box.innerHTML = "";
  const hint = $("#mHint");
  if (hint) hint.textContent = "";
  // 上一轮的来源状态、还没拉完的补齐结果也一起作废
  const sh = $("#vSrcHint");
  if (sh) { sh.innerHTML = ""; sh.hidden = true; }
  clearTimeout(vPullTimer);
  vToken = "";
}

document.querySelectorAll("#mPlats .plat").forEach((b) => {
  b.onclick = () => {
    applyPlat(b.dataset.p);
    resetResults();
    const q = $("#mQuery").value.trim();
    if (q) musicSearch(q);      // 换平台直接重搜
  };
});
applyPlat(mPlat);

function syncModeUI() {
  document.querySelectorAll("#mSrc .plat").forEach((x) => {
    x.classList.toggle("on", x.dataset.s === mMode);
  });
  const plats = $("#mPlats");
  if (plats) plats.hidden = mMode !== "music";
  applySourceUI();
  const q = $("#mQuery");
  if (q) {
    q.placeholder = mMode === "video" ? "视频关键词：电影解说、运镜、猫咪…"
      : mMode === "movie" ? "片名：哪吒、流浪地球、三体…"
      : "歌名、歌手";
  }
  const mic = $("#mRec");
  if (mic) mic.hidden = mMode !== "music";
}

document.querySelectorAll("#mSrc .plat").forEach((b) => {
  b.onclick = () => {
    mMode = b.dataset.s;
    localStorage.setItem("mMode", mMode);
    syncModeUI();
    // 换来源就是换一套东西，旧的计数、旧的列表、旧的提示都得清掉。
    // 不清就会出现「上面写着共 24 条，下面却是另一个来源的空列表」。
    resetResults();
    // 识曲只在音乐来源下有入口：认出来的是歌，跟搜片名不是一回事
    if (mMode !== "music") recOpen(false);
    const q = $("#mQuery").value.trim();
    if (q) musicSearch(q);
    else $("#mHint").textContent = SOURCE_GUIDE[mMode] || "";
  };
});
syncModeUI();   // 启动时按上次用的模式摆好界面

/* ---------------- 多来源聚合 ---------------- */
function applySourceUI() {
  const isVideo = mMode === "video";
  ["#mVPlats", "#vSites"].forEach((sel) => {
    const el = $(sel);
    if (el) el.hidden = !isVideo;
  });
  const sh = $("#vSrcHint");
  if (sh && !isVideo) sh.hidden = true;
  if (!isVideo) {
    const bar = $("#siteBar");
    if (bar) bar.hidden = true;
  }
}

function sourceName(id) {
  const s = vSources.find((x) => x.id === id);
  return s ? s.name : (PLAT_NAME[id] || id);
}

function renderSources(list) {
  vSources = list || [];
  const box = $("#mVPlats");
  if (!box) return;
  if (vPlat !== "all" && !vSources.some((x) => x.id === vPlat)) vPlat = "all";
  box.innerHTML = '<button class="plat' + (vPlat === "all" ? " on" : "") +
    '" data-p="all">全部</button>' +
    vSources.map((s) => '<button class="plat' + (vPlat === s.id ? " on" : "") +
      '" data-p="' + esc(s.id) + '" title="' +
      esc((s.note || "") + (s.slow ? "（点它才单独搜）" : "")) + '">' +
      esc(s.name) + "</button>").join("");
  box.querySelectorAll(".plat").forEach((b) => {
    b.onclick = () => {
      vPlat = b.dataset.p;
      box.querySelectorAll(".plat").forEach((x) => x.classList.toggle("on", x === b));
      resetResults();
      const q = $("#mQuery").value.trim();
      if (q) musicSearch(q);
    };
  });
}

function renderSites() {
  const box = $("#vSiteChips");
  if (!box) return;
  // 自己接的 JSON 接口也列在这儿，一个地方管增删
  const extras = vCustomSources.map((s) => ({
    id: "src:" + s.name, name: s.name + " · 接口",
    tip: "自己接的第三方接口，点一下直接用它搜", custom: true, isSource: true,
  }));
  const all = vSites.concat(extras);
  box.innerHTML = all.map((s) =>
    '<button class="site-chip" data-id="' + esc(s.id) + '" data-name="' + esc(s.name) +
    '" data-src="' + (s.isSource ? "1" : "0") + '" title="' + esc(s.tip || "") + '">' +
    esc(s.name) +
    (s.custom ? '<span class="x" data-del="' + esc(s.isSource ? s.name.replace(/ · 接口$/, "") : s.name) + '">×</span>' : "") +
    "</button>").join("");
  box.querySelectorAll(".site-chip").forEach((el) => {
    el.onclick = async (e) => {
      const del = e.target.dataset ? e.target.dataset.del : undefined;
      if (del !== undefined) {
        e.stopPropagation();
        if (el.dataset.src === "1") {
          const next = vCustomSources.filter((x) => x.name !== del);
          const r = await post("/api/config", { custom_sources: next });
          if (r.ok) vCustomSources = next;
        } else {
          const next = vCustomSites.filter((x) => x.name !== del);
          const r = await post("/api/config", { custom_sites: next });
          if (r.ok) vCustomSites = next;
        }
        toast("已移除");
        await initAggregate();
        return;
      }
      if (el.dataset.src === "1") {
        const name = el.dataset.name.replace(/ · 接口$/, "");
        const target = Array.from($("#mVPlats").querySelectorAll(".plat"))
          .find((b) => b.textContent === name);
        if (target) target.click();
        else toast("这个接口还没加载好，稍等一下");
        return;
      }
      const q = $("#mQuery").value.trim() || vLastQuery;
      if (!q) { toast("先输入关键词"); $("#mQuery").focus(); return; }
      const r = await post("/api/search/open_site", { id: el.dataset.id, q });
      if (!r.ok) toast(r.error || "打不开这个站");
    };
  });
}

function renderSourceHint(r) {
  const box = $("#vSrcHint");
  if (!box) return;
  if (mMode !== "video") { box.hidden = true; return; }
  const known = new Map((r.sources || []).map((s) => [s.id, s]));
  const ids = (r.sources || []).map((s) => s.id).concat(r.pending || []);
  const parts = [];
  ids.forEach((id) => {
    const s = known.get(id);
    if (!s) { parts.push(esc(sourceName(id)) + " 还在找…"); return; }
    if (s.state === "ok" && s.count) parts.push("<b>" + esc(s.name) + "</b> " + s.count + " 条");
    else if (s.state === "fail") parts.push('<span class="bad" title="' + esc(s.msg) + '">' + esc(s.name) + " 没连上</span>");
    else if (s.state === "skip") parts.push('<span class="bad" title="' + esc(s.msg) + '">' + esc(s.name) + " 跳过</span>");
    else parts.push(esc(s.name) + " 0 条");
  });
  box.innerHTML = parts.join("　·　");
  box.hidden = !parts.length;
}

function scheduleMore(r) {
  clearTimeout(vPullTimer);
  if (r.done || !r.token || !(r.pending || []).length) { vToken = ""; return; }
  vToken = r.token;
  vPulls = 0;
  vPullTimer = setTimeout(pullMore, 2200);
}

async function pullMore() {
  if (!vToken || mMode !== "video") return;
  vPulls++;
  try {
    const r = await fetch("/api/search/more?token=" + encodeURIComponent(vToken))
      .then((x) => x.json());
    if (!r.ok) { vToken = ""; return; }
    mItems = r.items || [];
    renderMusic();
    renderSourceHint(r);
    if (!mItems.length) $("#mHint").textContent = "没搜到，换个关键词或换个来源";
    if (!r.done && vPulls < 8) vPullTimer = setTimeout(pullMore, 2200);
    else vToken = "";
  } catch (e) { vToken = ""; }
}

async function initAggregate() {
  try {
    const r = await fetch("/api/search/sources").then((x) => x.json());
    if (!r.ok) return;
    vCustomSites = (r.custom_sites || []).slice();
    vCustomSources = (r.custom_sources || []).slice();
    renderSources(r.sources);
    vSites = r.sites || [];
    renderSites();
    applySourceUI();
  } catch (e) { /* 服务没起来就先按原来的静态按钮用 */ }
}

function initSiteBar() {
  const bar = $("#siteBar");
  if (!bar) return;
  const tip = $("#siteTip");
  const say = (t) => { if (tip) tip.textContent = t; };
  $("#vSiteAdd").onclick = () => { bar.hidden = !bar.hidden; say(""); if (!bar.hidden) $("#siteName").focus(); };
  $("#siteX").onclick = () => { bar.hidden = true; say(""); };
  $("#siteType").onchange = () => {
    const adv = $("#siteType").value === "source";
    $("#siteRoot").hidden = !adv;
    $("#siteFields").hidden = !adv;
    say(adv ? "接口要返回 JSON；{q} 会被换成关键词。字段映射填不下就留空，默认认 title / url / cover。" : "");
  };
  $("#siteSave").onclick = async () => {
    const name = $("#siteName").value.trim().slice(0, 20);
    const url = $("#siteUrl").value.trim();
    if (!name) { say("给网站起个名字"); return; }
    if (!/^https:\/\//.test(url) || url.indexOf("{q}") < 0) {
      say("地址要 https 开头，并把关键词的位置写成 {q}"); return;
    }
    if ($("#siteType").value === "source") {
      let fields = {};
      try { fields = JSON.parse($("#siteFields").value.trim() || "{}"); }
      catch (e) { say("字段映射要填 JSON，例如 {\"title\":\"name\",\"url\":\"link\"}"); return; }
      const next = vCustomSources.concat([{
        name, url, tip: "自己接的接口",
        root: $("#siteRoot").value.trim(), fields,
      }]).slice(-20);
      const r = await post("/api/config", { custom_sources: next });
      if (!r.ok) { say(r.error || "没保存上"); return; }
      vCustomSources = next;
    } else {
      const next = vCustomSites.concat([{ name, url, tip: "自己加的站点" }]).slice(-20);
      const r = await post("/api/config", { custom_sites: next });
      if (!r.ok) { say(r.error || "没保存上"); return; }
      vCustomSites = next;
    }
    $("#siteName").value = "";
    $("#siteUrl").value = "";
    bar.hidden = true;
    say("");
    await initAggregate();
    toast("加好了，重新搜一次就能用");
  };
}

initAggregate();
initSiteBar();

function musicRow(s, i) {
  const on = mSel.has(i);
  // 封面统一用 span 装着 img：图没出来时占位还在，不会塌成一个空白
  // 视频封面是横图，用宽一点的框，不然 16:9 被裁成正方形只剩中间一小块
  const wide = s.kind === "video";
  const cov = '<span class="cov' + (wide ? " w" : "") + '" data-p="' + esc(s.platform || "") + '">'
    + (s.cover ? '<img src="' + esc(imgUrl(s.cover)) + '" alt="" loading="lazy"'
      + ' onerror="this.remove()">' : "")
    + "</span>";
  const sub = esc(s.artist || s.author || "未知歌手") +
    (s.album ? " · " + esc(s.album) : "") +
    (s.hq ? " · 1080p" : "") +
    (s.kind === "audio" ? " · 预览音频" : "");
  // 来源由后端下发，加一个源不用回来改这张映射表
  const sid = s.source || s.platform || "";
  const src = sid
    ? '<span class="psrc" data-p="' + esc(sid) + '">' +
      esc(s.source_name || PLAT_NAME[sid] || sid) + "</span>"
    : "";
  // 试听按钮：只有这三个音乐平台的歌才有（视频结果不给听）
  const canPlay = MUSIC_PLATS.indexOf(s.platform || "") >= 0 && s.kind !== "video";
  const pv = canPlay
    ? '<button class="pv" data-i="' + i + '" title="试听" aria-label="试听这首歌">' +
      '<svg viewBox="0 0 24 24" width="12" height="12" aria-hidden="true">' +
      '<path d="M8.5 5.6l10 6.4-10 6.4z" fill="currentColor"/></svg></button>'
    : "";
  return '<div class="msong' + (on ? " on" : "") + '" data-i="' + i + '">' +
    '<input type="checkbox" class="mpick" data-i="' + i + '"' +
    (on ? " checked" : "") + ' aria-label="勾选这首歌">' + cov +
    '<div class="meta"><div class="t">' + esc(s.title || "未知曲目") + "</div>" +
    '<div class="a">' + src + sub + "</div></div>" +
    '<span class="mst"></span>' +
    '<span class="d">' + (s.duration ? fmtDur(s.duration) : "") + "</span>" + pv +
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
  // 计数条只跟着结果走：没结果就不显示，别留一句「共 N 条」配上空列表。
  // 电影卡没有勾选框，批量条用不上。
  if (bar) bar.hidden = !total || mMode === "movie";
  const all = $("#mAll");
  if (all) {
    all.checked = n > 0 && n === total;
    all.indeterminate = n > 0 && n < total;
  }
  const cnt = $("#mCount");
  const unit = mMode === "video" ? "条" : "首";
  if (cnt) cnt.textContent = n ? `已选 ${n} / ${total} ${unit}` : `共 ${total} ${unit}`;
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
  box.querySelectorAll(".pv").forEach((b) => {
    b.onclick = () => previewSong(mItems[+b.dataset.i], b);
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
  syncMusicRows(lastTasks);   // 重画之后把下载状态贴回去
  syncPvButtons();
}

/* ---------- 行内下载状态：让人一眼看出「这首下好没有」 ---------- */
function syncMusicRows(tasks) {
  const box = $("#mResults");
  if (!box || !mItems.length) return;
  const byId = new Map();
  for (const t of tasks || []) byId.set(String(t.id), t);

  box.querySelectorAll(".msong").forEach((el) => {
    const s = mItems[+el.dataset.i];
    if (!s) return;
    const key = mkey(s);
    let rec = mTask.get(key);
    if (rec && rec.tid) {
      const t = byId.get(String(rec.tid));
      if (t) rec.t = t;                 // 记住最后一次看到的快照
    }
    let t = rec ? rec.t : null;
    if (!t) {
      // 还没关联过，就按链接在当前任务里找一遍
      // （从「粘贴链接」加进来的歌，也能在这儿对上）
      const u = musicUrlOf(s);
      if (u) {
        const hit = (tasks || []).find((x) => x.url === u);
        if (hit) {
          rec = { tid: hit.id, t: hit };
          mTask.set(key, rec);
          t = hit;
        }
      }
    }
    const badge = el.querySelector(".mst");
    if (!badge) return;

    let text = "", kind = "";
    if (t) {
      if (t.status === "done") {
        text = "已下好"; kind = "ok";
      } else if (t.status === "error") {
        text = t.err_title || "下载失败"; kind = "err";
      } else if (t.status === "canceled") {
        text = "已取消"; kind = "dim";
      } else if (t.status === "running") {
        text = "下载中 " + Math.floor(t.percent || 0) + "%"; kind = "run";
      } else {
        text = "排队中"; kind = "dim";
      }
    } else {
      const note = mNote.get(key);
      if (note) { text = note.text; kind = note.kind; }
    }

    if (text.length > 9) {
      badge.title = text;
      text = text.slice(0, 9) + "…";
    } else {
      badge.title = text;
    }
    if (badge.textContent !== text) badge.textContent = text;
    badge.className = "mst" + (kind ? " " + kind : "");
    badge.hidden = !text;
    // 下好了就能点开所在文件夹，不用再翻到底下任务区
    badge.classList.toggle("act", kind === "ok");
    badge.onclick = kind === "ok"
      ? () => post("/api/open", { id: rec.tid }).then((r) => {
        if (!r.ok) toast(r.error || "文件还没下载好");
      })
      : null;
  });
}

/* ================= 试听：不用先下载就能听 ================= */

let pvKey = "";        // 正在试听的那首歌（platform|id）

/* 播放器上用 3:45 这种写法，比「3分45秒」省地方 */
function mmss(sec) {
  sec = Math.max(0, Math.floor(sec || 0));
  const m = Math.floor(sec / 60);
  const r = sec % 60;
  return m + ":" + (r < 10 ? "0" : "") + r;
}

function previewSong(s, btn) {
  if (!s) return;
  const au = $("#auPlayer");
  if (!au) return;
  const key = mkey(s);

  // 点同一首 = 暂停 / 继续
  if (pvKey === key && au.getAttribute("src")) {
    if (au.paused) au.play().catch(() => toast("这条播不出来，换一首试试"));
    else au.pause();
    syncPvButtons();
    return;
  }

  btn.classList.add("busy");
  btn.disabled = true;
  post("/api/music/preview", {
    platform: s.platform, id: s.id, title: s.title, artist: s.artist,
  }).then((r) => {
    if (!r.ok) {
      toast(r.error || "这首暂时试听不了", 4200);
      return;
    }
    pvKey = key;
    au.src = r.url;
    au.play().catch(() => toast("这条播不出来，换一首试试"));
    showPlayer(s);
  }).catch(() => {
    toast("连接失败，服务可能已经关了");
  }).finally(() => {
    btn.classList.remove("busy");
    btn.disabled = false;
    syncPvButtons();
  });
}

function showPlayer(s) {
  const bar = $("#pBar");
  if (!bar) return;
  bar.hidden = false;
  document.body.classList.add("playing");
  const t = $("#pbTitle");
  if (t) t.textContent = (s.title || "未知曲目") + (s.artist ? " · " + s.artist : "");
  updatePvTime();
}

function stopPreview() {
  const au = $("#auPlayer");
  if (au) {
    au.pause();
    au.removeAttribute("src");
    try { au.load(); } catch (e) {}
  }
  pvKey = "";
  const bar = $("#pBar");
  if (bar) bar.hidden = true;
  document.body.classList.remove("playing");
  syncPvButtons();
}

function updatePvTime() {
  const au = $("#auPlayer");
  if (!au) return;
  const dur = (isFinite(au.duration) && au.duration > 0) ? au.duration : 0;
  const fill = $("#pbBar i");
  if (fill) fill.style.width = (dur ? (au.currentTime / dur * 100) : 0) + "%";
  const tm = $("#pbTime");
  if (tm) tm.textContent = mmss(au.currentTime) + (dur ? " / " + mmss(dur) : "");
}

function syncPvButtons() {
  const box = $("#mResults");
  if (box) {
    box.querySelectorAll(".msong").forEach((el) => {
      const s = mItems[+el.dataset.i];
      const b = el.querySelector(".pv");
      if (!b || !s) return;
      b.classList.toggle("on", pvKey === mkey(s));
    });
  }
  const au = $("#auPlayer");
  const bar = $("#pBar");
  if (au && bar) {
    const on = !!pvKey && !au.paused && !au.ended;
    bar.classList.toggle("playing", on);
  }
}

/* 上一首 / 下一首：只挑能试听的歌，电影、没结果的条目直接跳过 */
function playAdjacent(step) {
  const n = mItems.length;
  if (!n) return;
  let i = mItems.findIndex((s) => mkey(s) === pvKey);
  if (i < 0) i = step > 0 ? -1 : 0;
  for (let k = 1; k <= n; k++) {
    const j = ((i + step * k) % n + n) % n;
    const s = mItems[j];
    if (!s || !MUSIC_PLATS.includes(s.platform)) continue;
    const el = document.querySelector(`#mResults .msong[data-i="${j}"] .pv`);
    previewSong(s, el || document.createElement("span"));
    return;
  }
}

function initPreview() {
  const au = $("#auPlayer");
  if (!au) return;
  au.addEventListener("timeupdate", updatePvTime);
  au.addEventListener("loadedmetadata", updatePvTime);
  au.addEventListener("play", syncPvButtons);
  au.addEventListener("pause", syncPvButtons);
  au.addEventListener("ended", () => {
    au.currentTime = 0;
    syncPvButtons();
    playAdjacent(1);        // 播完自动接下一首，不用回列表再点
  });
  au.addEventListener("error", () => {
    if (!au.getAttribute("src")) return;      // 主动 stopPreview 也会触发 error
    toast("这条音频拉不下来，换一首试试", 3600);
    stopPreview();
  });

  const play = $("#pbPlay");
  if (play) play.onclick = () => {
    if (!pvKey) return;
    if (au.paused) au.play().catch(() => toast("这条播不出来，换一首试试"));
    else au.pause();
  };
  const stop = $("#pbStop");
  if (stop) stop.onclick = stopPreview;
  const prev = $("#pbPrev");
  if (prev) prev.onclick = () => playAdjacent(-1);
  const next = $("#pbNext");
  if (next) next.onclick = () => playAdjacent(1);
  const pbar = $("#pbBar");
  if (pbar) pbar.onclick = (e) => {
    if (!isFinite(au.duration) || au.duration <= 0) return;
    const r = pbar.getBoundingClientRect();
    const x = (e.clientX - r.left) / r.width;
    au.currentTime = Math.max(0, Math.min(1, x)) * au.duration;
    updatePvTime();
  };
}

/* ================= 听歌识曲 ================= */

// 不知道歌名的时候用：录一段（或丢个视频进来），让 Shazam 认。
// 认出来直接当搜索词去搜，省掉自己敲歌名。
const REC_SECONDS = 10;      // 录多久。10 秒足够认，再长只是白等
const REC_MAX_MB = 20;       // 上传上限，跟后端对齐

let recStream = null;        // 麦克风流 / 电脑声音流
let recMr = null;            // MediaRecorder
let recChunks = [];
let recTimer = null;
let recLeft = 0;
let recBusy = false;         // 正在识别，别让人连点
let recAbort = false;        // 半路取消：录下的那段别拿去识别

// 从哪儿录：mic = 麦克风对着外放，sys = 直接抓电脑里正在放的声音。
// 记住上回的选择：常录电脑声音的人不用每次再点一下。
function recSavedSource() {
  try {
    return localStorage.getItem("vdl.rec.src") === "sys" ? "sys" : "mic";
  } catch (e) {
    return "mic";
  }
}
let recSource = recSavedSource();

function recListenTip() {
  return (recSource === "sys" ? "正在听电脑的声音… " : "正在听… ") + recLeft + " 秒";
}

function recOpen(on) {
  const bar = $("#recBar");
  if (!bar) return;
  bar.hidden = !on;
  const btn = $("#mRec");
  if (btn) btn.classList.toggle("on", on);
  if (on) {
    recIdle();
  } else {
    recCleanup();
  }
}

// 平时：提示 + 两个入口
function recIdle() {
  document.querySelectorAll("#recSrc .rs").forEach((b) =>
    b.classList.toggle("on", b.dataset.r === recSource));
  recSetButtons({ act: "录一段", pick: true, x: "取消" });
  recSetTip(recSource === "sys"
    ? "录电脑里正在放的声音：点「录一段」，在弹窗里选「整个屏幕」，把「分享音频」勾上"
    : "对着手机或音箱录一段，也可以选个视频/音频文件", "");
}

// 换录哪儿的来源；录着的时候不让换，免得半路换源录出半截
function recPickSource(v) {
  if (recStream) return;
  recSource = v === "sys" ? "sys" : "mic";
  try { localStorage.setItem("vdl.rec.src", recSource); } catch (e) {}
  document.querySelectorAll("#recSrc .rs").forEach((b) =>
    b.classList.toggle("on", b.dataset.r === recSource));
  recIdle();
}

function recSetTip(text, kind) {
  const t = $("#recTip");
  if (t) t.textContent = text;
  const d = $("#recDot");
  if (d) d.className = "rcdot" + (kind ? " " + kind : "");
}

function recSetButtons(o) {
  const act = $("#recAct"), pick = $("#recPick"), x = $("#recX");
  if (act) {
    act.hidden = !o.act;
    if (o.act) act.textContent = o.act;
  }
  if (pick) pick.hidden = !o.pick;
  if (x) {
    x.hidden = !o.x;
    if (o.x) x.textContent = o.x;
  }
  // 录着的时候锁住来源，但别把它藏起来（藏了布局会跳）
  recLockSource(!!recStream);
}

function recLockSource(locked) {
  const src = $("#recSrc");
  if (!src) return;
  src.classList.toggle("busy", locked);
  src.querySelectorAll("button").forEach((b) => { b.disabled = locked; });
}

function recCleanup() {
  if (recTimer) { clearInterval(recTimer); recTimer = null; }
  recAbort = true;                 // 收工了，正在录的那段别再去识别
  try { if (recMr && recMr.state !== "inactive") recMr.stop(); } catch (e) {}
  if (recStream) {
    recStream.getTracks().forEach((t) => t.stop());
    recStream = null;
  }
  recMr = null;
  recChunks = [];
  recBusy = false;
  recLockSource(false);        // 收工了把来源解锁，别让它一直灰着
}

async function recStart() {
  if (recStream) { recStop(); return; }        // 再点一下就是停
  if (!navigator.mediaDevices || !window.MediaRecorder) {
    recSetTip("这个浏览器录不了音，用「选文件」吧", "err");
    return;
  }
  const stream = await recOpenStream();
  if (!stream) { recIdle2(); return; }          // 失败原因已经在 recOpenStream 里说了
  recStream = stream;
  recChunks = [];
  recAbort = false;
  const canOpus = window.MediaRecorder.isTypeSupported
    && MediaRecorder.isTypeSupported("audio/webm;codecs=opus");
  try {
    recMr = new MediaRecorder(recStream, canOpus ? { mimeType: "audio/webm;codecs=opus" } : undefined);
  } catch (e) {
    recSetTip("录音没起来，用「选文件」吧", "err");
    recCleanup();
    return;
  }
  recMr.ondataavailable = (e) => { if (e.data && e.data.size) recChunks.push(e.data); };
  recMr.onstop = () => {
    if (recAbort) { recAbort = false; return; }
    const blob = new Blob(recChunks, { type: (recMr && recMr.mimeType) || "audio/webm" });
    recReleaseMic();
    if (blob.size < 2048) {
      recSetTip("这段太短了，没录到声音。再来一次", "err");
      recIdle2();
      return;
    }
    recUpload(blob, "webm");
  };
  recMr.start();
  recLeft = REC_SECONDS;
  recSetButtons({ act: "停止", pick: false, x: "取消" });
  recSetTip(recListenTip(), "rec");
  recTimer = setInterval(() => {
    recLeft -= 1;
    if (recLeft <= 0) { recStop(); return; }
    recSetTip(recListenTip(), "rec");
  }, 1000);
}

// 按来源取流：麦克风直接要；电脑声音得先让用户选共享哪一个窗口/屏幕。
// 系统内录在浏览器里只有这一条路（getDisplayMedia 的音频轨道），
// Windows 上选「整个屏幕」并勾「分享音频」就能录到全系统声音。
async function recOpenStream() {
  if (recSource !== "sys") {
    try {
      return await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (e) {
      recSetTip("没拿到麦克风。点地址栏左边的图标允许一下，或者换「系统声音」/「选文件」", "err");
      return null;
    }
  }
  if (!navigator.mediaDevices.getDisplayMedia) {
    recSetTip("这个浏览器录不了电脑的声音，换「麦克风」或「选文件」", "err");
    return null;
  }
  let cap = null;
  try {
    // 必须连视频一起要：Chrome 的分享选择器只有在带了画面时才弹，
    // 拿到手立刻把画面轨道扔掉，只留声音。
    cap = await navigator.mediaDevices.getDisplayMedia({
      video: true,
      audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false },
    });
  } catch (e) {
    // 用户自己关掉选择器不算出错，别拿红字吓人：回到「可以再来一次」
    if (e && e.name === "NotAllowedError") { recIdle(); return null; }
    recSetTip("没能开始录电脑的声音，再点一次试试", "err");
    return null;
  }
  const audios = cap.getAudioTracks();
  cap.getVideoTracks().forEach((t) => { t.stop(); cap.removeTrack(t); });
  if (!audios.length) {
    cap.getTracks().forEach((t) => t.stop());
    recSetTip("刚才选的那个没带声音。再点一次，选「整个屏幕」并勾上「分享音频」；"
      + "Mac 上只能连标签页的声音，要选正在放歌的那个标签页", "err");
    return null;
  }
  // 用户在分享条上按「停止共享」时，也跟着把录音收尾
  audios[0].onended = () => {
    if (recMr && recMr.state !== "inactive") recStop();
  };
  return new MediaStream(audios);
}

function recStop() {
  if (recTimer) { clearInterval(recTimer); recTimer = null; }
  if (recMr && recMr.state !== "inactive") recMr.stop();
}

function recReleaseMic() {
  if (recStream) {
    recStream.getTracks().forEach((t) => t.stop());
    recStream = null;
  }
}

// 出错后回到「可以再来一次」
function recIdle2() {
  recSetButtons({ act: "录一段", pick: true, x: "取消" });
}

function b64Of(buf) {
  // 分块转，一次性 apply 会爆栈
  const u8 = new Uint8Array(buf);
  let s = "";
  const CH = 0x8000;
  for (let i = 0; i < u8.length; i += CH) {
    s += String.fromCharCode.apply(null, u8.subarray(i, i + CH));
  }
  return btoa(s);
}

async function recUpload(blob, ext) {
  if (recBusy) return;
  recBusy = true;
  recSetButtons({ act: "", pick: false, x: "取消" });
  recSetTip("在找这首歌…", "rec");
  try {
    const b64 = b64Of(await blob.arrayBuffer());
    const r = await post("/api/music/recognize", { data: b64, ext });
    if (!r.ok) {
      recSetTip(r.error || "没认出来，再来一次", "err");
      recIdle2();
      return;
    }
    recOpen(false);
    // 认出来了：搜索框填上，直接搜。
    // 先切回音乐来源 —— 视频页的搜索用的是另一套接口，歌名丢进去没意义
    if (mMode !== "music") {
      document.querySelectorAll("#mSrc .plat").forEach((x) =>
        x.classList.toggle("on", x.dataset.s === "music"));
      mMode = "music";
      $("#mPlats").hidden = false;
      $("#mVPlats").hidden = true;
      $("#mQuery").placeholder = "歌名、歌手";
    }
    $("#mQuery").value = r.query || ((r.title || "") + " " + (r.artist || "")).trim();
    resetResults();
    const who = [r.title, r.artist].filter(Boolean).join(" · ");
    await musicSearch($("#mQuery").value);
    // 搜索过程会把提示刷成「搜索中…」，搜完再把「听出来了」补回去 ——
    // 不然后面看这屏的人不知道这些结果是听出来的
    $("#mHint").textContent = mItems.length
      ? "听出来了：" + who
      : "听出来了「" + who + "」，但国内几个平台都没搜到（多半是版权）";
  } catch (e) {
    recSetTip("识别失败了，再试一次", "err");
    recIdle2();
  } finally {
    recBusy = false;
  }
}

function recPickFile() {
  const inp = $("#recFile");
  if (inp) inp.click();
}

function initRecognize() {
  const btn = $("#mRec");
  if (!btn) return;
  btn.onclick = () => {
    const bar = $("#recBar");
    recOpen(!bar || bar.hidden);
  };
  const act = $("#recAct");
  if (act) act.onclick = () => { recStart(); };
  document.querySelectorAll("#recSrc .rs").forEach((b) => {
    b.onclick = () => recPickSource(b.dataset.r);
  });
  const pick = $("#recPick");
  if (pick) pick.onclick = recPickFile;
  const x = $("#recX");
  if (x) x.onclick = () => recOpen(false);
  const file = $("#recFile");
  if (file) {
    file.onchange = () => {
      const f = file.files && file.files[0];
      file.value = "";                       // 同一个文件再选一次也要能触发
      if (!f) return;
      if (f.size > REC_MAX_MB * 1024 * 1024) {
        recSetTip("文件太大了（超过 " + REC_MAX_MB + "MB）。截一小段，或者直接录音",
          "err");
        return;
      }
      const ext = (f.name.split(".").pop() || "").toLowerCase();
      recUpload(f, ext || "mp3");
    };
  }
}

/* ================= 电影条目卡 ================= */

/* 封面 / 海报的地址。
   B站图床浏览器直连会被 Chrome 的 ORB 拦掉（net::ERR_BLOCKED_BY_ORB），
   豆瓣图床有防盗链 —— 这两类统一走服务端代取，
   服务端还会顺手把 B站原图压成小图（原图一张 1.8MB，列表里扛不住）。 */
const IMG_PROXY_HOST = /(^|\.)(hdslb|doubanio|bilibili)\.com/i;
function imgUrl(u) {
  if (!u) return "";
  // 网易云封面原图动辄几百 KB，列表里缩一下够看就行
  if (/music\.126\.net/.test(u) && u.indexOf("param=") < 0) u += "?param=200y200";
  // YouTube 的 hq720 换成 mqdefault，小一圈
  if (/ytimg\.com/.test(u) && u.indexOf("hq720.jpg") > 0) {
    u = u.replace("hq720.jpg", "mqdefault.jpg");
  }
  if (IMG_PROXY_HOST.test(u)) return "/api/proxy_img?u=" + encodeURIComponent(u);
  return u;
}

function movieCard(m, i) {
  const cov = m.cover
    ? '<span class="poster"><img src="' + esc(imgUrl(m.cover)) + '" alt="" loading="lazy"'
      + ' onerror="this.remove()"></span>'
    : "";
  const rate = m.rating
    ? '<span class="mrate">' + esc(String(m.rating)) + " 分</span>"
    : "";
  const bits = [];
  if (m.year) bits.push(esc(m.year));
  if (m.genres && m.genres.length) bits.push(esc(m.genres.join(" / ")));
  if (m.duration) bits.push(esc(m.duration));
  if (m.region) bits.push(esc(m.region));
  const crew = [];
  if (m.director) crew.push("导演 " + esc(m.director));
  if (m.actors) crew.push("主演 " + esc(m.actors));

  const b = m.bili;
  let tags = "";
  if (b) {
    tags += '<span class="mtag" data-k="ok">B站有正版 · ' + esc(b.kind || "") + "</span>";
    if (b.vip) tags += '<span class="mtag" data-k="vip">大会员</span>';
    if (b.ep_size) tags += '<span class="mtag">' + esc(String(b.ep_size)) + " 集</span>";
    if (b.score) tags += '<span class="mtag">站内 ' + esc(String(b.score)) + " 分</span>";
  } else {
    tags += '<span class="mtag">B站暂无正版</span>';
  }

  let acts = "";
  if (b && b.dl_url) {
    acts += '<button class="mbtn primary" data-act="dl" data-i="' + i + '">下载正片</button>';
  }
  if (b && b.ep_size) {
    acts += '<button class="mbtn" data-act="eps" data-i="' + i + '">选集</button>';
  }
  if (b && b.url) {
    acts += '<button class="mbtn" data-act="open" data-i="' + i + '">在B站看</button>';
  }
  acts += '<button class="mbtn" data-act="mat" data-i="' + i + '">找解说素材</button>';
  if (m.url) acts += '<button class="mbtn" data-act="db" data-i="' + i + '">豆瓣详情</button>';

  return '<div class="mmovie">' + cov +
    '<div class="mbody"><div class="mtop"><span class="mt">' + esc(m.title || "未命名") + "</span>" +
    '<span class="my">' + (m.year ? esc(m.year) : "") + "</span>" + rate + "</div>" +
    (bits.length ? '<div class="mmeta">' + bits.join(" · ") + "</div>" : "") +
    (crew.length ? '<div class="mmeta">' + crew.join(" · ") + "</div>" : "") +
    (m.summary ? '<div class="mline">' + esc(m.summary) + "</div>" : "") +
    '<div class="mactions">' + tags + acts + "</div>" +
    "</div></div>";
}

function renderMovies() {
  const box = $("#mResults");
  if (!mItems.length) {
    box.innerHTML = '<div class="mempty">没搜到这部片子，换个简称试试</div>';
    return;
  }
  box.innerHTML = mItems.map(movieCard).join("");
  box.scrollTop = 0;
  box.querySelectorAll(".mbtn").forEach((b) => {
    b.onclick = () => movieAction(b.dataset.act, +b.dataset.i, b);
  });
}

async function movieAction(act, i, btn) {
  const m = mItems[i];
  if (!m) return;
  if (act === "open") {
    if (m.bili && m.bili.url) window.open(m.bili.url, "_blank", "noopener");
    return;
  }
  if (act === "db") {
    if (m.url) window.open(m.url, "_blank", "noopener");
    return;
  }
  if (act === "mat") {
    // 跳到视频搜索，找这部片的解说/花絮/混剪
    const t = (m.title || "").split(" ")[0];
    document.querySelectorAll("#mSrc .plat").forEach((x) =>
      x.classList.toggle("on", x.dataset.s === "video"));
    mMode = "video";
    $("#mPlats").hidden = true;
    $("#mVPlats").hidden = false;
    $("#mBar").hidden = true;
    $("#mQuery").placeholder = "视频关键词：电影解说、运镜、猫咪…";
    $("#mQuery").value = t + " 解说";
    musicSearch(t + " 解说");
    return;
  }
  if (act === "dl") {
    const u = m.bili && (m.bili.dl_url || m.bili.url);
    if (!u) return;
    const old = btn ? btn.textContent : "";
    if (btn) { btn.disabled = true; btn.textContent = "…"; }
    // 带上片名：番剧页 yt-dlp 只给「正片」，用我们查到的真名
    await submitBlocks([{ text: u, title: m.title || "" }]);
    if (btn) {
      btn.disabled = false;
      btn.textContent = "已加入";
      setTimeout(() => { btn.textContent = old; }, 1600);
    }
    return;
  }
  if (act === "eps") {
    if (!m.bili || !m.bili.url) return;
    const old = btn ? btn.textContent : "";
    if (btn) { btn.disabled = true; btn.textContent = "…"; }
    try {
      const r = await post("/api/movie/episodes", { url: m.bili.url, limit: 40 });
      if (!r.ok || !(r.items || []).length) {
        toast(r.error || "没取到集数，正片页可以直接下");
        return;
      }
      showEpisodes(m, r.items);
    } catch (e) {
      toast("取集数失败，稍后再试");
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = old; }
    }
  }
}

/* 选集弹层：列出每一集，点一下就加进任务 */
function showEpisodes(m, eps) {
  const old = $("#mEps");
  if (old) old.remove();
  const box = document.createElement("div");
  box.id = "mEps";
  box.className = "meps";
  box.innerHTML = '<div class="meps-box"><div class="meps-hd"><b>' + esc(m.title || "") + "</b>" +
    '<button class="meps-x" title="关闭">×</button></div>' +
    '<div class="meps-list">' + eps.map((e, i) =>
      '<div class="mep"><span class="mep-n">' + esc(e.index || (i + 1)) + "</span>" +
      '<span class="mep-t">' + esc(e.title || "") + "</span>" +
      '<button class="mep-dl" data-u="' + esc(e.url) + '" data-t="' +
      esc((m.title || "") + " " + (e.index || "") + " " + (e.title || "")) + '">下</button></div>'
    ).join("") + "</div>" +
    '<div class="meps-ft"><button class="mep-all">全下（' + eps.length + " 集）</button></div></div>";
  document.body.appendChild(box);
  box.querySelector(".meps-x").onclick = () => box.remove();
  box.querySelectorAll(".mep-dl").forEach((b) => {
    b.onclick = async () => {
      b.disabled = true;
      b.textContent = "…";
      await submitBlocks([{ text: b.dataset.u, title: b.dataset.t }]);
      b.disabled = false;
      b.textContent = "已加";
    };
  });
  const all = box.querySelector(".mep-all");
  if (all) {
    all.onclick = async () => {
      all.disabled = true;
      all.textContent = "加入中…";
      await submitBlocks(eps.map((e, i) => ({
        text: e.url,
        title: (m.title || "") + " " + (e.index || "") + " " + (e.title || "")
      })));
      all.textContent = "已加入 " + eps.length + " 集";
      setTimeout(() => { all.disabled = false; }, 1500);
    };
  }
}

async function movieSearch(q) {
  q = (q || $("#mQuery").value || "").trim();
  if (!q) { $("#mQuery").focus(); return; }
  $("#mResults").innerHTML = "";
  $("#mBar").hidden = true;
  $("#mHint").textContent = `电影搜索「${q}」…`;
  searching = true;
  $("#mGo").disabled = true;
  try {
    const r = await post("/api/movie/search", { q, limit: 10 });
    if (!r.ok) { $("#mHint").textContent = r.error || "搜索失败"; return; }
    mItems = r.items || [];
    renderMovies();
    const errs = (r.errors || []).join("；");
    $("#mHint").textContent = mItems.length ? errs : (errs || "没搜到，换个片名或简称试试");
  } catch (e) {
    $("#mHint").textContent = "没连上，稍等一下再搜一次";
  } finally {
    searching = false;
    $("#mGo").disabled = false;
  }
}

async function musicSearch(q) {
  q = (q || $("#mQuery").value || "").trim();
  if (!q) { $("#mQuery").focus(); return; }
  if (mMode === "movie") return movieSearch(q);
  $("#mResults").innerHTML = "";
  $("#mBar").hidden = true;
  const where = mMode === "video"
    ? (vPlat === "all" ? "所有来源" : sourceName(vPlat))
    : (PLAT_NAME[mPlat] || "全网");
  $("#mHint").textContent = `${where} 搜索「${q}」…`;
  mSel.clear();
  if (mMode === "video") {
    vLastQuery = q;
    clearTimeout(vPullTimer);
    vToken = "";
    const sh = $("#vSrcHint");
    if (sh) { sh.innerHTML = ""; sh.hidden = true; }
  }
  searching = true;
  $("#mGo").disabled = true;
  try {
    const r = mMode === "video"
      ? await post("/api/search", {
          q, limit: 24,
          sources: vPlat === "all" ? null : [vPlat],
        })
      : await post("/api/music/search", { platform: mPlat, q, limit: 24 });
    if (!r.ok) { $("#mHint").textContent = r.error || "搜索失败"; return; }
    mItems = r.items || [];
    renderMusic();
    // 各来源搜到几条、谁没连上，单独一行说清楚
    if (mMode === "video") { renderSourceHint(r); scheduleMore(r); }
    const errs = (r.errors || []).join("；");
    $("#mHint").textContent = mItems.length ? errs : (errs || "没搜到，换个关键词或换个来源");
  } catch (e) {
    $("#mHint").textContent = "没连上，稍等一下再搜一次";
  } finally {
    searching = false;
    $("#mGo").disabled = false;
  }
}

async function addMusic(list, btn) {
  if (!list.length) return;
  // 视频结果：把链接丢回普通下载管线，跟贴链接一个待遇
  if (list.every((x) => x.kind === "video" || x.kind === "audio")) {
    const old = btn ? btn.textContent : "";
    if (btn) { btn.disabled = true; btn.textContent = "…"; }
    try {
      // 素材站给的是可直接下的 mp4 / mp3 直链，B站那些才是页面链接
      await submitBlocks(list.map((x) => ({ text: x.download || x.url })));
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = old; }
    }
    return;
  }
  await ensureDir();
  const old = btn ? btn.textContent : "";
  if (btn) { btn.disabled = true; btn.textContent = "…"; }
  try {
    const r = await post("/api/music/add", { items: list });
    if (r.ok) {
      // 记下「哪首歌对应哪个任务」，搜索结果行里才能直接显示进度和结果
      (r.pairs || []).forEach((p) => {
        const key = p.platform + "|" + p.song;
        const rec = mTask.get(key) || {};
        rec.tid = p.tid;
        mTask.set(key, rec);
        mNote.delete(key);
      });
      const skipped = r.skipped || [];
      skipped.forEach((s) => {
        const key = s.platform + "|" + s.song;
        if (s.tid) {
          // 已经有这条任务了：跟着那条任务走，状态交给轮询填
          const st = s.status || "";
          mTask.set(key, {
            tid: String(s.tid),
            t: st ? { id: String(s.tid), status: st, percent: st === "done" ? 100 : 0 } : null,
          });
          mNote.delete(key);
        } else {
          // 以前下过、文件还在历史里
          mNote.set(key, { text: "以前下过", kind: "dim" });
        }
      });

      let msg = r.count ? "已加入 " + r.count + " 首" : "";
      if (skipped.length) {
        const done = skipped.filter((s) => s.status === "done").length;
        if (done === skipped.length) {
          msg += (msg ? "，" : "") + done + " 首早就下好了";
        } else {
          msg += (msg ? "，" : "") + skipped.length + " 首没加："
            + (skipped[0].reason || "已经在列表里了");
          if (skipped.length > 1) msg += " 等";
        }
      }
      toast(msg || "没有可添加的", skipped.length ? 4200 : 2400);
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
  // 搜索框被清空了，上一个关键词的结果就该跟着收起来，
  // 不然会留着一屏旧结果看着像没反应
  $("#mQuery").addEventListener("input", (e) => {
    if (e.isComposing) return;
    if ($("#mQuery").value.trim()) return;
    resetResults();
    $("#mHint").textContent = SOURCE_GUIDE[mMode] || "";
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
  // 跟搜索结果一样用 span 装着 img，图没出来时占位还在
  const cov = '<span class="cov" data-p="' + esc(x.platform || "") + '">'
    + (x.thumb ? '<img src="' + esc(imgUrl(x.thumb)) + '" alt="" loading="lazy"'
      + ' onerror="this.remove()">' : "")
    + "</span>";
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
  closeAllDrawers(true);
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

/* Esc：在高级页先退回常用页，再按一次才关 —— 和浏览器后退一个习惯 */
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  const adv = $("#setAdv");
  if (adv && !adv.hidden) { setPane(false); return; }
  closeAllDrawers();
});

applyTab(tabMode);
initPreview();
initRecognize();
// 第一次进搜索页，先给句引导，别让人对着空白框发呆
if (tabMode === "music") {
  const h = $("#mHint");
  if (h && !h.textContent) h.textContent = SOURCE_GUIDE[mMode] || "";
}


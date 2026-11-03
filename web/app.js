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
  return null;
}

function renderDetected(urls) {
  const box = $("#detected");
  box.innerHTML = "";
  if (!urls || !urls.length) {
    const el = document.createElement("span");
    el.className = "ghost";
    el.textContent = window.matchMedia("(hover: none)").matches
      ? "粘贴链接即可"
      : (autoPaste ? "Ctrl+V 粘贴" : "Ctrl+V 粘贴后点开始");
    box.appendChild(el);
    return;
  }
  const count = {};
  urls.forEach((u) => {
    const n = platformOf(u) || "未知";
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

const ICON_ALIAS = { weixin: "wechatchannels" };

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

function updateCard(el, t) {
  el.classList.toggle("done", t.status === "done");
  el.classList.toggle("running", t.status === "running" || t.status === "queued");
  el.classList.toggle("canceled", t.status === "canceled");
  el.classList.toggle("error", t.status === "error");

  const title = el.querySelector(".title");
  const text = t.title || t.stage || t.url;
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
  if (t.url) {
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
    line = `<span class="err-msg">${esc(t.error || "失败")}</span>`;
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

function renderStats(tasks) {
  const box = $("#stats");
  const c = { queued: 0, running: 0, done: 0, error: 0 };
  tasks.forEach((t) => { if (c[t.status] !== undefined) c[t.status]++; });
  const live = c.running + c.queued;
  const ca = $("#cancelAll");
  const cb = $("#clearBtn");
  const lhRight = document.querySelector(".list-head .lh-right");
  if (lhRight) lhRight.style.display = tasks.length ? "" : "none";
  if (ca) ca.disabled = live === 0;
  if (cb) cb.disabled = c.done + tasks.filter((t) => t.status === "canceled").length === 0;

  if (!tasks.length) { box.textContent = ""; return; }
  const parts = [`共 ${tasks.length}`];
  if (c.done) parts.push(`完成 ${c.done}`);
  if (c.running) parts.push(`下载中 ${c.running}`);
  if (c.queued) parts.push(`排队 ${c.queued}`);
  if (c.error) parts.push(`失败 ${c.error}`);
  box.textContent = parts.join(" · ");
  box.classList.toggle("warn", c.error > 0);
}

function render(tasks) {
  const box = $("#tasks");
  renderStats(tasks);
  const bs = new Set(tasks.map((t) => t.batch).filter(Boolean));
  multiBatch = bs.size > 1;      // 只有一批时不显示「第 N 批」

  if (!tasks.length) {
    if (!box.querySelector(".empty")) {
      box.innerHTML = '<div class="empty"><span class="e-main">还没有任务</span>'
        + '<span class="e-sub">在上面粘贴链接，下载会自动开始</span></div>';
    }
    selMap.clear();
    return;
  }
  const empty = box.querySelector(".empty");
  if (empty) empty.remove();

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
}

async function post(url, body) {
  const r = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  return r.json();
}

async function poll() {
  try {
    const r = await fetch("/api/state");
    const d = await r.json();
    applyMode(d.mode);
    render(d.tasks || []);
    cfg = d.config || {};
    renderSupport(d.platforms || []);
  } catch (e) {
    // 服务已关闭
  }
}

/* ---------- 独立窗口模式：显示退出按钮，关窗即退出软件 ---------- */
let modeDone = false;
function applyMode(mode) {
  if (modeDone) return;
  modeDone = true;
  if (mode !== "app") return;
  document.body.classList.add("app-mode");
  const q = $("#quitApp");
  if (q) q.hidden = false;
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
  box.innerHTML = '<span class="label">支持</span>';
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

  const more = document.createElement("button");
  more.type = "button";
  more.className = "more";
  box.appendChild(more);
  more.onclick = () => {
    const opened = box.classList.toggle("open");
    if (opened) {
      box.querySelectorAll(".tag").forEach((t) => { t.hidden = false; });
      more.textContent = "收起";
      more.title = "";
    } else {
      more.textContent = "";
      fitSupport();
    }
  };
  fitSupport();
}

/* 一行放不下就收起末尾几个，显示 +N */
function fitSupport() {
  const box = $("#support");
  if (!box) return;
  const more = box.querySelector(".more");
  const tags = [...box.querySelectorAll(".tag")];
  if (!more || !tags.length) return;
  if (box.classList.contains("open")) return;

  tags.forEach((t) => { t.hidden = false; });
  more.hidden = false;
  more.textContent = "";
  const over = () => box.scrollWidth > box.clientWidth + 1;
  let n = tags.length;
  while (n > 3 && over()) { tags[n - 1].hidden = true; n--; }
  const k = tags.length - n;
  if (k > 0) {
    more.textContent = "+" + k;
    more.title = "还有 " + tags.slice(n).map((t) => t.textContent).join("、");
  } else {
    more.hidden = true;
  }
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
  if (!text) { toast("先贴一个链接"); return; }
  const urls = urlsIn(text);
  if (!urls.length) { toast("没找到链接"); return; }
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
  if ((e.ctrlKey || e.metaKey) && e.key === "Enter") { e.preventDefault(); submitBox(); }
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
  if (!box) return;
  const busy = status.state === "opening" || status.state === "waiting";
  box.innerHTML = "";
  sites.forEach((s) => {
    const row = document.createElement("div");
    row.className = "login-row";
    const running = busy && status.platform === s.id;
    const stCls = running ? "st run" : (s.logged_in ? "st ok" : "st");
    const stText = running ? "等待登录…" : (s.logged_in ? "已登录" : "未登录");
    const need = s.required ? '<span class="need req">必须</span>' : '<span class="need">可选</span>';
    row.innerHTML = `<span class="nm">${esc(s.name)}</span>${need}`
      + `<span class="${stCls}">${stText}</span>`
      + `<button>${s.logged_in ? "重新登录" : "登录"}</button>`;
    row.title = s.tip || "";
    const btn = row.querySelector("button");
    btn.disabled = busy;
    btn.onclick = () => startLogin(s.id);
    box.appendChild(row);
  });

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
  if (status.state === "opening" || status.state === "waiting") {
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
  $("#mask").classList.add("on");
  $("#drawer").classList.add("on");
  $("#cfgOut").value = cfg.out_dir || "";
  $("#cfgQuality").value = cfg.quality || "best";
  $("#cfgProxy").value = cfg.proxy || "";
  $("#cfgCookies").value = cfg.cookies_file || "";
  $("#cfgSph").value = cfg.sph_api || "";
  $("#cfgConc").value = String(cfg.concurrency || 3);
  $("#cfgNameTpl").value = cfg.name_tpl || "";
  $("#cfgMusicDir").value = cfg.music_dir || "";
  $("#cfgCover").checked = !!cfg.save_cover;
  $("#cfgSubs").checked = !!cfg.down_subs;
  loadLogins(true);
}

function closeDrawer() {
  $("#mask").classList.remove("on");
  $("#drawer").classList.remove("on");
}

$("#openDir").onclick = async () => {
  const r = await post("/api/open", {});
  if (!r.ok) toast(r.error || "打不开");
};

$("#openSet").onclick = openDrawer;
$("#closeSet").onclick = closeDrawer;
$("#mask").onclick = closeDrawer;

$("#pickOut").onclick = async () => {
  const p = await openPicker($("#pickOut"), "浏览…");
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
        "已退出，可以把窗口关掉了</div>";
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
    name_tpl: $("#cfgNameTpl").value.trim(),
    music_dir: $("#cfgMusicDir").value.trim(),
    save_cover: $("#cfgCover").checked,
    down_subs: $("#cfgSubs").checked,
  });
  if (r.ok) { cfg = r.config; toast("已保存"); closeDrawer(); }
  else toast(r.error || "保存失败", 3600);
};

$("#clearBtn").onclick = async () => { await post("/api/clear", {}); poll(); };

$("#cancelAll").onclick = async () => {
  const r = await post("/api/cancel_all", {});
  toast(r.count ? `已取消 ${r.count} 条` : "没有正在跑的任务");
  poll();
};

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeDrawer();
});

/* 窗口变宽变窄时，重新算平台标签能放几个 */
let supportT = 0;
window.addEventListener("resize", () => {
  clearTimeout(supportT);
  supportT = setTimeout(() => {
    const box = $("#support");
    if (box && box.classList.contains("open")) box.classList.remove("open");
    fitSupport();
  }, 120);
});

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
// 窗口最小化 / 切到后台时不刷，省点 CPU
setInterval(() => { if (!document.hidden) poll(); }, 800);
document.addEventListener("visibilitychange", () => { if (!document.hidden) poll(); });
setInterval(() => {
  if ($("#drawer").classList.contains("on")) loadLogins();
}, 1500);

/* ================= 视频 / 音乐 切换 ================= */
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
  const sup = $("#support");
  if (box) box.hidden = m !== "video";
  if (sup) sup.hidden = m !== "video";
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

/* ================= 音乐搜索 ================= */
let mItems = [];

function musicRow(s, i) {
  const cov = s.cover
    ? '<img class="cov" src="' + esc(s.cover) + '" alt="" loading="lazy" onerror="this.style.visibility=\'hidden\'">'
    : '<span class="cov"></span>';
  const sub = esc(s.artist || "未知歌手") + (s.album ? " · " + esc(s.album) : "");
  return '<div class="msong">' + cov +
    '<div class="meta"><div class="t">' + esc(s.title || "未知曲目") + "</div>" +
    '<div class="a">' + sub + "</div></div>" +
    '<span class="d">' + (s.duration ? fmtDur(s.duration) : "") + "</span>" +
    '<button class="dl" data-i="' + i + '">下</button></div>';
}

function renderMusic() {
  const box = $("#mResults");
  if (!mItems.length) {
    box.innerHTML = '<div class="mempty">没搜到，换个关键词试试</div>';
    return;
  }
  box.innerHTML = mItems.map(musicRow).join("");
  box.querySelectorAll(".dl").forEach((b) => {
    b.onclick = () => addMusic([mItems[+b.dataset.i]], b);
  });
}

async function musicSearch() {
  const q = $("#mQuery").value.trim();
  if (!q) { $("#mQuery").focus(); return; }
  const btn = $("#mGo");
  btn.disabled = true;
  $("#mHint").textContent = "搜索中…";
  $("#mResults").innerHTML = "";
  try {
    const r = await post("/api/music/search", {
      platform: $("#mPlatform").value, q, limit: 24,
    });
    if (!r.ok) { $("#mHint").textContent = r.error || "搜索失败"; return; }
    mItems = r.items || [];
    renderMusic();
    $("#mHint").textContent = mItems.length
      ? "共 " + mItems.length + " 首，点「下」开始下载"
      : "";
  } catch (e) {
    $("#mHint").textContent = "搜索失败，服务可能已经关了";
  } finally {
    btn.disabled = false;
  }
}

async function addMusic(list, btn) {
  if (!list.length) return;
  await ensureDir();
  if (btn) { btn.disabled = true; btn.textContent = "…"; }
  try {
    const r = await post("/api/music/add", { items: list });
    if (r.ok) toast("已加入 " + r.count + " 首");
    else toast(r.error || "加入失败");
    poll();
  } catch (e) {
    toast("连接失败");
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = "下"; }
  }
}

if ($("#mGo")) $("#mGo").onclick = musicSearch;
if ($("#mQuery")) {
  $("#mQuery").addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); musicSearch(); }
  });
}

/* ================= 历史 ================= */
let histItems = [];

function histRow(x, i) {
  const cov = x.thumb
    ? '<img class="cov" src="' + esc(x.thumb) + '" alt="" loading="lazy" onerror="this.style.visibility=\'hidden\'">'
    : '<span class="cov"></span>';
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
  $("#mask").classList.add("on");
  $("#histDrawer").classList.add("on");
  loadHistory($("#histQ").value.trim());
}

function closeHist() {
  $("#histDrawer").classList.remove("on");
  $("#mask").classList.remove("on");
}

if ($("#openHist")) $("#openHist").onclick = openHist;
if ($("#closeHist")) $("#closeHist").onclick = closeHist;
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
$("#mask").onclick = () => { closeDrawer(); closeHist(); };
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeHist();
});

applyTab(tabMode);

if ($("#pickMusic")) {
  $("#pickMusic").onclick = async () => {
    const p = await openPicker($("#pickMusic"), "浏览…");
    if (!p) return;
    $("#cfgMusicDir").value = p;
    toast("点右上「保存」生效");
  };
}

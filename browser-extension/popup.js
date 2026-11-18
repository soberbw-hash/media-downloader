const $ = s => document.querySelector(s);

async function render() {
  const d = await chrome.storage.local.get(["lastSync", "lastMsg", "auto"]);
  $("#auto").checked = d.auto !== false;   // 没设置过 = 默认开
  if (d.lastSync) {
    const t = new Date(d.lastSync).toLocaleTimeString("zh-CN", {
      hour: "2-digit", minute: "2-digit"
    });
    $("#out").textContent = (d.lastMsg || "已同步") + " · " + t;
  }
}

async function doSync(showBtn) {
  if (showBtn) {
    $("#sync").disabled = true;
    $("#out").className = "";
    $("#out").textContent = "正在同步…";
  }
  try {
    const r = await chrome.runtime.sendMessage({ type: "sync" });
    const ok = !!(r && r.ok);
    $("#out").className = ok ? "ok" : "bad";
    $("#out").textContent = (ok ? "✓ " : "✗ ") +
      ((r && r.msg) || (ok ? "同步完成" : "同步失败"));
  } catch (e) {
    $("#out").className = "bad";
    $("#out").textContent = "✗ " + String(e);
  }
  $("#sync").disabled = false;
}

$("#sync").onclick = () => doSync(true);

$("#auto").onchange = async e => {
  try {
    await chrome.runtime.sendMessage({ type: "auto", on: e.target.checked });
  } catch (err) {
    $("#out").textContent = "设置没保存：" + String(err);
  }
};

/* 点开图标就顺手同步一次：重载扩展之后不用再琢磨点哪个按钮。
   刚同步过 5 秒内的不重复跑，免得来回点把列表刷花。 */
async function autoSync() {
  const d = await chrome.storage.local.get(["auto", "lastSync"]);
  if (d.auto === false) return;
  if (d.lastSync && Date.now() - d.lastSync < 5000) return;
  await doSync(false);
}

render().then(autoSync);

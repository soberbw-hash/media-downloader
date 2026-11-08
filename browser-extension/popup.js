const $ = s => document.querySelector(s);

async function render() {
  const d = await chrome.storage.local.get(["lastSync", "lastMsg", "auto"]);
  $("#auto").checked = !!d.auto;
  if (d.lastSync) {
    const t = new Date(d.lastSync).toLocaleTimeString("zh-CN", {
      hour: "2-digit", minute: "2-digit"
    });
    $("#out").textContent = (d.lastMsg || "已同步") + " · " + t;
  }
}

$("#sync").onclick = async () => {
  const b = $("#sync");
  b.disabled = true;
  $("#out").className = "";
  $("#out").textContent = "正在同步…";
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
  b.disabled = false;
  render();
};

$("#auto").onchange = async e => {
  try {
    await chrome.runtime.sendMessage({ type: "auto", on: e.target.checked });
  } catch (err) {
    $("#out").textContent = "设置没保存：" + String(err);
  }
};

render();

# -*- coding: utf-8 -*-
"""迟到的聚合结果不许画进新列表。

真机上的现象：视频档「全部」搜完还在后台补齐，这时点「YouTube」，
上一轮 Mixkit 的结果晚一步回来，直接把列表盖住 —— 界面就变成
「YouTube 高亮，底下全是 Mixkit」；切到「电影」也会被盖成视频列表。
根因是补齐请求回来时没确认它属于哪一轮搜索。

这里在页面里构造这个竞态：让 make-believe 的补齐请求「在路上」，
中途换源，再放它回来。不依赖真实网络时序，所以结果稳定。
跑法：python tests/test_late_results.py
"""
import json
import os
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXE = r"C:/Program Files/Google/Chrome/Application/chrome.exe"

# 上一轮（「全部」）的补齐结果，全都带着醒目的记号
LATE = [{"platform": "mixkit", "id": "mk%d" % i, "kind": "video",
         "title": "LATE-APPENDED-%d" % i, "author": "Mixkit",
         "cover": "", "duration": 5, "desc": "1080p"} for i in range(6)]

RACE = """
async (late) => {
  const of = window.fetch;
  // 只有补齐这一条被拦下来，返回上一轮的旧结果
  window.fetch = (u, o) => String(u).includes("/api/search/more")
    ? Promise.resolve(new Response(JSON.stringify({
        ok: true, items: late,
        sources: [{id: "mixkit", name: "Mixkit 视频", state: "ok", count: 6}],
        pending: [], token: "T1", done: true,
      }), {status: 200, headers: {"Content-Type": "application/json"}}))
    : of(u, o);
  vToken = "T1";
  vPulls = 0;
  const flying = pullMore();                       // 请求「在路上」
  document.querySelector('#mVPlats .plat[data-p="youtube"]').click();  // 用户这时换源
  await flying;                                    // 旧结果回来了
  window.fetch = of;
  return {
    text: document.querySelector('#mResults').innerText || "",
    token: vToken,
    on: Array.from(document.querySelectorAll('#mVPlats .plat.on')).map(e => e.textContent),
  };
}
"""

MOVIE = """
(late) => {
  // 切到电影档后，再来一份迟到的视频结果，也不能盖住电影列表
  mMode = "movie";
  mItems = late;
  renderMusic();
  return {
    rows: document.querySelectorAll('#mResults .msong').length,
    text: document.querySelector('#mResults').innerText || "",
  };
}
"""

# 只点了 YouTube、这家又连不上：得说「开梯子」，不能让人以为词写错了
BAD_SRC = """
() => {
  mMode = "video";        // 上一步切到电影了，先回到视频档
  vPlat = "youtube";
  const bad = singleSourceProblem({items: [], pending: [],
    sources: [{id: "youtube", name: "YouTube", state: "fail"}]});
  vPlat = "all";
  return bad;
}
"""


def log(*a):
    print(*a, flush=True)


def main():
    tmp = tempfile.mkdtemp(prefix="vdl_late_")
    env = os.environ.copy()
    env.update({"VDL_FORCE_NEW": "1", "VDL_NO_OPEN": "1",
                "VDL_NO_AUTO_UPDATE": "1", "VDL_DATA_DIR": tmp})
    pf = os.path.join(tmp, "port.txt")
    env["VDL_PORT_FILE"] = pf
    logf = open(os.path.join(tmp, "srv.log"), "wb")
    srv = subprocess.Popen([sys.executable, "server.py", "none"], cwd=ROOT,
                           env=env, stdout=logf, stderr=subprocess.STDOUT)
    try:
        port = 0
        for _ in range(140):
            if os.path.isfile(pf):
                try:
                    port = int(open(pf, encoding="utf-8").read().strip() or 0)
                except (OSError, ValueError):
                    port = 0
            if port:
                break
            time.sleep(0.15)
        assert port, "没起服务"
        log("隔离服务端口:", port)

        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            b = pw.chromium.launch(executable_path=EXE, headless=True)
            pg = b.new_page(viewport={"width": 1180, "height": 900})
            errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)))
            pg.goto("http://127.0.0.1:%d/" % port, wait_until="networkidle", timeout=30000)
            pg.click('#tabs .tab[data-tab="music"]')
            pg.wait_for_timeout(300)
            pg.click('#mSrc .plat[data-s="video"]')
            pg.wait_for_timeout(500)

            res = pg.evaluate(RACE, LATE)
            log("  换源后高亮:", res["on"], "| 列表:", (res["text"] or "")[:50].replace("\n", " / "))
            assert "LATE-APPENDED" not in res["text"], \
                "上一轮的补齐结果盖住了新来源的列表"
            assert res["on"] == ["YouTube"], "源没切过去：%s" % res["on"]
            log("  OK 迟到的补齐结果被丢掉了")

            mv = pg.evaluate(MOVIE, LATE)
            log("  电影档行数:", mv["rows"], "| 列表:", (mv["text"] or "")[:40].replace("\n", " / "))
            assert mv["rows"] == 0 and "LATE-APPENDED" not in mv["text"], \
                "电影档被视频结果盖住了"
            log("  OK 电影档不会被视频结果盖住")

            bad = pg.evaluate(BAD_SRC)
            log("  单源失败提示:", bad)
            assert "梯子" in bad and "关键词" not in bad, \
                "只点了 YouTube 却让人换关键词：%s" % bad
            log("  OK 单源连不上时说清是梯子的事")

            assert not errs, "界面报错：%s" % errs[:2]
            b.close()
        log("全部通过")
    finally:
        srv.terminate()
        try:
            srv.wait(timeout=5)
        except Exception:
            srv.kill()
        logf.close()


if __name__ == "__main__":
    main()

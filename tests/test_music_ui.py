# -*- coding: utf-8 -*-
"""最终验证：三个诉求 + 边角情况
A. 底部任务区完整可见
B. 点下载后行内出现状态，最后是「已下好」；清空任务后再点 → 「以前下过」
C. 点 ▶ 能试听、暂停、停止
D. 切回「链接下载」页没被影响
"""
import os
import sys

from playwright.sync_api import sync_playwright

URL = "http://127.0.0.1:8765/"
OUT = "D:/Projects/视频下载器/tests/rtest"


def log(*a):
    print(*a)
    sys.stdout.flush()


def _chrome():
    """优先用本机已装的 Chrome/Edge，找不到再退回 Playwright 自带的 Chromium"""
    for p in ("C:/Program Files/Google/Chrome/Application/chrome.exe",
              "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
              "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
              "C:/Program Files/Microsoft/Edge/Application/msedge.exe"):
        if os.path.exists(p):
            return p
    return None


def main():
    with sync_playwright() as p:
        exe = _chrome()
        kw = {"headless": True,
              "args": ["--autoplay-policy=no-user-gesture-required", "--mute-audio"]}
        if exe:
            kw["executable_path"] = exe
        br = p.chromium.launch(**kw)
        pg = br.new_page(viewport={"width": 1000, "height": 760})
        errs = []
        pg.on("pageerror", lambda e: errs.append("PAGEERROR " + str(e)))
        pg.goto(URL, wait_until="networkidle")
        pg.wait_for_timeout(500)

        # ================= 搜索页 =================
        pg.click('#tabs .tab[data-tab="music"]')
        pg.wait_for_timeout(300)
        # 搜索页有自己那排来源按钮，「支持 抖音 B站…」挂在这上头串味，必须收掉
        assert pg.evaluate(
            "() => getComputedStyle(document.querySelector('#platLine')).display"
        ) == "none", "搜索页还挂着「支持 抖音 B站…」那排"
        log("A0) 搜索页不显示支持平台一行")
        pg.fill("#mQuery", "告白气球")
        pg.click("#mGo")
        pg.wait_for_timeout(4500)
        rows = pg.query_selector_all(".msong")
        log("A) 搜索结果 %d 条" % len(rows))

        # --- B) 下载 → 行内状态 ---
        rows[0].query_selector(".dl").click()
        seen = []
        for _ in range(40):
            txt = pg.eval_on_selector(".msong .mst", "e => e ? e.textContent : ''")
            if txt and (not seen or seen[-1] != txt):
                seen.append(txt)
            if txt == "已下好":
                break
            pg.wait_for_timeout(700)
        log("B) 行内状态:", seen)

        # 任务区是否完整可见
        info = pg.evaluate("""() => {
          const blk = document.querySelector('#tasksBlock');
          const b = blk.getBoundingClientRect();
          const c = blk.querySelector('.task');
          const cb = c ? c.getBoundingClientRect() : null;
          return {hidden: blk.hidden, h: Math.round(b.height),
                  bottom: Math.round(b.bottom), winH: window.innerHeight,
                  cardBottom: cb ? Math.round(cb.bottom) : null};
        }""")
        log("A) 任务区:", info)
        log("A) 结论:", "OK 完整可见" if (info["bottom"] <= info["winH"] + 2
             and (info["cardBottom"] is None or info["cardBottom"] <= info["winH"] + 2))
             else "!! 被裁切")

        # --- C) 试听 / 暂停 / 停止 ---
        pv = pg.query_selector(".msong .pv")
        pv.click()
        pg.wait_for_timeout(3500)
        s1 = pg.evaluate("""() => {
          const au=document.querySelector('#auPlayer'), bar=document.querySelector('#pBar');
          return {paused:au.paused, t:au.currentTime, dur:au.duration,
                  barHidden:bar.hidden, playing:bar.classList.contains('playing'),
                  title:document.querySelector('#pbTitle').textContent,
                  time:document.querySelector('#pbTime').textContent};
        }""")
        log("C) 播放:", s1)
        pg.screenshot(path=OUT + "/f_preview.png")
        pv.click()
        pg.wait_for_timeout(700)
        log("C) 暂停:", pg.evaluate("() => document.querySelector('#auPlayer').paused"))
        pg.click("#pbStop")
        pg.wait_for_timeout(700)
        log("C) 停止后:", pg.evaluate("""() => {
          const au=document.querySelector('#auPlayer'), bar=document.querySelector('#pBar');
          return {barHidden:bar.hidden, bodyCls:document.body.className,
                  src:au.getAttribute('src')};
        }"""))

        # --- B2) 清空已完成后再点 → 应提示「以前下过」 ---
        pg.evaluate("""() => fetch('/api/clear', {method:'POST',
            headers:{'Content-Type':'application/json'}, body:'{}'})""")
        pg.wait_for_timeout(1200)
        pg.fill("#mQuery", "告白气球")
        pg.click("#mGo")
        pg.wait_for_timeout(4000)
        rows = pg.query_selector_all(".msong")
        rows[0].query_selector(".dl").click()
        pg.wait_for_timeout(1500)
        log("B2) 清空后再点同一首，状态位 =",
            repr(pg.eval_on_selector(".msong .mst", "e => e ? e.textContent : ''")))
        log("B2) 提示 =", repr(pg.eval_on_selector("#toast", "e => e.textContent")))
        pg.screenshot(path=OUT + "/f_history.png")

        # --- D) 切回链接下载页 ---
        pg.click('#tabs .tab[data-tab="video"]')
        pg.wait_for_timeout(700)
        assert pg.evaluate(
            "() => getComputedStyle(document.querySelector('#platLine')).display"
        ) != "none", "切回链接下载后「支持」那排没回来"
        d = pg.evaluate("""() => {
          const w=document.querySelector('.wrap').getBoundingClientRect();
          const c=document.querySelector('#composer').getBoundingClientRect();
          return {bodyCls:document.body.className, composerH:Math.round(c.height),
                  wrapBottom:Math.round(w.bottom), winH:window.innerHeight};
        }""")
        log("D) 链接下载页:", d)
        pg.screenshot(path=OUT + "/f_video.png")

        log("页面错误:", errs if errs else "无")
        br.close()


if __name__ == "__main__":
    main()

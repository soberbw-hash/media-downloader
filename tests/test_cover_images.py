# -*- coding: utf-8 -*-
"""封面 / 海报显示验证：
- 视频搜索（B站）封面能不能出来
- 电影卡（豆瓣 + B站番剧）海报能不能出来
- 音乐搜索封面不受影响
- 图片加载失败时有没有占位（不留白）
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
    for p in ("C:/Program Files/Google/Chrome/Application/chrome.exe",
              "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"):
        if os.path.exists(p):
            return p
    return None


def imgs(pg, sel, label):
    r = pg.evaluate("""(sel) => [...document.querySelectorAll(sel)].map(i => ({
        src: (i.getAttribute('src')||'').slice(0, 62),
        nw: i.naturalWidth,
        w: Math.round(i.getBoundingClientRect().width),
        h: Math.round(i.getBoundingClientRect().height),
        vis: getComputedStyle(i).visibility,
        inView: (() => { const r = i.getBoundingClientRect();
            return r.top < window.innerHeight && r.bottom > 0; })(),
      }))""", sel)
    # loading=lazy：视口外的图不会去下载，只看视口内的
    inview = [x for x in r if x["inView"]]
    ok = [x for x in inview if x["nw"] > 0 and x["vis"] == "visible"]
    log("%s: 视口内 %d 张，出来了 %d 张" % (label, len(inview), len(ok)))
    for x in r[:3]:
        log("   ", x)
    return r, ok


def main():
    with sync_playwright() as p:
        kw = {"headless": True, "args": ["--mute-audio"]}
        c = _chrome()
        if c:
            kw["executable_path"] = c
        br = p.chromium.launch(**kw)
        pg = br.new_page(viewport={"width": 1000, "height": 800})
        bad = []
        pg.on("requestfailed", lambda r: bad.append((r.url[:60], str(r.failure)[:36])))
        pg.goto(URL, wait_until="networkidle")
        pg.wait_for_timeout(600)

        # ---------- 视频搜索（B站封面）----------
        pg.click('#tabs .tab[data-tab="music"]')
        pg.wait_for_timeout(300)
        pg.click('#mSrc .plat[data-s="video"]')
        pg.wait_for_timeout(400)
        pg.click('#mVPlats .plat[data-p="bili"]')     # 只搜B站，YouTube 那条链路慢
        pg.wait_for_timeout(300)
        pg.fill("#mQuery", "人生")
        pg.click("#mGo")
        pg.wait_for_timeout(7000)
        r, ok = imgs(pg, ".msong img", "视频封面")
        assert ok, "视频封面一张都没出来"
        pg.screenshot(path=OUT + "/img_video.png")

        # ---------- 电影卡（豆瓣 + B站番剧海报）----------
        pg.click('#mSrc .plat[data-s="movie"]')
        pg.wait_for_timeout(400)
        pg.fill("#mQuery", "十二公民")
        pg.click("#mGo")
        pg.wait_for_timeout(7000)
        r, ok = imgs(pg, ".mmovie .poster img", "电影海报")
        assert ok, "电影海报一张都没出来"
        # 容器占位：海报区宽度应该在（即使某张失败）
        box = pg.evaluate("""() => [...document.querySelectorAll('.mmovie .poster')]
            .map(p => Math.round(p.getBoundingClientRect().width))""")
        log("海报容器宽度:", set(box))
        pg.screenshot(path=OUT + "/img_movie.png")

        # ---------- 音乐封面 ----------
        pg.click('#mSrc .plat[data-s="music"]')
        pg.wait_for_timeout(400)
        pg.fill("#mQuery", "告白气球")
        pg.click("#mGo")
        pg.wait_for_timeout(6000)
        r, ok = imgs(pg, ".msong img", "音乐封面")
        pg.screenshot(path=OUT + "/img_music.png")

        # ---------- 历史列表（有缩略图就显示）----------
        pg.click("#openHist")
        pg.wait_for_timeout(2500)
        r, ok = imgs(pg, ".hitem img", "历史缩略图")
        pg.screenshot(path=OUT + "/img_hist.png")

        log("请求失败:", bad[:6] if bad else "无")
        br.close()


if __name__ == "__main__":
    main()

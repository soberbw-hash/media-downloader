# -*- coding: utf-8 -*-
"""切类别别重搜：同一组「类别 + 来源 + 关键词」只打一次网络。

要防的 bug：搜完「告白气球」切到音乐、再切回全部，又从头搜一遍，
白白多等几秒；而且切档时正在放的试听条被收掉，歌就断了。

    python tests/test_search_cache.py      # 自己起服务，不依赖 8765
"""
import json
import os
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def chrome():
    for p in ("C:/Program Files/Google/Chrome/Application/chrome.exe",
              "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"):
        if os.path.exists(p):
            return p
    return None


def log(*a):
    print(*a, flush=True)


def check(ok, msg):
    if not ok:
        raise AssertionError(msg)
    log("+", msg)


FAKE = [{"id": "a", "status": "done", "title": "任务卡片 1.pdf",
         "url": "https://pan.baidu.com/s/1a", "kind": "files", "platform": "baidu",
         "platform_name": "百度网盘", "count": 1, "size": 11.0 * 1024 * 1024}]


def boot():
    tmp = tempfile.mkdtemp(prefix="vdl_cache_")
    env = os.environ.copy()
    env.update({"VDL_FORCE_NEW": "1", "VDL_NO_OPEN": "1", "VDL_NO_AUTO_UPDATE": "1",
                "VDL_DATA_DIR": tmp})
    pf = os.path.join(tmp, "p.txt")
    env["VDL_PORT_FILE"] = pf
    srv = subprocess.Popen([sys.executable, "server.py", "none"], cwd=ROOT, env=env,
                           stdout=open(os.path.join(tmp, "s.log"), "wb"),
                           stderr=subprocess.STDOUT)
    port = 0
    for _ in range(200):
        if os.path.isfile(pf):
            port = int((open(pf).read() or "0").strip() or 0)
            if port:
                break
        time.sleep(0.15)
    return srv, port


def main():
    srv, port = boot()
    if not port:
        log("服务没起来")
        sys.exit(1)
    log("port", port)
    kw = {"headless": True, "args": ["--autoplay-policy=no-user-gesture-required"]}
    exe = chrome()
    if exe:
        kw["executable_path"] = exe

    from playwright.sync_api import sync_playwright
    try:
        with sync_playwright() as p:
            b = p.chromium.launch(**kw)
            pg = b.new_page(viewport={"width": 1204, "height": 841})
            errs, hits = [], []

            def on_req(r):
                u = r.url
                for k in ("/api/search/intent", "/api/search/more", "/api/music/search",
                          "/api/search?"):
                    if k in u:
                        hits.append(k.strip("?/").split("/")[-1])
                        break
            pg.on("request", on_req)
            pg.on("pageerror", lambda e: errs.append(str(e)))
            pg.route("**/api/state", lambda r: r.fulfill(
                status=200, content_type="application/json",
                body=json.dumps({"tasks": FAKE, "running": 0, "paused": False})))
            pg.goto("http://127.0.0.1:%d/" % port, wait_until="networkidle")

            def count():
                return len(hits)

            pg.click('#tabs .tab[data-tab="music"]')
            pg.wait_for_timeout(300)
            pg.click('#mSrc .plat[data-s="music"]')
            pg.fill("#mQuery", "掌声")
            pg.click("#mGo")
            n0 = 0
            for _ in range(30):
                pg.wait_for_timeout(700)
                n0 = pg.evaluate("() => document.querySelectorAll('#mResults .msong').length")
                if n0 >= 3:
                    break
            check(n0 >= 3, "音乐档搜到 %d 首" % n0)
            first_hint = pg.evaluate("() => document.querySelector('#mHint').textContent")
            base = count()
            log("   搜完请求数", base, hits)

            # 1｜切到「全部」：这是新的一档，该搜
            pg.click('#mSrc .plat[data-s="all"]')
            pg.wait_for_timeout(600)
            for _ in range(30):
                pg.wait_for_timeout(700)
                if pg.evaluate("() => !document.querySelector('#mGo').disabled"):
                    break
            if pg.evaluate("() => document.querySelectorAll('#mResults .msong').length") == 0:
                log("   「全部」没搜到东西，后面的返回缓存照样能验")
            after_all = count()
            check(after_all > base, "换到「全部」会搜一次（%d → %d）" % (base, after_all))

            # 2｜切回音乐：关键词没改，不该再打网络
            pg.click('#mSrc .plat[data-s="music"]')
            pg.wait_for_timeout(700)
            back = count()
            check(back == after_all, "切回音乐不再重搜（请求数 %d → %d）" % (after_all, back))
            now = pg.evaluate("""() => ({n: document.querySelectorAll('#mResults .msong').length,
                hint: document.querySelector('#mHint').textContent})""")
            check(now["n"] == n0, "切回音乐结果原样摆回：%d 首" % now["n"])
            check(now["hint"] == first_hint, "提示也原样：%r" % now["hint"])

            # 3｜再切「全部」：同样不该重搜
            pg.click('#mSrc .plat[data-s="all"]')
            pg.wait_for_timeout(700)
            check(count() == back, "再切「全部」也不再重搜（请求数 %d）" % count())

            # 3b｜「全部」搜出歌时平台行会冒出来：换个平台是换东西，必须真搜
            plat_vis = pg.evaluate("""() => { const b = document.querySelector('#mPlats');
                return !!(b && !b.hidden && b.querySelectorAll('.plat').length > 1); }""")
            if plat_vis:
                n_before = count()
                pg.evaluate("""() => { const bs = [...document.querySelectorAll('#mPlats .plat')];
                    const other = bs.find(b => !b.classList.contains('on')); if (other) other.click(); }""")
                pg.wait_for_timeout(1500)
                check(count() > n_before, "换平台会真搜一次（%d → %d）" % (n_before, count()))

            # 4｜点「搜索」按钮＝要重来，必须真打一次
            pg.click("#mGo")
            pg.wait_for_timeout(1500)
            check(count() > back, "手动再搜会真的重搜（%d → %d）" % (back, count()))

            # 5｜试听中切类别：播放条不能消失，歌不能断
            pg.click('#mSrc .plat[data-s="music"]')
            pg.wait_for_timeout(600)
            shown = False
            for idx in range(4):
                pg.evaluate("(i) => document.querySelectorAll('#mResults .msong .pv')[i].click()", idx)
                for _ in range(12):
                    pg.wait_for_timeout(400)
                    if pg.evaluate("() => !document.querySelector('#pBar').hidden"):
                        shown = True
                        break
                if shown:
                    break
            check(shown, "点 ▶ 后播放条出现")
            pg.wait_for_timeout(1500)
            t1 = pg.evaluate("() => document.querySelector('#auPlayer').currentTime")
            pg.click('#mSrc .plat[data-s="all"]')
            pg.wait_for_timeout(2500)
            if pg.evaluate("() => document.querySelectorAll('#mResults .msong').length"):
                pg.click('#mSrc .plat[data-s="music"]')
                pg.wait_for_timeout(1200)
            st = pg.evaluate("""() => ({hidden: document.querySelector('#pBar').hidden,
                playing: document.body.classList.contains('playing'),
                paused: document.querySelector('#auPlayer').paused,
                t: document.querySelector('#auPlayer').currentTime,
                src: !!document.querySelector('#auPlayer').getAttribute('src')})""")
            log("   ", st)
            check(st["hidden"] is False, "切类别后播放条还在")
            check(st["playing"] and not st["paused"], "歌还在放，没被打断")
            check(st["t"] > t1, "进度在往前走 %.1fs → %.1fs" % (t1, st["t"]))

            # 6｜半路断流（换节点 / 网络抖一下）：按原进度接回来，别把播放条收掉
            pg.evaluate("""() => { const a = document.querySelector('#auPlayer');
                a.src = '/api/music/audio?platform=netease&id=0'; a.load(); }""")
            back_ok = False
            for _ in range(20):
                pg.wait_for_timeout(600)
                s2 = pg.evaluate("""() => ({hidden: document.querySelector('#pBar').hidden,
                    playing: document.body.classList.contains('playing'),
                    paused: document.querySelector('#auPlayer').paused,
                    src: (document.querySelector('#auPlayer').getAttribute('src')||'')})""")
                if not s2["hidden"] and s2["playing"] and not s2["paused"] and "id=0" not in s2["src"]:
                    back_ok = True
                    break
            check(back_ok, "断流后自动接回来，播放条没消失")

            check(not errs, "控制台无报错 %s" % (errs or ""))
            pg.screenshot(path=os.path.join(ROOT, "Temp", "search_cache.png"))
            b.close()
        log("\n全部通过")
    finally:
        srv.terminate()


if __name__ == "__main__":
    main()

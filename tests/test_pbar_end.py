# -*- coding: utf-8 -*-
"""一首放完：播放条淡出收起，不再自动接下一首。

要防的 bug：歌停了，播放条「啪」一下不见（没有过渡），
或者反过来说不停底、一直挂在下面不走。

    python tests/test_pbar_end.py      # 自己起服务
"""
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


def boot():
    tmp = tempfile.mkdtemp(prefix="vdl_pbarend_")
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
            errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)))
            pg.goto("http://127.0.0.1:%d/" % port, wait_until="networkidle")
            pg.click('#tabs .tab[data-tab="music"]')
            pg.wait_for_timeout(300)
            pg.click('#mSrc .plat[data-s="music"]')
            pg.fill("#mQuery", "掌声")
            pg.click("#mGo")
            n = 0
            for _ in range(30):
                pg.wait_for_timeout(700)
                n = pg.evaluate("() => document.querySelectorAll('#mResults .msong').length")
                if n >= 3:
                    break
            check(n >= 3, "搜到 %d 首" % n)

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
            pg.wait_for_timeout(1600)
            title = pg.evaluate("() => document.querySelector('#pbTitle').textContent")

            # 模拟这首歌放完
            pg.evaluate("() => document.querySelector('#auPlayer').dispatchEvent(new Event('ended'))")

            # 收尾是「淡出」：先站半拍（还在、带 out 类、牌面还是清楚的），
            # 再慢慢透明沉下去，最后才真隐藏
            pg.wait_for_timeout(120)
            hold = pg.evaluate("""() => { const b = document.querySelector('#pBar');
                return {hidden: b.hidden, out: b.classList.contains('out'),
                        op: parseFloat(getComputedStyle(b).opacity)}; }""")
            log("   站定中", hold)
            check(hold["out"] and not hold["hidden"], "先别消失：还在、带 out 类")
            check(hold["op"] > 0.9, "半拍里还是看得清的（opacity %s）" % hold["op"])

            t_end = time.time()
            faded = None
            for _ in range(40):
                pg.wait_for_timeout(60)
                faded = pg.evaluate("() => parseFloat(getComputedStyle(document.querySelector('#pBar')).opacity)")
                if faded < 0.9:
                    break
            log("   淡出中 opacity", faded)
            check(faded < 0.9, "确实在淡（opacity %s）" % faded)

            gone = None
            for _ in range(40):
                pg.wait_for_timeout(80)
                gone = pg.evaluate("""() => { const b = document.querySelector('#pBar');
                    return {hidden: b.hidden, out: b.classList.contains('out'),
                            playing: document.body.classList.contains('playing'),
                            paused: document.querySelector('#auPlayer').paused,
                            src: !!document.querySelector('#auPlayer').getAttribute('src')}; }""")
                if gone["hidden"]:
                    break
            cost = time.time() - t_end
            log("   收起后", gone, "%.2fs" % cost)
            check(gone["hidden"], "淡完真的隐藏了")
            check(cost < 1.6, "整套退场 %.2fs，别拖泥带水" % cost)
            check(not gone["out"], "out 类清干净（下次还能正常淡出）")
            check(not gone["playing"], "body.playing 也撤了，任务区留白复位")
            check(gone["paused"] and not gone["src"], "音频归位，没有偷偷接着播")

            # 不再自动接下一首：播放条收起后标题不该换人
            pg.wait_for_timeout(1200)
            after = pg.evaluate("() => document.querySelector('#pbTitle').textContent")
            check(after == title, "没有自动接下一首（%r）" % after)

            # 手动 ✕ 是立刻关，别等淡出
            pg.evaluate("(i) => document.querySelectorAll('#mResults .msong .pv')[i].click()", 0)
            for _ in range(12):
                pg.wait_for_timeout(400)
                if pg.evaluate("() => !document.querySelector('#pBar').hidden"):
                    break
            check(pg.evaluate("() => !document.querySelector('#pBar').hidden"), "再点一首能重新出现")
            pg.click("#pbStop")
            check(pg.evaluate("() => document.querySelector('#pBar').hidden"), "✕ 立刻关掉")

            check(not errs, "控制台无报错 %s" % (errs or ""))
            b.close()
        log("\n全部通过")
    finally:
        srv.terminate()


if __name__ == "__main__":
    main()

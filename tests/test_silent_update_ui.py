# -*- coding: utf-8 -*-
"""无感更新的界面侧：不弹提示、版本变了自己刷、重启后把上次搜的摆回来。

跑法：python tests/test_silent_update_ui.py
自己起隔离服务；VDL_NO_AUTO_UPDATE=1 保证它不会真的去替换重启。
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXE = r"C:/Program Files/Google/Chrome/Application/chrome.exe"
OP = urllib.request.build_opener(urllib.request.ProxyHandler({}))

PASS = [0]
FAIL = []


def check(cond, label):
    if cond:
        PASS[0] += 1
        print("  ok  " + label)
    else:
        FAIL.append(label)
        print("  FAIL " + label)


def log(*a):
    print(*a, flush=True)


def main():
    tmp = tempfile.mkdtemp(prefix="vdl_silent_ui_")
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
        for _ in range(150):
            if os.path.isfile(pf):
                try:
                    port = int(open(pf, encoding="utf-8").read().strip() or 0)
                except (OSError, ValueError):
                    port = 0
            if port:
                break
            time.sleep(0.15)
        check(bool(port), "隔离服务起来了（端口 %s）" % port)
        if not port:
            return
        base = "http://127.0.0.1:%d" % port
        ver = json.load(urllib.request.urlopen(base + "/api/state", timeout=8))["version"]

        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            b = pw.chromium.launch(executable_path=EXE, headless=True)
            pg = b.new_page(viewport={"width": 1180, "height": 900})
            errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)))
            # 不能用 networkidle：页面每两三秒轮询一次 /api/state，永远到不了 idle
            pg.goto(base + "/", wait_until="domcontentloaded", timeout=30000)
            pg.wait_for_timeout(1500)

            log("=== 1. 「正在更新」不再弹窗打扰 ===")
            pg.evaluate("""() => { window.__toasts = 0;
                const old = window.toast;
                window.toast = (...a) => { window.__toasts++; try { old(...a); } catch (e) {} };
            }""")
            pg.evaluate("""() => renderUpdateStatus({state: "applying", percent: 100,
                current: "1.0.0", detail: "正在更新，马上回来…"})""")
            pg.wait_for_timeout(600)
            n = pg.evaluate("() => window.__toasts")
            check(n == 0, "applying 状态没有 toast（%d 次）" % n)

            log("=== 2. 停下手里的活：没任务时空转也要安静 ===")
            pg.wait_for_timeout(2500)
            toast_text = pg.evaluate("""() => {
                const t = document.querySelector('#toast, .toast');
                return t && t.offsetParent !== null ? t.innerText : '';
            }""")
            check(not toast_text, "页面上没有挂着的提示条")

            log("=== 3. 版本号变了，前端自己刷成新版 ===")
            navs = []
            pg.on("framenavigated", lambda f: navs.append(f.url))
            pg.evaluate("() => { appVersion = '0.0.0'; }")
            pg.wait_for_timeout(4000)
            check(len(navs) >= 1, "检测到版本变化后刷新了一次（%d 次导航）" % len(navs))
            pg.wait_for_timeout(800)
            v = pg.evaluate("() => document.querySelector('#curVer')?.textContent || ''")
            check(v.endswith(ver), "刷新后显示的版本回到真实版本（%s）" % v)

            log("=== 4. 更新重启后再打开，上次搜的还在 ===")
            pg.evaluate("""() => {
                localStorage.setItem('tabMode', 'music');
                localStorage.setItem('vdlLastSearch', JSON.stringify({
                    q: '桌面上次搜的词', mode: 'music', plat: 'all', at: Date.now()}));
            }""")
            pg.reload(wait_until="domcontentloaded")
            pg.wait_for_timeout(1200)
            q = pg.evaluate("() => document.querySelector('#mQuery').value")
            check(q == "桌面上次搜的词", "搜索框自动填回上次的词（%s）" % q)
            mode = pg.evaluate("() => document.querySelector('#mSrc .plat.on')?.dataset.s || ''")
            check(mode == "music", "档位也回到音乐（%s）" % mode)

            check(not errs, "控制台没有报错 %s" % (errs or ""))
            b.close()
    finally:
        try:
            srv.terminate()
        except Exception:
            pass
        try:
            srv.wait(timeout=5)
        except Exception:
            srv.kill()

    log("")
    log("通过 %d 项，失败 %d 项" % (PASS[0], len(FAIL)))
    if FAIL:
        for f in FAIL:
            log("  - " + f)
        sys.exit(1)


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""真浏览器看自动更新的界面：进度、VPN 提示、手动兜底、默认开着且不摆开关。

跑法：python tests/test_update_ui.py
自己起隔离服务，用完自动关；VDL_NO_AUTO_UPDATE=1 不会真的替换重启。
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
SHOT = os.path.join(tempfile.gettempdir(), "vdl_update_ui.png")


def log(*a):
    print(*a, flush=True)


def main():
    tmp = tempfile.mkdtemp(prefix="vdl_upd_ui_")
    env = os.environ.copy()
    env.update({"VDL_FORCE_NEW": "1", "VDL_NO_OPEN": "1",
                "VDL_NO_AUTO_UPDATE": "1", "VDL_DATA_DIR": tmp})
    # 只认自己起的这个服务：8765 可能正被用户那份占着
    pf = os.path.join(tmp, "port.txt")
    env["VDL_PORT_FILE"] = pf
    logf = open(os.path.join(tmp, "srv.log"), "wb")
    srv = subprocess.Popen([sys.executable, "server.py", "none"], cwd=ROOT,
                           env=env, stdout=logf, stderr=subprocess.STDOUT)
    try:
        port = 0
        for _ in range(120):
            if os.path.isfile(pf):
                try:
                    port = int(open(pf, encoding="utf-8").read().strip() or 0)
                except (OSError, ValueError):
                    port = 0
            if port:
                break
            time.sleep(0.15)
        assert port, "没起带新代码的服务"
        log("隔离服务端口:", port)

        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            b = pw.chromium.launch(executable_path=EXE, headless=True)
            pg = b.new_page(viewport={"width": 1180, "height": 1050})
            errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)))
            pg.goto("http://127.0.0.1:%d/" % port, wait_until="networkidle", timeout=30000)
            pg.evaluate("() => document.querySelector('#openSet').click()")
            pg.wait_for_timeout(600)
            # 停掉轮询，别让真实 idle 状态把注入的状态刷掉。
            # 轮询走的是 setTimeout（不是 setInterval），光清 interval id 清不干净，
            # 而且注入那一刻可能正有一次请求在飞；直接把 poll 换成空函数并等它落地。
            pg.evaluate("""() => {
                clearTimeout(pollTimer);
                window.poll = async () => {};
            }""")
            pg.wait_for_timeout(300)

            downloading = {
                "state": "downloading", "percent": 42.0, "current": "1.24.3",
                "latest": "9.9.9", "proxy_hint": True,
                "manual_url": "https://github.com/soberbw-hash/media-downloader/releases/latest",
                "detail": "", "error": "",
            }
            pg.evaluate("(u) => renderUpdateStatus(u)", downloading)
            pg.wait_for_timeout(250)
            txt = pg.inner_text("#updMsg")
            assert "正在下载新版 42%" in txt, "下载进度文案不对: " + txt
            assert pg.locator("#updBar").is_visible(), "进度条没显示"
            width = pg.locator("#updBar i").evaluate("e => e.style.width")
            assert width == "42%", "进度条宽度不对: " + width
            assert pg.locator("#updTip").is_visible(), "没代理时没提示开 VPN"
            assert "VPN" in pg.inner_text("#updTip"), "VPN 提示文案不对"
            assert pg.inner_text("#checkUpd") == "正在更新", "按钮没切到进行中"
            log("  ok 下载中：进度 / VPN 提示 / 按钮都对")
            pg.locator("#drawer").screenshot(path=SHOT)
            log("  截图:", SHOT)

            pg.evaluate("""(u) => renderUpdateStatus(u)""", {
                "state": "idle", "percent": 0, "current": "1.24.3",
                "detail": "已是最新 v1.24.3", "error": "",
            })
            pg.wait_for_timeout(150)
            assert not pg.locator("#updBar").is_visible(), "空闲时进度条没收起来"
            assert not pg.locator("#updTip").is_visible(), "空闲时 VPN 提示还常驻"
            assert pg.inner_text("#checkUpd") == "检查更新", "按钮没还原"
            log("  ok 空闲时进度条和提示都不占位置")

            pg.evaluate("""(u) => renderUpdateStatus(u)""", {
                "state": "error", "percent": 0, "current": "1.24.3",
                "detail": "更新没成", "proxy_hint": False,
                "manual_url": "https://github.com/soberbw-hash/media-downloader/releases/latest",
                "error": "网络不通",
            })
            pg.wait_for_timeout(150)
            assert "下载安装包" in pg.inner_text("#updMsg"), "失败时没给手动下载出路"
            assert pg.locator("#updTip").is_visible(), "手动下载时没提示开 VPN"
            log("  ok 失败时给手动下载 + VPN 提示")

            # 自动更新默认就是开的，不摆开关占地方；设置里只留「检查更新」
            assert pg.locator("#cfgAutoUpd").count() == 0, "自动更新开关还在设置里"
            assert pg.is_visible("#checkUpd"), "设置里没有检查更新按钮"
            st = json.loads(OP.open("http://127.0.0.1:%d/api/state" % port, timeout=8)
                            .read().decode("utf-8"))
            assert (st.get("config") or {}).get("auto_update") is not False, \
                "自动更新没默认开: %s" % st.get("config")
            log("  ok 自动更新默认开、界面不摆开关")
            assert not errs, "界面脚本报错: %s" % errs[:2]
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

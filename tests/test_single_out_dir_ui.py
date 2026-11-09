# -*- coding: utf-8 -*-
"""真机看一眼设置面板：合并后只剩一个「下载目录」，且改一下仍能立刻存住。

跑法：python tests/test_single_out_dir_ui.py
自己起隔离服务，用完自动关；VDL_NO_OPEN=1 不会真弹窗口。
"""
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXE = r"C:/Program Files/Google/Chrome/Application/chrome.exe"
OP = urllib.request.build_opener(urllib.request.ProxyHandler({}))
SHOT = os.path.join(tempfile.gettempdir(), "vdl_settings_onedir.png")


def log(*a):
    print(*a, flush=True)


def find_new_service():
    """必须认准新代码的服务：桌面便携版常驻在 8765，打错了会看旧界面。"""
    for _ in range(50):
        for port in range(8765, 8786):
            try:
                html = OP.open("http://127.0.0.1:%d/" % port, timeout=0.6).read().decode(
                    "utf-8", "replace")
            except Exception:
                continue
            if 'id="cfgOut"' in html and "cfgMusicDir" not in html:
                return port
        time.sleep(0.2)
    return 0


def main():
    tmp = tempfile.mkdtemp(prefix="vdl_onedir_ui_")
    env = os.environ.copy()
    env.update({"VDL_FORCE_NEW": "1", "VDL_NO_OPEN": "1", "VDL_DATA_DIR": tmp})
    logf = open(os.path.join(tmp, "srv.log"), "wb")
    srv = subprocess.Popen([sys.executable, "server.py", "none"], cwd=ROOT,
                           env=env, stdout=logf, stderr=subprocess.STDOUT)
    try:
        port = find_new_service()
        assert port, "没起带新代码的服务（可能被旧桌面版占了）"
        log("隔离服务端口:", port)

        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.launch(executable_path=EXE, headless=True)
            pg = b.new_page(viewport={"width": 1280, "height": 1000})
            errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)))
            pg.goto("http://127.0.0.1:%d/" % port, wait_until="networkidle", timeout=30000)
            pg.evaluate("() => document.querySelector('#openSet').click()")
            pg.wait_for_timeout(600)

            assert pg.locator("#drawer .path-row").count() == 1, "设置里不止一个目录行"
            assert pg.locator("#cfgOut").is_visible(), "下载目录输入框没显示"
            assert pg.locator("#pickOut").is_visible(), "浏览按钮没显示"
            assert pg.locator("#cfgMusicDir").count() == 0, "音乐目录输入框还在"
            assert pg.locator("#pickMusic").count() == 0, "音乐目录浏览按钮还在"
            txt = pg.inner_text("#drawer")
            assert "音乐目录" not in txt and "跟视频放一起" not in txt, \
                "抽屉里还有音乐目录的文字"
            log("  OK 设置里只剩一个下载目录")

            # 改一下就存一下，别把 1.23 的无感保存改坏
            new_dir = os.path.join(tmp, "换个地方")
            pg.fill("#cfgOut", new_dir)
            pg.dispatch_event("#cfgOut", "change")
            pg.wait_for_timeout(700)
            state = OP.open("http://127.0.0.1:%d/api/state" % port, timeout=8).read()
            import json
            cfg = json.loads(state.decode("utf-8"))["config"]
            assert os.path.normcase(os.path.abspath(cfg["out_dir"])) == \
                os.path.normcase(os.path.abspath(new_dir)), "改目录没立刻存住"
            assert "music_dir" not in cfg, "配置里又混进了 music_dir"
            assert os.path.isdir(new_dir), "新目录没被建出来"
            log("  OK 改目录立刻生效，配置里没有 music_dir")

            assert not errs, "界面脚本报错: %s" % errs[:2]
            pg.locator("#drawer").screenshot(path=SHOT)
            log("  截图:", SHOT)
            b.close()
    finally:
        srv.terminate()
        try:
            srv.wait(timeout=5)
        except Exception:
            srv.kill()
        logf.close()
    log("全部通过")


if __name__ == "__main__":
    main()

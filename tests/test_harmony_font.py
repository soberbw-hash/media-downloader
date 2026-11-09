# -*- coding: utf-8 -*-
"""字体回归：全站鸿蒙 HarmonyOS Sans，字体随包走，不靠系统装。

跑法：python tests/test_harmony_font.py
自己起隔离服务（VDL_PORT_FILE），真机 Chrome 看 computed style。
"""
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXE = r"C:/Program Files/Google/Chrome/Application/chrome.exe"
FONTS = ("Regular", "Medium", "Bold")


def log(*a):
    print(*a, flush=True)


def find_port(pf):
    for _ in range(120):
        if os.path.isfile(pf):
            try:
                p = int(open(pf, encoding="utf-8").read().strip() or 0)
            except (OSError, ValueError):
                p = 0
            if p:
                return p
        time.sleep(0.15)
    return 0


def code(url):
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""
    except Exception:
        return 0, b""


def main():
    log("=== 1. 字体文件随包 ===")
    fdir = os.path.join(ROOT, "web", "fonts")
    for w in FONTS:
        p = os.path.join(fdir, "HarmonyOS_Sans_SC_%s.woff2" % w)
        assert os.path.isfile(p), "缺字重文件：%s" % w
        size = os.path.getsize(p)
        assert 3_000_000 < size < 6_000_000, "%s 大小异常：%d（是不是被截断了）" % (w, size)
    log("  OK Regular / Medium / Bold 都在，单个 3-6MB")

    log("=== 2. CSS 里没有别的字体家族 ===")
    css = open(os.path.join(ROOT, "web", "style.css"), encoding="utf-8").read()
    assert css.count("@font-face") == 4, "@font-face 不是 4 档：%d" % css.count("@font-face")
    for bad in ("Cascadia Mono", "Consolas", "monospace"):
        assert bad not in css, "还留着等宽字体：%s" % bad
    assert "--font: \"HarmonyOS Sans\"" in css, "根变量没指向鸿蒙"
    log("  OK 4 档字重全指鸿蒙，等宽字体已清掉")

    log("=== 3. 浏览器真机（computed style + 字体真的加载了）===")
    tmp = tempfile.mkdtemp(prefix="vdl_font_")
    env = os.environ.copy()
    env.update({"VDL_FORCE_NEW": "1", "VDL_NO_OPEN": "1",
                "VDL_NO_AUTO_UPDATE": "1", "VDL_DATA_DIR": tmp})
    pf = os.path.join(tmp, "port.txt")
    env["VDL_PORT_FILE"] = pf
    logf = open(os.path.join(tmp, "srv.log"), "wb")
    srv = subprocess.Popen([sys.executable, "server.py", "none"], cwd=ROOT,
                           env=env, stdout=logf, stderr=subprocess.STDOUT)
    try:
        port = find_port(pf)
        assert port, "没起带新代码的服务（可能被旧桌面版占了）"
        base = "http://127.0.0.1:%d" % port

        st, body = code(base + "/fonts/HarmonyOS_Sans_SC_Medium.woff2")
        assert st == 200 and body[:4] == b"wOF2", "字体路由没给到 woff2（%s）" % st
        st, _ = code(base + "/fonts/../server.py")
        assert st == 404, "字体路由能穿越目录"
        log("  OK /fonts/ 放行 woff2、穿越被挡")

        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.launch(executable_path=EXE, headless=True)
            pg = b.new_page(viewport={"width": 1204, "height": 841},
                            device_scale_factor=2)
            errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)))
            pg.goto(base + "/", wait_until="networkidle", timeout=30000)
            pg.wait_for_timeout(600)
            fam = pg.evaluate("() => getComputedStyle(document.body).fontFamily")
            assert fam.split(",")[0].strip().strip('"') == "HarmonyOS Sans", \
                "body 字体不是鸿蒙：%s" % fam

            ok = pg.evaluate("""() => {
              const miss = [];
              for (const w of [400, 500, 600, 700]) {
                if (!document.fonts.check(w + ' 14px "HarmonyOS Sans"')) miss.push(w);
              }
              return miss;
            }""")
            assert not ok, "这些字重没真的加载：%s" % ok

            # 弹窗里也别漏：标题、标签、输入框都吃同一套
            pg.click("#openSet")
            pg.wait_for_timeout(700)
            bad = pg.evaluate("""() => {
              const out = [];
              let hit = 0;
              for (const s of ['#drawer .mcard h3', '#drawer .row-l', '#drawer select',
                               '#drawer .acct-entry', '#drawer .save-btn']) {
                const e = document.querySelector(s);
                if (!e) continue;
                hit++;
                const f = getComputedStyle(e).fontFamily;
                if (!f.includes('HarmonyOS Sans')) out.push(s + ' -> ' + f);
              }
              return {out, hit};
            }""")
            assert bad["hit"] >= 4, "弹窗选择器没命中，测试白跑：%s" % bad
            assert not bad["out"], "弹窗里还有没换的：%s" % bad["out"]
            assert not errs, "JS 报错：%s" % errs
            log("  OK 正文 / 弹窗标题 / 标签 / 下拉 / 按钮 全是鸿蒙")
            b.close()
    finally:
        srv.terminate()
        try:
            srv.wait(timeout=5)
        except subprocess.TimeoutExpired:
            srv.kill()

    log("=== 4. 浏览器扩展弹窗也带字体 ===")
    ext = os.path.join(ROOT, "browser-extension")
    assert os.path.isfile(os.path.join(ext, "fonts",
                                       "HarmonyOS_Sans_SC_Regular.woff2")), \
        "扩展里没带字体，弹窗会退回系统字体"
    ph = open(os.path.join(ext, "popup.html"), encoding="utf-8").read()
    assert "@font-face" in ph and "HarmonyOS Sans" in ph, "扩展弹窗没接鸿蒙"
    log("  OK 扩展 popup 自带字体并已引用")
    log("全部通过")


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""设置改成居中弹窗后的行为：居中、两栏、点外面 / Esc 能关、换弹窗不叠、窄窗叠一栏。

跑法：python tests/test_settings_modal.py
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
SHOT = os.path.join(tempfile.gettempdir(), "vdl_settings_modal.png")


def log(*a):
    print(*a, flush=True)


def find_new_service(pf):
    """只认这次自己起的服务：桌面便携版可能常驻在 8765。"""
    for _ in range(120):
        if os.path.isfile(pf):
            try:
                port = int(open(pf, encoding="utf-8").read().strip() or 0)
            except (OSError, ValueError):
                port = 0
            if port:
                return port
        time.sleep(0.15)
    return 0


def card_box(pg, sel="#drawer .mcard"):
    return pg.evaluate("""(s) => {
        const r = document.querySelector(s).getBoundingClientRect();
        return {x: r.x, y: r.y, w: r.width, h: r.height,
                cx: r.x + r.width / 2, cy: r.y + r.height / 2};
    }""", sel)


def main():
    tmp = tempfile.mkdtemp(prefix="vdl_modal_ui_")
    env = os.environ.copy()
    env.update({"VDL_FORCE_NEW": "1", "VDL_NO_OPEN": "1",
                "VDL_NO_AUTO_UPDATE": "1", "VDL_DATA_DIR": tmp})
    pf = os.path.join(tmp, "port.txt")
    env["VDL_PORT_FILE"] = pf
    logf = open(os.path.join(tmp, "srv.log"), "wb")
    srv = subprocess.Popen([sys.executable, "server.py", "none"], cwd=ROOT,
                           env=env, stdout=logf, stderr=subprocess.STDOUT)
    try:
        port = find_new_service(pf)
        assert port, "没起带新代码的服务"
        log("隔离服务端口:", port)

        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.launch(executable_path=EXE, headless=True)
            pg = b.new_page(viewport={"width": 1180, "height": 900})
            errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)))
            pg.goto("http://127.0.0.1:%d/" % port, wait_until="networkidle", timeout=30000)
            pg.evaluate("() => document.querySelector('#openSet').click()")
            pg.wait_for_timeout(500)

            # ① 居中，不是贴右边的一条边栏
            box = card_box(pg)
            assert abs(box["cx"] - 590) <= 2, "弹窗没在水平中间: %s" % box
            assert abs(box["cy"] - 450) <= 2, "弹窗没在垂直中间: %s" % box
            assert 700 <= box["w"] <= 820, "弹窗宽度不对: %s" % box
            assert pg.evaluate("() => document.querySelector('#drawer').classList.contains('on')")
            log("  OK 设置是居中的弹窗（%.0fx%.0f）" % (box["w"], box["h"]))

            # ② 两栏并排：左栏在左、右栏在右，顶边对齐
            cols = pg.evaluate("""() => [...document.querySelectorAll('#drawer .set-col')]
                .map(e => { const r = e.getBoundingClientRect();
                            return {x: r.x, y: r.y, w: r.width}; })""")
            assert len(cols) == 2, "不是两栏: %s" % cols
            assert cols[1]["x"] > cols[0]["x"] + 300, "两栏没并排: %s" % cols
            assert abs(cols[0]["y"] - cols[1]["y"]) <= 1, "两栏顶边没对齐"
            log("  OK 两栏铺开（%s）" % " / ".join("%.0f" % c["w"] for c in cols))

            # ③ 弹出动画结束后是完全归位的（不是停在半透明 / 缩小状态）
            st = pg.evaluate("""() => {
                const c = document.querySelector('#drawer .mcard');
                const m = document.querySelector('#drawer');
                return {t: getComputedStyle(c).transform, o: getComputedStyle(c).opacity,
                        mo: getComputedStyle(m).opacity};
            }""")
            assert st["t"] in ("none", "matrix(1, 0, 0, 1, 0, 0)"), "卡片没归位: %s" % st
            assert abs(float(st["o"]) - 1) < .01 and abs(float(st["mo"]) - 1) < .01, \
                "弹窗没完全显示: %s" % st
            log("  OK 动画收尾停在原位（transform=%s）" % st["t"])
            pg.screenshot(path=SHOT)
            log("  截图:", SHOT)

            # ④ 点卡片外面的空白 = 点遮罩，能关
            pg.mouse.click(20, 20)
            pg.wait_for_timeout(400)
            assert not pg.evaluate("() => document.querySelector('#drawer').classList.contains('on')"), \
                "点外面没关上"

            # ⑤ Esc 也能关
            pg.evaluate("() => document.querySelector('#openSet').click()")
            pg.wait_for_timeout(350)
            pg.keyboard.press("Escape")
            pg.wait_for_timeout(350)
            assert not pg.evaluate("() => document.querySelector('#drawer').classList.contains('on')"), \
                "Esc 没关上"
            log("  OK 点外面 / Esc 都能关")

            # ⑥ 设置里点账号登录：旧的直接消失，别两个弹窗叠在中间
            pg.evaluate("() => document.querySelector('#openSet').click()")
            pg.wait_for_timeout(350)
            pg.evaluate("() => document.querySelector('#openAcct').click()")
            pg.wait_for_timeout(450)
            on = pg.evaluate("""() => ['#drawer', '#acctDrawer', '#histDrawer']
                .filter(s => document.querySelector(s).classList.contains('on'))""")
            assert on == ["#acctDrawer"], "弹窗叠在一起了: %s" % on
            log("  OK 换弹窗时旧的直接收掉，只剩账号")

            # ⑦ 完成按钮：存一下再关
            pg.keyboard.press("Escape")
            pg.wait_for_timeout(300)
            pg.evaluate("() => document.querySelector('#openSet').click()")
            pg.wait_for_timeout(350)
            pg.evaluate("() => { document.querySelector('#advBox').open = true; }")
            pg.wait_for_timeout(300)
            pg.fill("#cfgProxy", "http://127.0.0.1:7890")
            pg.evaluate("() => document.querySelector('#saveCfg').click()")
            pg.wait_for_timeout(700)
            assert not pg.evaluate("() => document.querySelector('#drawer').classList.contains('on')"), \
                "点完成没关上"
            import json
            cfg = json.loads(OP.open("http://127.0.0.1:%d/api/state" % port,
                                     timeout=8).read().decode("utf-8"))["config"]
            assert cfg["proxy"] == "http://127.0.0.1:7890", "完成没存住: %s" % cfg
            log("  OK 完成按钮：存下来再关")

            # ⑧ 窄窗叠成一栏，第二栏前要有分隔线（不然像连在一起）
            pg.set_viewport_size({"width": 700, "height": 900})
            pg.evaluate("() => document.querySelector('#openSet').click()")
            pg.wait_for_timeout(500)
            narrow = pg.evaluate("""() => {
                const cs = [...document.querySelectorAll('#drawer .set-col')];
                const r0 = cs[0].getBoundingClientRect(), r1 = cs[1].getBoundingClientRect();
                const first = cs[1].firstElementChild;
                const cs2 = getComputedStyle(first);
                return {sameX: Math.abs(r0.x - r1.x) < 1, below: r1.y > r0.y + 100,
                        border: cs2.borderTopWidth, w: document.querySelector('#drawer .mcard')
                                .getBoundingClientRect().width};
            }""")
            assert narrow["sameX"] and narrow["below"], "窄窗没叠成一栏: %s" % narrow
            assert narrow["border"] != "0px", "叠起来后第二栏第一块没有分隔线: %s" % narrow
            assert narrow["w"] <= 676, "窄窗还撑这么宽: %s" % narrow
            log("  OK 窄窗叠成一栏，分隔线还在")

            assert not errs, "有 JS 报错: %s" % errs
            log("  OK 没有 JS 报错")
            b.close()
    finally:
        srv.terminate()
        try:
            srv.wait(5)
        except Exception:
            srv.kill()
    log("全部通过")


if __name__ == "__main__":
    main()

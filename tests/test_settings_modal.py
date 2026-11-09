# -*- coding: utf-8 -*-
"""设置改成「一行一项，全部平铺」之后的行为。

要看住这几件事：
  ① 日常要用的项全在明面上，没有一个要展开才看得见（不再有 details / 折叠）
  ② 少用的项换一页，卡片尺寸一模一样 —— 换页不跳
  ③ 点外面 / Esc 能关；Esc 在高级页先退回常用页
  ④ 改一下就存；重开不丢
  ⑤ 窄窗不溢出
  ⑥ 支持平台挪到了首屏，没在设置里，也没把视频号算进去

跑法：python tests/test_settings_modal.py
自己起隔离服务，用完自动关；VDL_NO_OPEN=1 不会真弹窗口。
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


def card(pg):
    return pg.evaluate("""() => {
        const c = document.querySelector('#drawer .mcard');
        const b = document.querySelector('#drawer .drawer-body');
        const r = c.getBoundingClientRect();
        return {x: Math.round(r.x), y: Math.round(r.y),
                w: Math.round(r.width), h: Math.round(r.height),
                cx: r.x + r.width / 2, cy: r.y + r.height / 2,
                scrollTop: b.scrollTop, over: b.scrollHeight - b.clientHeight};
    }""")


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
            pg.wait_for_timeout(900)

            # ① 首屏：支持平台在粘贴框下面，不在设置里
            line = pg.evaluate("""() => {
                const l = document.querySelector('#platLine');
                if (!l) return null;
                const r = l.getBoundingClientRect();
                return {text: l.innerText.replace(/\\s+/g, ' ').trim(),
                        y: Math.round(r.y), underBox: r.y > document.querySelector('#composer')
                                                          .getBoundingClientRect().bottom - 2,
                        inDrawer: !!l.closest('#drawer')};
            }""")
            assert line and line["text"], "首屏没有支持平台那一行"
            assert "B站" in line["text"] and "抖音" in line["text"], "常用平台没列出来: %s" % line
            assert "视频号" not in line["text"], "视频号还挂在支持列表里"
            assert not line["inDrawer"] and line["underBox"], "平台行没挪到粘贴框下面"
            log("  OK 支持平台在首屏粘贴框下面：%s" % line["text"][:70])

            pg.evaluate("() => document.querySelector('#openSet').click()")
            pg.wait_for_timeout(500)

            # ② 居中 + 常用项全在明面上，没有一个藏在折叠里
            c0 = card(pg)
            assert abs(c0["cx"] - 590) <= 2 and abs(c0["cy"] - 450) <= 2, "弹窗没居中: %s" % c0
            assert pg.evaluate("() => document.querySelectorAll('#drawer details').length") == 0, \
                "设置里还有折叠块"
            shown = ["#cfgOut", "#cfgQuality", "#cfgConc", "#cfgNamePre",
                     "#checkUpd", "#saveCfg"]
            hidden = [s for s in shown if not pg.is_visible(s)]
            assert not hidden, "这些日常项要展开才看得见: %s" % hidden
            # 四个「一起存」是方框 chip：勾选框本身藏起来，露出来的是外围那颗
            assert pg.locator(".chips .chip").count() == 4, "一起存不是四个方框"
            for i in range(4):
                assert pg.locator(".chips .chip").nth(i).is_visible(), "第 %d 个存项没露出来" % (i + 1)
            assert pg.locator("#cfgProxy").count() == 0, "代理输入框该收走了（有梯子会自动用）"
            txt = pg.inner_text("#drawer")
            for bad in ("解析接口", "视频号", "自动更新", "高级设置", "支持平台"):
                assert bad not in txt, "「%s」还占着设置的地方" % bad
            log("  OK 常用项全平铺（%.0fx%.0f），解析接口 / 视频号 / 自动更新开关都不在" % (c0["w"], c0["h"]))
            pg.screenshot(path=SHOT)

            # ③ 换页：卡片一点不动，只有内容换
            pg.evaluate("() => document.querySelector('#openAdv').click()")
            pg.wait_for_timeout(400)
            c1 = card(pg)
            assert (c1["x"], c1["y"], c1["w"], c1["h"]) == (c0["x"], c0["y"], c0["w"], c0["h"]), \
                "换页时卡片跳了: %s -> %s" % (c0, c1)
            assert pg.is_visible("#cfgCookies"), "高级页没打开"
            assert pg.inner_text("#setTitle") == "高级", "换页后标题没变"
            assert pg.is_visible("#setBack"), "高级页没有返回键"
            assert not pg.is_visible("#setMain"), "常用页没让位"
            assert not pg.is_visible("#openAdv"), "高级页底部还挂着「高级选项」"
            assert c1["scrollTop"] == 0, "换页后没回到顶部"
            # 完成按钮仍在右下角
            fb = pg.evaluate("""() => { const r = document.querySelector('#saveCfg').getBoundingClientRect();
                const c = document.querySelector('#drawer .mcard').getBoundingClientRect();
                return {right: Math.round(c.right - r.right), centerY: r.y + r.height / 2}; }""")
            assert fb["right"] < 40, "「完成」跑到左边了: %s" % fb
            log("  OK 换页不跳（卡片 %s 完全一致）" % "x/y/w/h")

            # ④ Esc：先退回常用页，再按一次才关
            pg.keyboard.press("Escape")
            pg.wait_for_timeout(300)
            assert pg.is_visible("#setMain") and not pg.is_visible("#setAdv"), "Esc 没退回常用页"
            assert pg.evaluate("() => document.querySelector('#drawer').classList.contains('on')"), \
                "Esc 第一次就把弹窗关了"
            pg.keyboard.press("Escape")
            pg.wait_for_timeout(350)
            assert not pg.evaluate("() => document.querySelector('#drawer').classList.contains('on')"), \
                "再按 Esc 没关上"
            log("  OK Esc 先返一级，再关")

            # ⑤ 一起存：点一下就存，重开还在
            pg.evaluate("() => document.querySelector('#openSet').click()")
            pg.wait_for_timeout(400)
            pg.locator('.chip:has(#cfgCover)').click()
            pg.wait_for_timeout(700)
            want = pg.locator("#cfgCover").is_checked()
            got = json.loads(OP.open("http://127.0.0.1:%d/api/state" % port, timeout=8)
                             .read().decode("utf-8"))["config"].get("save_cover")
            assert bool(got) == want, "点封面图没存住: 期望 %s 实际 %s" % (want, got)
            pg.evaluate("() => document.querySelector('#mask').click()")
            pg.wait_for_timeout(300)
            pg.evaluate("() => document.querySelector('#openSet').click()")
            pg.wait_for_timeout(350)
            assert pg.locator("#cfgCover").is_checked() == want, "重开值回退了"
            log("  OK 一起存改一下就存（封面图 = %s），重开不丢" % want)

            # ⑥ 完成：存完再关
            pg.evaluate("() => document.querySelector('#openAdv').click()")
            pg.wait_for_timeout(300)
            pg.fill("#cfgCookies", "")
            pg.locator("#cfgDanmaku").evaluate(
                "e => { e.checked = !e.checked; e.dispatchEvent(new Event('change', {bubbles:true})); }")
            pg.wait_for_timeout(200)
            want_dm = pg.locator("#cfgDanmaku").is_checked()
            pg.evaluate("() => document.querySelector('#saveCfg').click()")
            pg.wait_for_timeout(700)
            assert not pg.evaluate("() => document.querySelector('#drawer').classList.contains('on')"), \
                "点完成没关上"
            cfg = json.loads(OP.open("http://127.0.0.1:%d/api/state" % port, timeout=8)
                             .read().decode("utf-8"))["config"]
            assert bool(cfg["save_danmaku"]) == want_dm, "完成没存住: %s" % cfg
            log("  OK 完成按钮：存下来再关")

            # ⑦ 窄窗：两页都不横向溢出
            pg.set_viewport_size({"width": 700, "height": 760})
            pg.evaluate("() => document.querySelector('#openSet').click()")
            pg.wait_for_timeout(400)
            narrow = pg.evaluate("""() => {
                const c = document.querySelector('#drawer .mcard');
                const b = document.querySelector('#drawer .drawer-body');
                const r = c.getBoundingClientRect();
                return {w: Math.round(r.width), overX: b.scrollWidth - b.clientWidth,
                        overY: b.scrollHeight - b.clientHeight};
            }""")
            assert narrow["w"] <= 676, "窄窗还撑这么宽: %s" % narrow
            assert narrow["overX"] <= 0, "窄窗横向溢出了: %s" % narrow
            pg.evaluate("() => document.querySelector('#openAdv').click()")
            pg.wait_for_timeout(350)
            over_x = pg.evaluate("""() => { const b = document.querySelector('#drawer .drawer-body');
                return b.scrollWidth - b.clientWidth; }""")
            assert over_x <= 0, "窄窗下高级页横向溢出: %s" % over_x
            log("  OK 窄窗（700）两页都不溢出")

            assert not errs, "有 JS 报错: %s" % errs
            log("  OK 没有 JS 报错")
            b.close()
    finally:
        srv.terminate()
        try:
            srv.wait(5)
        except Exception:
            srv.kill()
        logf.close()
    log("全部通过")


if __name__ == "__main__":
    main()

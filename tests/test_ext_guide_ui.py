# -*- coding: utf-8 -*-
"""真机看一眼「怎么装」图文三步：自己起隔离服务、截图、检查没挤出抽屉。

跑法：python tests/test_ext_guide_ui.py
服务用完自动关，VDL_NO_OPEN=1 不会真弹文件夹 / 浏览器。
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
SHOT = os.path.join(tempfile.gettempdir(), "vdl_ext_guide.png")


def log(*a):
    print(*a, flush=True)


def find_new_service(pf):
    """只认这次自己起的服务：本机 8765 可能正被用户那份占着。"""
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


def main():
    tmp = tempfile.mkdtemp(prefix="vdl_guide_")
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
        assert port, "没起带新代码的服务（可能被旧桌面版占了）"
        log("隔离服务端口:", port)

        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.launch(executable_path=EXE, headless=True)
            pg = b.new_page(viewport={"width": 1280, "height": 900})
            pg.goto("http://127.0.0.1:%d/" % port, wait_until="networkidle", timeout=30000)
            pg.evaluate("() => document.querySelector('#openAcct').click()")
            pg.wait_for_timeout(500)

            assert pg.locator("#extPage").count() == 0, "点不开的「只开安装页」还在"
            assert pg.locator("#extFolder").count() == 0, "点不开的「只开文件夹」还在"
            assert pg.locator("#extHow .gstep button").count() == 0, "图文步骤里不该再有按钮"
            log("  OK 点不开的次要按钮已清干净")

            # 「怎么装」默认收起，点一下才展开三步（折叠箭头是唯一那个 button）
            assert pg.locator("#extHow details").count() == 0, "又套回 <details> 了"
            assert pg.locator("#extHow").is_visible(), "「怎么装」标题没显示"
            assert pg.evaluate("() => document.querySelector('#egBody').hidden"), \
                "三步默认没收起"
            pg.evaluate("() => document.querySelector('#egToggle').click()")
            pg.wait_for_timeout(300)
            assert pg.locator("#extHow .gstep").first.is_visible(), "点开后三步没露出来"

            steps = pg.locator("#extHow .gstep")
            pics = pg.locator("#extHow img.gpic")
            assert steps.count() == 3 and pics.count() == 3, "图文三步不齐"
            for i in range(3):
                assert pics.nth(i).is_visible(), "第 %d 张图没显示" % (i + 1)
                nw = pics.nth(i).evaluate("e => e.naturalWidth")
                assert nw > 0, "第 %d 张图没真正加载出来" % (i + 1)
                # 图有多少像素就显示多大：拉宽了会糊，字看不清（+2 是两侧 1px 边框）
                shown = pics.nth(i).evaluate("e => e.getBoundingClientRect().width")
                assert shown <= nw + 2, \
                    "第 %d 张图被拉伸了（%dpx 拉到 %dpx），会糊" % (i + 1, nw, shown)
            log("  OK 怎么装默认收起，点开后三步齐全，截图都加载了且没被拉伸")

            # 点图看大图能弹出、Esc 能关
            pics.nth(0).evaluate("e => e.click()")   # 第一步那张就是菜单图
            pg.wait_for_timeout(400)
            assert pg.locator("#shotBox").is_visible(), "点了图没弹出大图"
            src = pg.locator("#shotImg").get_attribute("src")
            assert src and "ext-page-menu.png" in src, "大图不是整页标注图"
            pg.keyboard.press("Escape")
            pg.wait_for_timeout(300)
            assert not pg.locator("#shotBox").is_visible(), "Esc 没关掉大图"
            log("  OK 点图放大：弹的是整页标注图，Esc 能关")

            # Esc 是两级的：关完大图再按一次会关面板，所以这里重新打开
            pg.evaluate("() => document.querySelector('#openAcct').click()")
            pg.wait_for_timeout(500)
            assert pg.locator("#acctDrawer").evaluate("e => e.classList.contains('on')"), \
                "账号面板没重新打开"

            # 卡片不能横向溢出抽屉（330px），文字也不能被图挤没
            drawer = pg.locator("#acctDrawer").bounding_box()
            right = drawer["x"] + drawer["width"]
            for i in range(3):
                box = steps.nth(i).bounding_box()
                assert box["x"] >= drawer["x"] - 1, "第 %d 步跑出抽屉左边" % (i + 1)
                assert box["x"] + box["width"] <= right + 1, \
                    "第 %d 步横向溢出抽屉" % (i + 1)
                txt = steps.nth(i).locator(".gt").bounding_box()
                assert txt and txt["width"] >= 90, "第 %d 步文字被挤没了" % (i + 1)
            log("  OK 三步在弹窗里没溢出、文字没被挤掉")

            steps.first.scroll_into_view_if_needed()
            pg.locator("#extHow").screenshot(path=SHOT)
            log("  截图:", SHOT)

            # 按钮 = 选中文件夹 + 把它的地址复制进剪贴板，方便加载弹窗里粘贴
            pg.locator("#btnExtSetup").evaluate("e => e.click()")
            pg.wait_for_timeout(700)
            assert pg.locator("#extHow .gstep").count() == 3, "点完三步不见了"
            tst = pg.inner_text("#toast")
            assert "扩展程序" in tst and "管理扩展程序" in tst, \
                "按钮反馈没指路到浏览器菜单：%s" % tst
            assert "地址已复制" in tst and "粘贴" in tst, \
                "没提示地址已复制、可在加载弹窗粘贴：%s" % tst
            assert pg.locator("#extPaste").count() == 0, \
                "又整出常驻的复制提示条（提示走 toast 就够）"
            log("  OK 选中文件夹 + 地址复制提示，仍指路到浏览器菜单")
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

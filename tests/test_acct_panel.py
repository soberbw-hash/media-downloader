# -*- coding: utf-8 -*-
"""账号面板视觉回归：分组、每行信息、图标、进度条、窄窗（真机 Chrome）。

跑法：python tests/test_acct_panel.py
自己起隔离服务（VDL_PORT_FILE），用 route 拦下 /api/login/state 灌假数据，
不碰用户真实 cookie / 配置。
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
SHOT = os.path.join(tempfile.gettempdir(), "vdl_acct_panel.png")


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


def site(pid, name, required, logged, acc=None):
    return {"id": pid, "name": name, "required": required,
            "logged_in": logged, "account": acc or {}, "tip": ""}


def payload(port, synced=True):
    return {
        "sites": [
            site("xiaohongshu", "小红书", True, True, {"ok": True, "name": "小尤同学"}),
            site("instagram", "Instagram", True, False),
            site("qqmusic", "QQ音乐", True, False),
            site("douyin", "抖音", False, True, {"ok": True, "name": "尤老师"}),
            site("kuaishou", "快手", False, False),
            site("bilibili", "B站", False, True, {"ok": True, "name": "小尤"}),
            site("weibo", "微博", False, True, {"ok": True, "name": "空恋大大"}),
            site("netease", "网易云音乐", False, True,
                 {"ok": True, "name": "空恋大大", "vip": True, "detail": "黑胶VIP"}),
            site("suno", "Suno", False, True, {"ok": True, "name": "youlaoshi"}),
        ],
        "status": {"state": "idle", "platform": "", "msg": ""},
        "default_browser": {"name": "Google Chrome", "exe": "x", "chromium": True},
        "extension": {"exists": True, "synced": synced},
        "cookie_dir": "", "port": port,
    }


def main():
    log("=== 1. 图标：三个翻车的换成品牌原图 ===")
    icons = open(os.path.join(ROOT, "web", "icons.js"), encoding="utf-8").read()
    line = lambda k: next(l for l in icons.splitlines() if l.strip().startswith('"%s":' % k))
    dy = line("douyin")
    assert "#25F4EE" in dy and "#FE2C55" in dy, "抖音还是单色，没有青红错位"
    assert "#010101" not in dy, "抖音又用回黑色当主色"
    ne = line("netease")
    assert ne.count("<circle") == 0, "网易云还在用手画的圆圈，像禁止标志"
    assert '"#D43C33"' in ne, "网易云不是品牌红"
    su = line("suno")
    assert '"#18181B"' in su, "Suno 图标颜色不对（不是黑）"
    assert "M16.5 0C20.642" in su, "Suno 不是官方那个双弧形标志"
    log("  OK 抖音=青红错位音符、网易云=官方红、Suno=官方黑")

    tmp = tempfile.mkdtemp(prefix="vdl_acct_")
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
        log("=== 2. 面板结构（隔离服务 %d）===" % port)

        from playwright.sync_api import sync_playwright
        body = json.dumps(payload(port), ensure_ascii=False)
        with sync_playwright() as p:
            b = p.chromium.launch(executable_path=EXE, headless=True)
            pg = b.new_page(viewport={"width": 1204, "height": 841},
                            device_scale_factor=2)
            errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)))
            pg.route("**/api/login/state",
                     lambda r: r.fulfill(status=200, content_type="application/json",
                                         body=body))
            pg.goto("http://127.0.0.1:%d/" % port, wait_until="networkidle",
                    timeout=30000)
            pg.evaluate("() => document.querySelector('#openAcct').click()")
            pg.wait_for_timeout(700)

            rows = pg.locator("#logins .login-row")
            assert rows.count() == 9, "行数不对：%d" % rows.count()
            assert pg.locator("#logins .lg-list").count() == 2, "分组没合成两张卡"

            # 分组标题：文字 + 数量，没有解释小字（span 一个都不许有）
            assert pg.locator("#logins .lg-t span").count() == 0, "分组又加了解释小字"
            head = pg.locator("#logins .lg-t").first.inner_text()
            assert "必须登录" in head and "3" in head, "分组标题没带数量: %s" % head
            log("  OK 两段分组、标题带数量、没有副标题小字")

            # 行是一张卡里的细线分割，不是每行一个边框盒子
            r0 = rows.first.evaluate(
                "e => {const s=getComputedStyle(e);"
                "return {bw:s.borderTopWidth, br:s.borderTopLeftRadius,"
                " bg:s.backgroundColor};}")
            assert r0["bw"] in ("0px",), "每行还在套边框: %s" % r0["bw"]
            assert r0["br"] in ("0px",), "每行还在圆角: %s" % r0["br"]
            log("  OK 行与行只用细线切，不再一行一个框")

            # 图标：无边框盒子、17px 左右
            fav = rows.first.locator(".fav")
            fw = fav.evaluate("e => getComputedStyle(e).borderWidth")
            iw = fav.locator("svg").evaluate("e => e.getBoundingClientRect().width")
            assert fw in ("0px",), "图标还套着白色小方框（边框 %s）" % fw
            assert 15 <= iw <= 19, "图标大小不对：%.1fpx" % iw
            log("  OK 图标 %.0fpx、去掉了小方框" % iw)

            # 已登录行的账号名在同一行、基线贴近；VIP 是浅色小标签
            netease = rows.filter(has_text="网易云音乐").first
            nm = netease.locator(".nm")
            acc = nm.locator(".acc")
            vip = nm.locator(".vip")
            assert acc.count() == 1 and vip.count() == 1, "账号名/会员标缺失"
            box_nm = nm.bounding_box()
            row_box = netease.bounding_box()
            assert row_box["height"] <= 46, "行被撑高了（%.0fpx），信息没压在一行" % row_box["height"]
            assert acc.bounding_box()["height"] <= box_nm["height"] + 1, "账号名另起了一行"
            vcol = vip.evaluate("e => getComputedStyle(e).backgroundColor")
            assert vcol in ("rgb(246, 242, 234)",), "会员标签颜色不对: %s" % vcol
            log("  OK 账号名跟平台名同一行，VIP 标签是低调的浅米色")

            # 进度条：宽度比例 ≈ 6/9
            prog = pg.evaluate(
                "() => {const f=document.querySelector('#acctProg'),"
                "t=f.parentElement;"
                "return f.getBoundingClientRect().width / t.getBoundingClientRect().width;}")
            assert abs(prog - 6 / 9) < 0.04, "进度条比例不对：%.2f" % prog
            num = pg.inner_text("#acctNum").replace("\n", "")
            assert num.startswith("6") and "9" in num, "顶部数字不对：%s" % num
            log("  OK 顶部 6/9 与进度条一致（%.0f%%）" % (prog * 100))

            # 退出做成纯文字（浅灰、无边框），别跟「打开」抢
            out = rows.filter(has_text="微博").first.locator('button[data-act="out"]')
            ob = out.evaluate(
                "e => {const s=getComputedStyle(e);"
                "return {bc:s.borderTopColor, bg:s.backgroundColor, c:s.color};}")
            assert ob["bc"] in ("rgba(0, 0, 0, 0)", "transparent"), \
                "「退出」还看得见边框: %s" % ob["bc"]
            assert ob["bg"] in ("rgba(0, 0, 0, 0)",), "「退出」还有底色"
            assert ob["c"] == "rgb(168, 162, 158)", "「退出」颜色不对: %s" % ob["c"]
            log("  OK 「退出」是纯文字按钮，不抢「打开」")

            # 装好扩展以后，教程自动收起，9 个平台回到一屏
            assert not pg.locator("#extHow").is_visible(), \
                "扩展已连上，「怎么装」还占着地方"
            card = pg.locator("#acctDrawer .mcard").bounding_box()
            last = rows.last.bounding_box()
            assert last["y"] + last["height"] <= card["y"] + card["height"] + 1, \
                "最后一行被卡片切掉了"
            log("  OK 9 个平台在 841 高的屏幕上完整显示，没有半行")

            pg.locator("#acctDrawer .mcard").screenshot(path=SHOT)
            log("  截图:", SHOT)

            # 窄窗不横向溢出
            pg.set_viewport_size({"width": 700, "height": 760})
            pg.wait_for_timeout(400)
            over = pg.evaluate(
                "() => document.documentElement.scrollWidth - window.innerWidth")
            assert over <= 1, "窄窗横向溢出 %dpx" % over
            cw = pg.locator("#acctDrawer .mcard").bounding_box()["width"]
            assert cw <= 700 - 40, "窄窗卡片宽了：%.0f" % cw
            log("  OK 700px 窄窗不溢出")

            assert not errs, "界面脚本报错: %s" % errs[:2]

            # 没装扩展时：三步直接摊开（不用先点一下），第一步就是进扩展页
            pg2 = b.new_page(viewport={"width": 1204, "height": 841},
                             device_scale_factor=2)
            pg2.route("**/api/login/state",
                      lambda r: r.fulfill(status=200, content_type="application/json",
                                          body=json.dumps(payload(port, synced=False),
                                                          ensure_ascii=False)))
            pg2.goto("http://127.0.0.1:%d/" % port, wait_until="networkidle",
                     timeout=30000)
            pg2.evaluate("() => document.querySelector('#openAcct').click()")
            pg2.wait_for_timeout(700)
            guide = pg2.locator("#extHow")
            assert guide.is_visible(), "没装扩展时「怎么装」没显示"
            assert pg2.locator("#extHow details").count() == 0, "「怎么装」又套回折叠了"
            assert pg2.locator("#extHow .gstep").count() == 3, "步骤不是三步"
            first = pg2.locator("#extHow .gstep").first.inner_text()
            assert "扩展程序" in first and "管理扩展程序" in first, \
                "第一步不是从浏览器菜单进扩展页：%s" % first
            assert "地址栏" not in first and "粘贴" not in first, \
                "第一步还在教复制粘贴：%s" % first
            assert "点上面的「一键装小助手」" not in pg2.inner_text("#extHow"), \
                "还在让用户点一次已经点过的按钮"
            log("  OK 没装时三步摊着：先开扩展页 → 开发者模式 → 加载文件夹")
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

# -*- coding: utf-8 -*-
"""境外站：不用设代理，有梯子自动走；没梯子先说一句。

跑法：python tests/test_net_hint.py

不依赖外网：代理探测只打本机端口；UI 段把 /api/net 拦成「没梯子」或「有梯子」，
/api/add 拦成假的，所以不会真去下 YouTube。
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
EXE = r"C:/Program Files/Google/Chrome/Application/chrome.exe"


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


def get(port, path):
    return json.loads(urllib.request.urlopen(
        "http://127.0.0.1:%d%s" % (port, path), timeout=20).read())


def closed_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def main():
    log("=== 1. 代理怎么选（不动网络）===")
    import core
    assert core._proxy_works("127.0.0.1", closed_port()) is False, "空端口被当成代理了"
    assert core.proxy_reaches("http://127.0.0.1:%d" % closed_port()) is False, \
        "连不上的代理被当成能到境外"
    assert core.proxy_reaches("") is False, "空代理地址该是 False"
    assert core.proxy_reaches("socks5://127.0.0.1:9") is True, "socks 认不出来时先当好用"
    lp = core.local_proxy()
    assert lp == "" or lp.startswith("http://127.0.0.1:"), "本机代理地址不对：%r" % lp
    log("  本机自动探到的代理：", lp or "没有（这台机器现在没开梯子）")
    assert core.resolve_proxy("youtube", "http://127.0.0.1:9") == "http://127.0.0.1:9", \
        "老配置里填的代理不再生效"
    assert core.resolve_proxy("bilibili", "") == "", "国内站不该被塞代理"
    assert isinstance(core.reach_overseas(), bool), "境外连通性探测没返回真假"
    log("  OK 设置里填的照用、国内站直连、探测只回真假")

    log("=== 2. 界面把两个看不懂的入口收走了 ===")
    html = open(os.path.join(ROOT, "web", "index.html"), encoding="utf-8").read()
    js = open(os.path.join(ROOT, "web", "app.js"), encoding="utf-8").read()
    for gone in ("cfgProxy", "btnCopyBookmark", "btnGrabClip", "网络代理", "网页书签"):
        assert gone not in html, "index.html 里还有 %s" % gone
    assert "warnOverseas" in js and "梯子" in js, "前端没有没梯子的提醒"
    log("  OK 代理输入框 / 网页书签都不在，提醒逻辑在")

    tmp = tempfile.mkdtemp(prefix="vdl_net_")
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

        log("=== 3. /api/net 说清楚现在能不能到境外 ===")
        net = get(port, "/api/net")
        assert isinstance(net.get("proxy"), str), "proxy 该是字符串：%s" % net
        assert isinstance(net.get("overseas"), bool), "overseas 该是真假：%s" % net
        t0 = time.time()
        get(port, "/api/net")
        gap = time.time() - t0
        assert gap < 3, "第二次还去探网（%.1fs），缓存没生效" % gap
        log("  OK %s，二次调用 %.2fs" % (net, gap))

        log("=== 4. 没梯子贴 YouTube：先说一句，别让人干等 ===")
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            kw = {"headless": True, "args": ["--mute-audio"]}
            if os.path.isfile(EXE):
                kw["executable_path"] = EXE
            b = p.chromium.launch(**kw)
            pg = b.new_page(viewport={"width": 1204, "height": 841})
            errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)))
            net_body = {"proxy": "", "overseas": False}

            pg.route("**/api/net", lambda r: r.fulfill(
                status=200, content_type="application/json",
                body=json.dumps(net_body)))
            pg.route("**/api/add", lambda r: r.fulfill(
                status=200, content_type="application/json",
                body=json.dumps({"added": [{"platform_name": "YouTube"}],
                                 "skipped": []})))

            pg.goto("http://127.0.0.1:%d/" % port, wait_until="networkidle")
            pg.wait_for_timeout(700)
            pg.fill("#input", "https://www.youtube.com/watch?v=dQw4w9WgXcQ")
            pg.press("#input", "Enter")
            pg.wait_for_timeout(1200)
            txt = pg.inner_text("#toast")
            assert "梯子" in txt, "没梯子还闷头去下，界面上没提醒：%r" % txt
            log("  提示：", txt)

            log("=== 5. 有梯子就不啰嗦 ===")
            net_body.update({"proxy": "http://127.0.0.1:7890", "overseas": True})
            pg.reload(wait_until="networkidle")
            pg.wait_for_timeout(700)
            pg.fill("#input", "https://www.youtube.com/watch?v=dQw4w9WgXcQ")
            pg.press("#input", "Enter")
            pg.wait_for_timeout(1200)
            txt = pg.inner_text("#toast")
            assert "梯子" not in txt, "有梯子还弹提示：%r" % txt
            log("  OK 有代理时安静：", txt)

            log("=== 6. 高级页只剩命名模板 + Cookie ===")
            pg.evaluate("() => document.querySelector('#openSet').click()")
            pg.wait_for_timeout(300)
            pg.evaluate("() => document.querySelector('#openAdv').click()")
            pg.wait_for_timeout(300)
            body = pg.inner_text("#setAdv")
            for want in ("命名模板", "Cookie"):
                assert want in body, "高级页缺 %s" % want
            for gone in ("网络代理", "网页书签"):
                assert gone not in body, "高级页还有 %s" % gone
            assert pg.locator("#cfgProxy").count() == 0, "代理输入框还在"
            pg.screenshot(path=os.path.join(tempfile.gettempdir(), "vdl_net_adv.png"))
            assert not errs, "页面脚本报错：%s" % errs[:2]
            b.close()
            log("  OK 两页都干净")
    finally:
        try:
            srv.terminate()
            srv.wait(timeout=8)
        except Exception:
            try:
                srv.kill()
            except Exception:
                pass
        logf.close()
    log("=== 全过 ===")


if __name__ == "__main__":
    main()

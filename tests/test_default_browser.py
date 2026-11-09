# -*- coding: utf-8 -*-
"""登录跳「系统默认浏览器」这一轮。

跑之前先起服务（源码版，带 VDL_NO_OPEN=1 避免自测时真弹窗口）：
    VDL_NO_OPEN=1 python server.py none
    python tests/test_default_browser.py

验证点：
  1. 默认浏览器是运行时读系统设置得到的，不是写死的 Edge；exe 真实存在
  2. 登录窗口的启动参数指向这个 exe，且 playwright 真能把它拉起来
  3. 接口把默认浏览器名字给到前端；所有站点都能「粘贴」cookie，Suno 也有
  4. 「日常浏览器」入口调系统关联；贴错 cookie 给人话提示，贴对能存
  5. 界面：账号面板显示当前默认浏览器、每行有 ↗，点一下不报错
"""
import json
import os
import sys
import tempfile
import shutil
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

URL = os.environ.get("VDL_TEST_URL", "http://127.0.0.1:8765").rstrip("/")
OP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def log(*a):
    print(*a)
    sys.stdout.flush()


def get(path, timeout=20):
    with OP.open(URL + path, timeout=timeout) as r:
        return json.load(r)


def post(path, body, timeout=60):
    req = urllib.request.Request(
        URL + path, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with OP.open(req, timeout=timeout) as r:
        return json.load(r)


def test_detect():
    import login
    login._DEFAULT_CACHE.update(ts=0, value=None)
    d = login.default_browser()
    log("  默认浏览器:", d)
    assert d["name"] and d["name"] != "默认浏览器", "没认出默认浏览器是什么"
    assert d["exe"] and os.path.isfile(d["exe"]), "默认浏览器 exe 路径不对"
    opts, info = login.launch_options()
    log("  登录窗口参数:", opts)
    assert "executable_path" in opts, "没用默认浏览器的 exe"
    assert os.path.isfile(opts["executable_path"]), "登录窗口指向的 exe 不存在"
    assert opts["executable_path"] == d["exe"], "登录窗口跟默认浏览器不一致"
    log("  ✓ 识别 / 启动参数 OK")


def test_api():
    d = get("/api/login/state")
    b = d.get("default_browser") or {}
    assert b.get("name"), "/api/login/state 没带默认浏览器"
    assert b["name"] != "默认浏览器", "服务端没读到真实默认浏览器"
    assert b.get("login_name"), "没告诉前端登录窗口实际用哪个浏览器"
    sites = {s["id"]: s for s in d["sites"]}
    assert sites["suno"]["importable"], "Suno 应该能贴 cookie"
    assert "__session" in sites["suno"]["need"], "Suno 关键 cookie 没返回给前端"
    assert all(s["importable"] for s in d["sites"]), "还有站点不让贴 cookie"
    log("  服务端默认浏览器:", b["name"])

    bad = post("/api/login/open_default", {"platform": "nope"})
    assert not bad["ok"], "不存在的平台不该说打开成功"
    good = post("/api/login/open_default", {"platform": "suno"})
    assert good["ok"], "日常浏览器入口失败: %s" % good.get("error")
    log("  ✓ 接口 OK")


def test_import():
    import login
    ok, msg = login.import_cookie("suno", "foo=bar; abc=def")
    assert not ok and "__session" in msg, "贴错 cookie 没给出关键项提示: %s" % msg
    log("  贴错 ->", msg)

    # 真存一份再删掉，确认通用平台（非音乐）也能走通
    bak = os.path.join(login.COOKIE_DIR, "suno.txt")
    had = os.path.isfile(bak)
    old = open(bak, "rb").read() if had else None
    try:
        ok, msg = login.import_cookie("suno", "__session=fake_for_test; abc=1")
        assert ok, "贴对的 cookie 没存上: %s" % msg
        txt = open(bak, encoding="utf-8").read()
        assert "__session" in txt and "fake_for_test" in txt, "cookie 没写进文件"
        log("  ✓ 通用平台贴 cookie OK")
    finally:
        if had:
            with open(bak, "wb") as f:
                f.write(old)
        else:
            try:
                os.remove(bak)
            except OSError:
                pass


def test_launch_real_browser():
    """用默认浏览器自己的 exe 真起一个无头实例，别只验证字符串。"""
    import login
    from playwright.sync_api import sync_playwright
    opts, _ = login.launch_options()
    work = tempfile.mkdtemp(prefix="vdl_browser_e2e_")
    try:
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                user_data_dir=work, headless=True,
                args=["--disable-blink-features=AutomationControlled"], **opts)
            pg = ctx.pages[0] if ctx.pages else ctx.new_page()
            pg.goto("about:blank", timeout=15000)
            assert isinstance(ctx.cookies(), list), "读不到 cookies API"
            log("  ✓ 默认浏览器 exe 真能启动（UA:", pg.evaluate("navigator.userAgent")[:52], "…）")
            ctx.close()
    finally:
        shutil.rmtree(work, ignore_errors=True)


def test_ui():
    from playwright.sync_api import sync_playwright
    exe = r"C:/Program Files/Google/Chrome/Application/chrome.exe"
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=exe, headless=True,
                              args=["--autoplay-policy=no-user-gesture-required"])
        pg = b.new_page(viewport={"width": 1100, "height": 900})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(URL + "/", wait_until="networkidle", timeout=30000)
        pg.wait_for_timeout(900)
        # 顶栏按钮在窄视口可能滚出屏幕，直接触发点击，测的是面板行为
        pg.evaluate("() => document.querySelector('#openAcct').click()")
        pg.wait_for_timeout(900)
        # 1.24.3：面板只留动作，默认浏览器名放到「打开」按钮的 title 里
        assert pg.locator("#acctDef").count() == 0, "默认浏览器说明又回来了"
        assert pg.locator(".acct-lead").count() == 0, "解释段又回来了"
        title = pg.locator('#logins [data-act="login"]').first.get_attribute("title")
        log("  打开按钮 title:", title)
        assert "Chrome" in title or "Edge" in title or "Firefox" in title, \
            "没认出默认浏览器: %s" % title
        n = pg.locator('#logins [data-act="login"]').count()
        assert n >= 8, "每行都该有「打开」按钮，实际 %d" % n
        assert pg.locator('#logins [data-act="login"]').first.inner_text() == "打开"
        assert pg.locator('#logins [data-act="daily"]').count() == 0, "重复的 ↗ 没去掉"
        assert pg.locator("#extCard").count() == 1, "缺一键同步扩展引导"
        log("  打开按钮:", n, "个，扩展引导在")
        pg.locator('#logins [data-act="login"]').first.click()
        pg.wait_for_timeout(400)
        assert not errs, "界面脚本报错: %s" % errs[:2]
        log("  ✓ 界面 OK")
        b.close()


def main():
    log("=== 1/5 默认浏览器识别 ===")
    test_detect()
    log("=== 2/5 接口 ===")
    test_api()
    log("=== 3/5 通用平台贴 cookie ===")
    test_import()
    log("=== 4/5 真启动默认浏览器 ===")
    test_launch_real_browser()
    log("=== 5/5 界面 ===")
    test_ui()
    log("\n全部通过 ✅")


if __name__ == "__main__":
    main()

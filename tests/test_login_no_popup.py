# -*- coding: utf-8 -*-
"""「登录不再弹无痕窗口」这一轮。

跑之前先起服务（源码版，带 VDL_NO_OPEN=1 避免自测时真弹窗口）：
    VDL_NO_OPEN=1 python server.py none
    python tests/test_login_no_popup.py

验证点：
  1. 点「打开」不再创建 Playwright 线程 / 独立 profile，状态是 opened
  2. 接口返回人话消息，且指向用户日常的浏览器
  3. 扩展格式（含 HttpOnly）的 cookie 能正确落盘，别站域名 / 缺关键项会被挡
  4. /api/login/extension 端点通、CORS 预检通、同步后 state 显示已连接
  5. 扩展文件齐全且 manifest 合法；界面里没有「独立窗口登录」的旧说法
"""
import json
import os
import sys
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


def post(path, body, timeout=30, headers=None):
    h = {"Content-Type": "application/json"}
    h.update(headers or {})
    req = urllib.request.Request(
        URL + path, data=json.dumps(body).encode("utf-8"),
        headers=h, method="POST")
    with OP.open(req, timeout=timeout) as r:
        return json.load(r)


def ext_cookies(domain="suno.com", key="__session", val="fake_sync_value"):
    """模拟 chrome.cookies.getAll 的返回，HttpOnly 也在里面。"""
    return [
        {"name": key, "value": val, "domain": domain, "path": "/",
         "secure": True, "httpOnly": True, "expirationDate": 1999999999},
        {"name": "theme", "value": "dark", "domain": domain, "path": "/",
         "secure": False, "httpOnly": False, "expirationDate": 0},
    ]


def test_no_popup():
    import login
    before = [t.name for t in __import__("threading").enumerate()]
    ok, msg = login.start("suno")
    assert ok, "打开失败: %s" % msg
    st = login.status()
    log("  start ->", st["state"], "|", msg)
    assert st["state"] == "opened", "还在走旧的等待登录流程: %s" % st
    assert "已在" in msg and "Suno" in msg, "打开反馈不对: %s" % msg
    assert "窗口" not in msg and "无痕" not in msg, "又加回解释性文案了: %s" % msg
    assert "Chrome" in msg or "Edge" in msg or "浏览器" in msg, "没说用的哪个浏览器"
    after = [t.name for t in __import__("threading").enumerate()]
    assert after == before, "不该起任何后台线程（说明又去拉 Playwright 了）"
    assert not hasattr(login, "_worker"), "旧的 Playwright worker 还在"
    # 真的走系统关联，别哪天又被人改回 Playwright
    if sys.platform == "win32":
        from unittest.mock import patch
        # 自测进程设了 VDL_NO_OPEN 防止真弹窗，这里临时置空才测得到系统关联
        with patch.dict(os.environ, {"VDL_NO_OPEN": ""}), \
                patch("os.startfile") as m:
            login.start("suno")
            assert m.called, "没走系统关联"
            url = m.call_args[0][0]
            assert url.startswith("https://suno.com"), url
            log("  系统关联 os.startfile ->", url)
    log("  ✓ 不再弹独立 / 无痕窗口")


def test_api_start():
    d = post("/api/login/start", {"platform": "bilibili"})
    assert d["ok"] and d["msg"], "接口没返回人话消息: %s" % d
    assert d["status"]["state"] == "opened", "服务端状态不对: %s" % d["status"]
    assert "窗口" not in d["msg"], "打开反馈里还有解释性文案: %s" % d["msg"]
    log("  /api/login/start ->", d["msg"])
    bad = post("/api/login/start", {"platform": "nope"})
    assert not bad["ok"], "不存在的平台不该说成功"
    log("  ✓ 打开接口 OK")


def test_bulk_import():
    import login
    f = os.path.join(login.COOKIE_DIR, "suno.txt")
    had = os.path.isfile(f)
    old = open(f, "rb").read() if had else None
    try:
        # 别站域名、缺关键项，都得挡住并给原因
        ok, msg = login.import_cookies_bulk(
            "suno", [{"name": "__session", "value": "x", "domain": "evil.com"}])
        assert not ok and "先去网站登一次" in msg, "别站域名没挡住: %s" % msg
        ok, msg = login.import_cookies_bulk(
            "suno", [{"name": "theme", "value": "x", "domain": "suno.com"}])
        assert not ok and "__session" in msg, "缺关键项没提示要哪个: %s" % msg
        log("  挡住:", msg)

        ok, msg = login.import_cookies_bulk("suno", ext_cookies())
        assert ok, "扩展格式没存上: %s" % msg
        txt = open(f, encoding="utf-8").read()
        assert "__session\tfake_sync_value" in txt, "Netscape 行不对: %s" % txt[-200:]
        assert ".suno.com" in txt and "1999999999" in txt, "域名 / 过期时间丢了"
        log("  ✓ 扩展 cookie 落盘 OK:", msg)
    finally:
        if had:
            with open(f, "wb") as fp:
                fp.write(old)
        else:
            try:
                os.remove(f)
            except OSError:
                pass


def test_extension_endpoint():
    # HTTP 打的是服务端那份代码，cookie 也写在服务端目录；
    # 从 /api/login/state 拿到真实目录再备份，别只备份测试进程的源码目录。
    d0 = get("/api/login/state")
    cdir = d0.get("cookie_dir") or ""
    assert cdir, "服务端没给 cookie 目录，没法安全还原"
    # 隔离环境里这个目录还没被创建（真实用户是登录/导入时建），
    # 测试要往里写再还原，先补上
    os.makedirs(cdir, exist_ok=True)
    assert os.path.isdir(cdir), "cookie 目录建不出来"
    files = [os.path.join(cdir, "%s.txt" % p) for p in ("suno", "bilibili")]
    backup = [(p, open(p, "rb").read() if os.path.isfile(p) else None)
              for p in files]
    try:
        req = urllib.request.Request(URL + "/api/login/extension", method="OPTIONS")
        with OP.open(req, timeout=10) as r:
            assert r.headers.get("Access-Control-Allow-Origin") == "*", "预检没放行"
        log("  ✓ CORS 预检")

        d = post("/api/login/extension",
                 {"items": [{"platform": "suno", "cookies": ext_cookies()},
                            {"platform": "bilibili", "cookies": ext_cookies(
                                "bilibili.com", "SESSDATA", "fake_bili")}]},
                 headers={"X-VDL-Ext": "1"})
        assert d["ok"] and len(d["results"]) == 2, "批量同步失败: %s" % d
        assert all(r["ok"] for r in d["results"]), "有站点没同步上: %s" % d["results"]
        log("  批量 ->", d["msg"])

        st = get("/api/login/state")
        assert st["extension"]["synced"], "state 没显示扩展已连接"
        assert st["extension"]["exists"], "扩展目录没被打进包"
        assert isinstance(st["port"], int) and st["port"] >= 8765, "端口没给到前端"
        log("  ✓ 同步后端点 OK，端口", st["port"])
    finally:
        for p, old in backup:
            if old is not None:
                with open(p, "wb") as fp:
                    fp.write(old)
            else:
                try:
                    os.remove(p)
                except OSError:
                    pass


def test_extension_files():
    d = os.path.join(ROOT, "browser-extension")
    for f in ("manifest.json", "background.js", "popup.html", "popup.js",
              "安装说明.txt"):
        assert os.path.isfile(os.path.join(d, f)), "缺文件: %s" % f
    m = json.load(open(os.path.join(d, "manifest.json"), encoding="utf-8"))
    assert m["manifest_version"] == 3, "不是 MV3"
    assert "cookies" in m["permissions"], "没申请 cookies 权限，读不到 HttpOnly"
    hp = " ".join(m["host_permissions"])
    assert "127.0.0.1" in hp and "music.163.com" in hp and "suno.com" in hp, \
        "host_permissions 不全"
    assert "chrome.cookies.getAll" in open(
        os.path.join(d, "background.js"), encoding="utf-8").read(), \
        "扩展没用 cookies API"
    log("  ✓ 扩展文件齐全（MV3 / cookies 权限 / 本机地址）")


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
        pg.evaluate("() => document.querySelector('#openAcct').click()")
        pg.wait_for_timeout(900)
        assert pg.locator(".acct-lead").count() == 0, "解释段又回来了"
        assert pg.locator("#acctDef").count() == 0, "默认浏览器说明又回来了"
        assert pg.locator("#extCard").count() == 1, "缺扩展引导卡"
        assert pg.locator("#extFolder").count() == 0, "点不开的「只开文件夹」还在"
        assert pg.locator("#extPage").count() == 0, "点不开的「只开安装页」还在"
        btns = pg.locator('#logins [data-act="login"]')
        assert btns.count() >= 8, "每行的「打开」按钮少了"
        assert btns.first.inner_text() == "打开", "按钮还叫「登录」"
        assert pg.locator('#logins [data-act="daily"]').count() == 0, \
            "重复的 ↗ 按钮没去掉"
        log("  面板已无解释性小字")
        btns.first.click()
        pg.wait_for_timeout(500)
        assert not errs, "界面脚本报错: %s" % errs[:2]
        log("  ✓ 界面 OK（无痕窗口的入口已消失）")
        b.close()


def main():
    log("=== 1/6 点打开不再弹窗口 ===")
    test_no_popup()
    log("=== 2/6 打开接口 ===")
    test_api_start()
    log("=== 3/6 扩展 cookie 落盘 ===")
    test_bulk_import()
    log("=== 4/6 同步端点 / CORS / state ===")
    test_extension_endpoint()
    log("=== 5/6 扩展文件 ===")
    test_extension_files()
    log("=== 6/6 界面 ===")
    test_ui()
    log("\n全部通过 ✅")


if __name__ == "__main__":
    main()

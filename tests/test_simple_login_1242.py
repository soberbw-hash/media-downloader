# -*- coding: utf-8 -*-
"""1.24.2「傻瓜式登录」回归：界面上不许再出现 F12 / cookie 键名 / 粘贴弹窗。

跑之前起服务（VDL_NO_OPEN=1 免弹窗，建议配 VDL_DATA_DIR 隔离测试用的登录信息）：
    VDL_NO_OPEN=1 VDL_DATA_DIR=<临时目录> python server.py none
    python tests/test_simple_login_1242.py

验证点：
  1. 账号面板可见文字里没有 F12 / Application / DevTools / 任何 cookie 键名
  2. 每行只剩「打开」，旧「粘贴」按钮和旧弹窗都不在了
  3. 手动贴 Cookie 兜底已整个删除，面板上不留入口
  4. 安装只留一个大按钮，三步说明直接摊着（不折叠），点一下接口通并给出动作
  5. 扩展一送登录态进来，界面自己变「已连上」并提示一声
"""
import json
import os
import urllib.request

URL = os.environ.get("VDL_TEST_URL", "http://127.0.0.1:8765").rstrip("/")
OP = urllib.request.build_opener(urllib.request.ProxyHandler({}))
EXE = r"C:/Program Files/Google/Chrome/Application/chrome.exe"

BAD_WORDS = ["F12", "Application", "DevTools", "web_session", "MUSIC_U",
             "__session", "qm_keyst", "粘贴 Cookie", "粘贴 cookie", "Cookies",
             # 1.24.3：解释「自己多安全、多聪明」的话也不许留在面板上
             "不上传", "不共享", "不落盘", "只读取", "只发到本机",
             "127.0.0.1", "不再弹", "无痕", "不用复制", "不用管",
             "一次就够", "更省事", "本机 cookies"]


def log(*a):
    print(*a, flush=True)


def get(path):
    return json.load(OP.open(URL + path, timeout=10))


def post(path, body, headers=None):
    h = {"Content-Type": "application/json"}
    h.update(headers or {})
    req = urllib.request.Request(
        URL + path, data=json.dumps(body).encode("utf-8"),
        headers=h, method="POST")
    return json.load(OP.open(req, timeout=20))


def ext_cookies():
    return [
        {"name": "__session", "value": "fake_sync_1242", "domain": "suno.com",
         "path": "/", "secure": True, "httpOnly": True,
         "expirationDate": 1999999999},
        {"name": "theme", "value": "dark", "domain": "suno.com",
         "path": "/", "secure": False, "httpOnly": False, "expirationDate": 0},
    ]


def main():
    from playwright.sync_api import sync_playwright

    d0 = get("/api/login/state")
    cdir = d0.get("cookie_dir") or ""
    assert cdir, "服务端没给 cookie 目录，没法安全还原"
    # 全新隔离的数据目录里 cookies/ 可能还没建，先建好再备份
    os.makedirs(cdir, exist_ok=True)
    files = [os.path.join(cdir, "%s.txt" % p) for p in ("suno", "bilibili")]
    backup = [(p, open(p, "rb").read() if os.path.isfile(p) else None)
              for p in files]
    errs = []
    try:
        with sync_playwright() as p:
            b = p.chromium.launch(executable_path=EXE, headless=True)
            pg = b.new_page(viewport={"width": 1180, "height": 940})
            pg.on("pageerror", lambda e: errs.append(str(e)))
            pg.goto(URL + "/", wait_until="networkidle", timeout=30000)
            pg.evaluate("() => document.querySelector('#openAcct').click()")
            pg.wait_for_timeout(900)

            # ---- 1. 小白看不懂的词全都不许出现在面板上 ----
            txt = pg.inner_text("#acctDrawer")
            left = [w for w in BAD_WORDS if w in txt]
            assert not left, "账号面板还有术语: %s" % left
            log("  ✓ 面板里没有 F12 / 键名 / 技术弹窗文案")

            # ---- 2. 主流程只剩「打开」 ----
            assert pg.locator('#logins [data-act="paste"]').count() == 0, \
                "每行还留着「粘贴」按钮"
            assert pg.locator("#ckModal").count() == 0, "旧弹窗还在 DOM 里"
            n = pg.locator('#logins [data-act="login"]').count()
            assert n >= 8, "打开按钮少了: %d" % n
            log("  ✓ 每行只剩「打开」（%d 个），粘贴弹窗已删" % n)

            # ---- 3. 手动贴 Cookie 兜底整个删掉 ----
            for i in ("manualBox", "ckSite", "ckInput", "ckSave"):
                assert pg.locator("#" + i).count() == 0, \
                    "手动贴 Cookie 的入口 %s 还在" % i
            assert "手动贴" not in pg.inner_text("#acctDrawer"), \
                "面板上还留着手动贴的文案"
            log("  ✓ 手动贴 Cookie 入口已删干净")

            # ---- 4. 装小助手只留一个按钮 ----
            btn = pg.locator("#btnExtSetup")
            assert btn.count() == 1 and "选中扩展文件夹" in btn.inner_text(), \
                "安装主按钮不对: %s" % btn.inner_text()
            assert pg.locator("#extHow details").count() == 0, "三步又套回折叠了"
            assert pg.locator("#extHow .gstep").count() == 3, "三步没显示出来"
            btn.click()
            pg.wait_for_timeout(700)
            assert pg.locator("#extHow .gstep").count() == 3, "点完三步没了"
            assert "扩展程序" in pg.inner_text("#toast"), \
                "选中文件夹没给下一步"
            log("  ✓ 选中扩展文件夹：接口通、三步常驻、反馈指路到浏览器菜单")

            # ---- 5. 扩展一同步，界面自己变，还主动提示一声 ----
            pg.evaluate("""() => {
              window.__toastLog = [];
              const el = document.querySelector('#toast');
              new MutationObserver(() => {
                const t = (el.textContent || '').trim();
                if (t) window.__toastLog.push(t);
              }).observe(el, {childList: true, subtree: true, characterData: true});
            }""")
            r = post("/api/login/extension",
                     {"items": [{"platform": "suno", "cookies": ext_cookies()}]},
                     headers={"X-VDL-Ext": "1"})
            assert r.get("ok"), "模拟扩展同步失败: %s" % r
            pg.wait_for_timeout(2400)
            assert "已连上" in pg.inner_text("#extBadge"), "徽标没变「已连上」"
            assert "重新检查" in pg.locator("#btnExtSetup").inner_text(), \
                "按钮没切成「重新检查」"
            assert pg.locator("#extHow").is_hidden(), "连上后三步说明还占着位置"
            suno = pg.locator("#logins .login-row").filter(has_text="Suno")
            assert "已登录" in suno.first.inner_text(), "Suno 没自动变已登录"
            toasts = pg.evaluate("() => window.__toastLog || []")
            assert any("已登录，可以下了" in t for t in toasts), \
                "没主动提示登录成功: %s" % toasts
            log("  ✓ 扩展一同步就自动变「已连上」+ 提示，toast:", toasts[:2])

            assert not errs, "界面脚本报错: %s" % errs[:2]
            b.close()
        log("全部通过")
    finally:
        for path, old in backup:
            if old is not None:
                with open(path, "wb") as fp:
                    fp.write(old)
            else:
                try:
                    os.remove(path)
                except OSError:
                    pass


if __name__ == "__main__":
    main()

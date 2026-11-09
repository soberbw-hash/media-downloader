# -*- coding: utf-8 -*-
"""1.24.3 回归：账号面板只留「做什么」，解释 / 隐私 / 效果说明一律不许出现。

跑之前起服务（VDL_NO_OPEN=1 免弹窗，建议配 VDL_DATA_DIR 隔离）：
    VDL_NO_OPEN=1 VDL_DATA_DIR=<临时目录> python server.py none
    python tests/test_no_fluff_1243.py

验证点：
  1. 静态文件里被删掉的解释性 DOM / 文案没被加回来
  2. 浏览器里打开账号抽屉，可见文字零解释词
  3. 扩展卡片默认只有标题、徽标、一个按钮、一个折叠的三步
  4. 扩展小窗口、清单描述、随包安装说明也不再写隐私辩解
"""
import json
import os
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
URL = os.environ.get("VDL_TEST_URL", "http://127.0.0.1:8765").rstrip("/")
OP = urllib.request.build_opener(urllib.request.ProxyHandler({}))
EXE = r"C:/Program Files/Google/Chrome/Application/chrome.exe"

# 用户点名要删的：解释自己多安全、多聪明、多省事的话
FLUFF = ["不上传", "不共享", "不落盘", "只读取", "只发到本机",
         "127.0.0.1", "不再弹", "无痕窗口", "不用复制", "不用管",
         "一次就够", "更省事", "本机 cookies 目录", "只存在这台电脑",
         "什么都不用做", "不用回来点", "自动同步一次",
         "不登录拿不到地址", "登了更稳"]
GONE_IDS = ["acctDef", "extLead", "extLast"]


def log(*a):
    print(*a, flush=True)


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def static_checks():
    html = read("web/index.html")
    panel = html[html.index('id="acctDrawer"'):html.index('id="histDrawer"')]
    for i in GONE_IDS:
        assert 'id="%s"' % i not in panel, "解释性元素 %s 又加回来了" % i
    for cls in ("acct-def", "acct-lead", "ext-p", "ext-note", "manual-tip"):
        assert 'class="%s"' % cls not in panel, "解释性段落 .%s 又加回来了" % cls
    left = [w for w in FLUFF if w in panel]
    assert not left, "账号面板源码里还有废话: %s" % left
    assert panel.count("<p ") == 0, "账号面板里还留着说明段 <p>"
    log("  ✓ 账号面板源码：解释性 DOM 与文案为零")

    popup = read("browser-extension/popup.html")
    assert 'class="tip"' not in popup and 'class="note"' not in popup, \
        "扩展小窗口的说明又加回来了"
    left = [w for w in FLUFF if w in popup]
    assert not left, "扩展小窗口还有废话: %s" % left
    manifest = json.loads(read("browser-extension/manifest.json"))
    desc = manifest.get("description", "")
    assert not any(w in desc for w in ("不上传", "127.0.0.1")), \
        "扩展描述里还在解释隐私: %s" % desc
    inst = read("browser-extension/安装说明.txt")
    assert "【隐私】" not in inst and "【为什么非要装一下】" not in inst, \
        "安装说明里又写起长篇解释了"
    for name in ("使用说明.txt", "Mac使用说明.txt", "README.md"):
        t = read(name)
        left = [w for w in ("不上传", "不共享", "不落盘", "只发到本机")
                if w in t]
        assert not left, "%s 还有隐私辩解: %s" % (name, left)
    log("  ✓ 扩展小窗口 / 清单 / 安装说明 / 三份文档：只留动作")


def ui_checks():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=EXE, headless=True)
        pg = b.new_page(viewport={"width": 1180, "height": 940})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(URL + "/", wait_until="networkidle", timeout=30000)
        pg.evaluate("() => document.querySelector('#openAcct').click()")
        pg.wait_for_timeout(900)

        txt = pg.inner_text("#acctDrawer")
        left = [w for w in FLUFF if w in txt]
        assert not left, "面板可见文字还有废话: %s" % left
        for i in GONE_IDS:
            assert pg.locator("#" + i).count() == 0, "%s 又在界面上了" % i
        assert pg.locator("#logins .lg-t span").count() == 0, \
            "分组副标题（解释小字）又加回来了"
        log("  ✓ 抽屉可见文字零解释词（%d 字）" % len(txt))

        card = pg.locator("#extCard")
        assert card.locator("p").count() == 0, "扩展卡里还留着说明段"
        assert card.locator("button.primary").count() == 1, "主按钮不是一个"
        how = pg.locator("#extHow")
        assert not how.evaluate("e => e.open"), "三步说明没默认收起"
        # 别的回归可能先同步过扩展，这时按钮会变成「重新检查」，都算正常
        label = pg.locator("#btnExtSetup").inner_text()
        assert label in ("一键装小助手", "重新检查"), "主按钮文案不对: %s" % label
        log("  ✓ 扩展卡默认只有标题 + 一个按钮 + 折叠的三步")

        # 三步展开后是纯动作，不该夹带效果说明；已连上时它会自动隐藏
        if how.is_visible():
            how.evaluate("e => e.open = true")
            pg.wait_for_timeout(150)
            steps = pg.inner_text("#extHow")
            left = [w for w in FLUFF if w in steps]
            assert not left, "三步里夹了废话: %s" % left
            assert steps.count("装") >= 1, "三步说明不见了"
            log("  ✓ 三步说明只讲做什么")
        else:
            log("  ✓ 三步说明已收起（源码层面已校验文案）")

        assert not errs, "界面脚本报错: %s" % errs[:2]
        b.close()


def main():
    static_checks()
    ui_checks()
    log("全部通过")


if __name__ == "__main__":
    main()

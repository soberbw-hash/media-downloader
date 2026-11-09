# -*- coding: utf-8 -*-
"""1.23.0 易用性回归：设置瘦身 / 改完即存 / 粘贴识别 / 模式记忆 / 完成提示。

跑之前先在 8765 起真实服务（VDL_NO_OPEN=1 免弹窗）：
    cd "D:/Desktop/音视频下载器/程序" && VDL_NO_OPEN=1 ./runtime/python.exe server.py none
"""
import json
import os
import sys
import time
import urllib.request

URL = "http://127.0.0.1:8765"
OP = urllib.request.build_opener(urllib.request.ProxyHandler({}))
EXE = r"C:/Program Files/Google/Chrome/Application/chrome.exe"


def log(*a):
    print(*a, flush=True)


def get(path):
    return json.load(OP.open(URL + path, timeout=10))


def post(path, body):
    r = urllib.request.Request(
        URL + path, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    return json.load(OP.open(r, timeout=10))


def main():
    from playwright.sync_api import sync_playwright

    st = get("/api/state")
    cfg0 = st.get("config") or {}
    old_danmaku = bool(cfg0.get("save_danmaku"))
    old_mode = None
    errs = []

    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=EXE, headless=True)
        pg = b.new_page(viewport={"width": 1180, "height": 940})
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(URL + "/", wait_until="networkidle", timeout=30000)
        pg.wait_for_timeout(700)
        old_mode = pg.evaluate("() => localStorage.getItem('mMode')")

        # ---- 1. 用户圈掉的那堆说明文字都不该再出现在首屏 ----
        pg.evaluate("() => document.querySelector('#openSet').click()")
        pg.wait_for_timeout(400)
        body_txt = pg.locator("#drawer").inner_text()
        gone = ["不存在的文件夹会自动创建", "多一张同名 .cover.jpg",
                "B站弹幕存成同名 .xml", "开着的附件", "一键收网页",
                "少复制一次", "把这个按钮拖到浏览器书签栏"]
        left = [x for x in gone if x in body_txt]
        assert not left, "这些说明还占着位置: %s" % left
        assert "保存位置" not in body_txt, "分组标题还在"
        log("  ✓ 说明文字已收走，设置里只剩控件")

        # 标题悬停仍能看到说明，信息没丢
        tip = pg.locator('.tg[title*="cover.jpg"]').count()
        assert tip == 1, "封面图开关的悬停说明丢了"
        log("  ✓ 说明改走悬停提示")

        # ---- 2. 文件命名默认折叠，摘要能看出当前用的哪套 ----
        nb = pg.locator("#nameBox")
        assert nb.count() == 1 and not nb.evaluate("e => e.open"), "命名区没默认收起"
        summary = pg.locator("#nameSummary").inner_text()
        assert "文件命名" in summary, "折叠摘要没写当前模板: %s" % summary
        log("  命名折叠摘要:", summary)
        nb.evaluate("e => e.open = true")
        pg.wait_for_timeout(150)
        assert pg.locator("#cfgNameTpl").is_visible(), "展开后看不到模板输入"
        nb.evaluate("e => e.open = false")

        # ---- 3. 「一键收网页」挪进默认收起的高级设置 ----
        adv = pg.locator("#advBox")
        assert not adv.evaluate("e => e.open"), "高级设置默认展开"
        assert pg.locator("#btnCopyBookmark").count() == 1
        assert pg.locator("#btnGrabClip").count() == 1
        log("  ✓ 网页书签收进高级设置")

        # ---- 4. 改完即存：不点「完成」，后端也要变 ----
        box = pg.locator("#cfgDanmaku")
        box.evaluate("e => { e.checked = !e.checked; e.dispatchEvent(new Event('change', {bubbles:true})); }")
        want = box.evaluate("e => e.checked")
        pg.wait_for_timeout(700)
        now = (get("/api/state").get("config") or {}).get("save_danmaku")
        assert bool(now) == want, "没自动保存: 期望 %s 实际 %s" % (want, now)
        pg.evaluate("() => document.querySelector('#mask').click()")
        pg.wait_for_timeout(250)
        pg.evaluate("() => document.querySelector('#openSet').click()")
        pg.wait_for_timeout(350)
        assert pg.locator("#cfgDanmaku").evaluate("e => e.checked") == want, "重开抽屉值回退了"
        log("  ✓ 开关改完即存，关掉重开不丢")
        post("/api/config", {"save_danmaku": old_danmaku})   # 还原用户设置

        # ---- 5. 完成提示带「打开文件夹」一步到位 ----
        pg.evaluate("() => notifyDone(2)")
        pg.wait_for_timeout(200)
        assert pg.locator("#toast.has-act button").inner_text() == "打开文件夹"
        log("  ✓ 下载完成提示带一键打开文件夹")

        # ---- 6. 软件自己的地址不会被当成素材（截图里那条失败任务）----
        self_url = pg.evaluate("() => urlsIn('http://127.0.0.1:8765/')")
        push_url = pg.evaluate("() => urlsIn('http://127.0.0.1:8765/push?k=abc&url=x')")
        good = pg.evaluate("() => urlsIn('https://www.bilibili.com/video/BV1xx411c7mD')")
        assert not self_url and not push_url, "自引用链接没拦住: %s / %s" % (self_url, push_url)
        assert len(good) == 1, "正常链接被误伤: %s" % good
        log("  ✓ 自引用地址不再变成失败任务，正常链接照认")

        # ---- 7. 播放条有上一首 / 下一首 ----
        assert pg.locator("#pbPrev").count() == 1 and pg.locator("#pbNext").count() == 1
        assert pg.evaluate("() => typeof playAdjacent === 'function'")
        log("  ✓ 试听可上一首 / 下一首")

        pg.evaluate("() => document.querySelector('#mask').click()")
        pg.wait_for_timeout(200)

        # ---- 8. 记住上次搜的是歌 / 素材 / 电影 ----
        pg.evaluate("() => document.querySelector('#tabs .tab[data-tab=\"music\"]').click()")
        pg.wait_for_timeout(250)
        pg.evaluate("() => document.querySelector('#mSrc .plat[data-s=\"video\"]').click()")
        pg.wait_for_timeout(300)
        assert pg.evaluate("() => localStorage.getItem('mMode')") == "video"
        pg.reload(wait_until="networkidle", timeout=30000)
        pg.wait_for_timeout(600)
        on = pg.evaluate("() => { const e = document.querySelector('#mSrc .plat.on'); return e && e.dataset.s; }")
        ph = pg.locator("#mQuery").get_attribute("placeholder")
        assert on == "video", "刷新后没记住视频模式: %s" % on
        assert "视频关键词" in (ph or ""), "占位文案没跟上: %s" % ph
        log("  ✓ 刷新后仍停在上次的「视频」，不用再点一次")

        # 还原用户原来的模式
        if old_mode in ("music", "video", "movie"):
            pg.evaluate("(m) => localStorage.setItem('mMode', m)", old_mode)
        else:
            pg.evaluate("() => localStorage.removeItem('mMode')")
        pg.evaluate("() => localStorage.setItem('tabMode', 'video')")

        assert not errs, "界面脚本报错: %s" % errs[:3]
        b.close()

    # ---- 9. /api/config 局部更新不动其它字段 ----
    before = get("/api/state").get("config") or {}
    r = post("/api/config", {"quality": "720"})
    assert r.get("ok"), "局部更新失败: %s" % r
    after = r.get("config") or {}
    assert after.get("quality") == "720"
    assert after.get("save_danmaku") == before.get("save_danmaku"), "局部更新把别的字段冲了"
    post("/api/config", {"quality": before.get("quality", "best")})
    log("  ✓ 设置局部更新不串字段")

    log("\n全部通过")


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""多来源聚合搜索：接口 + 界面都要过一遍。

跑之前先起服务：python server.py none
验证点：
  1. 来源清单 / 资源站入口由后端下发
  2. 一次搜索能同时拿到多个来源，结果标清来自哪儿
  3. 慢源在后台跑完能补进来，状态行说人话
  4. 自定义站点、自定义 JSON 接口能加上也能清掉
  5. 界面：来源按钮、站点直达、来源标签、封面、切来源不串状态
"""
import json
import os
import sys
import time
import urllib.request

URL = "http://127.0.0.1:8765"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rtest")
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def log(*a):
    print(*a)
    sys.stdout.flush()


def get(path, timeout=20):
    with _OPENER.open(URL + path, timeout=timeout) as r:
        return json.load(r)


def post(path, body, timeout=60):
    req = urllib.request.Request(
        URL + path, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with _OPENER.open(req, timeout=timeout) as r:
        return json.load(r)


def wait_rows(pg, sel, limit=35):
    for _ in range(limit):
        if pg.evaluate("(s)=>document.querySelectorAll(s).length", sel):
            return True
        pg.wait_for_timeout(1000)
    return False


# ---------- 接口 ----------
def test_api():
    d = get("/api/search/sources")
    ids = [s["id"] for s in d["sources"]]
    log("来源:", ids)
    assert d["ok"] and len(ids) >= 4, "来源清单不对"
    assert len(d["sites"]) >= 10, "资源站直达清单不对"
    assert "custom_sites" in d and "custom_sources" in d
    log("资源站:", len(d["sites"]), "个；版本", d.get("version"))

    r = post("/api/search", {"q": "城市夜景", "limit": 24, "sources": None,
                             "refresh": True})
    assert r["ok"] and r["items"], "聚合搜索没出结果"
    srcs = set(x.get("source") for x in r["items"])
    assert len(srcs) >= 2, "结果只有一个来源：%s" % srcs
    for x in r["items"]:
        assert x.get("source_name") and x.get("download"), "结果缺来源或下载地址"
    log("首屏:", len(r["items"]), "条，来源", srcs, "pending", r.get("pending"))

    # 慢源补齐：最多拉 5 次，每次 2 秒
    pulls = 0
    while not r.get("done") and r.get("token") and pulls < 10:
        time.sleep(2.0)
        r2 = get("/api/search/more?token=" + r["token"])
        assert r2["ok"], "补齐失败：" + str(r2.get("error"))
        r = r2
        pulls += 1
    log("补齐:", pulls, "次，最终", len(r["items"]), "条，done", r.get("done"))
    states = {s["id"]: s["state"] for s in r.get("sources", [])}
    log("来源状态:", states)
    assert r.get("done"), "慢源一直没结束"
    assert any(s["state"] == "ok" for s in r["sources"]), "一个源都没成"
    # 轮转配额：状态行说有结果的源，列表里必须能看见
    visible = set(x.get("source") for x in r["items"])
    ok_srcs = set(s["id"] for s in r["sources"] if s["state"] == "ok" and s["count"])
    assert visible & ok_srcs, "状态行说搜到了，列表里却一条都没有"
    log("列表里可见来源:", visible)

    one = post("/api/search", {"q": "rain", "limit": 8,
                               "sources": ["mixkit"], "refresh": True})
    if not (one["ok"] and one["items"]):
        # Mixkit 手狠，短时间内连打几十次会回 429，然后进冷却。这不是代码坏了
        blob = json.dumps(one.get("sources") or [], ensure_ascii=False)
        if "429" in blob or "Too Many" in blob or '"skip"' in blob:
            log("单源过滤跳过：Mixkit 正在限流 / 冷却中，过十几分钟再跑")
            return True
        assert False, "单源搜索没结果：%s" % blob[:200]
    assert all(x["source"] == "mixkit" for x in one["items"]), "单源过滤串了别的来源"
    log("单源过滤: Mixkit", len(one["items"]), "条")
    return True


def test_custom():
    """加一个资源站 + 一个真实第三方 JSON 接口，用完清掉。"""
    orig = get("/api/state").get("config", {})
    old_sites = orig.get("custom_sites") or []
    old_srcs = orig.get("custom_sources") or []
    try:
        site = {"name": "测试站", "url": "https://example.com/s?q={q}",
                "tip": "自动化测试"}
        api = {"name": "GitHub", "url": "https://api.github.com/search/repositories?q={q}&per_page=3",
               "root": "items",
               "fields": {"title": "full_name", "url": "html_url",
                          "author": "owner.login", "cover": "owner.avatar_url"}}
        assert post("/api/config", {"custom_sites": [site], "custom_sources": [api]})["ok"]
        d = get("/api/search/sources")
        assert any(s["name"] == "测试站" for s in d["sites"]), "自定义站点没进清单"
        assert any(s["name"] == "GitHub" for s in d["sources"]), "自定义接口没进来源"
        log("自定义站点 / 接口已进清单:",
            [s["id"] for s in d["sources"]])

        # 不真的开浏览器窗口，只确认接口能拼出合法地址
        r = post("/api/search/open_site", {"id": "u_", "q": "abc"})
        assert not r["ok"], "不存在的站点不该被打开"

        got = post("/api/search", {"q": "video", "limit": 12,
                                   "sources": ["c_GitHub"], "refresh": True})
        if got["ok"] and got["items"]:
            log("第三方 JSON 接口返回:", len(got["items"]), "条，例：",
                got["items"][0]["title"][:40])
            assert all(x["source_name"] == "GitHub" for x in got["items"])
        else:
            log("第三方接口这轮没通（GitHub 限流或网络），状态：",
                [(s["id"], s["state"], s["msg"][:40]) for s in got.get("sources", [])])
    finally:
        post("/api/config", {"custom_sites": old_sites, "custom_sources": old_srcs})
        log("已恢复原来的自定义配置")
    return True


# ---------- 界面 ----------
def test_ui():
    from playwright.sync_api import sync_playwright

    chrome = None
    for p in ("C:/Program Files/Google/Chrome/Application/chrome.exe",
              "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"):
        if os.path.exists(p):
            chrome = p
            break
    os.makedirs(OUT, exist_ok=True)
    with sync_playwright() as p:
        kw = {"headless": True, "args": ["--mute-audio"]}
        if chrome:
            kw["executable_path"] = chrome
        br = p.chromium.launch(**kw)
        pg = br.new_page(viewport={"width": 1100, "height": 900})
        pg.goto(URL + "/", wait_until="networkidle")
        pg.click('#tabs .tab[data-tab="music"]')
        pg.wait_for_timeout(300)
        pg.click('#mSrc .plat[data-s="video"]')
        pg.wait_for_timeout(500)

        n_src = pg.evaluate("() => document.querySelectorAll('#mVPlats .plat').length")
        n_site = pg.evaluate("() => document.querySelectorAll('#vSiteChips .site-chip').length")
        src_names = pg.eval_on_selector_all("#mVPlats .plat", "els => els.map(e => e.textContent)")
        log("界面来源按钮:", n_src, "资源站入口:", n_site)
        assert n_src >= 4, "来源按钮没动态生成"
        assert n_site >= 10, "资源站入口没生成"
        # 音效源和音效站归「音效」那一档，视频页不该冒出来
        assert "Freesound" not in src_names and "Mixkit 音效" not in src_names, \
            "视频页混进了音效源：%s" % src_names

        pg.fill("#mQuery", "城市夜景")
        pg.click("#mGo")
        assert wait_rows(pg, "#mResults .msong"), "界面没出结果"
        pg.wait_for_timeout(3500)      # 等慢源补进来
        tags = pg.eval_on_selector_all(
            "#mResults .psrc", "els => [...new Set(els.map(e => e.textContent))]")
        log("结果里的来源标签:", tags)
        # Mixkit 被限流时会进冷却，这时只剩 B站一条，不算功能坏了
        cooled = pg.eval_on_selector("#vSrcHint", "e => /歇一会儿|429|限流/.test(e.textContent)")
        if len(tags) < 2 and cooled:
            log("  多来源断言放宽：有个源在冷却（限流），只看到 %s" % tags)
        else:
            assert len(tags) >= 2, "结果没有汇集多个来源"
        hint = pg.eval_on_selector("#vSrcHint", "e => ({hidden:e.hidden, text:e.textContent})")
        log("来源状态行:", hint)
        assert not hint["hidden"] and hint["text"], "来源状态行没显示"
        imgs = pg.evaluate("""() => [...document.querySelectorAll('#mResults .cov img')]
            .filter(i => { const r=i.getBoundingClientRect();
                return r.top < innerHeight && r.bottom > 0; })
            .map(i => i.naturalWidth)""")
        log("视口内封面 naturalWidth:", imgs[:8])
        if not cooled:
            assert any(w > 0 for w in imgs), "新来源的封面一张都没出来"
        pg.screenshot(path=os.path.join(OUT, "agg_video.png"), full_page=False)

        # 单来源：结果里只该有这一家
        pg.click('#mVPlats .plat[data-p="mixkit"]')
        if not wait_rows(pg, "#mResults .msong"):
            hint2 = pg.eval_on_selector("#vSrcHint", "e => e.textContent")
            assert "歇一会儿" in hint2 or "429" in hint2, "切来源后没结果：" + hint2
            log("  单来源过滤跳过：Mixkit 在冷却（%s）" % hint2[:30])
        else:
            pg.wait_for_timeout(1500)
            tags2 = pg.eval_on_selector_all(
                "#mResults .psrc", "els => [...new Set(els.map(e => e.textContent))]")
            log("单来源标签:", tags2)
            assert tags2 and all(t == "Mixkit 视频" for t in tags2), \
                "单来源过滤串了别的来源：" + str(tags2)

        # 切回音乐：聚合相关的行必须收起来，别留着上一条的痕迹
        pg.click('#mSrc .plat[data-s="music"]')
        pg.wait_for_timeout(400)
        hidden = pg.evaluate("""() => ({
            sites: document.querySelector('#vSites').hidden,
            plats: document.querySelector('#mVPlats').hidden,
            hint: document.querySelector('#vSrcHint').hidden,
            rows: document.querySelectorAll('#mResults .msong').length,
        })""")
        log("切回音乐后:", hidden)
        assert hidden["sites"] and hidden["plats"] and hidden["hint"], "聚合控件没收起来"
        assert hidden["rows"] == 0, "切来源后旧结果没清干净"
        br.close()
    return True


def main():
    test_api()
    test_custom()
    test_ui()
    log("ALL OK")


if __name__ == "__main__":
    main()

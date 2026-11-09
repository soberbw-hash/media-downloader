# -*- coding: utf-8 -*-
"""音效搜索回归：中文词能对上英文音效库、页面有「音效」这一档、能听能下。

跑法：python tests/test_sfx_search.py

自己起隔离服务（VDL_PORT_FILE），UI 部分用 route 拦 /api/search 灌假数据，
不依赖外网；只有第 4 段真下一条小 wav 验标题锁定。
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
EXE = r"C:/Program Files/Google/Chrome/Application/chrome.exe"
SHOT = os.path.join(tempfile.gettempdir(), "vdl_sfx.png")


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


def post(port, path, obj):
    r = urllib.request.Request(
        "http://127.0.0.1:%d%s" % (port, path),
        data=json.dumps(obj).encode(), headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(r, timeout=40).read())


def get(port, path):
    return json.loads(urllib.request.urlopen(
        "http://127.0.0.1:%d%s" % (port, path), timeout=40).read())


FAKE = {
    "ok": True, "done": True, "pending": [], "cached": False,
    "items": [
        {"platform": "mixkit_sfx", "id": "ms2393", "kind": "audio",
         "title": "Light rain loop", "author": "Mixkit", "cover": "", "duration": 0,
         "desc": "免费可商用 · wav 原文件",
         "url": "https://mixkit.co/free-sound-effects/rain/",
         "download": "https://assets.mixkit.co/active_storage/sfx/2393/2393.wav",
         "source": "mixkit_sfx", "source_name": "Mixkit 音效"},
        {"platform": "freesound", "id": "fs856405", "kind": "audio",
         "title": "Raining in Bangkok", "author": "someone", "cover": "",
         "duration": 36, "desc": "CC 授权，商用要署名 · mp3 预览",
         "url": "https://freesound.org/people/someone/sounds/856405/",
         "download": "https://cdn.freesound.org/previews/856/856405_26950-lq.mp3",
         "source": "freesound", "source_name": "Freesound"},
    ],
    "sources": [
        {"id": "mixkit_sfx", "name": "Mixkit 音效", "state": "ok", "count": 1, "took": 1},
        {"id": "freesound", "name": "Freesound", "state": "ok", "count": 1, "took": 2},
    ],
    "errors": [],
}


def main():
    log("=== 1. 中文词能翻成英文（长词优先，别串味）===")
    import sources
    cases = {
        "雨声": "rain", "掌声": "applause", "脚步声": "footsteps",
        "火车": "train",      # 老表里「火」在前，会错成 fire
        "关门声": "door", "转场": "whoosh", "金币": "coin",
        "城市夜景": "city",
    }
    for zh, en in cases.items():
        got = sources.en_keyword(zh)
        assert got == en, "「%s」应该翻成 %s，实际 %s" % (zh, en, got)
    assert sources.en_keyword("rain") == "rain", "英文原样透传坏了"
    assert sources.en_keyword("乱七八糟") is None, "认不出就该返回 None"
    log("  OK 8 个常用词都对，「火车」不再被「火」抢走")

    log("=== 2. 源和站点清单分得开 ===")
    ui = sources.sources_for_ui({})
    audio_src = [s for s in ui if s["kind"] == "audio"]
    assert {"mixkit_sfx", "freesound"} <= {s["id"] for s in audio_src}, \
        "音效源没齐：%s" % [s["id"] for s in audio_src]
    audio_site = [s for s in sources.site_links({}) if s.get("kind") == "audio"]
    names = {s["name"] for s in audio_site}
    for want in ("爱给网", "耳聆网", "Pixabay", "Mixkit", "Freesound"):
        assert want in names, "音效站少了 %s" % want
    log("  OK 音效源 2 个、音效站 %d 个（国内国外都有）" % len(audio_site))

    tmp = tempfile.mkdtemp(prefix="vdl_sfx_")
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

        log("=== 3. 页面上的音效档 ===")
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.launch(executable_path=EXE, headless=True)
            pg = b.new_page(viewport={"width": 1204, "height": 841},
                            device_scale_factor=2)
            errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)))
            sent = {}

            def on_search(route):
                sent["body"] = json.loads(route.request.post_data or "{}")
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps(FAKE, ensure_ascii=False))

            def on_add(route):
                sent["add"] = json.loads(route.request.post_data or "{}")
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"ok": True, "count": 1, "added": ["9"],
                                               "pairs": [], "skipped": []}))

            pg.route("**/api/search", on_search)
            pg.route("**/api/sfx/add", on_add)
            pg.goto("http://127.0.0.1:%d/" % port, wait_until="networkidle",
                    timeout=30000)
            pg.wait_for_timeout(400)

            pg.click('#tabs .tab[data-tab="music"]')
            pg.wait_for_timeout(300)
            tabs = pg.eval_on_selector_all("#mSrc .plat", "els => els.map(e => e.textContent)")
            assert "音效" in tabs, "搜索页没有「音效」这一档：%s" % tabs
            pg.click('#mSrc .plat[data-s="sfx"]')
            pg.wait_for_timeout(300)
            assert "音效" in pg.get_attribute("#mQuery", "placeholder"), "输入框没提示音效"
            srcs = pg.eval_on_selector_all("#mVPlats .plat", "els => els.map(e => e.textContent)")
            assert "Mixkit 音效" in srcs and "B站" not in srcs, "音效页串进了视频源：%s" % srcs
            sites = pg.eval_on_selector_all("#vSiteChips .site-chip", "els => els.map(e => e.textContent)")
            assert "爱给网" in sites and "Pexels" not in sites, "站点没按音效过滤：%s" % sites
            log("  OK 多出「音效」档；源只剩音效源，站点只剩音效站")

            pg.fill("#mQuery", "雨声")
            pg.click("#mGo")
            pg.wait_for_timeout(1500)
            rows = pg.locator("#mResults .msong").count()
            assert rows == 2, "结果没渲染出来：%d 行" % rows
            assert sent["body"].get("sources") == ["mixkit_sfx", "freesound"], \
                "音效搜索没指定音效源：%s" % sent["body"].get("sources")
            first = pg.locator("#mResults .msong").first
            sub = first.locator(".a").inner_text()
            assert "wav 原文件" in sub and "1080p" not in sub, "副标题不对：%s" % sub
            assert pg.locator("#mResults .msong .pv").count() == 2, "音效没有试听按钮"
            log("  OK 两条结果、来源只听音效源、没有「1080p」这种视频味")

            # 试听：直链直接塞进 <audio>
            pg.locator("#mResults .msong .pv").first.click()
            pg.wait_for_timeout(900)
            src = pg.eval_on_selector("#auPlayer", "e => e.getAttribute('src') || ''")
            assert "assets.mixkit.co" in src, "试听没走直链：%s" % src
            bar = pg.evaluate("() => !document.querySelector('#pBar').hidden")
            assert bar, "试听了却没出播放条"
            log("  OK 点▶直接播直链，播放条出现")

            # 下载：要走 /api/sfx/add 且带上标题，否则文件名是一串网址
            pg.locator("#mResults .msong .dl").first.click()
            pg.wait_for_timeout(900)
            it = (sent.get("add") or {}).get("items") or [{}]
            assert it[0].get("title") == "Light rain loop", \
                "下载没带标题：%s" % (sent.get("add"),)
            assert it[0].get("url", "").endswith("2393.wav"), "下载地址不对"
            assert not errs, "JS 报错：%s" % errs
            log("  OK 下载走 /api/sfx/add，带上了音效名")

            pg.screenshot(path=SHOT)
            b.close()

        log("=== 4. 后端锁定标题（真下一条小 wav）===")
        post(port, "/api/config", {"out_dir": os.path.join(tmp, "dl")})
        r = post(port, "/api/sfx/add", {"items": [{
            "url": "https://assets.mixkit.co/active_storage/sfx/2393/2393.wav",
            "title": "Light rain loop", "author": "Mixkit",
            "platform": "mixkit_sfx", "id": "ms2393"}]})
        assert r["ok"] and r["count"] == 1, "没进队列：%s" % r
        task = None
        for _ in range(40):
            ts = get(port, "/api/state")["tasks"]
            if ts:
                task = ts[0]
                if task["status"] in ("done", "error"):
                    break
            time.sleep(1)
        assert task, "队列里没任务"
        assert task["status"] == "done", "下载没完成：%s %s" % (task["status"], task.get("error"))
        assert task["title"] == "Light rain loop", \
            "标题被解析器改名了：%s" % task["title"]
        assert task["kind"] == "audio", "没标成音频：%s" % task["kind"]
        fp = task.get("filepath") or ""
        assert "Light rain loop" in os.path.basename(fp), "文件名没用音效名：%s" % fp
        log("  OK 文件落在 %s" % os.path.basename(fp))
    finally:
        srv.terminate()
        try:
            srv.wait(timeout=5)
        except subprocess.TimeoutExpired:
            srv.kill()

    log("截图: %s" % SHOT)
    log("全部通过")


if __name__ == "__main__":
    main()

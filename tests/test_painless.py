# -*- coding: utf-8 -*-
"""「无痛」这一轮：Suno 不再下坏文件 + 一键收网页 + 视频号解析增强。

跑之前先起服务：python server.py none
验证点：
  1. Suno 游客态：不再把加密片段 / 4.8KB 静音占位当歌，给人话提示且不留文件
  2. 公开可下的 Suno 歌回归正常（别把好的也拦了）
  3. 账号登录里能找到 Suno，境外站点登录窗口会自动带系统代理
  4. 老书签兜底：/push 带钥匙才收，页面说人话（界面上的入口已经收走）
  5. 视频号：本机辅助接口探测不拖慢流程，解析凭证能存能读
  6. 界面：代理 / 网页书签入口都不在，剩下的设置项点一下不报错
"""
import json
import os
import shutil
import sys
import tempfile
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

URL = "http://127.0.0.1:8765"
OP = urllib.request.build_opener(urllib.request.ProxyHandler({}))

GUEST_SONG = "https://suno.com/s/ZvBpJbuGTuN5j7UD"   # 只对登录用户开放
PUBLIC_SONG = "https://suno.com/s/IdazmB1kcRTOjU5p"  # 公开、以前能下


def log(*a):
    print(*a)
    sys.stdout.flush()


def get(path, timeout=20, raw=False):
    with OP.open(URL + path, timeout=timeout) as r:
        b = r.read()
    return b if raw else json.loads(b.decode("utf-8", "replace"))


def post(path, body, timeout=60):
    req = urllib.request.Request(
        URL + path, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with OP.open(req, timeout=timeout) as r:
        return json.load(r)


# ---------- Suno ----------
def test_suno():
    import core
    import suno

    proxy = core.system_proxy() or ""
    out = tempfile.mkdtemp(prefix="suno_e2e_")
    try:
        try:
            p = suno.download(GUEST_SONG, out, lambda **kw: None, "best", proxy)
            raise AssertionError("游客态居然下成了：%s (%s 字节)" %
                                 (p, os.path.getsize(p)))
        except RuntimeError as e:
            msg = str(e)
            log("游客态提示:", msg.replace("\n", " | ")[:150])
            assert "登录" in msg and "Suno" in msg, "提示没说清出路"
            assert "内容不存在" not in msg, "不该再误判成内容不存在"
        assert not os.listdir(out), "失败了还留下坏文件：%s" % os.listdir(out)

        # 顺手确认媒体头校验认得出各种真格式，也拦得住乱码
        good = os.path.join(out, "good.mp3")
        with open(good, "wb") as f:
            f.write(b"ID3\x03\x00\x00" + b"\0" * 40)
        assert suno._valid_media(good)
        with open(good, "wb") as f:
            f.write(b"\x79\x8f\x38\x80\x9c\x72" + b"\0" * 40)
        assert not suno._valid_media(good), "加密乱码没被拦住"
        os.remove(good)

        p = suno.download(PUBLIC_SONG, out, lambda **kw: None, "best", proxy)
        with open(p, "rb") as f:
            head = f.read(3)
        log("公开歌回归:", os.path.basename(p), os.path.getsize(p), head)
        assert os.path.getsize(p) > 200 * 1024 and head == b"ID3", "公开歌被误伤"
    finally:
        shutil.rmtree(out, ignore_errors=True)


def test_login_state():
    d = get("/api/login/state")
    ids = [s["id"] for s in d["sites"]]
    log("登录站点:", ids)
    assert "suno" in ids, "账号登录里没有 Suno"
    suno_site = next(s for s in d["sites"] if s["id"] == "suno")
    assert "加密" in suno_site["tip"], "Suno 说明没讲清为什么"

    import login
    assert login.SITES["suno"].get("overseas"), "Suno 登录窗口忘了走系统代理"
    assert "suno.com" in login.SITES["suno"]["domains"]


# ---------- 一键收网页 ----------
def test_push():
    d = get("/api/state")
    key = d.get("push_key")
    assert key and len(key) >= 8, "后端没发书签钥匙"
    log("书签钥匙:", key[:4] + "…")

    bad = get("/push?k=wrong&url=" + urllib.parse.quote(PUBLIC_SONG, safe=""),
              raw=True).decode("utf-8", "replace")
    assert "没能加进队列" in bad and "对不上" in bad, "错钥匙不该收"

    good = get("/push?k=" + key + "&url=" + urllib.parse.quote(PUBLIC_SONG, safe=""),
               raw=True).decode("utf-8", "replace")
    assert "已加入下载队列" in good, "书签没收下链接"
    tasks = get("/api/state")["tasks"]
    assert any("suno.com" in (t.get("url") or "") for t in tasks), "任务没进队列"
    log("书签推送成功，队列里现在有", len(tasks), "条")
    post("/api/cancel_all", {})     # 别让测试真的把歌下下来


def test_sph_config():
    old = get("/api/state")["config"]
    try:
        r = post("/api/config", {"sph_token": "unit-test-token",
                                 "sph_api": "https://example.com/api/parse"})
        assert r["ok"]
        cfg = get("/api/state")["config"]
        assert cfg["sph_token"] == "unit-test-token", "解析凭证没存住"
        assert cfg["sph_api"] == "https://example.com/api/parse"

        import sph
        t0 = time.time()
        assert sph._local_helper("https://weixin.qq.com/sph/Axv548mzBF") is None
        took = time.time() - t0
        log("本机视频号辅助接口探测: %.2fs（没装就应秒退）" % took)
        assert took < 1.5, "没装工具却拖了太久"

        try:
            sph.resolve("https://weixin.qq.com/sph/Axv548mzBF")
            raise AssertionError("没接口却解析成功了")
        except RuntimeError as e:
            msg = str(e)
            log("视频号提示:", msg.replace("\n", " | ")[:160])
            # 界面上已经没有「解析接口」入口了，提示必须自带出路
            assert "视频号助手" in msg or "保存到相册" in msg, "没给出路"
            assert "解析接口" not in msg, "还在让人去填已经删掉的解析接口"
    finally:
        post("/api/config", {"sph_token": old.get("sph_token", ""),
                             "sph_api": old.get("sph_api", "")})


# ---------- 界面 ----------
def test_ui():
    from playwright.sync_api import sync_playwright

    chrome = None
    for p in ("C:/Program Files/Google/Chrome/Application/chrome.exe",
              "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"):
        if os.path.exists(p):
            chrome = p
            break
    with sync_playwright() as p:
        kw = {"headless": True, "args": ["--mute-audio"]}
        if chrome:
            kw["executable_path"] = chrome
        br = p.chromium.launch(**kw)
        pg = br.new_page(viewport={"width": 1100, "height": 900})
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(URL + "/", wait_until="networkidle")
        pg.wait_for_timeout(900)

        assert pg.locator("#btnCopyBookmark").count() == 0, "网页书签入口该收走了"
        assert pg.locator("#cfgProxy").count() == 0, "代理输入框该收走了"

        pg.click("#openSet")
        pg.wait_for_timeout(300)
        pg.click("#openAdv")
        pg.wait_for_timeout(300)
        assert pg.is_visible("#cfgCookies"), "高级页里没有 Cookie"
        assert pg.locator("#cfgSphToken").count() == 0, "解析接口不该再出现在界面上"

        pg.click("#setBack")         # 退回常用页，账号入口在那儿
        pg.wait_for_timeout(300)
        pg.click("#openAcct")
        pg.wait_for_timeout(700)
        txt = pg.inner_text("#acctDrawer")
        assert "Suno" in txt, "账号面板里没出现 Suno"
        log("账号面板 Suno 已出现，页面错误:", errors[:2])
        assert not errors, "页面脚本报错：%s" % errors[:2]
        br.close()


def main():
    test_login_state()
    test_sph_config()
    test_push()
    test_ui()
    test_suno()          # 最慢，放最后
    log("\n全部通过")


if __name__ == "__main__":
    main()

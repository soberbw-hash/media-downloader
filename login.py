# -*- coding: utf-8 -*-
"""账号登录（扫码 / 账号密码）→ 导出 cookies

用系统里已安装的 Edge / Chrome 打开一个真实浏览器窗口，用户在窗口里登录，
脚本轮询 cookie，一旦出现登录态就导出成 Netscape 格式的 cookies 文件，
后续下载直接复用。这样不用装浏览器内核，也不用教用户去 DevTools 里复制。
"""
import os
import sys
import threading
import time
import json
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
COOKIE_DIR = os.path.join(BASE, "cookies")
PROFILE_DIR = os.path.join(COOKIE_DIR, "_profile")

# need: 出现任意一个就说明登录成功
SITES = {
    "xiaohongshu": {
        "name": "小红书", "url": "https://www.xiaohongshu.com/explore",
        "need": ["web_session"], "domains": ["xiaohongshu.com"],
        # 小红书连游客都发 web_session，必须再问一次接口是不是游客态
        "verify": "xhs", "required": True,
        "tip": "小红书不给未登录访客视频地址，必须登录",
    },
    "instagram": {
        "name": "Instagram", "url": "https://www.instagram.com/accounts/login/",
        "need": ["sessionid"], "domains": ["instagram.com"], "required": True,
        "tip": "必须登录；还要能打开 Instagram（代理已配好）",
    },
    "douyin": {
        "name": "抖音", "url": "https://www.douyin.com/",
        "need": ["sessionid", "sid_tt"], "domains": ["douyin.com"],
        "required": False,
        "tip": "可选。公开视频不登录也能下；登录后好友/私密内容也能下",
    },
    "kuaishou": {
        "name": "快手", "url": "https://www.kuaishou.com/",
        "need": ["kuaishou.server.web_st", "kuaishou.server.web_ph"],
        "domains": ["kuaishou.com"], "required": False,
        "tip": "可选。游客也能解析，登录后成功率更稳",
    },
    "bilibili": {
        "name": "B站", "url": "https://passport.bilibili.com/login",
        "need": ["SESSDATA"], "domains": ["bilibili.com"], "required": False,
        "tip": "可选。不登录能下 720P；登录后能下 1080P 以上",
    },
    "weibo": {
        "name": "微博", "url": "https://weibo.com/login.php",
        "need": ["SUB"], "domains": ["weibo.com", "weibo.cn"], "required": False,
        "tip": "可选。大部分视频不登录也能下",
    },
    "music_netease": {
        "name": "网易云音乐", "url": "https://music.163.com/",
        "need": ["MUSIC_U"], "domains": ["music.163.com"], "required": False,
        "tip": "可选。不登录能下免费歌，登录后 VIP / 无版权的也能试",
    },
    "music_qqmusic": {
        "name": "QQ音乐", "url": "https://y.qq.com/",
        "need": ["qm_keyst"], "domains": ["qq.com"], "required": True,
        "tip": "必须登录。QQ 音乐的播放地址是登录后按账号下发的",
    },
}

STATE = {"state": "idle", "platform": "", "msg": "", "since": 0}
_LOCK = threading.Lock()
_STOP = threading.Event()
_THREAD = None


def _set(state, platform="", msg=""):
    with _LOCK:
        STATE["state"] = state
        STATE["platform"] = platform
        STATE["msg"] = msg
        STATE["since"] = time.time()


def status():
    with _LOCK:
        return dict(STATE)


def cookie_file(pid):
    """返回该平台已保存的 cookies 文件，没有就返回 None"""
    p = os.path.join(COOKIE_DIR, "%s.txt" % pid)
    return p if os.path.exists(p) and os.path.getsize(p) > 0 else None


def sites():
    out = []
    for pid, site in SITES.items():
        f = cookie_file(pid)
        out.append({
            "id": pid, "name": site["name"], "tip": site["tip"],
            "required": bool(site.get("required")),
            "logged_in": bool(f),
            "saved_at": int(os.path.getmtime(f)) if f else 0,
        })
    out.sort(key=lambda x: not x["required"])  # 稳定的，必须登录的排前面
    return out


_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


def _cookie_header(cookies, domain):
    return "; ".join("%s=%s" % (c.get("name"), c.get("value"))
                     for c in cookies
                     if (c.get("domain") or "").lstrip(".").endswith(domain))


def _verify_xhs(cookies):
    """保留给单测用：直接打接口（线上会被 406 挡，真实判断走浏览器内的响应监听）"""
    ck = _cookie_header(cookies, "xiaohongshu.com")
    if not ck:
        return False
    try:
        req = urllib.request.Request(
            "https://edith.xiaohongshu.com/api/sns/web/v2/user/me",
            headers={"User-Agent": _UA, "Cookie": ck,
                     "Referer": "https://www.xiaohongshu.com/"})
        d = json.load(urllib.request.urlopen(req, timeout=15))
        data = d.get("data") or {}
        return data.get("guest") is False and bool(data.get("user_id"))
    except Exception:
        return False


_VERIFY = {"xhs": _verify_xhs}


def _netscape(cookies, domains):
    lines = ["# Netscape HTTP Cookie File",
             "# 由「音视频下载器」登录功能自动生成，请勿手改", ""]
    for c in cookies:
        dom = (c.get("domain") or "").lower()
        if not dom:
            continue
        if not any(dom.lstrip(".").endswith(d) for d in domains):
            continue
        exp = int(c.get("expires") or 0)
        if exp <= 0:
            exp = int(time.time()) + 86400 * 365
        lines.append("\t".join([
            dom,
            "TRUE" if dom.startswith(".") else "FALSE",
            c.get("path") or "/",
            "TRUE" if c.get("secure") else "FALSE",
            str(exp),
            c.get("name") or "",
            c.get("value") or "",
        ]))
    return "\n".join(lines) + "\n"


def _channel():
    """优先用系统 Edge，没有再试 Chrome；都不在就退化成 playwright 自带内核"""
    if sys.platform == "darwin":
        home = os.path.expanduser("~")
        cands = [
            ("msedge", "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
            ("chrome", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            ("chrome", os.path.join(home, "Applications/Google Chrome.app/Contents/MacOS/Google Chrome")),
        ]
    else:
        cands = [
            ("msedge", r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
            ("msedge", r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
            ("chrome", r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
            ("chrome", r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
        ]
    for ch, path in cands:
        if os.path.exists(path):
            return ch
    return None


def start(pid, proxy=None):
    global _THREAD
    if pid not in SITES:
        return False, "不支持的登录平台"
    with _LOCK:
        running = STATE["state"] in ("opening", "waiting")
    if running:
        return False, "已经有一个登录窗口开着了"

    _STOP.clear()
    _THREAD = threading.Thread(target=_worker, args=(pid, proxy), daemon=True)
    _THREAD.start()
    return True, ""


def cancel():
    _STOP.set()
    _set("idle", "", "已取消登录")
    return True


def _launch(p, kw):
    """启动浏览器；上次异常退出可能留下锁文件，清掉再试一次"""
    try:
        return p.chromium.launch_persistent_context(**kw)
    except Exception:
        for name in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
            try:
                os.remove(os.path.join(PROFILE_DIR, name))
            except OSError:
                pass
        return p.chromium.launch_persistent_context(**kw)


def _worker(pid, proxy):
    site = SITES[pid]
    _set("opening", pid, "正在打开浏览器窗口…")

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        _set("error", pid, "缺少 playwright，请先运行 pip install playwright")
        return

    os.makedirs(COOKIE_DIR, exist_ok=True)
    ctx = None
    ok = False
    try:
        with sync_playwright() as p:
            kw = {
                "user_data_dir": PROFILE_DIR,
                # 自测时可以用环境变量改成无头，正常使用是可见窗口
                "headless": os.environ.get("VDL_LOGIN_HEADLESS") == "1",
                "args": ["--window-size=1100,820", "--disable-blink-features=AutomationControlled"],
            }
            ch = _channel()
            if ch:
                kw["channel"] = ch
            if proxy:
                kw["proxy"] = {"server": proxy}
            ctx = _launch(p, kw)

            page = ctx.pages[0] if ctx.pages else ctx.new_page()

            # 小红书这类站点，光看 cookie 判断不出来（游客也有 web_session），
            # 只能听页面自己发出的 /user/me 响应，看 guest 字段
            probe = {"guest": None, "ready": False}

            def on_response(r):
                try:
                    if "/api/sns/web/v2/user/me" in r.url:
                        d = r.json()
                        probe["guest"] = bool((d.get("data") or {}).get("guest", True))
                        probe["ready"] = True
                except Exception:
                    pass

            try:
                page.on("response", on_response)
                ctx.on("response", on_response)
            except Exception:
                pass

            try:
                page.goto(site["url"], timeout=60000)
            except Exception:
                pass

            _set("waiting", pid, "请在浏览器窗口里完成登录")

            deadline = time.time() + 300
            last_probe = time.time()
            reloads = 0
            while time.time() < deadline and not _STOP.is_set():
                try:
                    cs = ctx.cookies()
                except Exception:
                    _set("closed", pid, "浏览器窗口被关掉了")
                    return
                names = {c.get("name") for c in cs}
                if any(n in names for n in site["need"]):
                    checker = _VERIFY.get(site.get("verify") or "")
                    if site.get("verify") == "xhs":
                        if not (probe["ready"] and probe["guest"] is False):
                            # cookie 有了但页面还是游客态：开个后台标签页去问一次，
                            # 不去 reload 当前页，免得把用户正在扫的登录框刷掉
                            if time.time() - last_probe > 12 and reloads < 20:
                                reloads += 1
                                last_probe = time.time()
                                try:
                                    tmp = ctx.new_page()
                                    tmp.goto("https://www.xiaohongshu.com/explore",
                                             timeout=30000)
                                    tmp.wait_for_timeout(2500)
                                    tmp.close()
                                except Exception:
                                    pass
                            try:
                                page.wait_for_timeout(900)
                            except Exception:
                                time.sleep(0.9)
                            continue
                    elif checker and not checker(cs):
                        try:
                            page.wait_for_timeout(1500)
                        except Exception:
                            time.sleep(1.5)
                        continue

                    time.sleep(0.8)
                    try:
                        cs = ctx.cookies()
                    except Exception:
                        pass
                    with open(os.path.join(COOKIE_DIR, "%s.txt" % pid), "w",
                              encoding="utf-8") as f:
                        f.write(_netscape(cs, site["domains"]))
                    ok = True
                    break
                try:
                    page.wait_for_timeout(800)
                except Exception:
                    time.sleep(0.8)

            if ok:
                _set("done", pid, "已登录 %s，cookies 已保存" % site["name"])
            elif _STOP.is_set():
                _set("idle", pid, "已取消登录")
            else:
                _set("timeout", pid, "等了 5 分钟没检测到登录，窗口已关闭")
    except Exception as e:
        import traceback
        traceback.print_exc()
        _set("error", pid, "打开浏览器失败：%s" % str(e)[:160])
    finally:
        try:
            if ctx:
                ctx.close()
        except Exception:
            pass

# -*- coding: utf-8 -*-
"""账号登录（扫码 / 账号密码）→ 导出 cookies

用「系统默认浏览器」打开一个真实浏览器窗口，用户在窗口里登录，
脚本轮询 cookie，一旦出现登录态就导出成 Netscape 格式的 cookies 文件，
后续下载直接复用。这样不用装浏览器内核，也不用教用户去 DevTools 里复制。

默认浏览器是运行时读系统设置拿到的，不写死 Edge/Chrome：谁用就认谁机器上
设的那个。另有 open_in_default_browser()，走系统关联直接开用户日常那个窗口。
"""
import os
import re
import sys
import shlex
import subprocess
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
        "overseas": True,
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
        "music": "netease",
        "tip": "可选。不登录能下免费歌；贴上会员 cookie 能拿无损，VIP 曲也能下",
    },
    "music_qqmusic": {
        "name": "QQ音乐", "url": "https://y.qq.com/",
        "need": ["qm_keyst"], "domains": ["qq.com"], "required": True,
        "music": "qqmusic",
        "tip": "必须登录。QQ 音乐的播放地址是登录后按账号下发的，会员能拿无损",
    },
    "suno": {
        "name": "Suno", "url": "https://suno.com/",
        "need": ["__session", "__clerk_db_jwt"],
        "domains": ["suno.com", "suno.ai"], "required": False,
        "overseas": True,
        "tip": "可选。登录后能下自己账号里的歌；不登录游客只给加密片段，下不了",
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
        mpid = site.get("music")
        acc = {}
        if mpid:
            try:
                import music as music_mod
                acc = music_mod.account(mpid)      # 自带 90 秒缓存，轮询扛得住
            except Exception:
                acc = {}
        out.append({
            "id": pid, "name": site["name"], "tip": site["tip"],
            "required": bool(site.get("required")),
            "logged_in": bool(f),
            "saved_at": int(os.path.getmtime(f)) if f else 0,
            # 所有站点都允许贴 cookie：用户在自己日常浏览器里已经登录的话，
            # 复制过来就行，不必在独立窗口里再登一次
            "importable": True,
            "need": site.get("need") or [],
            "account": acc,
        })
    out.sort(key=lambda x: not x["required"])  # 稳定的，必须登录的排前面
    return out


def import_cookie(pid, raw):
    """手动贴进来的 cookie。音乐平台走 music.save_cookie（会校验关键项），
    其余平台按「名字=值; 名字=值」存成 Netscape 文件。返回 (ok, 消息)。"""
    site = SITES.get(pid)
    if not site:
        return False, "不支持的平台"
    mpid = site.get("music")
    if mpid:
        try:
            import music as music_mod
            music_mod.save_cookie(mpid, raw)
            music_mod.account(mpid, fresh=True)
            return True, "已导入 %s 的 cookie" % site["name"]
        except ValueError as e:
            return False, str(e)
        except Exception as e:
            return False, "导入失败：%s" % str(e)[:120]
    # 其它平台：认得「名字=值; 名字=值」，也认 DevTools 里 Copy all 的 tab 表格
    pairs = []
    lines = (raw or "").strip().splitlines()
    if any("\t" in ln and "=" not in ln for ln in lines[:3]):
        for ln in lines:
            cols = ln.split("\t")
            if len(cols) >= 2 and cols[0].strip() and not cols[0].startswith("#"):
                pairs.append("%s=%s" % (cols[0].strip(), cols[1].strip()))
    else:
        for chunk in re.split(r"[;\n]+", (raw or "").strip()):
            chunk = chunk.strip()
            if "=" in chunk and not chunk.startswith("#"):
                pairs.append(chunk)
    if not pairs:
        return False, "没认出 cookie 格式，粘「名字=值; 名字=值」这样的整串就行"
    got = {p.split("=", 1)[0].strip() for p in pairs}
    need = site.get("need") or []
    if need and not any(n in got for n in need):
        return False, "没看到 %s 的关键登录 cookie（%s），确认是从登录后的页面复制的" % (
            site["name"], " / ".join(need[:2]))
    os.makedirs(COOKIE_DIR, exist_ok=True)
    lines = ["# Netscape HTTP Cookie File", "# 手动导入", ""]
    for p in pairs:
        k, v = p.split("=", 1)
        k, v = k.strip(), v.strip()
        dom = "." + site["domains"][0]
        lines.append("\t".join([dom, "TRUE", "/", "TRUE",
                                str(int(time.time()) + 86400 * 365), k, v]))
    with open(os.path.join(COOKIE_DIR, "%s.txt" % pid), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return True, "已导入 %s 的 cookie" % site["name"]


def logout(pid):
    """删掉这个平台的登录态。"""
    site = SITES.get(pid)
    if not site:
        return False, "不支持的平台"
    mpid = site.get("music")
    if mpid:
        import music as music_mod
        music_mod.clear_cookie(mpid)
    try:
        os.remove(os.path.join(COOKIE_DIR, "%s.txt" % pid))
    except OSError:
        pass
    if STATE.get("platform") == pid:
        _set("idle", pid, "")
    return True, "已退出 %s" % site["name"]


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


def _music_ok(mpid, cs, domains):
    """拿浏览器里刚拿到的 cookie 去问一次账号接口。
    返回 False 说明这还不是登录态（网易云登录成功瞬间 cookie 会分批写），继续等；
    网络类失败返回 True，别因为接口抽风把用户卡在窗口里。"""
    try:
        import music as music_mod
    except Exception:
        return True
    ck = "; ".join("%s=%s" % (c.get("name"), c.get("value")) for c in cs
                   if any((c.get("domain") or "").lstrip(".").endswith(d) for d in domains))
    r = music_mod.account(mpid, ck=ck)
    if r.get("ok"):
        return True
    return "失效" not in (r.get("error") or "")


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


# ---------- 系统默认浏览器：运行时读，谁用就认谁的 ----------

# Chromium 系可执行文件特征。命中就把它直接交给 playwright 当内核，
# 这样连 Brave / Vivaldi / 360 / QQ 浏览器这类「非官方但设成默认」的也认。
_CHROMIUM_EXE = ("chrome.exe", "msedge.exe", "brave.exe", "vivaldi.exe",
                 "opera.exe", "chromium.exe", "360chrome.exe", "qqbrowser.exe",
                 "chrome", "microsoft edge", "brave browser", "vivaldi",
                 "chromium", "google chrome")
_KNOWN_NAME = {
    "chrome.exe": "Chrome", "chrome": "Chrome", "google chrome": "Chrome",
    "msedge.exe": "Edge", "microsoft edge": "Edge",
    "brave.exe": "Brave", "vivaldi.exe": "Vivaldi", "opera.exe": "Opera",
    "chromium.exe": "Chromium", "firefox.exe": "Firefox", "firefox": "Firefox",
    "safari": "Safari", "360chrome.exe": "360浏览器", "qqbrowser.exe": "QQ浏览器",
}
_DEFAULT_CACHE = {"ts": 0.0, "value": None}


def _exe_from_cmd(cmd):
    """把 `"C:\\...\\chrome.exe" --single-argument %1` 里的 exe 摘出来。"""
    if not cmd:
        return ""
    try:
        parts = shlex.split(cmd, posix=(sys.platform != "win32"))
    except Exception:
        parts = cmd.split()
    for s in parts:
        low = s.lower()
        if low.endswith((".exe", ".app/contents/macos/microsoft edge")) or \
                low.endswith(("/chrome", "/chromium", "/firefox", "/brave", "/vivaldi")):
            return s.strip('"')
    return (parts[0].strip('"') if parts else "")


def _win_default():
    """Windows：UserChoice 的 ProgId → HKCR 打开命令。策略锁定时退回 StartMenuInternet。"""
    import winreg
    prog = ""
    for scheme in ("http", "https"):
        try:
            with winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations"
                    r"\%s\UserChoice" % scheme) as k:
                prog = str(winreg.QueryValueEx(k, "ProgId")[0] or "")
            if prog:
                break
        except Exception:
            continue
    if prog:
        for root in (winreg.HKEY_CLASSES_ROOT, winreg.HKEY_CURRENT_USER):
            try:
                with winreg.OpenKey(root, prog + r"\shell\open\command") as k:
                    exe = _exe_from_cmd(str(winreg.QueryValueEx(k, "")[0] or ""))
                    if exe and os.path.isfile(exe):
                        return exe
            except Exception:
                continue
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Clients\StartMenuInternet") as k:
            d = str(winreg.QueryValueEx(k, "")[0] or "")
        if d:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"SOFTWARE\Clients\StartMenuInternet\%s\shell\open\command" % d) as k:
                exe = _exe_from_cmd(str(winreg.QueryValueEx(k, "")[0] or ""))
                if exe and os.path.isfile(exe):
                    return exe
    except Exception:
        pass
    return ""


def _mac_default():
    """macOS：读 LaunchServices 的 http handler bundle id。"""
    import plistlib
    p = os.path.expanduser(
        "~/Library/Preferences/com.apple.LaunchServices/"
        "com.apple.launchservices.secure.plist")
    try:
        d = plistlib.load(open(p, "rb"))
        for h in d.get("LSHandlers", []):
            if h.get("LSHandlerURLScheme") == "http":
                bid = str(h.get("LSHandlerRoleAll") or "").lower()
                if "chrome" in bid:
                    return "Chrome"
                if "edge" in bid:
                    return "Edge"
                if "firefox" in bid:
                    return "Firefox"
                if "safari" in bid:
                    return "Safari"
    except Exception:
        pass
    return ""


def _linux_default():
    try:
        out = subprocess.check_output(
            ["xdg-settings", "get", "default-web-browser"],
            timeout=4, stderr=subprocess.DEVNULL).decode("utf-8", "replace").strip()
        return out
    except Exception:
        return ""


def default_browser():
    """返回 {name, exe, chromium}。exe 为空表示没探到，走兜底。
    缓存 30 秒，登录面板轮询时不要反复读注册表。
    """
    now = time.time()
    if _DEFAULT_CACHE["value"] and now - _DEFAULT_CACHE["ts"] < 30:
        return dict(_DEFAULT_CACHE["value"])
    raw = ""
    try:
        if sys.platform == "win32":
            raw = _win_default()
        elif sys.platform == "darwin":
            raw = _mac_default()
        else:
            raw = _linux_default()
    except Exception:
        raw = ""
    exe = raw if raw and os.path.isfile(raw) else ""
    base = os.path.basename(exe or raw).lower()
    if not exe and sys.platform == "darwin":
        # bundle id 形式：映射到常见安装路径
        mac_paths = {
            "chrome": "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "edge": "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            "firefox": "/Applications/Firefox.app/Contents/MacOS/firefox",
        }
        for key, path in mac_paths.items():
            if key in (raw or "").lower() and os.path.isfile(path):
                exe = path
                base = os.path.basename(path).lower()
                break
    name = _KNOWN_NAME.get(base, "")
    if not name:
        for key, label in _KNOWN_NAME.items():
            if key in base:
                name = label
                break
    if not name and (raw or exe):
        name = os.path.splitext(os.path.basename(exe or raw))[0] or "默认浏览器"
    chromium = bool(exe) and any(k in (base + " " + exe.lower()) for k in _CHROMIUM_EXE)
    val = {"name": name or "默认浏览器", "exe": exe, "chromium": chromium,
           "raw": raw}
    _DEFAULT_CACHE.update(ts=now, value=val)
    return dict(val)


def open_in_default_browser(url):
    """真的走系统关联，打开用户日常那个已登录的浏览器窗口。"""
    if not (url or "").startswith(("http://", "https://")):
        return False, "地址不合法"
    if os.environ.get("VDL_NO_OPEN") == "1":   # 自测时别真弹窗口打扰人
        return True, ""
    try:
        if sys.platform == "win32":
            os.startfile(url)          # ShellExecute，必走默认浏览器
        elif sys.platform == "darwin":
            subprocess.Popen(["open", url])
        else:
            import webbrowser
            webbrowser.open(url)
        return True, ""
    except Exception as e:
        return False, "打不开默认浏览器：%s" % str(e)[:100]


def launch_options():
    """给 playwright launch_persistent_context 的参数片段。
    默认浏览器是 Chromium 系就直接用它自己的 exe（不写死 Edge/Chrome）；
    是 Firefox/Safari 等就退回系统里已安装的 Chromium 系，再不行用自带内核。
    """
    d = default_browser()
    d["login_exe"] = ""
    d["login_name"] = ""
    if d["exe"] and d["chromium"]:
        d["login_exe"] = d["exe"]
        d["login_name"] = d["name"]
        return {"executable_path": d["exe"]}, d
    if sys.platform == "win32":
        cands = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        ]
    elif sys.platform == "darwin":
        cands = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                 "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"]
    else:
        cands = ["/usr/bin/google-chrome", "/usr/bin/chromium",
                 "/usr/bin/microsoft-edge"]
    for path in cands:
        if os.path.isfile(path):
            d["login_exe"] = path
            d["login_name"] = _KNOWN_NAME.get(os.path.basename(path).lower(), "Chromium")
            return {"executable_path": path}, d
    d["login_name"] = "内置浏览器"
    return {}, d


def open_site(pid):
    """点「日常浏览器」：用系统默认浏览器打开该站点的登录页。"""
    site = SITES.get(pid)
    if not site:
        return False, "不支持的平台"
    ok, err = open_in_default_browser(site["url"])
    if ok:
        return True, "已在%s里打开 %s" % (default_browser()["name"], site["name"])
    return False, err


def start(pid, proxy=None):
    global _THREAD
    if pid not in SITES:
        return False, "不支持的登录平台"
    with _LOCK:
        running = STATE["state"] in ("opening", "waiting")
    if running:
        return False, "已经有一个登录窗口开着了"

    # 境外站点的登录窗口直连打不开，设置里又没填代理时自己认系统代理
    if not (proxy or "").strip() and SITES[pid].get("overseas"):
        try:
            from core import system_proxy
            proxy = system_proxy() or None
        except Exception:
            proxy = None

    _STOP.clear()
    _THREAD = threading.Thread(target=_worker, args=(pid, proxy), daemon=True)
    _THREAD.start()
    return True, ""


def cancel():
    _STOP.set()
    _set("idle", "", "已取消登录")
    return True


def _launch(p, kw):
    """启动浏览器；上次异常退出可能留下锁文件，清掉再试一次。
    默认浏览器若是小众 Chromium 分支、吃不下 playwright 的启动参数，
    最后一档退回 playwright 自带内核，别让人卡在「打不开浏览器」。
    """
    try:
        return p.chromium.launch_persistent_context(**kw)
    except Exception:
        for name in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
            try:
                os.remove(os.path.join(PROFILE_DIR, name))
            except OSError:
                pass
        try:
            return p.chromium.launch_persistent_context(**kw)
        except Exception:
            if "executable_path" not in kw:
                raise
            plain = dict(kw)
            plain.pop("executable_path", None)
            return p.chromium.launch_persistent_context(**plain)


def _worker(pid, proxy):
    site = SITES[pid]
    opts, info = launch_options()
    bname = info.get("name") or "默认浏览器"
    _set("opening", pid, "正在打开 %s 登录窗口…" % bname)

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
            kw.update(opts)
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

            _set("waiting", pid, "在 %s 窗口里登录一次，之后就不用再登了" % bname)

            deadline = time.time() + 300
            last_probe = time.time()
            last_music = 0.0
            reloads = 0
            while time.time() < deadline and not _STOP.is_set():
                try:
                    cs = ctx.cookies()
                except Exception:
                    _set("closed", pid, "浏览器窗口被关掉了")
                    return
                names = {c.get("name") for c in cs}
                if any(n in names for n in site["need"]):
                    # 音乐平台：光有 cookie 不够，得确认这个号真的登上了，
                    # 否则会把一份游客 cookie 存下来，之后下载一直说「没登录」。
                    # 8 秒才查一次，别把账号接口打成限流。
                    mpid = site.get("music")
                    if mpid and time.time() - last_music > 8:
                        last_music = time.time()
                        if not _music_ok(mpid, cs, site["domains"]):
                            try:
                                page.wait_for_timeout(1500)
                            except Exception:
                                time.sleep(1.5)
                            continue
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
                    if site.get("music"):
                        try:
                            import music as music_mod
                            music_mod.account(site["music"], fresh=True)
                        except Exception:
                            pass
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
        msg = str(e)
        low = msg.lower()
        if "executable doesn't exist" in low or "playwright install" in low \
                or "browser not found" in low:
            human = ("这台电脑没找到能用的浏览器。点该行右边的 ↗ 用日常浏览器打开，"
                     "登录后把 cookie 粘进「粘贴」也行")
        else:
            human = "打开 %s 失败：%s" % (bname, msg[:120])
        _set("error", pid, human)
    finally:
        try:
            if ctx:
                ctx.close()
        except Exception:
            pass

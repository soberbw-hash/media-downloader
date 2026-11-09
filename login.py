# -*- coding: utf-8 -*-
"""账号登录 —— 只用「用户自己日常的浏览器」，不再弹独立 / 无痕窗口。

以前点「登录」会由 Playwright 拉起一个全新的空白 profile（cookies/_profile）。
虽然用的是 Chrome 的程序，窗口里却没有任何登录态，看起来就是个无痕窗，
用户还得在里面重新登一次。这一版彻底拿掉它：

  点「打开」   -> os.startfile / open / xdg-open，走系统关联，在用户日常
                 那个已经登录好的浏览器里新开一个标签页。默认浏览器是
                 运行时读系统设置拿到的，谁用就认谁机器上设的那个。
  同步登录态  -> 随包附带 browser-extension/，装一次，点扩展图标就用
                 chrome.cookies 把已登录站点的 cookie 送进本机服务。
  兜底        -> 手动粘贴 cookie，所有站点都支持。

不去撬正在运行的 Chrome 的 cookie 库：Windows 上 Cookies 库被独占锁，
Chrome 127+ 还有 App-Bound 加密，硬读既不稳也不体面（实测读不出来）。
扩展是用户主动授权、能读到 HttpOnly、且跨机器通用的干净通道。
"""
import os
import re
import sys
import shlex
import shutil
import subprocess
import threading
import time

import paths

BASE = os.path.dirname(os.path.abspath(__file__))
COOKIE_DIR = paths.cookie_dir()
PROFILE_DIR = os.path.join(COOKIE_DIR, "_profile")   # 旧版遗留，保留仅为兼容

# 冻结成 macOS .app 之后，程序包是只读的，而且拖进 Chrome 的扩展目录
# 不能跟着软件升级一起没掉。启动就复制一份到用户数据目录，持久保存。
_FROZEN = getattr(sys, "frozen", False)
EXT_DIR = (os.path.join(paths.data_dir(), "browser-extension")
           if _FROZEN else os.path.join(BASE, "browser-extension"))


def _prepare_ext_dir():
    if not _FROZEN:
        return
    try:
        if os.path.isfile(os.path.join(EXT_DIR, "manifest.json")):
            return
        os.makedirs(os.path.dirname(EXT_DIR), exist_ok=True)
        shutil.copytree(os.path.join(BASE, "browser-extension"),
                        EXT_DIR, dirs_exist_ok=True)
    except Exception:
        pass


_prepare_ext_dir()

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
_EXT_SYNC = {"at": 0, "count": 0}


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
            # 复制过来就行，不必再登一次
            "importable": True,
            "need": site.get("need") or [],
            "account": acc,
        })
    out.sort(key=lambda x: not x["required"])  # 稳定的，必须登录的排前面
    return out


def _match_domain(dom, domains):
    """后缀匹配，host == d 或 host.endswith('.' + d)。精确 key 会漏子域。"""
    dom = (dom or "").lower().lstrip(".")
    return any(dom == d or dom.endswith("." + d) for d in domains)


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
    _write_netscape(pid, [{"name": p.split("=", 1)[0].strip(),
                           "value": p.split("=", 1)[1].strip(),
                           "domain": site["domains"][0], "path": "/",
                           "secure": True, "expires": 0} for p in pairs],
                    "手动导入")
    return True, "已导入 %s 的 cookie" % site["name"]


def _write_netscape(pid, picked, note):
    os.makedirs(COOKIE_DIR, exist_ok=True)
    lines = ["# Netscape HTTP Cookie File", "# %s，请勿手改" % note, ""]
    now = int(time.time())
    for c in picked:
        dom = (c.get("domain") or "").lstrip(".")
        lines.append("\t".join([
            "." + dom,
            "TRUE",
            c.get("path") or "/",
            "TRUE" if c.get("secure") else "FALSE",
            str(int(c.get("expires") or 0) or (now + 86400 * 365)),
            c.get("name") or "",
            c.get("value") or "",
        ]))
    with open(os.path.join(COOKIE_DIR, "%s.txt" % pid), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def import_cookies_bulk(pid, cookies):
    """浏览器扩展送来的结构化 cookie（含 HttpOnly）。返回 (ok, 消息)。

    只挑这个站点域名下的、只写进本机 cookies 目录，不碰别的站点数据。
    """
    site = SITES.get(pid)
    if not site:
        return False, "不支持的平台"
    if not isinstance(cookies, list):
        return False, "cookie 格式不对"
    picked = []
    for c in cookies:
        if not isinstance(c, dict):
            continue
        name = str(c.get("name") or "").strip()
        val = str(c.get("value") or "")
        dom = str(c.get("domain") or "")
        if not name or not val or not _match_domain(dom, site["domains"]):
            continue
        try:
            exp = int(float(c.get("expirationDate") or c.get("expires") or 0))
        except (TypeError, ValueError):
            exp = 0
        picked.append({
            "name": name, "value": val,
            "domain": dom.lower().lstrip("."),
            "path": str(c.get("path") or "/"),
            "secure": bool(c.get("secure")),
            "expires": exp,
        })
    if not picked:
        return False, "浏览器里还没有 %s 的 cookie，先去网站登一次" % site["name"]
    got = {c["name"] for c in picked}
    need = site.get("need") or []
    if need and not any(n in got for n in need):
        return False, "%s 还没登录（缺 %s），先去网站登一次" % (
            site["name"], " / ".join(need[:2]))
    mpid = site.get("music")
    if mpid:
        raw = "; ".join("%s=%s" % (c["name"], c["value"]) for c in picked)
        try:
            import music as music_mod
            music_mod.save_cookie(mpid, raw)
            music_mod.account(mpid, fresh=True)
            return True, "已同步 %s 登录态（%d 项）" % (site["name"], len(picked))
        except ValueError as e:
            return False, str(e)
        except Exception as e:
            return False, "同步失败：%s" % str(e)[:120]
    _write_netscape(pid, picked, "由浏览器扩展从你日常的浏览器同步")
    return True, "已同步 %s 登录态（%d 项）" % (site["name"], len(picked))


def mark_synced(count):
    """记在内存里就行，重启服务不用留痕，也不写进分发包。"""
    if count > 0:
        _EXT_SYNC.update(at=int(time.time()), count=count)


def extension_info():
    return {
        "dir": EXT_DIR,
        "exists": os.path.isdir(EXT_DIR) and os.path.isfile(
            os.path.join(EXT_DIR, "manifest.json")),
        "synced": _EXT_SYNC["at"] > 0,
        "synced_at": _EXT_SYNC["at"],
        "synced_count": _EXT_SYNC["count"],
    }


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
    """真的走系统关联，打开用户日常那个已登录的浏览器窗口。

    只接 http(s)：chrome:// / edge:// 是浏览器内部页，不是系统注册的协议，
    Windows 上 os.startfile 只会弹「获取打开此 'chrome' 链接的应用」，让用户
    去 Microsoft Store 找兼容程序。扩展页只能从浏览器菜单自己点进去（见 setup_ext）。
    """
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


def chromium_exe():
    """找一台能装扩展的 Chromium 浏览器：默认浏览器优先，其次常见安装位置。

    扩展是 MV3，只有 Chromium 系装得上。默认浏览器若是 Firefox / Safari，
    也别把内部页丢给它，直接找本机装着的 Chrome / Edge。
    """
    d = default_browser()
    if d.get("exe") and d.get("chromium"):
        return d["exe"]
    if sys.platform == "win32":
        cands = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        ]
    elif sys.platform == "darwin":
        cands = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        ]
    else:
        cands = ["/usr/bin/google-chrome", "/usr/bin/chromium",
                 "/usr/bin/microsoft-edge"]
    for path in cands:
        if os.path.isfile(path):
            return path
    return ""


def launch_options():
    """兼容保留：早期版本给 playwright 用的启动参数。

    现在登录不再拉起任何独立窗口，这个函数只用于告诉界面「默认浏览器是谁」，
    以及旧测试里的浏览器识别校验。别再拿它去 launch_persistent_context。
    """
    d = default_browser()
    d["login_exe"] = ""
    d["login_name"] = d["name"]
    exe = chromium_exe()
    if exe:
        d["login_exe"] = exe
        return {"executable_path": exe}, d
    return {}, d


def open_site(pid):
    """点「打开」：用系统默认浏览器打开该站点的登录页。"""
    site = SITES.get(pid)
    if not site:
        return False, "不支持的平台"
    ok, err = open_in_default_browser(site["url"])
    if ok:
        return True, "已在%s里打开 %s" % (
            default_browser()["name"], site["name"])
    return False, err


def open_ext_dir():
    """在文件管理器里**选中** browser-extension 文件夹本身，而不是打开进去。

    Chrome 2018 起就不收单个 .crx 了（Win/Mac 只认商店签名），但把整个扩展
    文件夹拖进 chrome://extensions 一直有效。选中状态用户按住就能拖，
    不用自己退回上一级去找那个文件夹。
    """
    if not os.path.isdir(EXT_DIR):
        return False, "没找到扩展文件夹（browser-extension）"
    if os.environ.get("VDL_NO_OPEN") == "1":
        return True, "已选中扩展文件夹"
    try:
        if sys.platform == "win32":
            subprocess.Popen(["explorer.exe",
                              "/select," + os.path.normpath(EXT_DIR)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", EXT_DIR])   # Finder 里选中它
        else:
            subprocess.Popen(["xdg-open", os.path.dirname(EXT_DIR)])
        return True, "已选中扩展文件夹"
    except Exception as e:
        return False, "打不开文件夹：%s" % str(e)[:100]


def setup_ext():
    """「选中扩展文件夹」：选中文件夹，剩下三下点浏览器菜单就完事。

    chrome:// 是特权内部页，外部程序打不开（命令行传进去只会被换成新标签页），
    而浏览器菜单里本来就有「扩展程序 → 管理扩展程序」这条路。既然反正要手动
    点进去，再让人复制地址、切过去粘贴就是多一道手 —— 所以只选中文件夹。
    """
    ok_dir, msg_dir = open_ext_dir()
    if not ok_dir:
        return False, msg_dir
    return True, "文件夹已选中；去浏览器右上角 ⋮ →「扩展程序」→「管理扩展程序」"


def start(pid, proxy=None):
    """「登录」主入口：直接用系统默认浏览器打开，不再弹任何自动化窗口。

    登录态由浏览器扩展一键同步，或手动粘贴 cookie。返回 (ok, 人话消息)。
    proxy 参数留着是为了兼容旧调用，现在用不上。
    """
    if pid not in SITES:
        return False, "不支持的登录平台"
    ok, msg = open_site(pid)
    _set("opened" if ok else "error", pid, msg)
    return ok, msg


def cancel():
    """旧版「取消登录」入口，现在没有后台窗口了，留个空实现免得前端报错。"""
    _set("idle", "", "")
    return True

# -*- coding: utf-8 -*-
"""自动更新：查新版 → 后台下好 → 主程序退出后替换 → 自动重启。

Windows 便携版是 Python 源码目录，Release 里 103MB 的 zip 真正的代码只有
0.2MB。这里用 HTTP Range 直接读远端 zip 的中央目录，只把 *.py / web/ 等
代码文件取下来，一次更新通常几秒完成，不用重下整包，也不碰用户数据。
macOS 的 .app 被 PyInstaller 冻住，只能整包 DMG 替换；被系统权限拦住时
保留原包并给出手动出路，绝不把原来能用的删掉。

本模块既被 server.py 导入，也作为「替换助手」被独立进程执行：
    python updater.py --apply <plan.json>
助手等主进程退出后才动文件，所以不会和正在运行的程序抢句柄。
"""
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import zlib

REPO = "soberbw-hash/media-downloader"
# 回归测试可以把这俩指到本地 HTTP 服务，跑完整链路而不碰线上 Release
API_LATEST = (os.environ.get("VDL_UPDATE_API") or
              "https://api.github.com/repos/%s/releases/latest" % REPO)
VERSION_TXT = (os.environ.get("VDL_UPDATE_VERSION_TXT") or
               "https://api.github.com/repos/%s/contents/version.txt" % REPO)
MANUAL_URL = (os.environ.get("VDL_UPDATE_MANUAL") or
              "https://github.com/%s/releases/latest" % REPO)
USER_AGENT = "VideoDL-Updater/1.0"

IS_WIN = os.name == "nt"
IS_MAC = sys.platform == "darwin"

# 这些是用户数据 / 运行时，热更时一个字节都不许覆盖
PROTECTED = ("config.json", "history.json", "cookies/", "logs/", "runtime/",
             "bin/", "__pycache__/", "下载的文件/", "downloads/", ".part")
CODE_ROOT_EXT = (".py", ".txt", ".md", ".json", ".bat", ".command", ".spec")
CODE_DIRS = ("web/", "browser-extension/", "assets/")
# 合并相邻 Range 的间隔，小于它就一次取回来，28 个文件通常只要 3~5 个请求
RANGE_GAP = 96 * 1024


def log(msg, data_dir=None):
    """更新日志写进数据目录，pythonw / .app 下也看得到。"""
    try:
        if not data_dir:
            import paths
            data_dir = paths.data_dir()
        d = os.path.join(data_dir, "logs")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "update.log"), "a", encoding="utf-8") as f:
            f.write("%s  %s\n" % (time.strftime("%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


def version_numbers(v):
    return tuple(int(x) for x in re.findall(r"\d+", v or "0"))


def opener(proxy="auto"):
    """auto = 走系统代理（公司网 / 开了 VPN 时必需）；空 = 强制直连。"""
    if proxy == "auto":
        return urllib.request.build_opener()
    if not proxy:
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return urllib.request.build_opener(urllib.request.ProxyHandler({
        "http": proxy, "https": proxy,
    }))


def proxy_hint():
    """没发现任何系统代理时返回 True，前端据此提示「下载慢？先开 VPN」。"""
    try:
        import core
        if core.system_proxy():
            return False
    except Exception:
        pass
    try:
        if urllib.request.getproxies().get("https") or urllib.request.getproxies().get("http"):
            return False
    except Exception:
        pass
    for k in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY"):
        if os.environ.get(k):
            return False
    return True


def _is_local(url):
    try:
        return (urllib.parse.urlparse(url).hostname or "") in (
            "127.0.0.1", "localhost", "::1")
    except Exception:
        return False


def _fetch(op, url, headers=None, rng=None, timeout=12, tries=3):
    if _is_local(url):
        # 本地清单 / 回归测试服务永远直连，别被系统代理带走
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    h = {"User-Agent": USER_AGENT}
    h.update(headers or {})
    if rng:
        h["Range"] = rng
    last = None
    for i in range(tries):
        try:
            with op.open(urllib.request.Request(url, headers=h), timeout=timeout) as r:
                return r.read(), dict(r.headers), r.geturl()
        except Exception as e:
            last = e
            # 代理隧道偶发 502，隔半秒再试，别让整次更新白跑
            time.sleep(0.4 * (i + 1))
    raise last


def _json(op, url, timeout=8, tries=2):
    body, _, _ = _fetch(op, url, {
        "Accept": "application/vnd.github+json",
    }, timeout=timeout, tries=tries)
    return json.loads(body.decode("utf-8", "replace"))


def pick_asset(assets, mac=False):
    if mac:
        for a in assets:
            n = (a.get("name") or "").lower()
            if n.endswith(".dmg") and ("apple-m" in n or "-mac" in n):
                return a
    else:
        for a in assets:
            n = (a.get("name") or "").lower()
            if n.endswith(".zip") and "windows" in n:
                return a
    return None


def _version_urls():
    """查版本号的备用地址，按「最不容易挂」排序。

    GitHub 的 Release API 按出口 IP 限流（一小时 60 次），公司网、校园网、
    开着代理的机器经常是几十个人共用一个 IP，很容易被 403；raw 直链几乎不管，
    contents API 兜最后。三个都试，别因为一个接口 403 就告「已是最新」。
    """
    urls = [VERSION_TXT]
    alts = [x.strip() for x in (os.environ.get("VDL_UPDATE_VERSION_ALT") or "").split(",")
            if x.strip()]
    if alts:
        urls.extend(alts)
    elif _is_local(VERSION_TXT):
        return urls          # 回归测试把地址指到本地假发布页，别掺线上地址
    else:
        urls.append("https://raw.githubusercontent.com/%s/main/version.txt" % REPO)
        urls.append("https://api.github.com/repos/%s/contents/version.txt" % REPO)
    seen, out = set(), []
    for u in urls:
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _net_reason(e):
    """把网络异常翻成人话。用户不该看到 HTTP Error 403 这种原文。"""
    s = str(e)
    m = re.search(r"HTTP Error (\d{3})", s)
    if m:
        code = m.group(1)
        if code == "403":
            return "同一网络查得太频繁，被 GitHub 限流了"
        if code == "404":
            return "发布页打不开"
        if code[:1] == "5":
            return "GitHub 那边出错了"
        return "更新服务返回 %s" % code
    low = s.lower()
    if "timed out" in low or "timeout" in low:
        return "连接超时"
    if "certificate" in low or "ssl" in low:
        return "证书校验没过，代理软件可能拦了 HTTPS"
    if "getaddrinfo" in low or "name or service" in low:
        return "域名解析不了，可能被限制了"
    return "网络不通"


def check(current, timeout=8, op=None):
    """查最新版和对应安装包。连不上就如实说，不假装是最新。

    整个过程有总预算：这个函数是在界面的请求线程里跑的，卡半分钟用户就以为
    点「检查更新」没反应。宁可快一点给一句「连不上」，也别干等。
    """
    op = op or opener()
    deadline = time.time() + max(10, min(timeout * 2, 14))
    out = {
        "ok": False, "current": current, "latest": "", "has_update": False,
        "url": MANUAL_URL, "asset": None, "proxy_hint": proxy_hint(),
        "can_auto": True, "platform": "mac" if IS_MAC else "win", "error": "",
    }
    api_err = None
    try:
        # 一次就够：被墙的话重试三次只是让用户多点半分钟等待
        d = _json(op, API_LATEST, min(timeout, 6), tries=1)
        latest = (d.get("tag_name") or "").lstrip("v").strip()
        if not latest:
            raise ValueError("发布页没有版本号")
        asset = pick_asset(d.get("assets") or [], IS_MAC)
        out.update({
            "ok": True, "latest": latest,
            "has_update": version_numbers(latest) > version_numbers(current),
            "asset": ({"name": asset.get("name"), "url": asset.get("browser_download_url"),
                       "size": asset.get("size") or 0,
                       "digest": asset.get("digest") or ""} if asset else None),
        })
        if not asset:
            # 版本查到了但对应系统的包没找到：Mac 的 DMG 是发版后由 CI 单独补传的，
            # 刚发完的那几分钟确实会空一段。别说成「已是最新」，那会让人以为没更新。
            out["can_auto"] = False
            out["error"] = ("Mac 包还在生成，过几分钟再点一次检查"
                            if IS_MAC else "发布页里没有 Windows 安装包")
        return out
    except Exception as e:
        api_err = e

    # Release API 被限流 / 连不上：退到只读 version.txt 的地址，至少能说清有没有新版
    for u in _version_urls():
        left = deadline - time.time()
        if left <= 1.0:
            break
        try:
            body, _, _ = _fetch(op, u, {"Accept": "application/vnd.github.raw"},
                                timeout=min(4, left), tries=1)
            latest = body.decode("utf-8", "replace").strip().lstrip("v")
            if not version_numbers(latest):
                continue
            out.update({"ok": True, "latest": latest,
                        "has_update": version_numbers(latest) > version_numbers(current),
                        "can_auto": False, "error": "查到新版了，这次得手动下载"})
            return out
        except Exception:
            continue

    why = _net_reason(api_err)
    out["error"] = ("连不上更新服务器（%s）。先开代理再点一次检查" % why
                    if out["proxy_hint"] else
                    "连不上更新服务器（%s）。开了代理的话，换个节点再试" % why)
    return out


# ---------------- 远端 zip：只读需要的几个文件 ----------------
class UnsupportedZip(Exception):
    pass


class RemoteZip(object):
    """通过 HTTP Range 读 zip 中央目录，再按需取回指定文件。

    GitHub 的资产会 302 到 release-assets.githubusercontent.com，那边支持
    显式 Range（负 Range 会 501，所以不能用 bytes=-65536）。
    """
    def __init__(self, url, size=0, op=None):
        self.url = url
        self.op = op or opener()
        self.size = int(size or 0)
        self.entries = []

    def _range(self, start, end, tries=4):
        if end < start:
            return b""
        data, _, _ = _fetch(self.op, self.url, rng="bytes=%d-%d" % (start, end),
                            timeout=25, tries=tries)
        return data

    def read_dir(self):
        if not self.size:
            data, hdrs, _ = _fetch(self.op, self.url, rng="bytes=0-0", timeout=20)
            cr = hdrs.get("Content-Range") or ""
            self.size = int(cr.split("/")[-1]) if "/" in cr else 0
        if not self.size:
            raise UnsupportedZip("服务器没告诉文件总长")
        tail = self._range(max(0, self.size - 22), self.size - 1)
        i = tail.rfind(b"PK\x05\x06")
        if i < 0:
            tail = self._range(max(0, self.size - 65557), self.size - 1)
            i = tail.rfind(b"PK\x05\x06")
        if i < 0:
            raise UnsupportedZip("找不到 zip 结尾")
        count, cd_size, cd_off = (struct.unpack_from("<H", tail, i + 10)[0],
                                  struct.unpack_from("<I", tail, i + 12)[0],
                                  struct.unpack_from("<I", tail, i + 16)[0])
        if count == 0xFFFF or cd_off == 0xFFFFFFFF or cd_size == 0xFFFFFFFF:
            raise UnsupportedZip("zip64 暂不读目录，改走整包")
        cd = self._range(cd_off, cd_off + cd_size - 1)
        if len(cd) < cd_size:
            raise UnsupportedZip("中央目录没读全")
        pos, out = 0, []
        while pos + 46 <= len(cd) and cd[pos:pos + 4] == b"PK\x01\x02":
            method = struct.unpack_from("<H", cd, pos + 10)[0]
            csize, usize = struct.unpack_from("<II", cd, pos + 20)
            nlen, elen, clen = struct.unpack_from("<HHH", cd, pos + 28)
            off = struct.unpack_from("<I", cd, pos + 42)[0]
            raw = cd[pos + 46:pos + 46 + nlen]
            try:
                name = raw.decode("utf-8")
            except UnicodeDecodeError:
                try:
                    name = raw.decode("gbk")
                except UnicodeDecodeError:
                    name = raw.decode("cp437", "replace")
            out.append({"name": name, "csize": csize, "usize": usize,
                        "method": method, "off": off})
            pos += 46 + nlen + elen + clen
        self.entries = out
        return out

    @staticmethod
    def _unpack(entry, buf, start):
        """从一段连续字节里切出某个 entry 的完整 local header + 数据。"""
        rel = entry["off"] - start
        if rel < 0 or rel + 30 > len(buf) or buf[rel:rel + 4] != b"PK\x03\x04":
            raise UnsupportedZip("局部文件头对不上")
        nlen, elen = struct.unpack_from("<HH", buf, rel + 26)
        begin = rel + 30 + nlen + elen
        end = begin + entry["csize"]
        if end > len(buf):
            raise UnsupportedZip("文件数据没读全")
        raw = buf[begin:end]
        data = zlib.decompress(raw, -15) if entry["method"] == 8 else raw
        if len(data) != entry["usize"]:
            raise UnsupportedZip("解压后大小对不上")
        return data

    def read_many(self, entries):
        """把 offset 相邻的条目合并成少量 Range 请求，一次取回一批。"""
        if not entries:
            return {}
        order = sorted(entries, key=lambda e: e["off"])
        groups, cur = [], [order[0]]
        for e in order[1:]:
            # 用上一项自己的 csize 估它结束在哪，间隔小就合并成一次 Range
            prev_end = cur[-1]["off"] + 30 + cur[-1]["csize"] + 4096
            if e["off"] <= prev_end + RANGE_GAP:
                cur.append(e)
            else:
                groups.append(cur)
                cur = [e]
        groups.append(cur)
        out = {}
        for g in groups:
            start = g[0]["off"]
            last = max(g, key=lambda e: e["off"])
            end = last["off"] + 30 + last["csize"] + 4096
            buf = self._range(start, min(end, self.size - 1))
            for e in g:
                out[e["name"]] = self._unpack(e, buf, start)
        return out


def is_code_entry(rel):
    """只认程序代码，用户数据和 runtime 一律排除。"""
    rel = rel.replace("\\", "/").lstrip("/")
    low = rel.lower()
    if not rel or rel.endswith("/"):
        return False
    if low in ("config.json", "history.json") or ".part" in low:
        return False
    if low.startswith(PROTECTED):
        return False
    if "/" not in rel and low.endswith(CODE_ROOT_EXT):
        return True
    return low.startswith(CODE_DIRS)


def code_entries(rz, root_prefix=None):
    """挑出 zip 里 程序/ 下的代码文件，返回 (条目, 相对路径)。"""
    names = [e["name"] for e in rz.entries]
    if root_prefix is None:
        cands = [n for n in names if "/程序/" in n or n.startswith("程序/")]
        if not cands:
            raise UnsupportedZip("包里找不到程序目录")
        root = min(cands, key=len).split("程序/")[0] + "程序/"
    else:
        root = root_prefix
    out = []
    for e in rz.entries:
        if not e["name"].startswith(root) or e["name"].endswith("/"):
            continue
        rel = e["name"][len(root):]
        if is_code_entry(rel):
            out.append((e, rel))
    if not any(rel == "server.py" for _, rel in out):
        raise UnsupportedZip("包里没有 server.py，不像本软件的安装包")
    return out


def fetch_hot(info, workdir, progress=None, op=None):
    """Windows 热更：只下代码文件到 staged/，返回 (staged 目录, 文件数)。"""
    asset = info["asset"]
    rz = RemoteZip(asset["url"], asset["size"], op)
    rz.read_dir()
    pairs = code_entries(rz)
    staged = os.path.join(workdir, "staged")
    shutil.rmtree(staged, ignore_errors=True)   # 别把上次更新的残留一起盖上去
    os.makedirs(staged, exist_ok=True)
    total = sum(e["csize"] for e, _ in pairs) or 1
    done = 0
    # 一次把相邻条目都取回来，避免 28 次网络往返
    got = rz.read_many([e for e, _ in pairs])
    for e, rel in pairs:
        data = got[e["name"]]
        dst = os.path.join(staged, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, "wb") as f:
            f.write(data)
        done += e["csize"]
        if progress:
            progress(done=done, total=total, stage="正在下载新版")
    # 版本必须自洽，半截包绝不拿去覆盖
    ver = os.path.join(staged, "version.txt")
    srv = os.path.join(staged, "server.py")
    if not (os.path.isfile(ver) and os.path.isfile(srv)):
        raise UnsupportedZip("新版里缺 version.txt 或 server.py")
    v = open(ver, encoding="utf-8", errors="replace").read().strip()
    body = open(srv, encoding="utf-8", errors="replace").read()
    if version_numbers(v) != version_numbers(info["latest"]):
        raise UnsupportedZip("包里的版本号和发布页对不上")
    m = re.search(r'APP_VERSION\s*=\s*["\']([\d.]+)', body)
    if m and version_numbers(m.group(1)) != version_numbers(info["latest"]):
        raise UnsupportedZip("包里的 server.py 版本对不上")
    return staged, len(pairs)


def fetch_full(info, workdir, progress=None, op=None):
    """整包下载（Mac / 热更失败兜底），带 Range 续传和 sha256 校验。"""
    asset = info["asset"]
    os.makedirs(workdir, exist_ok=True)
    url, size = asset["url"], int(asset["size"] or 0)
    path = os.path.join(workdir, asset["name"])
    part = path + ".part"
    src = path + ".part.src"
    done = 0
    if os.path.isfile(part) and os.path.isfile(src):
        try:
            lines = open(src, encoding="utf-8").read().splitlines()
            if len(lines) > 1 and lines[1].strip() == url:
                done = os.path.getsize(part)
        except Exception:
            done = 0
    if size and done > size:
        done = 0
    op = (urllib.request.build_opener(urllib.request.ProxyHandler({}))
          if _is_local(url) else (op or opener()))
    headers = {"User-Agent": USER_AGENT}
    if done:
        headers["Range"] = "bytes=%d-" % done
    with open(part, "ab" if done else "wb") as f:
        with open(src, "w", encoding="utf-8") as sf:
            sf.write("%d\n%s\n" % (size, url))
        with op.open(urllib.request.Request(url, headers=headers), timeout=30) as r:
            if done and r.status not in (206, 200):
                raise IOError("续传被服务器拒了")
            while True:
                chunk = r.read(256 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done=done, total=size or done, stage="正在下载新版")
    if size and done < size:
        raise IOError("没下全（%d/%d 字节），先别覆盖" % (done, size))
    digest = (asset.get("digest") or "").lower()
    if digest.startswith("sha256:"):
        h = hashlib.sha256()
        with open(part, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        if h.hexdigest() != digest.split(":", 1)[1]:
            os.remove(part)
            raise IOError("安装包校验没过，已丢弃")
    os.replace(part, path)
    try:
        os.remove(src)
    except OSError:
        pass
    return path


# ---------------- 替换 + 重启 ----------------
def mac_app_path(fallback=""):
    """PyInstaller 的模块目录是临时解压路径，找 .app 得从可执行文件往上走。"""
    for base in (sys.executable, fallback):
        p = os.path.abspath(base or "")
        for _ in range(7):
            if p.endswith(".app"):
                return p
            q = os.path.dirname(p)
            if q == p:
                break
            p = q
    return ""


def win_runtime(app_dir, name="pythonw.exe"):
    """便携版里自带的 Python，优先无窗口那个。拿不到返回空。"""
    for n in (name, "python.exe"):
        p = os.path.join(app_dir, "runtime", n)
        if os.path.isfile(p):
            return p
    return ""


def default_launch(mode="server", app_dir=None):
    """算出「更新完怎么把软件再拉起来」。

    Windows 上不给 exe 派活：根目录那个 音视频下载器.exe 是 PyInstaller
    单文件包，每启动一次都要把整个 Python 解到 %TEMP%\\_MEIxxxx 再 LoadLibrary。
    临时目录被安全软件锁、盘满、或者解包到一半被拦，就会甩一个英文框
    「Failed to load Python DLL ... \\_MEI000\\python313.dll」——用户看到的是
    「更新完了打不开」，还以为是更新把软件搞坏了。包里的 runtime 是真 Python，
    不经过解包，直接起 server.py 最稳。
    """
    app_dir = app_dir or os.path.dirname(os.path.abspath(__file__))
    parent = os.path.dirname(app_dir)
    if IS_WIN:
        entry = os.path.join(app_dir, "server.py")
        py = win_runtime(app_dir)
        if py and os.path.isfile(entry):
            return {"cmd": [py, entry, mode or "server"], "cwd": app_dir}
        for name in ("音视频下载器.exe",):
            exe = os.path.join(parent, name)
            if os.path.isfile(exe):
                return {"cmd": [exe], "cwd": parent}
    elif IS_MAC:
        app = mac_app_path(app_dir)
        if app:
            return {"cmd": ["open", "-n", app], "cwd": app}
    return {"cmd": [sys.executable, os.path.join(app_dir, "server.py"), mode or "server"],
            "cwd": app_dir}


def hot_from_archive(archive, workdir):
    """整包下载后的 Windows 兜底：从 zip 里挑出代码文件，照样走热更替换。"""
    import zipfile
    staged = os.path.join(workdir, "staged_full")
    shutil.rmtree(staged, ignore_errors=True)
    with zipfile.ZipFile(archive) as z:
        names = z.namelist()
        cands = [n for n in names if "程序/" in n]
        if not cands:
            raise UnsupportedZip("整包里找不到程序目录")
        root = min(cands, key=len).split("程序/")[0] + "程序/"
        files = []
        for n in names:
            if not n.startswith(root) or n.endswith("/"):
                continue
            rel = n[len(root):].replace("\\", "/")
            if not is_code_entry(rel):
                continue
            dst = os.path.join(staged, rel.replace("/", os.sep))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst, "wb") as f:
                f.write(z.read(n))
            files.append(rel)
    if "server.py" not in files:
        raise UnsupportedZip("整包里没有 server.py")
    return staged, sorted(files)


def make_plan(plan_dir, version, target, staged, files, launch,
              port=0, archive="", mode="hot", no_restart=False, no_wait=False):
    plan = {
        "version": version, "target": target, "staged": staged,
        "backup": os.path.join(plan_dir, "backup"), "files": files,
        "launch": launch, "port": port, "pid": os.getpid(),
        "archive": archive, "mode": mode, "no_restart": bool(no_restart),
        "no_wait": bool(no_wait), "created": time.time(),
    }
    p = os.path.join(plan_dir, "plan.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)
    return p


def _mac_helper_script(plan, plan_dir):
    """Mac 的 .app 里没有可独立调用的 python，助手改用系统自带的 /bin/sh。

    全程先备份旧 .app 再替换，任何一步失败都能退回去，绝不把能用的删掉。
    """
    def q(s):
        return "'" + str(s).replace("'", "'\\''") + "'"

    app = mac_app_path(plan.get("target") or "")
    if not app:
        raise IOError("找不到 .app 本体")
    port = int(plan.get("port") or 0)
    body = """#!/bin/sh
# 等主程序退出，避免和正在运行的进程抢文件
if command -v /usr/bin/curl >/dev/null 2>&1; then
  i=0
  while [ $i -lt 60 ]; do
    /usr/bin/curl -s --noproxy '*' --max-time 0.3 "http://127.0.0.1:%d/api/ping" >/dev/null 2>&1 || break
    sleep 0.4
    i=$((i+1))
  done
else
  sleep 3
fi
APP=%s
ARCHIVE=%s
NEW="$APP.vdlnew.app"
OLD="$APP.vdlold.app"
MNT=$(mktemp -d /tmp/vdl_dmg.XXXXXX)
hdiutil attach -nobrowse -readonly -mountpoint "$MNT" "$ARCHIVE" >/dev/null 2>&1 || exit 1
SRC=""
for d in "$MNT"/*.app; do [ -d "$d" ] && SRC="$d" && break; done
if [ -z "$SRC" ]; then hdiutil detach "$MNT" -quiet 2>/dev/null; exit 1; fi
rm -rf "$NEW" "$OLD"
ditto "$SRC" "$NEW" || { hdiutil detach "$MNT" -quiet 2>/dev/null; exit 1; }
mv "$APP" "$OLD" || { hdiutil detach "$MNT" -quiet 2>/dev/null; exit 1; }
mv "$NEW" "$APP" || { mv "$OLD" "$APP"; hdiutil detach "$MNT" -quiet 2>/dev/null; exit 1; }
xattr -dr com.apple.quarantine "$APP" 2>/dev/null
hdiutil detach "$MNT" -quiet 2>/dev/null
rm -rf "$OLD"
rm -f "$ARCHIVE"
open -n "$APP"
""" % (port, q(app), q(plan.get("archive") or ""))
    path = os.path.join(plan_dir, "apply.sh")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(body)
    return path


def spawn_helper(plan_path, no_restart=False):
    """起一个脱离主程序的助手：等主进程退出后替换文件并重启软件。"""
    if os.environ.get("VDL_NO_AUTO_UPDATE") == "1" or no_restart:
        return False, "测试环境不启动替换助手"
    if IS_MAC:
        try:
            plan = json.load(open(plan_path, encoding="utf-8"))
            if not plan.get("archive"):
                return False, "Mac 版只能整包更新"
            sh = _mac_helper_script(plan, os.path.dirname(plan_path))
            subprocess.Popen(["/bin/sh", sh], stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True, close_fds=True)
            return True, ""
        except Exception as e:
            return False, "起不来替换助手：%s" % str(e)[:100]
    me = os.path.abspath(__file__)
    py = sys.executable
    kwargs = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
              "stderr": subprocess.DEVNULL, "close_fds": True}
    if IS_WIN:
        # 助手也指定包内运行时：万一 sys.executable 指向的是那个单文件 exe，
        # 用它跑脚本又会撞上同一套解包问题，替换助手直接起不来。
        here = os.path.dirname(me)
        py = win_runtime(here) or sys.executable
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP：主程序退了它照样干完
        kwargs["creationflags"] = 0x00000008 | 0x00000200
        # cwd 钉在程序目录：助手要 import paths，靠 exe 外的相对路径会找不到
        kwargs["cwd"] = here
    else:
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen([py, me, "--apply", plan_path], **kwargs)
        return True, ""
    except Exception as e:
        return False, "起不来替换助手：%s" % str(e)[:100]


def _wait_gone(port, pid, limit=40):
    """等主服务真的退了。端口探活最靠谱：不用权限、跨平台一样。"""
    if not port:
        time.sleep(3)
        return True
    probe = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    end = time.time() + limit
    while time.time() < end:
        try:
            probe.open("http://127.0.0.1:%d/api/ping" % port, timeout=0.3).read()
        except Exception:
            return True
        time.sleep(0.4)
    return False


def _compile_ok(path):
    try:
        src = open(path, "rb").read().decode("utf-8", "replace")
        compile(src, path, "exec")
        return True
    except Exception:
        return False


def apply_hot(plan):
    """把 staged 里的代码盖到程序目录，出错整体回滚。"""
    target, staged, backup = plan["target"], plan["staged"], plan["backup"]
    files = plan["files"]
    os.makedirs(backup, exist_ok=True)
    for rel in files:
        src = os.path.join(staged, rel.replace("/", os.sep))
        if not os.path.isfile(src):
            raise IOError("缺文件：" + rel)
        if rel.endswith(".py") and not _compile_ok(src):
            raise IOError("新版代码有语法错：" + rel)
    # 先备份所有会被覆盖的旧文件
    for rel in files:
        old = os.path.join(target, rel.replace("/", os.sep))
        if os.path.isfile(old):
            bak = os.path.join(backup, rel.replace("/", os.sep))
            os.makedirs(os.path.dirname(bak), exist_ok=True)
            shutil.copy2(old, bak)
    written = []
    try:
        for rel in files:
            src = os.path.join(staged, rel.replace("/", os.sep))
            dst = os.path.join(target, rel.replace("/", os.sep))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            tmp = dst + ".vdlnew"
            shutil.copy2(src, tmp)
            os.replace(tmp, dst)
            written.append(dst)
        return len(written)
    except Exception:
        for rel, dst in zip(files, written):
            bak = os.path.join(backup, rel.replace("/", os.sep))
            if os.path.isfile(bak):
                shutil.copy2(bak, dst)
            else:
                try:
                    os.remove(dst)
                except OSError:
                    pass
        raise


def _mac_replace(archive, app_dir):
    """Mac：挂载 DMG → 换 .app → 去隔离 → 重启。失败保留原包。"""
    app = mac_app_path(app_dir)
    if not app:
        raise IOError("找不到 .app 本体")
    mnt = tempfile.mkdtemp(prefix="vdl_dmg_")
    subprocess.run(["hdiutil", "attach", "-nobrowse", "-readonly",
                    "-mountpoint", mnt, archive], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=90)
    try:
        src = ""
        for root, dirs, _ in os.walk(mnt):
            for d in dirs:
                if d.endswith(".app"):
                    src = os.path.join(root, d)
                    break
            if src:
                break
        if not src:
            raise IOError("DMG 里没有 .app")
        new, old = app + ".vdlnew.app", app + ".vdlold.app"
        shutil.rmtree(new, ignore_errors=True)
        subprocess.run(["ditto", src, new], check=True, timeout=180)
        shutil.rmtree(old, ignore_errors=True)
        os.rename(app, old)
        os.rename(new, app)
        subprocess.run(["xattr", "-dr", "com.apple.quarantine", app],
                       timeout=30, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
        shutil.rmtree(old, ignore_errors=True)
        return app
    except Exception:
        if os.path.isdir(app + ".vdlold.app") and not os.path.isdir(app):
            os.rename(app + ".vdlold.app", app)
        raise
    finally:
        subprocess.run(["hdiutil", "detach", mnt, "-quiet"], timeout=30,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _notify(msg):
    """重启失败时把人叫醒：这时候主程序已经退了，界面上的字没人看得见。"""
    try:
        if IS_WIN:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, msg, "音视频下载器", 0x00000040)
        elif IS_MAC:
            script = 'display dialog %s buttons {"好"} default button 1 with title "音视频下载器"' % (
                '"' + msg.replace("\\", "\\\\").replace('"', '\\"') + '"')
            subprocess.Popen(["osascript", "-e", script])
        else:
            sys.stderr.write(msg + "\n")
    except Exception:
        pass


def _clean_workdir(plan_path):
    """更新完把下载残留扫掉。

    Windows 整包兜底会往 %TEMP% 里落一个 150MB 的 zip，热更也留 staged。
    留着不管，临时目录迟早被塞满——那时候连 exe 解包都会失败，下一个版本
    就彻底更不动了。只删我们自己的产物（staged*、zip/part），计划和备份留着。
    """
    try:
        workdir = os.path.dirname(os.path.dirname(os.path.abspath(plan_path)))
        # 只在自己造的 vdl_update_* 目录里动手，别误删临时目录里的别的东西
        if not os.path.basename(workdir).startswith("vdl_update_"):
            return
        for name in os.listdir(workdir):
            if name == "plan":
                continue
            p = os.path.join(workdir, name)
            # 只认自己造出来的那几样：staged 目录和下载下来的整包。
            # 别的目录（比如用户自己放的东西）一个字节都不碰。
            if name.startswith("staged") and os.path.isdir(p):
                shutil.rmtree(p, ignore_errors=True)
            elif name.endswith((".zip", ".part", ".part.src")):
                try:
                    os.remove(p)
                except OSError:
                    pass
        log("已清理更新临时文件：%s" % workdir)
    except Exception as e:
        log("清理临时文件失败：%r" % (e,))


def launch_app(plan):
    """按计划把软件重新拉起来。返回 (成功?, 人话说明)。"""
    launch = plan.get("launch") or {}
    cmd = launch.get("cmd") or []
    if not cmd:
        return False, "没有可用的启动方式"
    try:
        kw = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
              "stderr": subprocess.DEVNULL, "close_fds": True}
        if IS_WIN:
            # DETACHED | CREATE_NO_WINDOW：别给用户闪一个黑框
            kw["creationflags"] = 0x00000008 | 0x08000000
        else:
            kw["start_new_session"] = True
        subprocess.Popen(cmd, cwd=launch.get("cwd") or None, **kw)
        return True, ""
    except Exception as e:
        return False, str(e)[:160]


def run_helper(plan_path):
    """助手进程入口。返回 (成功?, 说明)。"""
    plan = json.load(open(plan_path, encoding="utf-8"))
    if not plan.get("no_wait"):
        _wait_gone(plan.get("port") or 0, plan.get("pid") or 0)
    log("助手开始替换 v%s（%s）" % (plan.get("version"), plan.get("mode")))
    try:
        if plan.get("mode") == "hot":
            n = apply_hot(plan)
            log("已替换 %d 个代码文件" % n)
        elif plan.get("archive") and IS_MAC:
            app = _mac_replace(plan["archive"], plan["target"])
            plan["launch"] = {"cmd": ["open", "-n", app], "cwd": app}
            log("已整包替换 .app")
        else:
            raise IOError("不支持的更新方式")
        if not IS_MAC:
            _clean_workdir(plan_path)
        if not plan.get("no_restart"):
            ok, why = launch_app(plan)
            if ok:
                log("已重新拉起软件")
            else:
                # 新版已经在盘上了，只是没自己打开。这时候必须说话，
                # 否则用户以为更新把软件搞没了。
                log("重启失败：%s" % why)
                _notify("新版 v%s 已经装好了，但自动打开没成功。\n\n"
                        "请手动打开一次：\n"
                        "  · 双击桌面上的「音视频下载器」图标，或\n"
                        "  · 打开程序文件夹里的「音视频下载器-公司.lnk」\n\n"
                        "（技术信息：%s）" % (plan.get("version") or "", why))
                return True, "更新好了，但没能自动打开"
        return True, "ok"
    except Exception as e:
        log("更新失败，已回滚：%r" % (e,))
        return False, str(e)[:200]


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--apply":
        try:
            ok, msg = run_helper(sys.argv[2])
        except Exception as _e:
            log("助手异常：%r" % (_e,))
            ok, msg = False, str(_e)
        print(json.dumps({"ok": ok, "msg": msg}, ensure_ascii=False))
        sys.exit(0 if ok else 1)
    else:
        print(json.dumps(check(sys.argv[1] if len(sys.argv) > 1 else "0"),
                         ensure_ascii=False, indent=2))

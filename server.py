# -*- coding: utf-8 -*-
"""音视频下载器 - 本地服务

启动方式（默认就是独立窗口，不用选）：
  诊断.bat        -> server.py --app   无地址栏的独立窗口，关窗即退出
  诊断.bat web    -> server.py --web   普通浏览器标签，仅作兜底
  （诊断.bat 是前台跑，报错会留在窗口里；它不是日常启动入口）

服务只跑一个实例：第二次双击会复用已在运行的服务，只负责开窗口。
"""
import io
import json
import os
import shutil
import socket
import socketserver
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

IS_WIN = os.name == "nt"
IS_MAC = sys.platform == "darwin"

import core  # noqa: E402
import login as login_mod  # noqa: E402
import picker  # noqa: E402

APP_VERSION = "1.17.0"          # 改版本号记得同步 version.txt 和 GitHub 上的 version.txt
UPDATE_REPO = "soberbw-hash/media-downloader"

CONFIG_PATH = os.path.join(BASE, "config.json")
DEFAULT_CFG = {
    "out_dir": os.path.join(BASE, "downloads"),
    "out_dir_set": False,
    "quality": "best",
    "proxy": "",
    "cookies_file": "",
    "concurrency": 3,
    "sph_api": "",
    "name_tpl": "{date}_{platform}_{author}_{title}",   # 默认按日期_平台_作者_标题命名，空 = 沿用解析器自己的命名
    "save_cover": False,     # 下载完顺手存一张封面
    "music_dir": "",         # 音乐单独放一个目录，空 = 跟视频同目录
    "down_subs": False,      # 顺便把字幕存成 .srt
    "save_danmaku": False,   # B站弹幕存成同名 .xml（附件都收进同名文件夹）
    "save_desc": False,      # 简介/元信息存成同名 .txt（附件都收进同名文件夹）
}

PORT_MIN = 8765
PORT_MAX = 8785

# 搜索列表里点「下载」时，把 (平台, id) 还原成一条能走 classify 的链接
_music_url = core.music_song_url

# 封面 / 海报代理：只放行这几个域名
# hdslb.com  —— 浏览器直连会被 Chrome 的 ORB 拦（net::ERR_BLOCKED_BY_ORB）
# doubanio.com —— 有防盗链，必须带 Referer
IMG_PROXY_HOSTS = ("hdslb.com", "doubanio.com", "bilibili.com")
IMG_REFERER = {
    "doubanio.com": "https://movie.douban.com/",
    "bilibili.com": "https://www.bilibili.com/",
}
IMG_CACHE = {}          # url -> (content-type, bytes)


def _img_referer(host):
    """按域名后缀找 Referer。img3.doubanio.com 也要用豆瓣的 Referer，
    拿不到的话豆瓣防盗链会直接拒掉。"""
    for d, ref in IMG_REFERER.items():
        if host == d or host.endswith("." + d):
            return ref
    return "https://www.bilibili.com/"

MODE = "web"          # web | app
_SRV = None
_QUIT_TIMER = None
_QUIT_AT = 0.0
_QUIT_LOCK = threading.Lock()

_lock = threading.Lock()


def load_cfg():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            for k, v in DEFAULT_CFG.items():
                cfg.setdefault(k, v)
            return cfg
        except Exception:
            pass
    return dict(DEFAULT_CFG)


def save_cfg(cfg):
    """先写临时文件再换名，避免断电 / 被杀时把配置写坏。"""
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG_PATH)


def normalize_dir(path):
    """把用户填的目录整理成绝对路径；不可用时返回 (None, 原因)。"""
    p = (path or "").strip().strip('"').strip("'")
    if not p:
        return None, "目录不能为空"
    p = os.path.expandvars(os.path.expanduser(p))
    if not os.path.isabs(p):
        return None, "请填完整路径，例如 D:\\视频\\下载"
    p = os.path.normpath(p)
    try:
        os.makedirs(p, exist_ok=True)
        probe = os.path.join(p, ".vd_writable")
        with open(probe, "w", encoding="utf-8") as f:
            f.write("1")
        os.remove(probe)
    except Exception as e:
        return None, "这个目录写不进去：%s" % e
    return p, ""


CFG = load_cfg()
MANAGER = core.Manager(CFG)


# ---------------- 退出（关掉独立窗口 = 退出软件） ----------------
def arm_quit(delay=1.6):
    """安排一次退出；期间若有别的页面还在请求，会被 note_activity 取消。"""
    global _QUIT_TIMER, _QUIT_AT
    with _QUIT_LOCK:
        if _QUIT_TIMER is not None:
            _QUIT_TIMER.cancel()
        _QUIT_AT = time.time()
        t = threading.Timer(delay, _do_quit)
        t.daemon = True
        _QUIT_TIMER = t
        t.start()


def cancel_quit():
    global _QUIT_TIMER
    with _QUIT_LOCK:
        if _QUIT_TIMER is not None:
            _QUIT_TIMER.cancel()
            _QUIT_TIMER = None


def note_activity():
    """收到普通请求就记一笔。如果刚发过退出、之后还有人在请求，
    说明窗还没真关（比如别处还开着页面），撤销这次退出。"""
    global _QUIT_TIMER
    with _QUIT_LOCK:
        if _QUIT_TIMER is not None and time.time() - _QUIT_AT > 0.15:
            _QUIT_TIMER.cancel()
            _QUIT_TIMER = None


def _do_quit():
    try:
        MANAGER.cancel_all()
    except Exception:
        pass
    try:
        if _SRV is not None:
            _SRV.shutdown()
    except Exception:
        pass
    time.sleep(0.25)
    # 兜底：确保进程真的结束（后台线程还可能挂着）
    try:
        os._exit(0)
    except Exception:
        pass


class Handler(BaseHTTPRequestHandler):
    server_version = "VideoDL/1.0"

    def log_message(self, fmt, *args):
        pass

    # ---------- 工具 ----------
    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path, ctype):
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return {}

    def _export_csv(self):
        """把下载记录导成 CSV 放到下载目录，Excel 双击就能开（带 BOM，不乱码）。"""
        import csv
        try:
            rows = MANAGER.history_snapshot("", limit=100000)
            target = os.path.join(CFG.get("out_dir") or BASE, "下载记录.csv")
            tmp = target + ".tmp"
            with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
                w = csv.writer(f)
                w.writerow(["标题", "作者", "平台", "类型", "时长(秒)", "大小(MB)",
                            "下载时间", "文件路径", "原链接"])
                for x in rows:
                    w.writerow([
                        x.get("title") or "", x.get("author") or "",
                        x.get("platform_name") or "",
                        {"audio": "音乐", "images": "图集"}.get(x.get("kind"), "视频"),
                        int(x.get("duration") or 0),
                        round((x.get("size") or 0) / 1048576.0, 1),
                        time.strftime("%Y-%m-%d %H:%M",
                                      time.localtime(x.get("done_at") or time.time())),
                        x.get("path") or "", x.get("url") or "",
                    ])
            os.replace(tmp, target)
            self._json({"ok": True, "path": target, "count": len(rows)})
        except Exception as e:
            self._json({"ok": False, "error": str(e)[:200]})

    # ---------- 图集：任务里的图片文件 ----------
    def _task(self, tid):
        with MANAGER.lock:
            return MANAGER.tasks.get(str(tid or ""))

    def _img_path(self, t, idx):
        try:
            i = int(idx)
        except (TypeError, ValueError):
            return None
        if not t or not t.images or i < 0 or i >= len(t.images):
            return None
        name = t.images[i].get("name") or ""
        # 只允许任务自己记录的文件名，防止越权读盘
        if not name or "/" in name or "\\" in name:
            return None
        p = os.path.join(t.filepath or "", name)
        return p if os.path.isfile(p) else None

    def _img_ctype(self, path):
        ext = os.path.splitext(path)[1].lower()
        return {
            ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
            ".webp": "image/webp", ".gif": "image/gif",
            ".bmp": "image/bmp", ".avif": "image/avif", ".heic": "image/heic",
        }.get(ext, "application/octet-stream")

    def _zip_names(self, t, idxs):
        base = core.safe_name("%s - %s" % (t.title or "图集", t.author or ""), 50)
        return "%s（%d张）.zip" % (base, len(idxs))

    def _fetch_img(self, u, host):
        """取一张图。取不到返回 (None, None)。"""
        try:
            req = urllib.request.Request(u, headers={
                "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                               "AppleWebKit/537.36 (KHTML, like Gecko) "
                               "Chrome/131.0.0.0 Safari/537.36"),
                "Referer": _img_referer(host),
                "Accept": "image/avif,image/webp,image/*,*/*;q=0.8",
            })
            with urllib.request.urlopen(req, timeout=15) as r:
                data = r.read()
                ct = r.headers.get("Content-Type") or ""
            if not ct.startswith("image/"):
                # 返回的不是图（比如被重定向到 HTML 页面），当失败处理
                return None, None
            if len(IMG_CACHE) > 300:
                IMG_CACHE.clear()
            IMG_CACHE[u] = (ct, data)
            return ct, data
        except Exception:
            return None, None

    def _proxy_img(self):
        """封面 / 海报代理。

        为什么要有这个：
        - B站图床（i0/i1/i2.hdslb.com）浏览器直连会被 Chrome 的 ORB 拦掉，
          报 net::ERR_BLOCKED_BY_ORB，图根本进不来
        - 豆瓣图床（img*.doubanio.com）有防盗链，必须带 Referer 才给图

        顺带两件实在事：
        - B站原图一张 1.8MB，列表里 24 张就是 40 多兆；
          加上 @320w_200h_1c.jpg 之后一张 18KB，够看就行
        - 豆瓣 l_ratio 海报一张 250KB，换 s_ratio 只要 30KB
        - 取过的图缓存在内存里，重复翻同一页不用再下载一遍

        只放行白名单里的域名，不做通用代理，免得本地服务变成漏洞。
        """
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        u = (qs.get("u") or [""])[0]
        if not u.startswith(("http://", "https://")):
            self.send_error(400)
            return
        host = ""
        try:
            host = (urllib.parse.urlparse(u).hostname or "").lower()
        except Exception:
            host = ""
        if not any(host == d or host.endswith("." + d) for d in IMG_PROXY_HOSTS):
            self.send_error(403)
            return

        # 先换成小图规格；万一这个规格没有，再回头取原图
        orig = u
        if host.endswith("hdslb.com") and "@" not in u:
            u = u + "@320w_200h_1c.jpg"
        elif host.endswith("doubanio.com") and "/l_ratio_poster/" in u:
            u = u.replace("/l_ratio_poster/", "/s_ratio_poster/")

        hit = IMG_CACHE.get(u)
        if hit:
            ct, data = hit
        else:
            ct, data = self._fetch_img(u, host)
            if not data and u != orig:
                ct, data = self._fetch_img(orig, host)
            if not data:
                self.send_error(404)
                return

        self.send_response(200)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "public, max-age=3600")
        self.end_headers()
        self.wfile.write(data)

    def _music_audio(self):
        """试听音频代理：把 CDN 的音频流转给浏览器的 <audio>。

        要点：
        - 带对 Referer，否则 CDN 回 403（网易云尤其明显）
        - 浏览器的 Range 头原样带过去，<audio> 才能拖进度、读时长
        - 只允许转发这三个音乐平台的地址，不做通用代理，免得变成漏洞
        """
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        plat = (qs.get("platform") or [""])[0]
        sid = (qs.get("id") or [""])[0]
        if plat not in ("netease", "qqmusic", "kugou") or not sid:
            self.send_error(400)
            return
        try:
            import music as music_mod
            info = music_mod.preview_url(plat, sid)
        except Exception:
            self.send_error(404)
            return

        referer = {"qqmusic": "https://y.qq.com/",
                   "kugou": "https://www.kugou.com/"}.get(plat, "https://music.163.com/")
        headers = {
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/131.0.0.0 Safari/537.36"),
            "Referer": referer,
        }
        rng = self.headers.get("Range")
        if rng:
            headers["Range"] = rng
        try:
            req = urllib.request.Request(info["url"], headers=headers)
            with urllib.request.urlopen(req, timeout=30) as r:
                ctype = r.headers.get("Content-Type") or "audio/mpeg"
                if "html" in ctype.lower() or "json" in ctype.lower():
                    self.send_error(502)
                    return
                self.send_response(r.status)
                self.send_header("Content-Type", ctype)
                for h in ("Content-Length", "Content-Range", "Accept-Ranges"):
                    v = r.headers.get(h)
                    if v:
                        self.send_header(h, v)
                self.end_headers()
                while True:
                    chunk = r.read(65536)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
        except Exception:
            try:
                self.send_error(502)
            except Exception:
                pass

    # ---------- GET ----------
    def do_GET(self):
        p = urllib.parse.urlparse(self.path).path
        if p in ("/", "/index.html"):
            self._file(os.path.join(BASE, "web", "index.html"), "text/html; charset=utf-8")
        elif p == "/app.js":
            self._file(os.path.join(BASE, "web", "app.js"), "application/javascript; charset=utf-8")
        elif p == "/style.css":
            self._file(os.path.join(BASE, "web", "style.css"), "text/css; charset=utf-8")
        elif p == "/icons.js":
            self._file(os.path.join(BASE, "web", "icons.js"),
                       "application/javascript; charset=utf-8")
        elif p == "/api/state":
            note_activity()
            from urllib.parse import parse_qs
            qs = parse_qs(urllib.parse.urlparse(self.path).query)
            if qs.get("clear"):
                MANAGER.clear_done()
            self._json({
                "mode": MODE,
                "tasks": MANAGER.snapshot(),
                "config": CFG,
                "platforms": [{"id": x["id"], "name": x["name"], "level": x["level"]}
                              for x in core.PLATFORMS],
            })
        elif p == "/api/img":
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            t = self._task((qs.get("t") or [""])[0])
            fp = self._img_path(t, (qs.get("i") or ["0"])[0])
            if not fp:
                self.send_error(404)
                return
            self._file(fp, self._img_ctype(fp))
        elif p == "/api/proxy_img":
            # 豆瓣图床有防盗链、B站图床被 Chrome 的 ORB 拦，
            # 封面和海报统一由服务端代取
            self._proxy_img()
        elif p == "/api/music/audio":
            # 试听代理：网易云/QQ 的 CDN 有防盗链，浏览器直连拿不到。
            # 这儿代取音频流，顺带把浏览器的 Range 请求原样带过去，
            # 这样 <audio> 能拖进度、也能显示时长
            self._music_audio()
        elif p == "/api/check_update":
            self._json(self._check_update())
        elif p == "/api/ping":
            self._json({"ok": True, "mode": MODE})
        elif p == "/api/history":
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            q = (qs.get("q") or [""])[0]
            self._json({"items": MANAGER.history_snapshot(q)})
        elif p == "/api/export":
            self._export_csv()
        elif p == "/api/login/state":
            self._json({"sites": login_mod.sites(), "status": login_mod.status()})
        else:
            self.send_error(404)

    def _check_update(self):
        """和 GitHub 上的 version.txt 比版本。网络不通就如实说，别假装是最新。"""
        import re as _re

        def _norm(v):
            return tuple(int(x) for x in _re.findall(r"\d+", v or "0"))

        url = "https://api.github.com/repos/%s/contents/version.txt" % UPDATE_REPO
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "VideoDL/" + APP_VERSION,
                "Accept": "application/vnd.github.raw",
            })
            # 走系统代理（公司网访问 GitHub 需要），8 秒超时
            with urllib.request.urlopen(req, timeout=8) as r:
                latest = r.read().decode("utf-8", "replace").strip()
        except Exception as e:
            return {"ok": False, "current": APP_VERSION,
                    "error": "连不上更新服务器（" + str(e)[:60] + "）"}
        has = _norm(latest) > _norm(APP_VERSION)
        return {"ok": True, "current": APP_VERSION, "latest": latest,
                "has_update": has,
                "url": "https://github.com/%s/releases/latest" % UPDATE_REPO}

    # ---------- POST ----------
    def do_POST(self):
        p = urllib.parse.urlparse(self.path).path
        data = self._body()

        if p == "/api/add":
            blocks = data.get("blocks")
            if not isinstance(blocks, list):
                # 兼容旧调用：整段文本当成一批
                blocks = [{"text": data.get("text") or ""}]
            blocks = [b for b in blocks if isinstance(b, dict)]
            if not any((b.get("text") or "").strip() for b in blocks):
                self._json({"ok": False, "error": "没找到有效链接", "added": [],
                            "skipped": [], "batches": []})
                return
            self._json(core.add_blocks(MANAGER, blocks))

        elif p == "/api/cancel_all":
            self._json({"ok": True, "count": MANAGER.cancel_all()})

        elif p == "/api/login/start":
            pid = str(data.get("platform") or "")
            ok, err = login_mod.start(pid, (CFG.get("proxy") or "").strip() or None)
            self._json({"ok": ok, "error": err, "status": login_mod.status()})

        elif p == "/api/login/cancel":
            login_mod.cancel()
            self._json({"ok": True, "status": login_mod.status()})

        elif p == "/api/login/import":
            ok, msg = login_mod.import_cookie(str(data.get("platform") or ""),
                                              str(data.get("cookie") or ""))
            self._json({"ok": ok, "error": "" if ok else msg, "msg": msg})

        elif p == "/api/login/logout":
            ok, msg = login_mod.logout(str(data.get("platform") or ""))
            self._json({"ok": ok, "msg": msg})

        elif p == "/api/retry":
            ok = MANAGER.retry(str(data.get("id")))
            self._json({"ok": ok})

        elif p == "/api/retry_failed":
            self._json({"ok": True, "count": MANAGER.retry_failed()})

        elif p == "/api/cancel":
            ok = MANAGER.cancel(str(data.get("id")))
            self._json({"ok": ok})

        elif p == "/api/clear":
            MANAGER.clear_done()
            self._json({"ok": True})

        elif p == "/api/history_clear":
            MANAGER.clear_history()
            self._json({"ok": True})

        elif p == "/api/music/search":
            plat = str(data.get("platform") or "all")
            kw = str(data.get("q") or "").strip()
            limit = int(data.get("limit") or 24)
            if plat not in ("netease", "qqmusic", "kugou", "all"):
                self._json({"ok": False, "error": "这个平台还没接", "items": []})
                return
            try:
                import music as music_mod
                if plat == "all":
                    # 全网：能下的两个平台一起搜，结果交错排，免得一个平台把列表占满
                    parts = []
                    for name in ("netease", "qqmusic"):
                        try:
                            parts.append(music_mod.search(name, kw, max(8, limit // 2)))
                        except Exception:
                            parts.append([])      # 单个平台挂了不影响另一个
                    merged = []
                    for i in range(max([len(x) for x in parts] or [0])):
                        for x in parts:
                            if i < len(x):
                                merged.append(x[i])
                    items = merged[:limit]
                else:
                    items = music_mod.search(plat, kw, limit)
                self._json({"ok": True, "items": items})
            except Exception as e:
                self._json({"ok": False, "error": str(e)[:200], "items": []})

        elif p == "/api/music/add":
            items = data.get("items") or []
            batch = MANAGER.next_batch()
            added = []
            pairs = []
            skipped = []
            for it in items:
                if not isinstance(it, dict):
                    continue
                plat = it.get("platform") or "netease"
                sid = str(it.get("id") or "")
                url = _music_url(plat, sid)
                if not url:
                    continue
                t, why = MANAGER.add(url, batch=batch)
                if t:
                    # 名字先在界面上占住，下载器后面还会再查一次做校正
                    if it.get("title"):
                        t.title = str(it["title"])
                    if it.get("artist"):
                        t.author = str(it["artist"])
                    added.append(t.id)
                    # 回一份「哪首歌对应哪个任务」，前端才能在搜索结果行里
                    # 直接显示这首的进度，不用翻到底部任务区才知道下好没有
                    pairs.append({"tid": t.id, "platform": plat, "song": sid})
                elif why:
                    # 已经有同一条任务时，把那条任务的状态也带回去，
                    # 这样搜索结果行里能直接显示「已下好」，
                    # 而不是笼统一句「已经在列表里了」
                    ex = MANAGER.find_task(url)
                    skipped.append({"platform": plat, "song": sid, "reason": why,
                                    "tid": ex.id if ex else "",
                                    "status": ex.status if ex else ""})
            self._json({"ok": True, "added": added, "count": len(added),
                        "pairs": pairs, "skipped": skipped})

        elif p == "/api/music/preview":
            # 试听：先探一条能播的直链，把本地代理地址给前端
            plat = str(data.get("platform") or "")
            sid = str(data.get("id") or "")
            if plat not in ("netease", "qqmusic", "kugou") or not sid:
                self._json({"ok": False, "error": "这个平台暂时不能试听"})
                return
            try:
                import music as music_mod
                music_mod.preview_url(plat, sid)
            except Exception as e:
                self._json({"ok": False, "error": str(e)[:200]})
                return
            qs = urllib.parse.urlencode({"platform": plat, "id": sid})
            self._json({"ok": True, "url": "/api/music/audio?" + qs,
                        "title": str(data.get("title") or ""),
                        "artist": str(data.get("artist") or "")})

        elif p == "/api/search":
            kw = str(data.get("q") or "").strip()
            limit = int(data.get("limit") or 20)
            plat = str(data.get("platform") or "all")
            if not kw:
                self._json({"ok": False, "error": "先输入关键词", "items": []})
                return
            try:
                import search as search_mod
                items, errs = search_mod.search_video(
                    kw, limit, plat,
                    proxy=(CFG.get("proxy") or "").strip() or None)
                self._json({"ok": True, "items": items, "errors": errs})
            except Exception as e:
                self._json({"ok": False, "error": str(e)[:200], "items": []})

        elif p == "/api/movie/search":
            kw = str(data.get("q") or "").strip()
            limit = int(data.get("limit") or 10)
            if not kw:
                self._json({"ok": False, "error": "先输入片名", "items": []})
                return
            try:
                import movies as movies_mod
                items, errs = movies_mod.search_movie(kw, limit)
                self._json({"ok": True, "items": items, "errors": errs})
            except Exception as e:
                self._json({"ok": False, "error": str(e)[:200], "items": []})

        elif p == "/api/movie/source":
            # 单片「查正版」：只查 B站影视区有没有正版、是不是大会员
            kw = str(data.get("q") or "").strip()
            if not kw:
                self._json({"ok": False, "error": "先输入片名", "items": []})
                return
            try:
                import movies as movies_mod
                got = movies_mod.bili_sources(kw)
                self._json({"ok": True, "items": got})
            except Exception as e:
                self._json({"ok": False, "error": str(e)[:200], "items": []})

        elif p == "/api/movie/episodes":
            # 挑集：番剧整季页里选单集
            u = str(data.get("url") or "").strip()
            if not u:
                self._json({"ok": False, "error": "先输入番剧链接", "items": []})
                return
            try:
                import movies as movies_mod
                eps = movies_mod.bili_episodes(u, int(data.get("limit") or 30))
                self._json({"ok": True, "items": eps})
            except Exception as e:
                self._json({"ok": False, "error": str(e)[:200], "items": []})

        elif p == "/api/config":
            with _lock:
                if "out_dir" in data:
                    fixed, err = normalize_dir(data.get("out_dir"))
                    if not fixed:
                        self._json({"ok": False, "error": err, "config": CFG})
                        return
                    CFG["out_dir"] = fixed
                    CFG["out_dir_set"] = True   # 用户明确设过目录
                if data.get("quality") in ("best", "1080", "720", "audio"):
                    CFG["quality"] = data["quality"]
                for k in ("proxy", "cookies_file", "sph_api", "name_tpl", "music_dir"):
                    if k in data:
                        CFG[k] = str(data[k]).strip()
                if "save_cover" in data:
                    CFG["save_cover"] = bool(data["save_cover"])
                if "down_subs" in data:
                    CFG["down_subs"] = bool(data["down_subs"])
                if "save_danmaku" in data:
                    CFG["save_danmaku"] = bool(data["save_danmaku"])
                if "save_desc" in data:
                    CFG["save_desc"] = bool(data["save_desc"])
                if "concurrency" in data:
                    try:
                        CFG["concurrency"] = max(1, min(8, int(data["concurrency"])))
                    except (TypeError, ValueError):
                        pass
                save_cfg(CFG)
                MANAGER.set_config(CFG)
            self._json({"ok": True, "config": CFG})

        elif p == "/api/open":
            target = data.get("path") or CFG.get("out_dir")
            tid = data.get("id")
            if tid:
                with MANAGER.lock:
                    t = MANAGER.tasks.get(str(tid))
                    if t and t.filepath:
                        target = t.filepath
            try:
                if target and os.path.exists(target):
                    if os.path.isfile(target):
                        _open_path(os.path.dirname(target))
                    else:
                        _open_path(target)
                    self._json({"ok": True})
                else:
                    self._json({"ok": False, "error": "路径不存在"})
            except Exception as e:
                self._json({"ok": False, "error": str(e)})

        elif p == "/api/open_url":
            # 只在系统浏览器里打开白名单站点（现在只有视频号助手），
            # 不接任意网址，免得变成「随便点一下就开网页」的口子
            url = (data.get("url") or "").strip()
            host = urllib.parse.urlsplit(url).hostname or ""
            if not url.startswith("https://") or not host.endswith("weixin.qq.com"):
                self._json({"ok": False, "error": "不支持的地址"})
                return
            threading.Thread(target=webbrowser.open, args=(url,), daemon=True).start()
            self._json({"ok": True})

        elif p == "/api/pickdir":
            start = (data.get("path") or CFG.get("out_dir") or "").strip()
            got = picker.pick_folder(initial=start, title="选择下载目录")
            if got and os.path.isdir(got):
                self._json({"ok": True, "path": got})
            elif got:
                self._json({"ok": True, "path": got, "warn": "目录不存在，保存时会自动创建"})
            else:
                self._json({"ok": False, "canceled": True})

        elif p == "/api/alive":
            # 页面刚加载完：撤销一次待执行的退出（用于区分"刷新"和"关窗"）
            cancel_quit()
            self._json({"ok": True})

        elif p in ("/api/img_save", "/api/img/save"):
            # 单张保存：复制一份到下载目录根下
            t = self._task(data.get("id"))
            fp = self._img_path(t, data.get("i"))
            if not fp:
                self._json({"ok": False, "error": "这张图还没下好或已被清理"})
            else:
                try:
                    dst = core.unique_path(os.path.join(CFG["out_dir"], os.path.basename(fp)))
                    shutil.copy2(fp, dst)
                    self._json({"ok": True, "path": dst})
                except Exception as e:
                    self._json({"ok": False, "error": str(e)})

        elif p == "/api/zip":
            # 打包：items 为空 = 全部
            t = self._task(data.get("id"))
            if not t or not t.images:
                self._json({"ok": False, "error": "这个任务里没有图片"})
                return
            raw = data.get("items")
            if isinstance(raw, list) and raw:
                idxs = []
                for x in raw:
                    try:
                        idxs.append(int(x))
                    except (TypeError, ValueError):
                        pass
            else:
                idxs = list(range(len(t.images)))
            idxs = [i for i in idxs if 0 <= i < len(t.images)]
            if not idxs:
                self._json({"ok": False, "error": "没有选中任何图片"})
                return
            try:
                os.makedirs(CFG["out_dir"], exist_ok=True)
                name = self._zip_names(t, idxs)
                dst = core.unique_path(os.path.join(CFG["out_dir"], name))
                with zipfile.ZipFile(dst, "w", zipfile.ZIP_STORED) as z:
                    n = 0
                    for i in idxs:
                        fp = self._img_path(t, i)
                        if fp:
                            z.write(fp, t.images[i].get("name") or os.path.basename(fp))
                            n += 1
                self._json({"ok": True, "path": dst, "count": n,
                            "size": os.path.getsize(dst)})
            except Exception as e:
                self._json({"ok": False, "error": str(e)})

        elif p == "/api/quit":
            # 关窗：等一会儿再退，给刷新留出撤销的时间
            self._json({"ok": True})
            arm_quit(2.0)

        elif p == "/api/quit_now":
            self._json({"ok": True})
            arm_quit(0.6)

        else:
            if p.startswith("/api/"):
                self._json({"ok": False, "error": "没有这个接口：%s" % p}, 404)
            else:
                self.send_error(404)


# ---------------- 启动 ----------------
class LocalServer(ThreadingHTTPServer):
    """本地服务。重写 server_bind 跳过 getfqdn()：
    默认实现会对 127.0.0.1 做反向 DNS 解析，公司网络里能卡十几秒。"""
    daemon_threads = True

    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = "127.0.0.1"
        self.server_port = port


def free_port(start=PORT_MIN, end=PORT_MAX):
    for port in range(start, end):
        with socket.socket() as s:
            s.settimeout(0.15)
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    return start


def _probe(port):
    """探测该端口上是否已经跑着本软件。空代理，避免被系统 HTTP_PROXY 带走。"""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open("http://127.0.0.1:%d/api/ping" % port, timeout=0.15) as r:
            return json.loads(r.read().decode("utf-8")).get("ok") is True
    except Exception:
        return False


def find_running():
    """找已经在跑的本软件实例。先看端口有没有人监听（很快），再验一下是不是自己，
    因为 8765-8767 可能被别的程序占着。"""
    for port in range(PORT_MIN, PORT_MAX):
        with socket.socket() as s:
            s.settimeout(0.06)
            if s.connect_ex(("127.0.0.1", port)) != 0:
                continue
        if _probe(port):
            return port
    return None


def _browser_exe():
    """找一个能开 --app 独立窗口的 Chromium 系浏览器。

    Safari 不支持 --app，所以 mac 上只认装了 Chrome / Edge / Chromium / Brave 的机器；
    一个都没有就退化成普通浏览器标签（功能一样，只是多了地址栏）。
    """
    if IS_MAC:
        home = os.path.expanduser("~")
        cands = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            os.path.join(home, "Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
            "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
        ]
    elif IS_WIN:
        pf = os.environ.get("ProgramFiles", r"C:\Program Files")
        pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
        la = os.environ.get("LOCALAPPDATA", "")
        cands = [
            os.path.join(pf86, r"Microsoft\Edge\Application\msedge.exe"),
            os.path.join(pf, r"Microsoft\Edge\Application\msedge.exe"),
            os.path.join(pf, r"Google\Chrome\Application\chrome.exe"),
            os.path.join(pf86, r"Google\Chrome\Application\chrome.exe"),
            os.path.join(la, r"Google\Chrome\Application\chrome.exe"),
        ]
    else:
        cands = ["/usr/bin/google-chrome", "/usr/bin/chromium",
                 "/usr/bin/chromium-browser", "/usr/bin/microsoft-edge"]
    for p in cands:
        if p and os.path.isfile(p):
            return p
    return None


def app_profile_dir():
    if IS_MAC:
        return os.path.join(os.path.expanduser("~"), "Library", "Application Support",
                            "VideoDL", "appwin")
    if not IS_WIN:
        root = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
        return os.path.join(root, "VideoDL", "appwin")
    root = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(root, "VideoDL", "appwin")


def _work_area():
    """主屏可用区域（已扣掉任务栏）：(x, y, w, h)。
    这里拿到的和 GetSystemMetrics 一样是逻辑像素，正好喂给 Chromium 的 --window-position。"""
    try:
        import ctypes

        class RECT(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                        ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

        r = RECT()
        if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(r), 0):
            return r.left, r.top, r.right - r.left, r.bottom - r.top
    except Exception:
        pass
    return 0, 0, 0, 0


def _window_size():
    """按屏幕大小给个合适的初始窗口，别在小屏上超出边界。

    注意：Chromium 的 --window-size 认的是逻辑像素（DIP），跟 GetSystemMetrics
    在非 DPI 感知进程里拿到的是同一套单位，所以这里**不要**乘 DPI 缩放比。
    乘了的话，150% 缩放的 4K 屏上窗口会大出一半。"""
    try:
        import ctypes
        u = ctypes.windll.user32
        u.GetSystemMetrics.restype = ctypes.c_int
        sw, sh = u.GetSystemMetrics(0), u.GetSystemMetrics(1)
        if sw and sh:
            return max(860, min(1220, sw - 80)), max(600, min(880, sh - 120))
    except Exception:
        pass
    return 1220, 880


def open_window(url, mode):
    """app = 无地址栏独立窗口（走 Edge/Chrome 的 --app），web = 普通浏览器标签。"""
    if mode != "app":
        webbrowser.open(url)
        return "browser"

    exe = _browser_exe()
    if not exe:
        webbrowser.open(url)
        return "browser"

    try:
        profile = app_profile_dir()
        fresh = not os.path.isdir(profile)      # 第一次开：给窗口摆个居中位置
        os.makedirs(profile, exist_ok=True)
        w, h = _window_size()
        args = [
            exe,
            "--app=" + url,
            "--window-size=%d,%d" % (w, h),
            "--user-data-dir=" + profile,
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-features=Translate",
            "--hide-crash-restore-bubble",  # 崩过一次后别再弹"恢复页面"横条，破坏软件感
            "--disable-sync",
        ]
        if fresh:
            ax, ay, aw, ah = _work_area()
            if aw and ah:
                args.append("--window-position=%d,%d" % (
                    max(0, ax + (aw - w) // 2), max(0, ay + (ah - h) // 2)))
        flags = 0x00000008 if os.name == "nt" else 0  # DETACHED_PROCESS
        subprocess.Popen(args, creationflags=flags, close_fds=True)
        return "app"
    except Exception:
        webbrowser.open(url)
        return "browser"


def _open_path(target):
    """用系统默认程序打开文件 / 文件夹。Windows 用 startfile，mac 用 open，Linux 用 xdg-open。"""
    if IS_WIN:
        os.startfile(target)                # noqa: 只有 Windows 有
        return
    try:
        subprocess.Popen(["open" if IS_MAC else "xdg-open", target], close_fds=True)
    except Exception:
        pass


def fail_box(msg):
    if IS_MAC:
        try:
            esc = str(msg).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")
            subprocess.run(["osascript", "-e",
                            'display alert "音视频下载器" message "%s" as critical' % esc],
                           capture_output=True, timeout=120)
            return
        except Exception:
            print(msg)
            return
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(
            None, msg, "音视频下载器", 0x00000010)  # MB_ICONERROR
    except Exception:
        print(msg)


def _fix_streams():
    """pythonw.exe 下 sys.stdout / sys.stderr 是 None，print 会直接崩，
    这里改成写日志文件，出错也能翻得到。"""
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        d = os.path.join(BASE, "logs")
        os.makedirs(d, exist_ok=True)
        f = open(os.path.join(d, "app.log"), "a", encoding="utf-8", buffering=1)
        f.write("\n==== %s ====\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
        if sys.stdout is None:
            sys.stdout = f
        if sys.stderr is None:
            sys.stderr = f
    except Exception:
        class _Null(object):
            def write(self, *a):
                pass

            def flush(self):
                pass

        if sys.stdout is None:
            sys.stdout = _Null()
        if sys.stderr is None:
            sys.stderr = _Null()


def blog(msg):
    """启动过程打点，写到 logs/boot.log，pythonw 下也看得到。"""
    try:
        d = os.path.join(BASE, "logs")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "boot.log"), "a", encoding="utf-8") as f:
            f.write("%s  %s\n" % (time.strftime("%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


def main():
    global MODE, _SRV

    _fix_streams()
    blog("main start argv=%s" % (sys.argv[1:],))

    mode = "web"
    for a in sys.argv[1:]:
        a = a.strip().lstrip("-").lower()
        if a in ("app", "window", "exe"):
            mode = "app"
        elif a in ("web", "browser"):
            mode = "web"
        elif a in ("none", "server", "silent"):
            mode = "server"
    MODE = mode
    blog("mode=%s" % mode)

    try:
        os.makedirs(CFG["out_dir"], exist_ok=True)
    except Exception:
        # 目录没了（移动硬盘拔掉 / 路径写错）：退回程序内的 downloads，别让启动失败
        CFG["out_dir"] = DEFAULT_CFG["out_dir"]
        CFG["out_dir_set"] = False
        try:
            os.makedirs(CFG["out_dir"], exist_ok=True)
            save_cfg(CFG)
        except Exception:
            pass

    # 已经有实例在跑：不再起第二个服务，只开窗口
    running = find_running()
    blog("find_running=%s" % running)
    if running:
        url = "http://127.0.0.1:%d/" % running
        how = open_window(url, mode)
        blog("reused url=%s how=%s" % (url, how))
        print("服务已在运行：%s（%s）" % (url, "独立窗口" if how == "app" else "浏览器"))
        return

    port = free_port()
    blog("free_port=%s" % port)
    try:
        srv = LocalServer(("127.0.0.1", port), Handler)
    except Exception as e:
        blog("bind failed: %r" % (e,))
        fail_box("启动失败：端口被占用\n%s" % e)
        return

    _SRV = srv
    blog("bound ok")
    url = "http://127.0.0.1:%d/" % port

    if mode == "app":
        threading.Timer(0.6, lambda: open_window(url, "app")).start()
        print("音视频下载器已在独立窗口打开。关掉窗口即退出。")
    elif mode == "server":
        print("音视频下载器已启动：", url)
    else:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
        print("音视频下载器已启动：", url)
        print("关闭这个窗口即停止服务。")

    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            srv.server_close()
        except Exception:
            pass


if __name__ == "__main__":
    try:
        main()
    except Exception as _e:
        import traceback
        traceback.print_exc()
        if MODE == "app":
            fail_box("启动失败：\n%s" % _e)
        time.sleep(0.2)

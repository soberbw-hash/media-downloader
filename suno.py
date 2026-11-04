# -*- coding: utf-8 -*-
"""Suno（AI 音乐）分享链接解析与下载

链接长这样：
    https://suno.com/s/IdazmB1kcRTOjU5p          短链，302 到 /song/{uuid}
    https://suno.com/song/6cf789dd-73ae-...      完整页
    https://cdn1.suno.ai/{uuid}.mp4              视频直链

拿到 uuid 之后有一条**公开接口**（不需要登录、不需要 cookie）：
    GET https://studio-api.prod.suno.com/api/clip/{uuid}/
    返回 title / handle / image_url / video_url / audio_url / metadata

音频为什么不能直接用 audio_url：
    游客态有时返回的是 https://studio-api.prod.suno.com/api/forbidden —— Suno
    不给未登录用户直链；cdn1.suno.ai/{uuid}.mp3 也要 CloudFront 签名，裸链
    403 MissingKey。页面里那条 m4a 是 AES 加密的，下下来是乱码。
    但**登录态**下 audio_url 是带签名的真地址，能直接下 mp3。

所以下载按「谁能下就用谁」排队试：
    1. audio_url（有 cookie 时通常是能用的 mp3，音质最好）
    2. video_url（mp4，抽音轨）
    3. cdn1 / cdn2 的 {uuid}.mp4（接口没给 video_url 时兜底）
    4. 分享页 HTML 里带的签名地址（页面自己的播放器要用，通常有效）
S3 对「不存在」的 key 返回 403 AccessDenied 而不是 404 —— 所以别把 403
一律当成「作者限制了下载」，否则会误导人。
"""

import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

from core import BASE, safe_name, unique_path

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

API_CLIP = "https://studio-api.prod.suno.com/api/clip/%s/"

#: 分享页 / 短链 / 直链都能认
HOSTS = ("suno.com", "suno.ai", "studio-api.prod.suno.com",
         "cdn1.suno.ai", "cdn2.suno.ai")

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
SHORT_RE = re.compile(r"/s/([A-Za-z0-9]{5,})")

#: 这些不是媒体地址，别去下（Suno 用它们表示「不给你」）
NOT_MEDIA = ("/api/forbidden", "/forbidden", "studio-api.prod.suno.com")

MEDIA_EXT = (".mp3", ".m4a", ".wav", ".flac", ".mp4")

MP3_BITRATE = "192k"


def host_ok(url):
    try:
        h = (urllib.parse.urlsplit(url).hostname or "").lower()
    except Exception:
        return False
    return any(h == d or h.endswith("." + d) for d in HOSTS)


def find_uuid(url):
    m = UUID_RE.search(url or "")
    return m.group(0) if m else ""


def is_suno(url):
    return bool(SHORT_RE.search(url or "")) or bool(UUID_RE.search(url or "")) \
        if host_ok(url) else False


def _cookie_header():
    """有 cookies/suno.txt 就带上（登录态能拿到可下的 audio_url）。
    Netscape cookies.txt 和「一行 key=value; key=value」都认。"""
    path = os.path.join(BASE, "cookies", "suno.txt")
    if not os.path.isfile(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            raw = f.read()
    except OSError:
        return ""
    if "\t" not in raw and "\n" not in raw.strip():
        return raw.strip()
    pairs = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) >= 7:
            pairs.append("%s=%s" % (parts[5], parts[6]))
    return "; ".join(pairs)


def _opener(proxy=""):
    """proxy 为空时显式屏蔽环境变量里的代理，和项目其它模块保持一致。"""
    proxy = (proxy or "").strip()
    if proxy:
        handler = urllib.request.ProxyHandler({"http": proxy, "https": proxy})
    else:
        handler = urllib.request.ProxyHandler({})
    return urllib.request.build_opener(handler)


def _get(url, proxy="", timeout=30, headers=None):
    h = {"User-Agent": UA, "Referer": "https://suno.com/", "Accept": "*/*"}
    # cookie 只送给 suno 自己的站点，别撒到 CDN 上
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    if host.endswith("suno.com") or host.endswith("suno.ai"):
        ck = _cookie_header()
        if ck:
            h["Cookie"] = ck
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    return _opener(proxy).open(req, timeout=timeout)


def _get_json(url, proxy="", timeout=30):
    r = _get(url, proxy, timeout)
    try:
        raw = r.read()
    finally:
        r.close()
    return json.loads(raw.decode("utf-8", "replace"))


def resolve(url, proxy=""):
    """把任意形式的 Suno 链接解析成歌曲信息。失败抛 RuntimeError（中文原因）。"""
    u = (url or "").strip()
    if not u:
        raise RuntimeError("链接是空的")
    if not u.startswith("http"):
        u = "https://" + u

    uid = find_uuid(u)
    if not uid:
        # 短链只能跟一次跳转，页面本身是 SPA，读不出信息
        try:
            r = _get(u, proxy, timeout=25)
            final = r.geturl()
            r.close()
        except Exception as e:
            raise RuntimeError("打不开这条 Suno 链接（%s）" % _brief(e))
        uid = find_uuid(final)
    if not uid:
        raise RuntimeError("这不是 Suno 的歌曲链接，认不出歌曲编号")

    try:
        data = _get_json(API_CLIP % uid, proxy, timeout=25)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise RuntimeError("这首歌不存在，或者作者设成了私密")
        raise RuntimeError("Suno 接口返回 %s" % e.code)
    except Exception as e:
        raise RuntimeError("连不上 Suno（%s），先确认浏览器里能打开这个页面"
                           % _brief(e))

    if not isinstance(data, dict):
        raise RuntimeError("Suno 返回的数据看不懂")

    meta = data.get("metadata") or {}
    status = (data.get("status") or "").lower()
    if status and status not in ("complete", "streaming"):
        raise RuntimeError("这首还在生成中（%s），等它完成再来" % status)

    return {
        "id": uid,
        "title": (data.get("title") or "").strip() or "Suno - " + uid[:8],
        "author": (data.get("display_name") or data.get("handle") or "").strip(),
        "cover": data.get("image_large_url") or data.get("image_url") or "",
        "video": _clean(data.get("video_url")),
        "audio": _clean(data.get("audio_url")),
        "tags": (meta.get("tags") or "").strip(),
        "lyrics": (meta.get("prompt") or "").strip(),
        "model": data.get("model_name") or "",
        "created": data.get("created_at") or "",
        "page": "https://suno.com/song/%s" % uid,
    }


def _brief(e):
    s = str(e) or e.__class__.__name__
    return s if len(s) <= 60 else s[:57] + "..."


def _clean(u):
    """把「不给你」的占位地址当成空。"""
    u = (u or "").strip()
    if not u or not u.startswith("http"):
        return ""
    if any(x in u for x in NOT_MEDIA):
        return ""
    return u


def _ext_of(u, default=".mp3"):
    p = urllib.parse.urlsplit(u).path.lower()
    for e in MEDIA_EXT:
        if p.endswith(e):
            return e
    return default


def candidates(info):
    """能下到声音的地址，按音质从好到次排。"""
    out = []
    for u in (_clean(info.get("audio")), _clean(info.get("video"))):
        if u and u not in out:
            out.append(u)
    for host in ("cdn1.suno.ai", "cdn2.suno.ai"):
        u = "https://%s/%s.mp4" % (host, info["id"])
        if u not in out:
            out.append(u)
    return out


_MEDIA_RE = re.compile(
    r"https://[a-z0-9.\-]*suno\.ai/[0-9a-fA-F\-]{36}\."
    r"(?:mp3|m4a|wav|flac|mp4)(?:\?[^\"'\\\s<>]*)?")


def _looks_media(u):
    low = u.lower()
    if "/forbidden" in low:
        return False
    return any(e in low.split("?")[0] for e in MEDIA_EXT)


def page_media(page, uid="", proxy=""):
    """从分享页 HTML 里捞媒体地址。

    页面的播放器是给匿名访客用的，所以它拿到的音频地址通常不带登录态也能下。
    签名的地址可能不在 cdn1 上，所以按「同一首歌的 uuid」再扫一遍。
    """
    try:
        r = _get(page or "", proxy, timeout=25)
        html = r.read().decode("utf-8", "replace")
        r.close()
    except Exception:
        return []
    html = (html.replace("\\u0026", "&").replace("\\/", "/")
                .replace("&amp;", "&"))
    pats = [_MEDIA_RE]
    if uid:
        pats.insert(0, re.compile(
            r"https://[a-z0-9.\-]+/[^\"'\\\s<>]*" + re.escape(uid) +
            r"[^\"'\\\s<>]*", re.I))
    out = []
    for p in pats:
        for m in p.finditer(html):
            u = m.group(0)
            if u not in out and _looks_media(u):
                out.append(u)
    out.sort(key=lambda x: 0 if ".mp3" in x else 1)
    return out


# ---------- ffmpeg ----------
def find_ffmpeg():
    """找可用的 ffmpeg：先看程序目录（便携版会自带的那个位置），再看 PATH。"""
    here = os.path.dirname(os.path.abspath(__file__))
    names = ("ffmpeg.exe", "ffmpeg") if os.name == "nt" else ("ffmpeg",)
    for d in (here, os.path.join(here, "bin"), os.path.join(here, "runtime")):
        for n in names:
            p = os.path.join(d, n)
            if os.path.isfile(p):
                return p
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    return ""


def _to_mp3(ffmpeg, src, dst):
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
           "-i", src, "-vn", "-c:a", "libmp3lame", "-b:a", MP3_BITRATE, dst]
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if p.returncode != 0 or not os.path.isfile(dst) or os.path.getsize(dst) < 10240:
        err = (p.stderr or b"").decode("utf-8", "replace").strip()
        raise RuntimeError("转 mp3 失败：%s" % (err[:120] or "ffmpeg 返回错误"))
    return dst


# ---------- 下载 ----------
def _stream(url, out_path, progress, proxy="", label="下载中"):
    r = _get(url, proxy, timeout=60)
    total = int(r.headers.get("Content-Length") or 0)
    got = 0
    t0 = time.time()
    try:
        with open(out_path, "wb") as f:
            while True:
                chunk = r.read(262144)
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
                if total:
                    progress(done=got, total=total,
                             speed=got / 1048576.0 / max(time.time() - t0, 0.01),
                             percent=got * 100.0 / total, stage=label)
    finally:
        try:
            r.close()
        except Exception:
            pass
    if total and got < total:
        raise RuntimeError("下载中断了（%d/%d 字节）" % (got, total))
    return out_path


def _try_all(urls, base, out_dir, progress, proxy):
    """按顺序试，第一个成功的就返回 (临时文件, 扩展名)。"""
    tried = []
    for u in urls:
        ext = _ext_of(u)
        tmp = unique_path(os.path.join(out_dir, base + ".part" + ext))
        try:
            _stream(u, tmp, progress, proxy, "下载中")
            return tmp, ext, tried
        except urllib.error.HTTPError as e:
            _quiet_rm(tmp)
            why = "%s %s" % (ext.lstrip("."), e.code)
        except Exception as e:
            _quiet_rm(tmp)
            why = "%s %s" % (ext.lstrip("."), type(e).__name__)
        if why not in tried:      # cdn1 / cdn2 都 403 时别重复说两遍
            tried.append(why)
    return "", "", tried


def download(url, out_dir, progress, quality="best", proxy=""):
    """返回音频文件路径（和 sph.download 一致，单个路径，不是元组）。

    优先拿真音频（mp3），拿不到就把视频下下来抽音轨；
    连 ffmpeg 都没有就把 mp4 留着（剪映能直接分离音频）。
    """
    progress(stage="解析中", percent=0, done=0, total=0, speed=0)
    info = resolve(url, proxy)
    progress(title=info["title"], author=info["author"], thumb=info["cover"])

    base = safe_name(info["title"]) or "suno"
    if info["author"]:
        base = safe_name("%s - %s" % (info["title"], info["author"]))

    os.makedirs(out_dir, exist_ok=True)

    tmp, ext, tried = _try_all(candidates(info), base, out_dir, progress, proxy)

    # 接口给的地址全不行，再从分享页里翻一遍
    if not tmp:
        extra = [u for u in page_media(info["page"], info["id"], proxy)
                 if u not in candidates(info)]
        if extra:
            progress(stage="换个来源再试", percent=0, done=0, total=0, speed=0)
            tmp, ext, more = _try_all(extra, base, out_dir, progress, proxy)
            tried += more

    if not tmp:
        raise RuntimeError(
            "这首歌的音频拿不到\n"
            "过一会儿点「重试」试试\n"
            "Suno 没给这首生成视频版，公开的音频地址也没拿到（试过：%s）。"
            "刚做好的歌要等几分钟才有视频版；如果一直这样，说明这首只对"
            "登录用户开放，先在浏览器里打开它确认能播放。"
            % ("、".join(tried[:4]) or "没有可用地址"))

    # 收尾：mp4 抽音轨 / mp3 直接留
    ffmpeg = find_ffmpeg()
    if ext == ".mp3":
        dst = unique_path(os.path.join(out_dir, base + ".mp3"))
        os.replace(tmp, dst)
        path = dst
    elif ffmpeg:
        progress(stage="转 mp3", percent=100, done=0, total=0, speed=0)
        dst = unique_path(os.path.join(out_dir, base + ".mp3"))
        try:
            _to_mp3(ffmpeg, tmp, dst)
        except Exception:
            _quiet_rm(dst)
            raise
        finally:
            _quiet_rm(tmp)
        path = dst
    else:
        dst = unique_path(os.path.join(out_dir, base + ext))
        os.replace(tmp, dst)
        path = dst

    # 歌名/作者/封面写进 ID3，播放器和剪辑软件里都能看到
    if path.endswith(".mp3"):
        try:
            from music import write_id3
            write_id3(path, {"title": info["title"], "artist": info["author"],
                             "album": "Suno", "cover": info["cover"]}, referer="")
        except Exception:
            pass
    elif info["cover"]:
        # mp4 没有 ID3，封面单独存一张
        try:
            cover = os.path.splitext(path)[0] + ".jpg"
            _stream(info["cover"], cover, _noop, proxy, "封面")
        except Exception:
            _quiet_rm(cover)

    # 把真实文件大小回报，否则界面上会显示 0 KB
    try:
        total = os.path.getsize(path)
    except OSError:
        total = 0
    progress(stage="完成", percent=100, done=total, total=total, speed=0)
    return path


def _noop(**kw):
    pass


def _quiet_rm(p):
    try:
        if os.path.isfile(p):
            os.remove(p)
    except Exception:
        pass

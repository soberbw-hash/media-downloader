# -*- coding: utf-8 -*-
"""Suno（AI 音乐）分享链接解析与下载

链接长这样：
    https://suno.com/s/IdazmB1kcRTOjU5p          短链，302 到 /song/{uuid}
    https://suno.com/song/6cf789dd-73ae-...      完整页
    https://cdn1.suno.ai/{uuid}.mp4              视频直链

拿到 uuid 之后有一条**公开接口**（不需要登录、不需要 cookie）：
    GET https://studio-api.prod.suno.com/api/clip/{uuid}/
    返回 title / handle / image_url / video_url / metadata(tags, prompt 歌词)

音频为什么不用 audio_url：
    游客态返回的是 https://studio-api.prod.suno.com/api/forbidden —— Suno 现在
    不给未登录用户直链。cdn1.suno.ai/{uuid}.mp3 也一样要 CloudFront 签名，
    裸链 403 MissingKey。页面里那条 m4a（d2lwuy8qc234o3.cloudfront.net）是
    AES 加密的，下下来是乱码。

能用的是 video_url 指向的 mp4：
    https://cdn1.suno.ai/{uuid}.mp4
    实测 5.6MB / 824x1464 竖屏 H.264 + **197kbps AAC 48kHz 立体声**，
    音质够用，而且没加密、不用登录。
所以下载流程是「下 mp4 → 抽音频」：
    机器上有 ffmpeg 就转成 mp3（192k），没有就把 mp4 留着（剪映能直接分离音频）。
"""

import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from core import safe_name, unique_path

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

API_CLIP = "https://studio-api.prod.suno.com/api/clip/%s/"

#: 分享页 / 短链 / 直链都能认
HOSTS = ("suno.com", "suno.ai", "studio-api.prod.suno.com",
         "cdn1.suno.ai", "cdn2.suno.ai")

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
SHORT_RE = re.compile(r"/s/([A-Za-z0-9]{5,})")

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
    video = data.get("video_url") or ""
    if not video:
        video = "https://cdn1.suno.ai/%s.mp4" % uid

    status = (data.get("status") or "").lower()
    if status and status not in ("complete", "streaming"):
        raise RuntimeError("这首还在生成中（%s），等它完成再来" % status)

    return {
        "id": uid,
        "title": (data.get("title") or "").strip() or "Suno - " + uid[:8],
        "author": (data.get("display_name") or data.get("handle") or "").strip(),
        "cover": data.get("image_large_url") or data.get("image_url") or "",
        "video": video,
        "audio": data.get("audio_url") or "",
        "tags": (meta.get("tags") or "").strip(),
        "lyrics": (meta.get("prompt") or "").strip(),
        "model": data.get("model_name") or "",
        "created": data.get("created_at") or "",
        "page": "https://suno.com/song/%s" % uid,
    }


def _brief(e):
    s = str(e) or e.__class__.__name__
    return s if len(s) <= 60 else s[:57] + "..."


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


def download(url, out_dir, progress, quality="best", proxy=""):
    """返回音频文件路径（和 sph.download 一致，单个路径，不是元组）。

    有 ffmpeg 给 mp3，没有就把 mp4 留着（剪映能直接分离音频）。
    """
    progress(stage="解析中", percent=0, done=0, total=0, speed=0)
    info = resolve(url, proxy)
    progress(title=info["title"], author=info["author"], thumb=info["cover"])

    base = safe_name(info["title"]) or "suno"
    if info["author"]:
        base = safe_name("%s - %s" % (info["title"], info["author"]))

    os.makedirs(out_dir, exist_ok=True)

    # 1) 视频版（唯一能公开拿到的完整音源）
    tmp = unique_path(os.path.join(out_dir, base + ".part.mp4"))
    try:
        _stream(info["video"], tmp, progress, proxy, "下载中")
    except urllib.error.HTTPError as e:
        _quiet_rm(tmp)
        if e.code == 403:
            raise RuntimeError("这首歌的音频拿不到（作者可能限制了下载）")
        raise RuntimeError("下载失败：HTTP %s" % e.code)
    except Exception:
        _quiet_rm(tmp)
        raise

    # 2) 有 ffmpeg 就转 mp3，没有就留 mp4
    ffmpeg = find_ffmpeg()
    if ffmpeg:
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
        # 歌名/作者/封面写进 ID3，播放器和剪辑软件里都能看到
        try:
            from music import write_id3
            write_id3(path, {"title": info["title"], "artist": info["author"],
                             "album": "Suno", "cover": info["cover"]}, referer="")
        except Exception:
            pass
    else:
        dst = unique_path(os.path.join(out_dir, base + ".mp4"))
        os.replace(tmp, dst)
        path = dst
        # mp4 没有 ID3，封面单独存一张
        if info["cover"]:
            try:
                cover = os.path.splitext(dst)[0] + ".jpg"
                _stream(info["cover"], cover, _noop, proxy, "封面")
            except Exception:
                _quiet_rm(cover)

    # 收尾：把真实文件大小回报，否则界面上会显示 0 KB
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

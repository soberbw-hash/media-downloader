# -*- coding: utf-8 -*-
"""直链 / HLS(m3u8) / DASH(mpd) 下载。

判断顺序借了浏览器嗅探扩展（猫抓 cat-catch, GPL-3.0）的思路：
先问"这条链接是什么"，再决定怎么下。代码是自己写的，没有抄它的实现。

三种形态：
    master.m3u8   多码率清单，按设置里的清晰度挑一档
    media.m3u8    单码率切片列表，交给 ffmpeg 直接拉
    a.mp4 / a.mpd 普通直链 / DASH，ffmpeg 能吃就吃

为什么不用 yt-dlp 兜：它对 m3u8 只是"能跑"，不给码率选择，
也不会自动带对 Referer —— 大部分站的 CDN 是认 Referer 的，
带错了直接 403。
"""

import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from core import UA_PC, unique_path
import paths

#: 播放列表后缀
PLAYLIST_EXT = {"m3u8", "m3u", "mpd"}
#: 媒体文件后缀（顺序从"像视频"到"像音频"）
MEDIA_EXT = {
    "mp4", "m4v", "mov", "mkv", "webm", "flv", "ts", "m4s", "avi", "wmv",
    "mpg", "mpeg", "3gp", "m4a", "mp3", "aac", "flac", "wav", "ogg", "opus",
}
#: 这些扩展名出来的是音频，界面上要标成音频
AUDIO_EXT = {"m4a", "mp3", "aac", "flac", "wav", "ogg", "opus"}

_NO_WIN = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0


# ---------- 识别 ----------
def _ext_of(url):
    try:
        p = urllib.parse.urlsplit(url)
    except Exception:
        return ""
    name = (p.path or "").rsplit("/", 1)[-1]
    if "." not in name:
        return ""
    return name.rsplit(".", 1)[-1].lower()


def looks_like(url):
    """这条链接像不像可以直接下。像就给 detect_platform 认成「直链」"""
    try:
        p = urllib.parse.urlsplit(url)
    except Exception:
        return False
    if p.scheme not in ("http", "https"):
        return False
    ext = _ext_of(url)
    if ext in PLAYLIST_EXT or ext in MEDIA_EXT:
        return True
    # 不少站把播放列表藏在参数里：/play?format=m3u8&id=xxx
    q = (p.query or "").lower()
    return "m3u8" in q or "format=m3u" in q or "format=mpd" in q


def is_audio_name(url):
    return _ext_of(url) in AUDIO_EXT


# ---------- 网络 ----------
def default_headers(url):
    """防盗链：绝大多数站只认自己域名下的 Referer。
    浏览器的 Referer 是页面地址，这里拿不到，用同源根路径，
    实测足够过大部分 CDN。"""
    p = urllib.parse.urlsplit(url)
    origin = "%s://%s/" % (p.scheme, p.netloc)
    return {
        "User-Agent": UA_PC,
        "Referer": origin,
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }


def _opener(proxy):
    if proxy:
        return urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _open(url, headers, proxy, timeout=30, extra=None):
    h = dict(headers or {})
    if extra:
        h.update(extra)
    req = urllib.request.Request(url, headers=h)
    return _opener(proxy).open(req, timeout=timeout)


def _read_text(url, headers, proxy, limit=524288):
    """抓播放列表。返回 (文本, 最终地址)"""
    r = _open(url, headers, proxy)
    try:
        data = r.read(limit)
        final = r.geturl() or url
        ctype = (r.headers.get("Content-Type") or "").lower()
    finally:
        r.close()
    text = data.decode("utf-8", "replace")
    if text.lstrip().startswith("#EXTM3U") or "<MPD" in text[:2048]:
        return text, final
    if "mpegurl" not in ctype:
        # 不是播放列表，别硬解析
        raise RuntimeError("这条链接不是播放列表（返回的是 %s）" % (ctype or "未知内容"))
    return text, final


# ---------- 播放列表解析 ----------
def _attrs(s):
    """BANDWIDTH=1234,RESOLUTION=640x360,CODECS="a,b" → dict"""
    out = {}
    for m in re.finditer(r'([A-Za-z0-9-]+)=("[^"]*"|[^,]*)', s):
        out[m.group(1).upper()] = m.group(2).strip().strip('"')
    return out


def is_master(text):
    return "#EXT-X-STREAM-INF" in text


def variants(text, base_url):
    """多码率清单 → [{height, width, bandwidth, codecs, url}]"""
    lines = [ln.strip() for ln in text.splitlines()]
    out = []
    for i, ln in enumerate(lines):
        if not ln.startswith("#EXT-X-STREAM-INF:"):
            continue
        a = _attrs(ln.split(":", 1)[1])
        uri = ""
        for nx in lines[i + 1:]:
            if nx and not nx.startswith("#"):
                uri = nx
                break
        if not uri:
            continue
        w = h = 0
        m = re.match(r"(\d+)x(\d+)", a.get("RESOLUTION", ""))
        if m:
            w, h = int(m.group(1)), int(m.group(2))
        out.append({
            "width": w, "height": h,
            "bandwidth": int(re.sub(r"\D", "", a.get("BANDWIDTH", "0")) or 0),
            "codecs": a.get("CODECS", ""),
            "url": urllib.parse.urljoin(base_url, uri),
        })
    return out


def pick_variant(vs, quality="best"):
    """按设置里的清晰度挑一档；挑不到就最高的"""
    if not vs:
        return None

    def score(v):
        return (v.get("height") or 0, v.get("bandwidth") or 0)

    q = (quality or "best").strip().lower()
    target = int(re.sub(r"\D", "", q) or 0) if q not in ("best", "max") else 0
    if target:
        # 只要分辨率明确的档：height=0 表示清单里没写分辨率，
        # 那种档不能当成"最低档"混进来
        cand = [v for v in vs if 0 < (v.get("height") or 0) <= target]
        if cand:
            return max(cand, key=score)
        # 设的清晰度比所有档都低（比如想要 480 但只有 720/1080）：
        # 取最接近的那个，别掉到没标分辨率的最低档上
        above = [v for v in vs if (v.get("height") or 0) > target]
        if above:
            return min(above, key=score)
    return max(vs, key=score)


def duration_of(text):
    """切片列表里的总时长（秒），用于算进度"""
    total = 0.0
    for m in re.finditer(r"#EXTINF:\s*([\d.]+)", text):
        try:
            total += float(m.group(1))
        except ValueError:
            pass
    return total


def segments(text, base_url):
    """切片列表 → [(绝对地址, 时长)]。加密/初始化段的信息单独返回"""
    key = None
    init = ""
    seq = 0
    out = []
    pending = 0.0
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        if ln.startswith("#EXT-X-KEY:"):
            a = _attrs(ln.split(":", 1)[1])
            method = (a.get("METHOD") or "NONE").upper()
            key = None if method == "NONE" else {
                "method": method,
                "uri": urllib.parse.urljoin(base_url, a["URI"]) if a.get("URI") else "",
                "iv": a.get("IV", ""),
            }
        elif ln.startswith("#EXT-X-MAP:"):
            a = _attrs(ln.split(":", 1)[1])
            if a.get("URI"):
                init = urllib.parse.urljoin(base_url, a["URI"])
        elif ln.startswith("#EXT-X-MEDIA-SEQUENCE:"):
            seq = int(re.sub(r"\D", "", ln.split(":", 1)[1]) or 0)
        elif ln.startswith("#EXTINF:"):
            try:
                pending = float(ln.split(":", 1)[1].split(",")[0])
            except ValueError:
                pending = 0.0
        elif not ln.startswith("#"):
            out.append({
                "url": urllib.parse.urljoin(base_url, ln),
                "dur": pending,
                "seq": seq + len(out),
                "key": key,
            })
            pending = 0.0
    return init, out


# ---------- ffmpeg ----------
def find_ffmpeg():
    """找 ffmpeg：随包 bin/ → PATH → imageio-ffmpeg 自带的静态版。"""
    return paths.ffmpeg_path()


def _hms(s):
    try:
        parts = s.strip().split(":")
        sec = 0.0
        for p in parts:
            sec = sec * 60 + float(p)
        return sec
    except Exception:
        return 0.0


def _ffmpeg_pull(ffmpeg, url, out, headers, proxy, total_sec, progress, stage):
    """ffmpeg 直接拉流，边走边报进度。成功返回 True"""
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-nostdin"]
    hdr = "".join("%s: %s\r\n" % (k, v) for k, v in (headers or {}).items())
    if hdr:
        cmd += ["-headers", hdr]
    if proxy:
        cmd += ["-http_proxy", proxy]
    # 网络抖一下自动重连，别一断就整个重来
    cmd += ["-reconnect", "1", "-reconnect_streamed", "1",
            "-reconnect_delay_max", "10"]
    cmd += ["-i", url, "-c", "copy", "-progress", "pipe:1", "-nostats", out]

    errs = []
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, creationflags=_NO_WIN)
    try:
        for raw in proc.stdout:
            line = raw.decode("utf-8", "replace").strip()
            if line.startswith("out_time="):
                t = _hms(line.split("=", 1)[1])
                pct = (t / total_sec * 100.0) if total_sec > 0 else 0.0
                progress(stage=stage, percent=min(99.0, pct),
                         done=int(t * 1000), total=int(total_sec * 1000), speed=0)
            elif line and not line.startswith(("frame=", "fps=", "bitrate=", "total_size=",
                                               "out_time_", "dup_frames", "drop_frames",
                                               "speed=", "progress=", "stream_")):
                errs.append(line)
                del errs[:-6]
    finally:
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
    if proc.returncode != 0 or not os.path.isfile(out) or os.path.getsize(out) < 20480:
        _quiet_rm(out)
        raise RuntimeError("ffmpeg 没拉下来：" + (errs[-1] if errs else "未知原因"))
    return True


def _quiet_rm(p):
    try:
        if p and os.path.exists(p):
            os.remove(p)
    except Exception:
        pass


# ---------- 纯 Python 兜底（没有 ffmpeg 时）----------
def _py_pull(media_url, text, out, headers, proxy, progress, stage):
    """逐个切片拼进 out。支持断点：.idx 里记「拼到第几个、文件多长」，
    重试时把半截的最后一段截掉，从下一个接着拼。"""
    init, segs = segments(text, media_url)
    if not segs:
        raise RuntimeError("切片列表是空的")
    for s in segs:
        if s["key"] and s["key"].get("method") not in (None, "NONE"):
            raise RuntimeError("这个流是加密的（%s），需要 ffmpeg 才能下"
                               % s["key"]["method"])
    items = ([{"url": init, "dur": 0.0}] if init else []) + segs
    total = sum(s["dur"] for s in segs) or 0

    # 接着上次的进度拼：.idx 记「已完成几段 / 文件多长」
    idx = out + ".idx"
    done_n, done_bytes = 0, 0
    if os.path.exists(out) and os.path.exists(idx):
        try:
            with open(idx, "r", encoding="utf-8") as f:
                done_n, done_bytes = [int(x) for x in f.read().split()[:2]]
        except (OSError, ValueError):
            done_n, done_bytes = 0, 0
        if done_n > len(items) or os.path.getsize(out) < done_bytes:
            done_n, done_bytes = 0, 0        # 文件被动过就别信检查点
        with open(out, "r+b") as f:          # 半截的最后一段丢掉
            f.truncate(done_bytes)
    elif os.path.exists(idx):
        _quiet_rm(idx)

    done = sum(s["dur"] for s in segs[:max(0, done_n - (1 if init else 0))])
    with open(out, "ab" if done_bytes else "wb") as f:
        for i, s in enumerate(items):
            if i < done_n:
                continue
            r = _open(s["url"], headers, proxy, timeout=40)
            try:
                want = int(r.headers.get("Content-Length") or 0)
                buf = bytearray()
                while True:
                    c = r.read(262144)
                    if not c:
                        break
                    buf += c
            finally:
                r.close()
            # 连接硬断时 read() 会安静返回空串，必须拿 Content-Length 兜底
            if want and len(buf) != want:
                raise RuntimeError("切片没下全（%d/%d 字节）" % (len(buf), want))
            if not buf:
                raise RuntimeError("切片是空的")
            f.write(buf)
            done_n = i + 1
            if s["dur"]:
                done += s["dur"]
            with open(idx, "w", encoding="utf-8") as f2:
                f2.write("%d %d" % (done_n, f.tell()))
            progress(stage=stage, percent=(done / total * 100.0) if total else 0.0,
                     done=min(done_n, len(segs)), total=len(segs), speed=0)
    _quiet_rm(idx)
    return True


# ---------- 文件名 ----------
_GENERIC = {"index", "master", "playlist", "hls", "media", "video", "main",
            "manifest", "stream", "output", "v", "a", "m"}


def guess_name(url):
    try:
        p = urllib.parse.urlsplit(url)
    except Exception:
        return "video_" + time.strftime("%Y%m%d_%H%M%S")
    base = urllib.parse.unquote((p.path or "").rsplit("/", 1)[-1])
    if "." in base:
        base = base.rsplit(".", 1)[0]
    base = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", base).strip(" ._-")
    if len(base) < 3 or base.lower() in _GENERIC or base.isdigit():
        base = "%s_%s" % ((p.hostname or "video").replace(".", "_"),
                          time.strftime("%Y%m%d_%H%M%S"))
    return base[:70]


# ---------- 主流程 ----------
def download(url, out_dir, progress, quality="best", proxy=""):
    """下一条直链 / 播放列表。返回单个文件路径（跟 sph.download 一致）"""
    url = (url or "").strip()
    if not url:
        raise RuntimeError("没有链接")
    os.makedirs(out_dir, exist_ok=True)
    headers = default_headers(url)
    name = guess_name(url)

    progress(stage="解析中", percent=0, done=0, total=0, speed=0, title=name)

    ext = _ext_of(url)
    text = ""

    # 1) 是播放列表吗
    if ext in PLAYLIST_EXT or "m3u8" in (urllib.parse.urlsplit(url).query or "").lower():
        text, final = _read_text(url, headers, proxy)
        url = final
        if "<MPD" in text[:4096]:
            raise RuntimeError("这是 DASH(mpd) 清单，暂时不支持；"
                               "用浏览器下载或者换条 m3u8")
        if is_master(text):
            vs = variants(text, url)
            pick = pick_variant(vs, quality)
            if not pick:
                raise RuntimeError("多码率清单是空的")
            label = ("%dp" % pick["height"]) if pick.get("height") else \
                    ("%dkbps" % (pick["bandwidth"] // 1000))
            progress(stage="选清晰度 %s（共 %d 档）" % (label, len(vs)),
                     percent=0, done=0, total=0, speed=0)
            media_url = pick["url"]
            text, media_url = _read_text(media_url, headers, proxy)
        else:
            media_url = url
        total_sec = duration_of(text)
    else:
        media_url, total_sec = url, 0.0
        text = ""

    ffmpeg = find_ffmpeg()
    is_audio = is_audio_name(media_url)

    # 2) 有 ffmpeg 就走 ffmpeg（能吃 AES-128、EXT-X-MAP、byterange）
    if ffmpeg and (text or ext in PLAYLIST_EXT):
        stage = "下载中"
        for oext in ((".m4a" if is_audio else ".mp4"), ".mkv"):
            out = unique_path(os.path.join(out_dir, name + oext))
            tmp = os.path.splitext(out)[0] + ".part" + oext   # 下完才顶正式名
            try:
                _ffmpeg_pull(ffmpeg, media_url, tmp, headers, proxy,
                             total_sec, progress, stage)
                os.replace(tmp, out)
                size = os.path.getsize(out)
                progress(stage="完成", percent=100, done=1, total=size, speed=0)
                return out
            except Exception as e:
                _quiet_rm(tmp)
                if oext != ".mkv":
                    continue          # 换个容器再试一次
                raise e
    elif text:
        # 3) 没 ffmpeg：只能下没加密的 TS/MP4 切片，拼起来（支持断点）
        out = unique_path(os.path.join(out_dir, name + (".mp4" if "#EXT-X-MAP" in text else ".ts")))
        tmp = os.path.splitext(out)[0] + ".part" + os.path.splitext(out)[1]
        _py_pull(media_url, text, tmp, headers, proxy, progress, "下载中")
        os.replace(tmp, out)
        size = os.path.getsize(out)
        progress(stage="完成", percent=100, done=1, total=size, speed=0)
        return out

    # 4) 普通直链：流式下载
    out = unique_path(os.path.join(out_dir, name + ("." + ext if ext else ".bin")))
    _plain_pull(url, out, headers, proxy, progress)
    size = os.path.getsize(out)
    progress(stage="完成", percent=100, done=1, total=size, speed=0)
    return out


def _plain_pull(url, out, headers, proxy, progress):
    from core import resumable_download

    def _req(u, h, t):
        r = _open(u, h, proxy, timeout=t)
        ctype = (r.headers.get("Content-Type") or "").lower()
        if "text/html" in ctype:
            r.close()
            raise RuntimeError("这条链接返回的是网页，不是文件")
        return r

    resumable_download(url, out, progress, _req, headers,
                       min_bytes=1024, timeout=60, label="下载中")
    if os.path.getsize(out) < 1024:
        _quiet_rm(out)
        raise RuntimeError("文件太小，可能链接失效了")

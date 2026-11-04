# -*- coding: utf-8 -*-
"""微信视频号

视频号的媒体地址长这样（明文 mp4，拿到就能直接下）：
    https://finder.video.qq.com/251/20302/stodownload?encfilekey=...&token=...&sign=...

难点只在于「怎么拿到这串地址」：
  · 分享链接 https://weixin.qq.com/sph/xxxxx 本身不含媒体信息，
    官方预览页只给封面和文案，视频流要登录态才发。
  · 所以这里提供两条路：
      1) 直接粘 finder.video.qq.com 的直链 —— 软件负责下载（带正确的 Referer）
      2) 在设置里填一个「解析接口」，软件把分享链接发过去，取回直链
"""

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from core import (UA_PC, http_get, safe_name, stream_download, unique_path)

DIRECT_HOSTS = ("finder.video.qq.com", "findermp.video.qq.com", "wxapp.tc.qq.com")
SHARE_RE = re.compile(r"/sph/([A-Za-z0-9_-]{4,})")

REFERER = "https://channels.weixin.qq.com/"
WX_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
         "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 "
         "MicroMessenger/7.0.20 NetType/WIFI Language/zh_CN")

_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def is_direct(url):
    h = (urllib.parse.urlsplit(url).hostname or "").lower()
    return any(h == d or h.endswith("." + d) for d in DIRECT_HOSTS) and "stodownload" in url


def share_id(url):
    m = SHARE_RE.search(url or "")
    return m.group(1) if m else ""


VIDEO_KEYS = ("videoUrl", "video_url", "playable_url", "playUrl", "play_url",
              "download_url", "downloadUrl", "media_url", "mediaUrl",
              "url", "src", "video")


def _walk(obj, depth=0):
    """摊平 JSON：产出 (key, value) 和 (None, 裸字符串)。"""
    if depth > 8:
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k, v
            yield from _walk(v, depth + 1)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v, depth + 1)


def _find_media(obj):
    """在任意结构的 JSON 里找视频地址。

    先找官方直链（带 stodownload 的最可信），再按常见字段名兜底。
    """
    if isinstance(obj, str):
        return obj if ("stodownload" in obj and obj.startswith("http")) else ""

    for _, v in _walk(obj):
        if isinstance(v, str) and "stodownload" in v and v.startswith("http"):
            return v

    for k, v in _walk(obj):
        if k in VIDEO_KEYS and isinstance(v, str) and v.startswith("http"):
            low = v.lower()
            if ".mp4" in low or ".m3u8" in low or "/video" in low:
                return v
    return ""


def _text_field(obj, *names, depth=0):
    """从 JSON 里找第一个非空字符串字段（标题、作者之类）。"""
    if depth > 6:
        return ""
    if isinstance(obj, dict):
        for n in names:
            v = obj.get(n)
            if isinstance(v, str) and v.strip():
                return v.strip()
        for v in obj.values():
            got = _text_field(v, *names, depth=depth + 1)
            if got:
                return got
    elif isinstance(obj, list):
        for v in obj:
            got = _text_field(v, *names, depth=depth + 1)
            if got:
                return got
    return ""


def _call_api(api, share_url, timeout=25):
    """按「POST JSON → GET 查参数」的顺序调解析接口，返回解析后的 JSON。"""
    base = api.strip()
    if not base:
        return None

    # 支持 {url} 占位符
    if "{url}" in base:
        target = base.replace("{url}", urllib.parse.quote(share_url, safe=""))
        try:
            with _OPENER.open(urllib.request.Request(target, headers={"User-Agent": UA_PC}),
                              timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8", "ignore"))
        except Exception:
            return None

    body = json.dumps({"url": share_url, "link": share_url}).encode()
    req = urllib.request.Request(base, data=body, headers={
        "User-Agent": WX_UA,
        "Content-Type": "application/json",
        "Accept": "application/json, text/plain, */*",
        "Referer": REFERER,
    })
    try:
        with _OPENER.open(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "ignore"))
    except Exception:
        pass

    q = base + ("&" if "?" in base else "?") + "url=" + urllib.parse.quote(share_url, safe="")
    try:
        with _OPENER.open(urllib.request.Request(q, headers={"User-Agent": WX_UA,
                                                             "Accept": "application/json"}),
                          timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "ignore"))
    except Exception:
        return None


def resolve(share_url, api, timeout=25):
    """把分享链接交给用户配的解析接口，返回 (title, author, media_url)。"""
    data = _call_api(api, share_url, timeout)
    if not data:
        raise RuntimeError("解析接口没有响应，检查设置里的「视频号解析接口」地址对不对"
                           "（接口约定见设置里「怎么获取？」）")
    media = _find_media(data)
    if not media:
        msg = _text_field(data, "error", "errMsg", "msg", "message", "detail")
        raise RuntimeError(msg or "解析接口没返回视频地址，可能这会儿限流了")
    title = _text_field(data, "title", "desc", "description", "caption")
    author = _text_field(data, "nickname", "author", "userName", "name")
    return title, author, media


def _headers():
    return {"User-Agent": WX_UA, "Referer": REFERER, "Accept": "*/*"}


def download(url, out_dir, progress, quality="best", api=""):
    """直链直接下；分享链接走解析接口。返回文件路径。"""
    title = author = ""
    media = url

    if not is_direct(url):
        sid = share_id(url)
        if not sid:
            raise RuntimeError("这不是视频号链接")
        if not (api or "").strip():
            raise RuntimeError(
                "视频号分享链接里没有视频\n"
                "微信只把地址发给微信客户端\n"
                "别人的视频在网页里拿不到，这是微信的限制。自己的作品可以在"
                "「设置 → 高级设置 → 视频号 → 打开视频号助手」里扫码登录后下载原片；"
                "有解析接口的话也能填进去。")
        progress(stage="正在解析分享链接…")
        title, author, media = resolve(url, api)
        progress(title=title, author=author)

    if not (media or "").startswith("http"):
        raise RuntimeError("拿到的视频地址无效")

    name = "%s - %s.mp4" % (safe_name(title or "视频号视频"),
                            safe_name(author or time.strftime("%Y%m%d-%H%M")))
    out_path = unique_path(os.path.join(out_dir, name))

    path, _ = stream_download([media], out_path, progress, _headers())
    return path

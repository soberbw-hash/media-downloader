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
import socket
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

#: 有人本机跑着 wx_channels_download（视频号下载器）的话，它自带解析接口。
#: 发现了就直接用，用户什么都不用配；没装就跳过，别拖慢正常流程。
LOCAL_HELPERS = (
    "http://127.0.0.1:2022/api/channels/parse_sph",
    "http://127.0.0.1:2022/api/channels/feed/profile",
)


def _opener(proxy=""):
    """proxy 留空时显式屏蔽环境变量代理，和项目其它模块一致。"""
    p = (proxy or "").strip()
    handler = (urllib.request.ProxyHandler({"http": p, "https": p}) if p
               else urllib.request.ProxyHandler({}))
    return urllib.request.build_opener(handler)


def _candidate_openers(api):
    """本地接口只直连；外部接口先直连，不通再自动走系统代理。

    公司机器上境外解析站直连必然超时，而系统代理是通的——这一步之前没人做，
    所以用户以为「解析接口没用」。
    """
    yield _opener()
    host = (urllib.parse.urlsplit(api).hostname or "").lower()
    if host in ("127.0.0.1", "localhost", "::1"):
        return
    try:
        from core import system_proxy
        p = system_proxy()
        if p:
            yield _opener(p)
    except Exception:
        pass


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


def _call_api(api, share_url, timeout=25, token="", op=None):
    """按「POST JSON → GET 查参数」的顺序调解析接口，返回解析后的 JSON。

    token 用来填 Bearer 鉴权（有些公共解析服务要凭证）。
    """
    base = (api or "").strip()
    if not base:
        return None
    op = op or _opener()

    def heads(extra=None):
        h = {"User-Agent": WX_UA, "Accept": "application/json, text/plain, */*"}
        if token:
            h["Authorization"] = "Bearer " + token
        if extra:
            h.update(extra)
        return h

    # 支持 {url} 占位符
    if "{url}" in base:
        target = base.replace("{url}", urllib.parse.quote(share_url, safe=""))
        try:
            with op.open(urllib.request.Request(target, headers=heads()),
                         timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8", "ignore"))
        except Exception:
            return None

    body = json.dumps({"url": share_url, "link": share_url}).encode()
    req = urllib.request.Request(base, data=body, headers=heads({
        "Content-Type": "application/json", "Referer": REFERER}))
    try:
        with op.open(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "ignore"))
    except Exception:
        pass

    q = base + ("&" if "?" in base else "?") + "url=" + urllib.parse.quote(share_url, safe="")
    try:
        with op.open(urllib.request.Request(q, headers=heads()), timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "ignore"))
    except Exception:
        return None


def _local_helper(share_url, timeout=3):
    """本机装了视频号下载器就蹭它的解析接口，零配置。没装立刻放弃。"""
    # 先探端口：没服务时两次 HTTP 超时会白白拖好几秒，用户以为卡死了
    try:
        s = socket.create_connection(("127.0.0.1", 2022), timeout=0.3)
        s.close()
    except OSError:
        return None
    op = _opener()
    body = json.dumps({"url": share_url, "share_url": share_url}).encode()
    for api in LOCAL_HELPERS:
        try:
            req = urllib.request.Request(api, data=body, headers={
                "User-Agent": WX_UA, "Content-Type": "application/json"})
            with op.open(req, timeout=timeout) as r:
                data = json.loads(r.read().decode("utf-8", "ignore"))
            if _find_media(data):
                return data
        except Exception:
            continue
    return None


def resolve(share_url, api="", timeout=25, token=""):
    """把分享链接交给解析接口，返回 (title, author, media_url)。

    优先用设置里填的接口（自动试直连 / 系统代理）；没填就找本机已运行的
    视频号下载器。都没有才报错，而且把能走的路一次说清。
    """
    base = (api or "").strip()
    data = None
    if base:
        for op in _candidate_openers(base):
            got = _call_api(base, share_url, timeout, token, op)
            if got and _find_media(got):
                data = got
                break
        if data is None:
            raise RuntimeError(
                "解析接口没回应，或没给视频地址\n"
                "检查设置里的接口地址和凭证；境外接口会自动走系统代理\n"
                "也可能是这条视频设了权限，或者接口这会儿限流了")
    else:
        data = _local_helper(share_url)
        if not data:
            raise RuntimeError(
                "视频号分享链接里没有视频\n"
                "微信只把地址发给微信客户端，网页里拿不到，这是微信的限制\n"
                "自己的作品：设置里点「打开视频号助手」下原片；别人的视频："
                "在设置里填解析接口，或本机装 wx_channels_download（我会自动接上）")
    media = _find_media(data)
    if not media:
        msg = _text_field(data, "error", "errMsg", "msg", "message", "detail")
        raise RuntimeError(msg or "解析接口没返回视频地址，可能这会儿限流了")
    title = _text_field(data, "title", "desc", "description", "caption")
    author = _text_field(data, "nickname", "author", "userName", "name")
    return title, author, media


def _headers():
    return {"User-Agent": WX_UA, "Referer": REFERER, "Accept": "*/*"}


def download(url, out_dir, progress, quality="best", api="", token=""):
    """直链直接下；分享链接走解析接口。返回文件路径。"""
    title = author = ""
    media = url

    if not is_direct(url):
        if not share_id(url):
            raise RuntimeError("这不是视频号链接")
        progress(stage="正在解析分享链接…")
        title, author, media = resolve(url, api, token=token)
        progress(title=title, author=author)

    if not (media or "").startswith("http"):
        raise RuntimeError("拿到的视频地址无效")

    name = "%s - %s.mp4" % (safe_name(title or "视频号视频"),
                            safe_name(author or time.strftime("%Y%m%d-%H%M")))
    out_path = unique_path(os.path.join(out_dir, name))

    path, _ = stream_download([media], out_path, progress, _headers())
    return path

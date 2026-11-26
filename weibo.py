# -*- coding: utf-8 -*-
"""微博兜底：走 m.weibo.cn 的 status 接口，不用登录也能拿到视频 / 图集地址。

yt-dlp 的 Weibo extractor 要解析桌面页，桌面页这两年改成 SSR + 一堆风控，
时不时就「no video found」。移动端接口反而一直很朴素：给个 mid 就回 JSON，
视频地址明明白白躺在 page_info 里。
"""
import json
import os
import re
import urllib.parse
import urllib.request

UA_M = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")
UA_PC = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
         "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

# 清晰度从高到低。键名是微博自己的命名，换过一次，两套都留着
_QUALITY_KEYS = (
    "mp4_1080p_mp4", "mp4_720p_mp4", "mp4_hd_mp4", "mp4_ld_mp4",
    "stream_url_hd", "stream_url", "hd_url", "url",
)


def _opener(proxy=""):
    if proxy:
        return urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _get_json(url, op, cookie="", timeout=20):
    hdr = {"User-Agent": UA_M, "Referer": "https://m.weibo.cn/",
           "MWeibo-Pwa": "1", "X-Requested-With": "XMLHttpRequest",
           "Accept": "application/json, text/plain, */*"}
    if cookie:
        hdr["Cookie"] = cookie
    raw = op.open(urllib.request.Request(url, headers=hdr), timeout=timeout)
    data = json.loads(raw.read().decode("utf-8", "ignore"))
    if data.get("ok") not in (1, "1", True):
        msg = data.get("msg") or ""
        if data.get("ok") in (-100, "-100") or "登录" in msg:
            raise RuntimeError("这条微博要登录才看得到")
        raise RuntimeError("微博接口没返回内容：%s" % (msg or "可能已删除"))
    return data.get("data") or {}


def mid_of(url):
    """从各种微博地址里抠出 mid（可能是 base62 的短 ID）。"""
    for pat in (r"/status(?:es)?/([0-9A-Za-z]{6,})", r"/detail/(\d{6,})",
                r"[?&]mid=(\d{6,})", r"/tv/show/\d+[:%3A]+(\w{6,})",
                r"[?&]fid=\d+[:%3A]+(\w{6,})"):
        m = re.search(pat, url or "", re.I)
        if m:
            return m.group(1)
    m = re.search(r"weibo\.(?:com|cn)/(?:u/)?\d+/([0-9A-Za-z]{6,})", url or "")
    return m.group(1) if m else ""


def is_video_page(url):
    """video.weibo.com/show?fid=1034:xxx 这类「视频专页」的 id 不是博文 id。"""
    return bool(re.search(r"(?:[?&]fid=|[?&]oid=|/tv/show/)", url or "", re.I))


_B62 = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _b62_to_id(s):
    """微博网页长链里的 mid 是 base62 编码，接口只认纯数字 id。"""
    if not s or s.isdigit():
        return s
    try:
        out = ""
        for i in range(0, len(s), 4):
            n = 0
            for ch in s[i:i + 4]:
                n = n * 62 + _B62.index(ch)
            # 除最后一组外都补到 7 位，不然拼起来位数会错
            out += str(n).zfill(7) if i + 4 < len(s) else str(n)
        return out
    except ValueError:
        return s


def _pick_urls(page):
    """从 page_info 里挑出所有能下的地址，清晰的排前面。"""
    out = []
    media = page.get("media_info") or {}
    if isinstance(media, dict):
        # playback_list 是这两年的主力结构：[{url, quality, ...}]
        for item in media.get("playback_list") or []:
            if not isinstance(item, dict):
                continue
            u = item.get("url") or ((item.get("play_info") or {}).get("url"))
            if isinstance(u, str) and u.startswith("http"):
                out.append(u)
        for key in _QUALITY_KEYS:
            u = media.get(key)
            if isinstance(u, str) and u.startswith("http"):
                out.append(u)
    urls = page.get("urls") or {}
    if isinstance(urls, dict):
        # 键里带 1080/720/hd 的优先
        def rank(kv):
            k = kv[0].lower()
            if "1080" in k:
                return 0
            if "720" in k or "hd" in k:
                return 1
            return 2
        for _, u in sorted(urls.items(), key=rank):
            if isinstance(u, str) and u.startswith("http"):
                out.append(u)
    elif isinstance(urls, list):
        out.extend([u for u in urls if isinstance(u, str) and u.startswith("http")])
    for key in ("stream_url", "stream_url_hd", "page_url"):
        u = page.get(key)
        if isinstance(u, str) and u.startswith("http") and u not in out:
            out.append(u)
    # 去重、保序
    seen, uniq = set(), []
    for u in out:
        if u not in seen:
            seen.add(u)
            uniq.append(u)
    return uniq


# 新浪图床靠 URL 里的目录名决定尺寸和水印。
# mw690 / large / woriginal 这些是「带博主昵称水印」的版本，
# osj1080 / oslarge 才是无水印原图 —— 直接换目录名就行，不用二次处理。
_WM_DIR_RE = re.compile(r"/(?:orj360|orj1080|thumbnail|wap\d+|thumb\d+|mw\d+|large|woriginal|bmiddle|small)/", re.I)


def _no_watermark(url):
    if "sinaimg.cn" not in (url or ""):
        return url
    return _WM_DIR_RE.sub("/osj1080/", url, count=1)


def _images(data):
    out = []
    for pic in data.get("pics") or []:
        if not isinstance(pic, dict):
            continue
        u = pic.get("large", {}).get("url") if isinstance(pic.get("large"), dict) else ""
        u = u or pic.get("url") or ""
        u = _no_watermark(u)
        if u.startswith("http"):
            out.append(u)
    return out


def _live_photos(data):
    """实况图（Live Photo）自带的短视频。有就顺手当视频素材留着。"""
    out = []
    for pic in data.get("pics") or []:
        if isinstance(pic, dict) and pic.get("videoSrc"):
            out.append(pic["videoSrc"])
    for item in (data.get("live_photo") or []):
        if isinstance(item, dict) and item.get("videoSrc"):
            out.append(item["videoSrc"])
    return out


def download(url, out_dir, progress, quality="best", proxy="", cookies_file=None):
    import core

    mid = mid_of(url)
    if not mid:
        raise RuntimeError("这不是单条微博的地址")
    if is_video_page(url):
        # 视频专页的 oid 不是博文 id，硬查只会得到「该微博不存在」这种误导
        raise RuntimeError("这是微博视频专页地址，换成 App 里「分享 → 复制链接」那条更稳")
    op = _opener(proxy)
    cookie = ""
    if cookies_file and os.path.exists(cookies_file):
        try:
            cookie = core._read_cookie_file(cookies_file) or ""
        except Exception:
            cookie = ""

    progress(stage="解析中…")
    try:
        data = _get_json("https://m.weibo.cn/statuses/show?id=%s" % mid, op, cookie)
    except RuntimeError as e:
        real = _b62_to_id(mid)
        if real == mid:
            raise
        data = _get_json("https://m.weibo.cn/statuses/show?id=%s" % real, op, cookie)

    text = re.sub(r"<[^>]+>", " ", data.get("text") or "")
    text = re.sub(r"\s+", " ", text).strip()
    author = ((data.get("user") or {}).get("screen_name")) or "微博"
    progress(title=text[:40] or "微博视频", author=author)

    page = data.get("page_info") or {}
    imgs = _images(data)
    urls = _pick_urls(page)
    # 明确是视频博文（type=video 或拿到了视频流）才下视频；
    # 否则有图就下图 —— 图文 / 实况图博文里那些 pics 才是用户要的
    is_video_post = (page.get("type") == "video") or bool(urls)
    if not is_video_post:
        urls = _live_photos(data)

    headers = {"User-Agent": UA_PC, "Referer": "https://weibo.com/"}

    if imgs and not is_video_post:
        folder = core.unique_path(os.path.join(
            out_dir, "%s - %s（图集）" % (core.safe_name(text[:30] or "微博"), core.safe_name(author))))
        files = core.download_images(imgs, folder, progress, headers, tag="图片")
        return folder, files

    if not urls:
        raise RuntimeError("这条微博里没有视频地址")

    out_path = core.unique_path(os.path.join(
        out_dir, "%s - %s.mp4" % (core.safe_name(text[:40] or "微博视频"),
                                  core.safe_name(author))))
    path, _ = core.stream_download(urls, out_path, progress, headers)
    return path

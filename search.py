# -*- coding: utf-8 -*-
"""全网搜索：视频素材（B站 / YouTube）。

结果统一成卡片格式（标题/作者/封面/时长/链接），下载不用这边管——
把链接丢回 /api/add 走现有下载管线就行。

B站搜索 2022 年后要过 WBI 签名 + 带 cookie，这里两样都备齐：
先访问一次首页拿 buvid，再按 nav 接口的 wbi_img 算 w_rid。
"""
import hashlib
import http.cookiejar
import json
import re
import threading
import time
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


# ---------- B站（WBI 签名） ----------
_MIXIN_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
]
_wbi = {"key": "", "ts": 0.0}
_boot = {"done": False}
_jar = http.cookiejar.CookieJar()
_opener = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(_jar),
    urllib.request.ProxyHandler({}),        # B站走直连，不吃系统代理
)


def _get(url, timeout=15):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Referer": "https://www.bilibili.com/",
    })
    with _opener.open(req, timeout=timeout) as r:
        return r.read()


def _bootstrap():
    """先访问一次 B站首页，把 buvid 这些 cookie 攒到手，否则搜索接口 -412。"""
    if _boot["done"]:
        return
    try:
        _get("https://www.bilibili.com/", timeout=10)
    except Exception:
        pass          # 拿不到也接着试，说不定接口不较真
    _boot["done"] = True


def _mixin_key():
    if _wbi["key"] and time.time() - _wbi["ts"] < 3600:
        return _wbi["key"]
    try:
        raw = _get("https://api.bilibili.com/x/web-interface/nav", timeout=10)
        d = json.loads(raw.decode("utf-8", "replace")).get("data") or {}
    except Exception:
        return ""
    img = ((d.get("wbi_img") or {}).get("img_url") or "").rsplit("/", 1)[-1].split(".")[0]
    sub = ((d.get("wbi_img") or {}).get("sub_url") or "").rsplit("/", 1)[-1].split(".")[0]
    if not img or not sub:
        return ""
    k = "".join((img + sub)[i] for i in _MIXIN_TAB)[:32]
    _wbi.update(key=k, ts=time.time())
    return k


def _signed_query(params):
    key = _mixin_key()
    clean = {k: re.sub(r"[!'()*]", "", str(v)) for k, v in params.items()}
    clean["wts"] = str(int(time.time()))
    # 必须用 quote（空格→%20）：跟 encodeURIComponent 一致，
    # 用 quote_plus（空格→+）会让 w_rid 对不上，B站静默返回空结果
    q = urllib.parse.urlencode(sorted(clean.items()), safe="!'()*",
                               quote_via=urllib.parse.quote)
    if key:
        q += "&w_rid=" + hashlib.md5((q + key).encode("utf-8")).hexdigest()
    return q


def _dur_seconds(text):
    m = re.match(r"^(?:(\d+):)?(\d+):(\d+)$", (text or "").strip())
    if not m:
        return 0
    return int(m.group(1) or 0) * 3600 + int(m.group(2)) * 60 + int(m.group(3))


def search_bili(kw, limit=10):
    """B站视频搜索。返回统一卡片列表，失败抛异常（调用方转成人话）。"""
    _bootstrap()
    q = _signed_query({"search_type": "video", "keyword": kw, "page": "1"})
    raw = _get("https://api.bilibili.com/x/web-interface/wbi/search/type?" + q)
    d = json.loads(raw.decode("utf-8", "replace"))
    if d.get("code") != 0:
        raise RuntimeError(d.get("message") or ("B站搜索失败 code=%s" % d.get("code")))
    out = []
    for s in (d.get("data") or {}).get("result") or []:
        bvid = s.get("bvid") or ""
        if not bvid:
            continue
        pic = s.get("pic") or ""
        if pic.startswith("//"):
            pic = "https:" + pic
        out.append({
            "platform": "bili", "id": bvid, "kind": "video",
            "title": re.sub(r"<[^>]+>", "", s.get("title") or ""),
            "author": s.get("author") or "",
            "cover": pic,
            "duration": _dur_seconds(s.get("duration")),
            "desc": re.sub(r"<[^>]+>", "", s.get("description") or "")[:120],
            "url": "https://www.bilibili.com/video/" + bvid,
        })
        if len(out) >= limit:
            break
    return out


def search_youtube(kw, limit=10, proxy=None):
    """YouTube 搜索（yt-dlp 自带 ytsearch）。网络要能到 Google。"""
    import yt_dlp
    opts = {"quiet": True, "no_warnings": True, "skip_download": True,
            "extract_flat": True, "noplaylist": True}
    if proxy:
        opts["proxy"] = proxy
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info("ytsearch%d:%s" % (max(1, limit), kw), download=False)
    out = []
    for e in (info or {}).get("entries") or []:
        vid = e.get("id") or ""
        if not vid:
            continue
        thumb = e.get("thumbnail") or ""
        if not thumb:
            ths = e.get("thumbnails") or []
            if ths:
                thumb = ths[-1].get("url") or ""
        out.append({
            "platform": "youtube", "id": vid, "kind": "video",
            "title": e.get("title") or "",
            "author": e.get("uploader") or e.get("channel") or "",
            "cover": thumb,
            "duration": int(e.get("duration") or 0),
            "desc": "",
            "url": e.get("url") or ("https://www.youtube.com/watch?v=" + vid),
        })
        if len(out) >= limit:
            break
    return out


_NAME = {"bili": "B站", "youtube": "YouTube"}


def search_video(kw, limit=10, platform="all", proxy=None):
    """B站 / YouTube 一起搜，结果交错排。返回 (items, errors)。"""
    kw = (kw or "").strip()
    if not kw:
        return [], []
    want = []
    if platform in ("all", "bili"):
        want.append("bili")
    if platform in ("all", "youtube"):
        want.append("youtube")
    if not want:
        return [], []
    half = max(5, limit // len(want))
    res, errs = {}, []

    def run(name):
        try:
            res[name] = (search_bili(kw, half) if name == "bili"
                         else search_youtube(kw, half, proxy))
        except Exception as e:
            res[name] = []
            errs.append("%s没搜成：%s" % (_NAME.get(name, name), str(e)[:100]))

    ths = [threading.Thread(target=run, args=(n,)) for n in want]
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    merged = []
    for i in range(max([len(res.get(n, [])) for n in want] or [0])):
        for n in want:
            if i < len(res.get(n, [])):
                merged.append(res[n][i])
    return merged[:limit], errs

# -*- coding: utf-8 -*-
"""B 站播放接口兜底：yt-dlp 那条路走不通时，直接问 B 站网页接口要文件。

为什么值得单独写一条：yt-dlp 走 DASH（视频轨 + 音频轨分开下再合并），链路长、
依赖 ffmpeg、还容易被 wbi 签名或风控卡住。B 站自己的 playurl 在 fnval=1 下
直接给**单文件 mp4/flv**，未登录也能拿到 720P/1080P，失败面小很多。

只处理普通投稿（BV / av 号）。番剧、影视正片交给 yt-dlp —— 那边有 pgc 分支，
这里再抄一遍纯属重复。
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
REFERER = "https://www.bilibili.com/"

# wbi 签名用的重排表（B 站前端硬编码的，官方文档外流传很久了）
_MIXIN_TAB = (
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
    61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
    36, 20, 34, 44, 52,
)


def _opener(proxy=""):
    if proxy:
        return urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    # 空 handler = 不走系统代理。B 站在国内，挂梯子反而容易触发风控
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _get_json(url, op, cookie="", referer=REFERER, timeout=15, allow=()):
    hdr = {"User-Agent": UA, "Referer": referer,
           "Accept": "application/json, text/plain, */*",
           "Accept-Language": "zh-CN,zh;q=0.9"}
    if cookie:
        hdr["Cookie"] = cookie
    req = urllib.request.Request(url, headers=hdr)
    raw = op.open(req, timeout=timeout).read().decode("utf-8", "ignore")
    d = json.loads(raw)
    code = d.get("code", 0)
    # -101 = 未登录。nav 接口照样给 wbi 密钥，不该当成错误
    if code not in (0, -404) and code not in allow:
        raise RuntimeError("B站接口返回 %s：%s" % (code, d.get("message") or "未知错误"))
    return d.get("data") or {}


# ---------------------------------------------------------------- 签名
def _mixin_key(img_url, sub_url):
    """从 nav 的两个图片地址里抠出文件名，按表重排取前 32 位。"""
    img = (img_url or "").rsplit("/", 1)[-1].split(".")[0]
    sub = (sub_url or "").rsplit("/", 1)[-1].split(".")[0]
    raw = img + sub
    if len(raw) < 64:
        return ""
    return "".join(raw[i] for i in _MIXIN_TAB)[:32]


def _wbi_keys(op, cookie=""):
    """拿 wbi 密钥。失败返回空串 —— 后面还有不带签名的一次尝试。"""
    try:
        d = _get_json("https://api.bilibili.com/x/web-interface/nav", op, cookie,
                      allow=(-101,))
        img = ((d.get("wbi_img") or {}).get("img_url")) or ""
        sub = ((d.get("wbi_img") or {}).get("sub_url")) or ""
        return _mixin_key(img, sub)
    except Exception:
        return ""


def _signed(params, mixin):
    """按 B 站规则签名：值里的 !'()* 先剔掉，再排序拼 query、md5(query + 密钥)。

    注意剔完的值要**留在参数里**发出去 —— 签名算的和发的不是同一份参数时，
    B 站一律回「签名错误」，而且很难看出来。
    """
    p = {k: re.sub(r"[!'()*]", "", str(v)) for k, v in params.items()}
    p["wts"] = int(time.time())
    query = urllib.parse.urlencode(sorted(p.items()))
    p["w_rid"] = hashlib.md5((query + mixin).encode("utf-8")).hexdigest()
    return urllib.parse.urlencode(sorted(p.items()))


# ---------------------------------------------------------------- 取 ID
def _ids(url):
    """返回 (bvid, aid, 分P序号)。"""
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    try:
        page = max(1, int((qs.get("p") or ["1"])[0]))
    except (ValueError, TypeError):
        page = 1
    m = re.search(r"(BV[0-9A-Za-z]{10})", url)
    bvid = m.group(1) if m else ""
    m2 = re.search(r"/av(\d+)", url, re.I) or re.search(r"[?&]aid=(\d+)", url)
    aid = m2.group(1) if m2 else ""
    if not bvid and not aid:
        raise RuntimeError("这不是 B 站单个视频的地址")
    return bvid, aid, page


def _cid_of(bvid, aid, page, op, cookie=""):
    q = ("bvid=%s" % bvid) if bvid else ("aid=%s" % aid)
    d = _get_json("https://api.bilibili.com/x/player/pagelist?%s" % q, op, cookie)
    if not isinstance(d, list) or not d:
        raise RuntimeError("B站没给分P列表")
    idx = min(page, len(d)) - 1
    cid = d[idx].get("cid")
    if not cid:
        raise RuntimeError("B站没给 cid")
    return cid


def _view(bvid, aid, op, cookie=""):
    q = ("bvid=%s" % bvid) if bvid else ("aid=%s" % aid)
    d = _get_json("https://api.bilibili.com/x/web-interface/view?%s" % q, op, cookie)
    return {
        "title": d.get("title") or "",
        "author": ((d.get("owner") or {}).get("name")) or "",
        "cover": d.get("pic") or "",
        "duration": int(d.get("duration") or 0),
        "pages": d.get("pages") or [],
    }


def _episode(url, op, cookie=""):
    """番剧/影视正片页（/bangumi/play/ep123）→ (bvid, aid, cid, ep_id)。

    这一类链接里没有 BV 号，得先问 pgc 接口要这一集的播放参数，
    否则连 playurl 都发不出去。整季页（ss 开头）在 core 那层已经拦掉了。
    """
    m = re.search(r"/bangumi/play/ep(\d+)", url or "")
    if not m:
        return None
    epid = m.group(1)
    d = _get_json("https://api.bilibili.com/pgc/view/web/season?ep_id=%s" % epid,
                  op, cookie)
    for ep in d.get("episodes") or []:
        if str(ep.get("id")) == epid:
            return (ep.get("bvid") or "", ep.get("aid") or "",
                    ep.get("cid") or "", epid)
    return None


# ---------------------------------------------------------------- 播放地址
_QUALITY_DESC = {"best": 80, "1080p": 80, "720p": 64, "480p": 32, "360p": 16}


def _playurl(bvid, aid, cid, qn, op, cookie="", ep_id=""):
    base = {"cid": cid, "qn": qn, "fnval": 1, "fnver": 0, "fourk": 1}
    if bvid:
        base["bvid"] = bvid
    else:
        base["avid"] = aid
    if ep_id:
        base["ep_id"] = ep_id          # 番剧必须带，否则不给地址
    mixin = _wbi_keys(op, cookie)
    tried = []
    if mixin:
        tried.append("https://api.bilibili.com/x/player/wbi/playurl?" +
                     _signed(base, mixin))
    # 不带签名的老接口：有些时候仍然给 720P，多一条路总没坏处
    tried.append("https://api.bilibili.com/x/player/playurl?" +
                 urllib.parse.urlencode(base))

    last = None
    for u in tried:
        try:
            d = _get_json(u, op, cookie)
        except Exception as e:
            last = e
            continue
        durl = d.get("durl") or []
        urls = [x.get("url") for x in durl if x.get("url")]
        if urls:
            return urls, int(d.get("quality") or qn)
        last = RuntimeError("B站没给播放地址（可能要登录或这是付费内容）")
    raise last or RuntimeError("B站没给播放地址")


# ---------------------------------------------------------------- 合并
def _ffmpeg():
    try:
        import paths
        return paths.ffmpeg_path()
    except Exception:
        return ""


def _merge(parts, out_path, work_dir):
    """多段 durl 拼成一个文件。ffmpeg 不在就退化成「按顺序二进制拼接」。"""
    ff = _ffmpeg()
    if ff and len(parts) > 1:
        lst = os.path.join(work_dir, "concat.txt")
        with open(lst, "w", encoding="utf-8") as f:
            for p in parts:
                f.write("file '%s'\n" % p.replace("\\", "/").replace("'", r"'\''"))
        cmd = [ff, "-y", "-hide_banner", "-loglevel", "error", "-nostdin",
               "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", out_path]
        try:
            subprocess.run(cmd, check=True, timeout=1800)
            if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                return out_path
        except Exception:
            pass
    # 兜底：二进制顺序拼接（flv 分段通常能这么拼）
    with open(out_path, "wb") as dst:
        for p in parts:
            with open(p, "rb") as src:
                shutil.copyfileobj(src, dst, 1024 * 1024)
    return out_path


# ---------------------------------------------------------------- 主入口
def download(url, out_dir, progress, quality="best", proxy="", cookies_file=None):
    import core

    op = _opener(proxy)
    cookie = ""
    if cookies_file and os.path.exists(cookies_file):
        try:
            cookie = core._read_cookie_file(cookies_file) or ""
        except Exception:
            cookie = ""

    progress(stage="解析中…")
    ep = _episode(url, op, cookie) if "/bangumi/play/" in url else None
    if ep:
        bvid, aid, cid, epid = ep
        if not cid:
            raise RuntimeError("B站没给这一集的播放参数（可能要会员）")
        page = 1
        info = _view(bvid, aid, op, cookie) if (bvid or aid) else {}
        title = info.get("title") or "B站视频"
    else:
        bvid, aid, page = _ids(url)
        epid = ""
        info = _view(bvid, aid, op, cookie)
        pages = info.get("pages") or []
        title = info.get("title") or "B站视频"
        if len(pages) > 1 and page <= len(pages):
            title = ("%s P%d %s" % (title, page, pages[page - 1].get("part") or "")).strip()
        cid = _cid_of(bvid, aid, page, op, cookie)

    progress(title=title, author=info.get("author") or "",
             thumb=info.get("cover") or "", duration=info.get("duration") or 0)

    qn = _QUALITY_DESC.get(str(quality or "best").lower(), 80)
    urls, got = _playurl(bvid, aid, cid, qn, op, cookie, epid)

    ext = ".flv" if ".flv" in (urls[0] or "").lower() else ".mp4"
    out_path = core.unique_path(os.path.join(
        out_dir, "%s - %s%s" % (core.safe_name(title), core.safe_name(info.get("author") or ""), ext)))
    headers = {"User-Agent": UA, "Referer": REFERER}

    if len(urls) == 1:
        path, _ = core.stream_download(urls, out_path, progress, headers)
        return path

    # 分段：先落到临时目录，再合
    work = out_path + ".parts"
    os.makedirs(work, exist_ok=True)
    parts = []
    try:
        total = len(urls)
        for i, u in enumerate(urls):
            seg = os.path.join(work, "seg%02d.flv" % i)
            progress(stage="下载分片 %d/%d" % (i + 1, total), percent=round(i * 100.0 / total, 1))
            core.resumable_download(u, seg, progress, headers=headers, label="下载中")
            parts.append(seg)
        progress(stage="正在合并…", percent=99, speed=0)
        return _merge(parts, out_path, work)
    finally:
        shutil.rmtree(work, ignore_errors=True)

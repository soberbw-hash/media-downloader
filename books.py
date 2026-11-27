# -*- coding: utf-8 -*-
"""电子书：把「书目页」翻译成真正能下的文件。

以前电子书搜索给的是 Open Library 的作品页、Internet Archive 的条目页，
下载时直接丢给 yt-dlp —— 它只认视频，文本条目一律解析失败。用户看到的
就是「三体点三次，三次都失败」，还常被盖上一句「触发了风控」。

这里补上真正的两跳：
  Open Library 作品 / 版本页 → editions.json 里的 ocaid → Internet Archive
  Internet Archive details 页 → /metadata/<id> → files 里挑 epub / pdf / txt

全部走公开接口，不用登录。借阅书（access-restricted）没有公开文件，
会明确说「只能在线借阅」，不再让用户白等。
"""
import json
import os
import re
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

BOOK_HOSTS = ("openlibrary.org", "archive.org", "gutenberg.org")
BOOK_EXTS = (".epub", ".pdf", ".mobi", ".azw3", ".fb2", ".djvu", ".txt")
# archive.org 上也有电影条目，这些后缀的直链要留给 yt-dlp，别当书解析
AV_EXTS = (".mp4", ".webm", ".ogv", ".ogg", ".mpeg", ".mpg", ".mkv", ".avi",
           ".mov", ".mp3", ".m4a", ".flac", ".wav", ".opus")

# Internet Archive 的 files 里还混着一堆中间产物，不能挑给用户
_JUNK = ("_chocr", "_hocr", "_djvu.xml", "_djvu.txt.gz", "_scandata",
         "scandata", "_files.xml", "meta_", "_meta", ".torrent", "_abbyy",
         "chocr.html.gz", "_page_numbers", "_jp2.zip",
         # LCP / ACSM 是带 DRM 的借阅文件，下下来也打不开，不能算「可下」
         "_lcp", "lcp.epub", ".acsm", "_encrypted")


def is_book(url):
    """这条链接是不是该走电子书这条路。"""
    try:
        h = (urllib.parse.urlparse(url or "").hostname or "").lower()
    except ValueError:
        return False
    path = (urllib.parse.urlparse(url or "").path or "").lower()
    if path.endswith(AV_EXTS):
        return False
    if any(h == d or h.endswith("." + d) for d in BOOK_HOSTS):
        return True
    return path.endswith(BOOK_EXTS)


# ---------------------------------------------------------------- 网络
def _opener(proxy):
    """proxy 为空 = 显式直连（和 core.resolve_proxy 的约定一致）。

    不用 build_opener() 的默认行为：那会读环境变量里的 http_proxy，
    用户关掉梯子后残留的代理设置会把请求打成 502，反而看不出真实原因。
    """
    if proxy:
        return urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _get(url, proxy="", timeout=25, binary=False):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/json,text/html,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    })
    try:
        r = _opener(proxy).open(req, timeout=timeout)
    except urllib.error.HTTPError:
        raise
    except Exception as exc:                      # 502 / 超时 / 梯子没开
        raise RuntimeError("连不上 %s（境外站点），先开梯子再试一次"
                           % _host(url)) from exc
    try:
        raw = r.read()
        return raw if binary else raw.decode("utf-8", "replace")
    finally:
        try:
            r.close()
        except Exception:
            pass


def _json(url, proxy="", timeout=25):
    text = _get(url, proxy, timeout)
    try:
        return json.loads(text)
    except ValueError:
        raise RuntimeError("%s 返回的不是书目数据，可能被网关拦了" % _host(url))


def _host(url):
    try:
        return (urllib.parse.urlparse(url).hostname or "").lower()
    except ValueError:
        return "电子书站"


# ---------------------------------------------------------------- 挑文件
def _ext_of(name):
    return os.path.splitext(name or "")[1].lower()


def pick_file(files):
    """从 Internet Archive 的 files 里挑最合适的一本。返回 file dict 或 None。

    优先级：EPUB（排版可重排、体积小）→ PDF → 纯文本。
    私有文件（private=true）不是给公众下的，直接跳过。
    """
    cands = []
    for f in files or []:
        name = f.get("name") or ""
        low = name.lower()
        if not name or str(f.get("private") or "").lower() == "true":
            continue
        if any(j in low for j in _JUNK):
            continue
        ext = _ext_of(name)
        if ext not in (".epub", ".pdf", ".txt", ".mobi", ".azw3", ".fb2", ".djvu"):
            continue
        fmt = (f.get("format") or "").lower()
        if ext == ".epub" or "epub" in fmt:
            rank = 0
        elif ext == ".pdf" or "pdf" in fmt:
            rank = 1
        elif ext == ".txt" or "djvutxt" in fmt:
            rank = 2
        elif ext in (".mobi", ".azw3", ".fb2"):
            rank = 3
        else:
            rank = 4
        cands.append((rank, int(f.get("size") or 0), name, f))
    if not cands:
        return None
    # 同档里挑体积大的：插图版 / 完整版通常更大，.txt 那种几百字节的是目录
    cands.sort(key=lambda x: (x[0], -x[1]))
    return cands[0][3]


def _archive_meta(ident, proxy="", timeout=25):
    d = _json("https://archive.org/metadata/" + urllib.parse.quote(ident), proxy, timeout)
    if not d or not (d.get("metadata") or d.get("files")):
        raise RuntimeError("Internet Archive 上没有这个条目（%s），链接可能失效了" % ident[:24])
    return d


def _archive_blocked(meta):
    """借阅书 / 下架书：条目在，但没有公众能下的文件。"""
    m = meta.get("metadata") or {}
    if meta.get("is_dark"):
        return "这本书在 Internet Archive 已下架"
    if str(m.get("access-restricted-item") or "").lower() == "true":
        return "这本书在 Internet Archive 只能在线借阅"
    cols = m.get("collection") or []
    if isinstance(cols, str):
        cols = [cols]
    if "inlibrary" in [str(c).lower() for c in cols]:
        return "这本书在 Internet Archive 只能在线借阅"
    return ""


# ---------------------------------------------------------------- 各站解析
def _archive_id(url):
    """从 details / download / stream 链接里取 Internet Archive 的条目号。"""
    p = urllib.parse.urlparse(url)
    segs = [x for x in (p.path or "").split("/") if x]
    if not segs:
        return "", []
    if segs[0] in ("details", "download", "stream") and len(segs) > 1:
        return segs[1], segs
    return segs[0], segs


def _from_archive(url, proxy="", timeout=25):
    """archive.org/details/<id> 或 /download/<id>/... → 具体文件。"""
    p = urllib.parse.urlparse(url)
    ident, segs = _archive_id(url)
    if not ident:
        return None
    meta = _archive_meta(ident, proxy, timeout)
    if p.path.startswith("/download/") and len(segs) > 2:
        # 已经是具体文件了
        name = "/".join(segs[2:])
        return {"url": "https://archive.org/download/%s/%s" % (
                    urllib.parse.quote(ident), urllib.parse.quote(name)),
                "name": name,
                "title": (meta.get("metadata") or {}).get("title") or "",
                "ext": _ext_of(name)}
    blocked = _archive_blocked(meta)
    if blocked:
        raise RuntimeError(blocked + "，下不了（没有公开文件）。换个版本或来源试试")
    f = pick_file(meta.get("files"))
    if not f:
        files = meta.get("files") or []
        # 电影条目也长这样：没有文本文件就别判死，让 yt-dlp 接着试
        if any((x.get("name") or "").lower().endswith(AV_EXTS) for x in files):
            raise RuntimeError("这个条目里没有电子书文件，只有音视频，换视频引擎再试")
        raise RuntimeError("这个条目里没有 epub / pdf 文件，下不了。换一个版本试试")
    name = f.get("name") or ""
    return {"url": "https://archive.org/download/%s/%s" % (
                urllib.parse.quote(ident), urllib.parse.quote(name)),
            "name": name,
            "title": (meta.get("metadata") or {}).get("title") or "",
            "ext": _ext_of(name)}


def _ocaid_from_editions(key, proxy=""):
    """作品页 → 任一版本在 Internet Archive 上的编号。"""
    d = _json("https://openlibrary.org%s/editions.json?limit=50" % key, proxy)
    for e in d.get("entries") or []:
        ocaid = str(e.get("ocaid") or "").strip()
        if ocaid:
            return ocaid
    return ""


def _from_openlibrary(url, proxy=""):
    """openlibrary.org/works/OL…W 或 /books/OL…M → archive 文件。"""
    p = urllib.parse.urlparse(url)
    segs = [s for s in (p.path or "").split("/") if s]
    if len(segs) < 2:
        return None
    kind, key = segs[0], segs[1]
    ocaid = ""
    if kind == "books":
        d = _json("https://openlibrary.org/books/%s.json" % key, proxy)
        ocaid = str(d.get("ocaid") or "").strip()
    if not ocaid and kind in ("works", "books"):
        ocaid = _ocaid_from_editions("/" + kind + "/" + key, proxy)
    if not ocaid:
        raise RuntimeError("Open Library 上这本书只有书目信息，没有公开的电子书文件，"
                           "下不了。换一个版本或来源试试")
    return _from_archive("https://archive.org/details/" + ocaid, proxy)


def _from_gutenberg(url, proxy=""):
    """古登堡：页面里找 .epub 链接（优先无图版）。"""
    html = _get(url, proxy)
    links = re.findall(r'href="([^"]+\.epub[^"]*)"', html, re.I)
    if not links:
        links = re.findall(r'href="([^"]+\.(?:pdf|txt))"', html, re.I)
    if not links:
        raise RuntimeError("古登堡这个页面没有 epub / pdf 下载链接，下不了")
    links.sort(key=lambda x: 0 if "noimages" in x.lower() else 1)
    href = links[0]
    if href.startswith("//"):
        href = "https:" + href
    elif href.startswith("/"):
        href = "https://www.gutenberg.org" + href
    # pg1342.epub.noimages 这种「后缀 + 修饰」的链接，真格式要往前找
    m = re.search(r"\.(epub|pdf|txt|mobi|azw3|fb2)(?=[^/]*$)", href, re.I)
    ext = ("." + m.group(1).lower()) if m else _ext_of(href)
    return {"url": href, "name": os.path.basename(urllib.parse.urlparse(href).path),
            "title": "", "ext": ext}


def resolve(url, proxy=""):
    """把用户贴的链接变成 {url, name, title, ext}。解析不了抛 RuntimeError。"""
    h = (_host(url) or "")
    if h.endswith("archive.org"):
        return _from_archive(url, proxy)
    if h.endswith("openlibrary.org"):
        return _from_openlibrary(url, proxy)
    if h.endswith("gutenberg.org"):
        return _from_gutenberg(url, proxy)
    path = urllib.parse.urlparse(url).path or ""
    if _ext_of(path) in BOOK_EXTS:
        return {"url": url, "name": os.path.basename(path), "title": "",
                "ext": _ext_of(path)}
    return None


# ---------------------------------------------------------------- 下载
def _filename(title, name, ext):
    import core
    base = core.safe_name(title or os.path.splitext(name or "book")[0], 60)
    ext = ext or _ext_of(name) or ".epub"
    if not ext.startswith("."):
        ext = "." + ext
    return base + ext


def download(url, out_dir, progress, proxy="", cookies="", title_hint=""):
    import core
    progress(stage="正在找这本书的文件…", percent=0, speed=0)
    info = resolve(url, proxy)
    if not info:
        raise RuntimeError("这个链接不是电子书页面，下不了")
    name = info.get("name") or ""
    title = title_hint or info.get("title") or os.path.splitext(name)[0]
    out = core.unique_path(os.path.join(out_dir, _filename(title, name, info.get("ext"))))
    progress(title=title, stage="正在下载 " + (name or "电子书"))

    headers = {"User-Agent": UA, "Accept": "*/*"}
    if cookies:
        headers["Cookie"] = cookies

    def open_fn(u, h, timeout):
        r = _opener(proxy).open(urllib.request.Request(u, headers=h), timeout=timeout)
        mime = (r.headers.get("Content-Type") or "").split(";")[0].lower()
        ext = _ext_of(out)
        if mime in ("text/html", "application/xhtml+xml") and ext not in (".html", ".htm"):
            r.close()
            raise RuntimeError("下载到的是网页而不是书，链接可能失效了")
        return r

    return core.resumable_download(info["url"], out, progress, open_fn, headers,
                                   label="下载中")


def availability(url, proxy="", timeout=6):
    """搜索卡上的「能不能直下」标注。探测失败就不标，绝不拖慢结果太久。"""
    if not (_host(url) or "").endswith("archive.org"):
        return ""
    ident, _ = _archive_id(url)
    if not ident:
        return ""
    try:
        meta = _archive_meta(ident, proxy, timeout)
    except Exception:
        return ""
    blocked = _archive_blocked(meta)
    if blocked:
        return "仅在线借阅" if "借阅" in blocked else "已下架"
    return "可直下" if pick_file(meta.get("files")) else "无公开文件"

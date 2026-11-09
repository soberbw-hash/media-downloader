# -*- coding: utf-8 -*-
"""快手 / 小红书解析

这两个平台网页端都不给未登录访客视频地址，思路是：
先用游客 cookie 换个身份，再从服务端渲染的页面数据里取播放源。
快手优先走 live_api，失败退回移动端分享页。
"""
import json
import os
import re
import urllib.parse

from core import (UA_M, UA_PC, download_images, http_get, safe_name,
                  stream_download, unique_path)


def _cookie_header(url, ua=UA_PC):
    """访问一次页面，把 Set-Cookie 拼成请求头，充当游客身份"""
    jar = {}
    try:
        r = http_get(url, {"User-Agent": ua,
                           "Accept-Language": "zh-CN,zh;q=0.9"}, timeout=20)
        for c in r.headers.get_all("Set-Cookie") or []:
            k, _, v = c.split(";")[0].partition("=")
            if k and v:
                jar[k.strip()] = v.strip()
        r.read(0)
    except Exception:
        pass
    return "; ".join("%s=%s" % (k, v) for k, v in jar.items())


def _read_cookie_file(path):
    """把 Netscape 格式的 cookies 文件读成 Cookie 请求头"""
    parts = []
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if line.startswith("#") or not line.strip():
                    continue
                cols = line.rstrip("\n").split("\t")
                if len(cols) >= 7:
                    parts.append("%s=%s" % (cols[5], cols[6]))
    except Exception:
        pass
    return "; ".join(parts)


def _json_object_at(text, idx):
    """从 idx 开始按花括号配对，截出一个完整 JSON 对象"""
    depth = 0
    in_str = False
    esc = False
    for i in range(idx, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[idx:i + 1]
    return ""


# ==================== 快手 ====================
KS_ID_RE = re.compile(r"(?:short-video|fw/photo|/photo)/([A-Za-z0-9_-]{8,})")


def kuaishou_resolve(url):
    """短链 -> (photo_id, author_id)"""
    data = {"photo_id": "", "author_id": "", "final": url}

    m = KS_ID_RE.search(url)
    if m:
        data["photo_id"] = m.group(1)
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    for key in ("authorId", "author_id", "principalId"):
        if qs.get(key):
            data["author_id"] = qs[key][0]
            break

    if data["photo_id"] and data["author_id"]:
        return data

    # 短链要跟一次跳转
    try:
        r = http_get(url, {"User-Agent": UA_PC,
                           "Accept-Language": "zh-CN,zh;q=0.9"}, timeout=25)
        final = r.geturl()
        body = ""
        try:
            body = r.read().decode("utf-8", "ignore")
        except Exception:
            pass
        data["final"] = final
        m = KS_ID_RE.search(final)
        if m and not data["photo_id"]:
            data["photo_id"] = m.group(1)
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(final).query)
        for key in ("authorId", "author_id", "principalId"):
            if qs.get(key) and not data["author_id"]:
                data["author_id"] = qs[key][0]
        if not data["photo_id"] and body:
            m = KS_ID_RE.search(body)
            if m:
                data["photo_id"] = m.group(1)
    except Exception:
        pass
    return data


def _ks_images(obj):
    """快手图集地址。atlas 可能挂在作品本身，也可能在 ext_params 下面。"""
    out = []
    atlas = ((((obj.get("ext_params") or {}).get("atlas") or {}).get("list"))
             or ((obj.get("atlas") or {}).get("list"))
             or [])
    for im in atlas or []:
        if not isinstance(im, dict):
            continue
        u = im.get("url") or im.get("cdnUrl") or ""
        if u:
            out.append(u)
    return out


def _ks_from_live_api(photo_id, author_id, cookie):
    """live_api 接口，返回 (title, author, duration, cover, urls)"""
    if not author_id:
        return None
    api = ("https://live.kuaishou.com/live_api/profile/feedbyid"
           "?photoId=%s&principalId=%s" % (photo_id, author_id))
    hdr = {"User-Agent": UA_PC, "Referer": "https://www.kuaishou.com/",
           "Accept": "application/json, text/plain, */*"}
    if cookie:
        hdr["Cookie"] = cookie
    try:
        raw = http_get(api, hdr, timeout=20).read().decode("utf-8", "ignore")
        d = json.loads(raw)
    except Exception:
        return None
    work = (d.get("data") or {}).get("currentWork") or {}
    if not work:
        return None
    urls = []
    for k in ("mp4Url", "photoUrl"):
        if work.get(k):
            urls.append(work[k])
    for item in (work.get("manifest") or {}).get("adaptationSet") or []:
        for rep in item.get("representation") or []:
            if rep.get("url"):
                urls.append(rep["url"])
    imgs = _ks_images(work)
    if not urls and not imgs:
        return None
    cover = ""
    for c in work.get("coverUrls") or []:
        cover = c.get("url") or ""
        if cover:
            break
    return {
        "title": work.get("caption") or "",
        "author": (work.get("userName") or ""),
        "duration": int((work.get("duration") or 0) / 1000),
        "cover": cover,
        "urls": urls,
        "images": imgs,
    }


def _ks_from_share_page(photo_id, cookie):
    """移动端分享页 window.INIT_STATE 兜底"""
    url = "https://v.m.chenzhongtech.com/fw/photo/%s" % photo_id
    hdr = {"User-Agent": UA_M, "Referer": "https://www.kuaishou.com/"}
    if cookie:
        hdr["Cookie"] = cookie
    html = http_get(url, hdr, timeout=25).read().decode("utf-8", "ignore")
    idx = html.find('"photo":{')
    if idx < 0:
        idx = html.find('"photo": {')
    if idx < 0:
        raise RuntimeError("分享页里没有作品数据，链接可能已失效或需要登录")
    start = html.find("{", idx)
    blob = _json_object_at(html, start)
    if not blob:
        raise RuntimeError("分享页数据结构异常")
    photo = json.loads(blob)

    urls = []
    if photo.get("mp4Url"):
        urls.append(photo["mp4Url"])
    for item in photo.get("mainMvUrls") or []:
        if item.get("url"):
            urls.append(item["url"])
    imgs = _ks_images(photo)
    if not urls:
        manifest = photo.get("manifest") or {}
        if isinstance(manifest, str):
            try:
                manifest = json.loads(manifest)
            except Exception:
                manifest = {}
        for aset in manifest.get("adaptationSet") or []:
            for rep in aset.get("representation") or []:
                if rep.get("url"):
                    urls.append(rep["url"])
    if not urls and not imgs:
        raise RuntimeError("没找到可下载的视频地址")

    cover = ""
    for c in photo.get("coverUrls") or []:
        cover = c.get("url") or ""
        if cover:
            break
    return {
        "title": photo.get("caption") or "",
        "author": photo.get("userName") or "",
        "duration": int((photo.get("duration") or 0) / 1000),
        "cover": cover,
        "urls": urls,
        "images": imgs,
    }


def kuaishou_download(url, out_dir, progress, quality="best", cookies_file=None):
    ids = kuaishou_resolve(url)
    pid = ids.get("photo_id")
    if not pid:
        raise RuntimeError("没解析出快手作品 ID，请用 App 里的分享链接")

    cookie = _cookie_header("https://www.kuaishou.com/")
    if cookies_file and os.path.exists(cookies_file):
        ck = _read_cookie_file(cookies_file)
        if ck:
            cookie = (ck + "; " + cookie) if cookie else ck
    progress(stage="解析中…")

    info = _ks_from_live_api(pid, ids.get("author_id", ""), cookie)
    if not info:
        info = _ks_from_share_page(pid, cookie)

    title = info["title"] or "快手作品"
    author = info["author"] or "未知作者"
    progress(title=title, author=author)

    imgs = info.get("images") or []
    if imgs and not info.get("urls"):
        folder = unique_path(os.path.join(
            out_dir, "%s - %s（图集）" % (safe_name(title), safe_name(author))))
        files = download_images(imgs, folder, progress,
                                {"User-Agent": UA_PC,
                                 "Referer": "https://www.kuaishou.com/"})
        return folder, files

    ext = ".mp4"
    out_path = unique_path(os.path.join(
        out_dir, "%s - %s%s" % (safe_name(title), safe_name(author), ext)))
    path, _ = stream_download(info["urls"], out_path, progress,
                              {"User-Agent": UA_PC, "Referer": "https://www.kuaishou.com/"})
    return path


# ==================== 小红书 ====================
XHS_NOTE_RE = re.compile(r"/(?:explore|discovery/item|item)/([0-9a-f]{20,32})", re.I)


def xhs_resolve(url):
    """返回 (note_id, xsec_token, 最终地址)"""
    final = url
    if "xhslink.com" in url or "xiaohongshu.com" not in url:
        try:
            r = http_get(url, {"User-Agent": UA_M}, timeout=25)
            final = r.geturl()
            r.read(0)
        except Exception:
            pass

    m = XHS_NOTE_RE.search(final) or XHS_NOTE_RE.search(url)
    note_id = m.group(1) if m else ""
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(final).query)
    token = (qs.get("xsec_token") or [""])[0]
    if not token:
        qs2 = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        token = (qs2.get("xsec_token") or [""])[0]
    return note_id, token, final


def _sanitize_state_json(blob):
    """__INITIAL_STATE__ 里混着 JS 语法，先洗成合法 JSON 再 json.loads。

    实测会遇到：new Map([]) / new Set([])（空集合直接替掉就够，
    真带数据的 Map 页面数据里用不到）；undefined 也不是 JSON。
    """
    blob = re.sub(r"new Map\(\s*\[\s*\]\s*\)", "{}", blob)
    blob = re.sub(r"new Set\(\s*\[\s*\]\s*\)", "[]", blob)
    blob = blob.replace("undefined", "null")
    return blob


def _xhs_note_from_html(html, note_id):
    """从 explore 页 HTML 里抠出笔记对象；失败时抛带出路的 RuntimeError。"""
    idx = html.find("__INITIAL_STATE__")
    if idx < 0:
        raise RuntimeError("小红书没给笔记数据，一般是没登录。去「设置 → 账号」登录后重试")
    start = html.find("{", idx)
    blob = _json_object_at(html, start)
    if not blob:
        raise RuntimeError("小红书页面结构变了，解析不了。重试一次，还不行就反馈")
    try:
        state = json.loads(_sanitize_state_json(blob))
    except Exception:
        # 清洗后还挂，多半是页面改版；别再往「要登录」上带
        raise RuntimeError("小红书页面数据读不出来。重试一次，还不行就反馈")

    detail = (state.get("note") or {}).get("noteDetailMap") or {}
    note = (detail.get(note_id) or {}).get("note") or {}
    if not note:
        for v in detail.values():
            note = (v or {}).get("note") or {}
            if note:
                break
    if not note:
        raise RuntimeError("没拿到笔记内容：可能已删除、设为私密，或要登录")
    return note


def xhs_images(note):
    """小红书图文笔记的图片地址，尽量取原图（去掉 !nd_dft_xxx 缩略后缀）。"""
    out = []
    for im in note.get("imageList") or []:
        if not isinstance(im, dict):
            continue
        u = (im.get("urlDefault") or im.get("urlPre") or im.get("url") or "").strip()
        if not u:
            infos = [x for x in (im.get("infoList") or []) if isinstance(x, dict)]
            infos.sort(key=lambda x: 0 if x.get("imageScene") in ("WB_DFT", "CRD_WB_DFT") else 1)
            for info in infos:
                if info.get("url"):
                    u = info["url"].strip()
                    break
        if u:
            out.append(u)
    return out


def _xhs_video_urls(note, quality="best"):
    """从 video.media / video.mediaV2 里收播放地址，按质量排序。

    编码键历史上叫 h264/h265/av1，现在改叫 EF4/EF5/EF6——别写死，
    stream 下面每一路都扫，新名字也接得住。mediaV2 是 JSON 字符串、蛇形键。
    """
    video = note.get("video") or {}
    picked = []   # (短边, 宽*高, 码率, 主地址, [备用])

    def take(group, url_key="masterUrl", bak_key="backupUrls"):
        for s in group or []:
            if not isinstance(s, dict):
                continue
            u = (s.get(url_key) or "").strip()
            if not u:
                continue
            w, h = s.get("width") or 0, s.get("height") or 0
            picked.append((min(w, h), w * h, s.get("videoBitrate") or 0,
                           u, s.get(bak_key) or []))

    def take_stream(container, url_key="masterUrl", bak_key="backupUrls"):
        for group in (container or {}).values():
            take(group, url_key, bak_key)

    take_stream((video.get("media") or {}).get("stream"))
    take((video.get("media") or {}).get("video"))

    mv2 = video.get("mediaV2")
    if isinstance(mv2, str):
        try:
            mv2 = json.loads(mv2)
        except Exception:
            mv2 = {}
    if isinstance(mv2, dict):
        # mediaV2 是蛇形键：master_url / backup_urls
        take_stream(mv2.get("stream"), "master_url", "backup_urls")
        take(mv2.get("video"), "master_url", "backup_urls")

    key = (video.get("consumer") or {}).get("originVideoKey")
    if key:
        picked.append((0, 0, 0, "https://sns-video-bd.xhscdn.com/%s" % key, []))

    want720 = (quality or "").strip().lower() == "720"
    if want720:
        # 720p 档（短边 ≤ 720）优先；一档都没有就退回全量，总比下不到强
        floor = [p for p in picked if p[0] and p[0] <= 720]
        if floor:
            picked = floor
    picked.sort(key=lambda x: (x[1], x[2]), reverse=True)

    urls = []
    for _, _, _, u, baks in picked:
        urls.append(u)
        urls.extend([b for b in baks if b])
    return urls


def xhs_download(url, out_dir, progress, quality="best", cookies_file=None):
    note_id, token, final = xhs_resolve(url)
    if not note_id:
        raise RuntimeError("没解析出小红书笔记 ID，请用 App 里的分享链接")

    qs = "xsec_token=%s&xsec_source=pc_share" % token if token else ""
    page = "https://www.xiaohongshu.com/explore/%s%s" % (
        note_id, ("?" + qs) if qs else "")

    cookie = _cookie_header("https://www.xiaohongshu.com/", UA_PC)
    if cookies_file and os.path.exists(cookies_file):
        ck = _read_cookie_file(cookies_file)
        if ck:
            cookie = (ck + "; " + cookie) if cookie else ck

    progress(stage="解析中…")
    hdr = {"User-Agent": UA_PC, "Referer": "https://www.xiaohongshu.com/",
           "Accept-Language": "zh-CN,zh;q=0.9"}
    if cookie:
        hdr["Cookie"] = cookie
    html = http_get(page, hdr, timeout=25).read().decode("utf-8", "ignore")
    note = _xhs_note_from_html(html, note_id)

    title = note.get("title") or (note.get("desc") or "")[:40] or "小红书笔记"
    author = ((note.get("user") or {}).get("nickname")) or "未知作者"
    progress(title=title, author=author)

    urls = _xhs_video_urls(note, quality)

    if not urls:
        imgs = xhs_images(note)
        if imgs:
            folder = unique_path(os.path.join(
                out_dir, "%s - %s（图文）" % (safe_name(title), safe_name(author))))
            files = download_images(imgs, folder, progress,
                                    {"User-Agent": UA_PC,
                                     "Referer": "https://www.xiaohongshu.com/"})
            return folder, files
        if (note.get("type") or "") == "video":
            raise RuntimeError("视频地址没解析出来，重试一次；还不行就反馈")
        raise RuntimeError("这条笔记里既没有视频也没有图片")

    out_path = unique_path(os.path.join(
        out_dir, "%s - %s.mp4" % (safe_name(title), safe_name(author))))
    path, _ = stream_download(urls, out_path, progress,
                              {"User-Agent": UA_PC,
                               "Referer": "https://www.xiaohongshu.com/"})
    return path

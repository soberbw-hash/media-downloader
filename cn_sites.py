# -*- coding: utf-8 -*-
"""快手 / 小红书解析

快手：先用游客 cookie 换个身份，优先走 live_api，失败退回移动端分享页。
小红书：靠「App 分享链自带的 xsec_token」+ 分享页服务端渲染（SSR）拿播放源，
不要求登录；同一个 token 只在它被签发的形态下有效，所以别把 app_share 的
token 硬塞进 pc_share 的地址里（实测会被踢到登录页）。
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


def _xhs_url_parts(u):
    """从地址里抠 (note_id, token, source, 还原后的地址)。

    token 有两个来源：分享链自身的 query，和 /login?redirectPath=… 里塞的原始地址。
    后者是关键 —— 桌面 UA 打开 App 分享链时小红书不 302 到笔记页，而是踢到登录页，
    真正的 xsec_token 只在 redirectPath 里；以前只读 final 的 query，token 就丢了，
    于是明明链接带着凭证，我们还是当成「没登录」。
    """
    for _ in range(4):                       # redirectPath 可能被二次编码
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(u).query)
        rp = (qs.get("redirectPath") or [""])[0]
        if not rp:
            break
        u = urllib.parse.unquote(rp) if "%" in rp else rp
    # 这里不能用 parse_qs 取参数：它会把 "+" 当成空格，而 token 里真的可能有 "+"，
    # 拿回来变成空格签名就废了。直接抠原文再走一次 unquote。
    m_tok = re.search(r"[?&]xsec_token=([^&#]+)", u)
    m_src = re.search(r"[?&]xsec_source=([^&#]+)", u)
    m = XHS_NOTE_RE.search(u)
    return {
        "note_id": m.group(1) if m else "",
        "token": urllib.parse.unquote(m_tok.group(1)) if m_tok else "",
        "source": urllib.parse.unquote(m_src.group(1)) if m_src else "",
        "url": u,
    }


def xhs_resolve(url):
    """返回 (note_id, xsec_token, 最终地址)

    最终地址尽量是「带 token 的笔记页」：App 分享链在手机 UA 下常常直接 302 过去；
    桌面 UA 会落到 /login?redirectPath=…，这时把 redirectPath 拆开还原。
    """
    final = url
    short = ("xhslink.com" in url) or ("xhslink.cn" in url)
    if short or "xiaohongshu.com" not in url:
        for ua in (UA_M, UA_PC):
            try:
                r = http_get(url, {"User-Agent": ua}, timeout=25)
                got = r.geturl()
                r.read(0)
            except Exception:
                continue
            if "xiaohongshu.com" in (got or ""):
                final = got
                if "/login" not in got:      # 手机 UA 拿到了真笔记页就够
                    break

    # 失效/被删的短链会跳到 /404/ 中间页，这时 URL 里根本没有笔记 ID。
    # 以前这里返回空 ID 不报错，走到后面才含糊地说「没拿到笔记内容」，
    # 用户不知道是链接废了还是自己登录过期 —— 在这里就说清楚。
    if short and "/404" in final:
        raise RuntimeError("这条小红书分享链接失效了（笔记被删或改私密）。让对方重新分享一条，再贴进来")

    info = _xhs_url_parts(final)
    if not info["note_id"] or not info["token"]:
        alt = _xhs_url_parts(url)            # 落点没线索就回头看原始链接
        info = {k: (info[k] or alt[k]) for k in info}
    note_id, token = info["note_id"], info["token"]
    if not note_id and short:
        # 短链能打开但没跳到笔记页：多半也是失效，或者复制时带了多余文字
        raise RuntimeError("这条小红书短链没指向具体笔记，可能失效了。从 App 里重新点「分享 → 复制链接」，只贴链接本身再试")
    return note_id, token, (info["url"] if note_id else final)



def _sanitize_state_json(blob):
    """__INITIAL_STATE__ 里混着 JS 语法，先洗成合法 JSON 再 json.loads。

    实测会遇到：new Map([]) / new Set([])（空集合直接替掉就够，
    真带数据的 Map 页面数据里用不到）；undefined 也不是 JSON。
    """
    blob = re.sub(r"new Map\(\s*\[\s*\]\s*\)", "{}", blob)
    blob = re.sub(r"new Set\(\s*\[\s*\]\s*\)", "[]", blob)
    blob = blob.replace("undefined", "null")
    return blob


def _xhs_risk(html):
    """小红书的风控/验证页长什么样。

    300012 是「当前网络存在风险」——和登录无关，换网络或过一会儿才有用；
    captcha 页是滑块验证。这两种都别再让用户去登录，登了也白登。
    """
    if not html:
        return ""
    if "300012" in html or "当前网络存在风险" in html or "安全限制" in html:
        return "risk"
    if "/website-login/captcha" in html or ("安全验证" in html and "拼图" in html):
        return "captcha"
    if ("/login?redirectPath" in html) or ("/website-login/error" in html):
        return "login"
    return ""


def _xhs_state(html):
    """把 __INITIAL_STATE__ 解成 dict；截断了就一段段往前缩，尽量榨出能用的部分。

    小红书 SSR 里的 state 经常被截在半截字符串上（开源工具里有同样的记录），
    严格配对会直接失败。这里退而求其次：从后往前找 `}`，试到能 json.loads 为止。
    """
    idx = html.find("__INITIAL_STATE__")
    if idx < 0:
        return None
    start = html.find("{", idx)
    if start < 0:
        return None
    blob = _json_object_at(html, start)
    candidates = [blob] if blob else []
    if not blob:
        # 截断：取到 </script> 之前，然后逐步往回收
        tail = html.find("</script>", start)
        raw = html[start:tail if tail > start else len(html)]
        cuts = [m.start() for m in re.finditer(r"\}", raw)]
        for cut in reversed(cuts[-40:]):
            candidates.append(raw[:cut + 1])
    for cand in candidates:
        try:
            return json.loads(_sanitize_state_json(cand))
        except Exception:
            continue
    return None


def _xhs_note_from_regex(html):
    """state 没了 / 被截断 / 结构改版时的兜底：直接从 HTML 里捞。

    小红书把播放地址写成 "masterUrl":"https://sns-video-….mp4?…"，
    有的只给 originVideoKey（拼 sns-video-bd 前缀就能下）。
    这就是「在播放器里找到 MP4 直链」那条路子的静态版。
    """
    if not html:
        return None
    vids = re.findall(r'"(?:masterUrl|master_url)"\s*:\s*"(https?://[^"]+)"', html)
    keys = re.findall(r'"originVideoKey"\s*:\s*"([^"]+)"', html)
    imgs = re.findall(r'"urlDefault"\s*:\s*"(https?://[^"]+)"', html)
    if not vids and not keys and not imgs:
        return None
    og = re.search(r'<meta[^>]+property="og:title"[^>]+content="([^"]*)"', html)
    title = (og.group(1).strip() if og else "") or (
        (re.search(r"<title>(.*?)</title>", html, re.S) or [None, ""])[1]).strip()
    note = {"title": title, "desc": "", "user": {}}
    if vids or keys:
        note["type"] = "video"
        note["video"] = {"media": {"stream": {"EF4": [{"masterUrl": u} for u in vids]}}}
        if keys:
            note["video"]["consumer"] = {"originVideoKey": keys[0]}
    if imgs:
        note["imageList"] = [{"urlDefault": u} for u in dict.fromkeys(imgs)]
    return note


def _xhs_note_paths(state):
    """把 state 里可能放笔记的几个位置都试一遍。

    两套页面结构并存：
      · 桌面 explore 页   → note.noteDetailMap[<id>].note
      · 手机分享页（App 分享链落到的那种）→ noteData.data.noteData
    后者能在不登录的情况下拿到完整正文和图片/视频，是现在的正路；
    以前只认前者，所以手机分享页明明有内容，我们却报「登录过期」。
    """
    out = []
    detail = ((state.get("note") or {}).get("noteDetailMap") or {})
    for v in detail.values():
        n = (v or {}).get("note") or {}
        if n:
            out.append(n)
    share = (((state.get("noteData") or {}).get("data") or {}).get("noteData") or {})
    if isinstance(share, dict) and share:
        out.append(share)
    return out


def _xhs_note_from_html(html, note_id):
    """从分享页 / explore 页 HTML 里抠出笔记对象；失败时抛带出路的 RuntimeError。"""
    risk = _xhs_risk(html)
    if risk == "risk":
        raise RuntimeError("小红书把这次访问判成风险（错误码 300012），跟登录无关。"
                           "先关掉代理/换网络，过几分钟再试")
    if risk == "captcha":
        raise RuntimeError("小红书弹了滑块验证，这次没能过。过几分钟再试，或换个网络")

    state = _xhs_state(html)
    if state is None:
        if html.find("__INITIAL_STATE__") < 0:
            # 页面里根本没有 state：说清楚多半是没登录，给「设置 → 账号」出路
            note = _xhs_note_from_regex(html)
            if note:
                return note
            raise RuntimeError("小红书没给笔记数据，一般是没登录。去「设置 → 账号」登录后重试")
        # state 在但读不出来（截断/改版）：先试正则兜底，再报「重试/反馈」
        note = _xhs_note_from_regex(html)
        if note:
            return note
        raise RuntimeError("小红书页面数据读不出来。重试一次，还不行就反馈")

    notes = _xhs_note_paths(state)
    note = next((n for n in notes if note_id and n.get("noteId") == note_id), None) \
        or (notes[0] if notes else {})
    if note:
        # 手机分享页的图在另一个包里（带 1080 尺寸的那版），顺手合进来
        share = state.get("noteData") or {}
        pre = (share.get("normalNotePreloadData") or state.get("normalNotePreloadData") or {})
        imgs = pre.get("imagesList") if isinstance(pre, dict) else None
        if isinstance(imgs, list) and imgs:
            note = dict(note)
            note["_preloadImages"] = [p for p in imgs if isinstance(p, dict)]
        return note
    # SSR 里没有笔记，但播放地址/图片可能还在别处（老分享页）
    note = _xhs_note_from_regex(html)
    if note:
        return note
    # 页面能打开、state 也能解析，但就是没有笔记数据，且登录态是假的 ——
    # 这是登录过期，不是链接失效。实测带/不带 cookie 返回完全一样即此情况。
    logged = (state.get("user") or {}).get("loggedIn")
    if logged is False:
        raise RuntimeError("小红书登录过期了，去「设置 → 账号」重新登录一次，再贴链接就能下")
    raise RuntimeError("没拿到笔记内容：可能已删除、设为私密，或要登录")



def xhs_images(note):
    """小红书笔记的图片地址，尽量取高清那版。

    新版分享页给的是「fileId + !h5_1080jpg」这种带处理后缀的地址，
    实测把后缀去掉会 403（要签名），所以有 h5_/nd_dft 后缀就原样用；
    另有 urlDefault（老页面）和 preload 里的 urlSizeLarge 两路兜底。
    """
    out = []

    def push(u):
        u = (u or "").strip()
        if u and u not in out:
            out.append(u)

    for im in note.get("imageList") or []:
        if not isinstance(im, dict):
            continue
        u = (im.get("urlDefault") or im.get("url") or im.get("urlPre") or "").strip()
        if not u:
            infos = [x for x in (im.get("infoList") or []) if isinstance(x, dict)]
            infos.sort(key=lambda x: 0 if x.get("imageScene") in ("WB_DFT", "CRD_WB_DFT") else 1)
            for info in infos:
                if info.get("url"):
                    u = info["url"].strip()
                    break
        push(u)
    # preload 里的图和 imageList 是同一批（只是不同域名/参数），
    # 有 imageList 就别再叠一份，否则用户会拿到一堆重复图
    if not out:
        for p in note.get("_preloadImages") or []:
            push(p.get("urlSizeLarge") or p.get("url"))
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


def _xhs_candidates(note_id, token, final):
    """按「最可能拿到内容」的顺序排队试。

    第一条永远是分享链还原出来的原地址（token 跟它被签发的形态配套）；
    后面才是拼出来的 discovery/explore —— 只当原地址被拦时的备胎。
    """
    out = []
    if final and note_id and note_id in final and "xiaohongshu.com" in final:
        out.append((final, UA_M))
    if token:
        tok = urllib.parse.quote(token, safe="")
        src = _xhs_url_parts(final)["source"] or "app_share"
        out.append(("https://www.xiaohongshu.com/discovery/item/%s?xsec_token=%s&xsec_source=%s"
                    % (note_id, tok, src), UA_M))
        out.append(("https://www.xiaohongshu.com/explore/%s?xsec_token=%s&xsec_source=pc_feed"
                    % (note_id, tok), UA_PC))
    out.append(("https://www.xiaohongshu.com/explore/%s" % note_id, UA_PC))
    seen, uniq = set(), []
    for u, ua in out:
        if u not in seen:
            seen.add(u)
            uniq.append((u, ua))
    return uniq


def _xhs_fetch_note(note_id, token, final, cookies_file, progress):
    """匿名先试，有 cookie 再补一轮；全挂了挑最有用的那句错误抛出去。

    顺序是刻意反过来的：以前带着 cookie 先试，cookie 一过期页面就退化成
    「登录墙」，明明匿名 + token 能看的内容反而下不了。
    """
    cands = _xhs_candidates(note_id, token, final)
    errors = []
    for round_no in (0, 1):
        cookie = ""
        if round_no == 1:
            if not (cookies_file and os.path.exists(cookies_file)):
                break
            cookie = _read_cookie_file(cookies_file)
            if not cookie:
                break
        for page, ua in cands:
            hdr = {"User-Agent": ua, "Referer": "https://www.xiaohongshu.com/",
                   "Accept-Language": "zh-CN,zh;q=0.9"}
            if cookie:
                hdr["Cookie"] = cookie
            try:
                html = http_get(page, hdr, timeout=25).read().decode("utf-8", "ignore")
                return _xhs_note_from_html(html, note_id)
            except RuntimeError as e:
                errors.append(str(e))
            except Exception as e:
                errors.append("解析请求失败：%s" % e)
    if not errors:
        raise RuntimeError("小红书没给出笔记内容，稍后重试")
    # 优先把「登录过期」这类能自救的信息透出来，其次才是风控/通用
    for key in ("登录过期", "风控", "300012", "验证", "失效", "删除"):
        for msg in errors:
            if key in msg:
                raise RuntimeError(msg)
    raise RuntimeError(errors[-1])


def xhs_download(url, out_dir, progress, quality="best", cookies_file=None):
    note_id, token, final = xhs_resolve(url)
    if not note_id:
        raise RuntimeError("没解析出小红书笔记 ID，请用 App 里的分享链接")
    if not token:
        # 2024 年起 token 就是这条链接的通行证，缺了只能落到登录页/404。
        # 与其试四遍再含糊报错，不如当场说清楚该去哪儿拿链接。
        raise RuntimeError("这条链接少了通行证（xsec_token），浏览器地址栏复制的通常都没有。"
                           "回小红书 App 里点「分享 → 复制链接」，把那条贴进来就行")

    progress(stage="解析中…")
    note = _xhs_fetch_note(note_id, token, final, cookies_file, progress)

    title = note.get("title") or (note.get("desc") or "").split("\n")[0][:40] or "小红书笔记"
    # 手机分享页里作者字段叫 nickName，桌面页叫 nickname，两种都认
    user = note.get("user") or {}
    author = user.get("nickname") or user.get("nickName") or "未知作者"
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

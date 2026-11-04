# -*- coding: utf-8 -*-
"""音乐下载：网易云 / QQ音乐 / 酷狗

只做「搜索 → 拿直链 → 下载」这一条链，元数据交给文件名和 history.json。

实测结论（2026-11，写死在这里免得以后又去试一遍）：
  · 网易云  搜索免登录；直链走 /song/media/outer/url 的 302，免费曲目直接可用，
            VIP/无版权曲目会 302 到 163.com/404。带 Cookie 能多解锁一批。
  · QQ音乐  搜索免登录；播放地址要 vkey，未登录时 purl 恒为空 → 必须登录。
  · 酷狗    搜索要跳过证书校验（CDN 证书对不上）；play_url 目前返回空，仅留实验位。
  · Apple Music / Spotify 完整音轨有 DRM，不做；iTunes 目录只能拿到 30 秒试听。
"""
import json
import os
import re
import ssl
import time
import urllib.parse
import urllib.request

UA_PC = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
         "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

BASE = os.path.dirname(os.path.abspath(__file__))

MUSIC_PLATFORMS = ("netease", "qqmusic", "kugou")


# ---------- 网络 ----------
def _opener(verify=True, follow=True):
    handlers = [urllib.request.ProxyHandler({})]
    if not verify:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        handlers.append(urllib.request.HTTPSHandler(context=ctx))
    if not follow:
        class _NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None
        handlers.append(_NoRedirect())
    return urllib.request.build_opener(*handlers)


def _request(url, headers=None, data=None, timeout=25, verify=True, follow=True):
    h = {"User-Agent": UA_PC}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=data, headers=h)
    return _opener(verify, follow).open(req, timeout=timeout)


def _get_json(url, headers=None, data=None, timeout=25, verify=True):
    with _request(url, headers, data, timeout, verify) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def cookie_header(platform):
    """把 cookies/music_<platform>.txt 读成一行 Cookie 头。
    支持 Netscape cookies.txt 和「一整行 key=value; key=value」两种写法。"""
    path = os.path.join(BASE, "cookies", "music_%s.txt" % platform)
    if not os.path.exists(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            raw = f.read()
    except OSError:
        return ""
    if "=" in raw and "\t" not in raw and "\n" not in raw.strip():
        return raw.strip()
    pairs = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) >= 7:
            pairs.append("%s=%s" % (parts[5], parts[6]))
    return "; ".join(pairs)


# ---------- 链接识别 ----------
SHORT_HOSTS = ("163cn.tv", "y.qq.com/n/ryqq/song", "url.cn")


def _flatten_hash(url):
    """网易云分享出来常是 music.163.com/#/song?id=xxx 这种 hash 路由，
    urlsplit 会把 # 后面整段当 fragment 丢掉，这里先把它拼回路径。"""
    if "#" not in url:
        return url
    head, _, tail = url.partition("#")
    tail = tail.strip()
    if not tail:
        return head
    base = head.split("?")[0].rstrip("/")
    return base + (tail if tail.startswith("/") else "/" + tail)


def parse_url(url):
    """认出这是哪个平台、什么类型。返回 dict 或 None。"""
    u = (url or "").strip()
    sp = urllib.parse.urlsplit(_flatten_hash(u) if "://" in u else "https://" + _flatten_hash(u))
    host = (sp.netloc or "").lower().split(":")[0]
    q = urllib.parse.parse_qs(sp.query)

    if host.endswith("music.163.com"):
        if "/song" in sp.path:
            sid = (q.get("id") or [""])[0]
            if sid:
                return {"platform": "netease", "kind": "song", "id": sid}
        if "/playlist" in sp.path:
            pid = (q.get("id") or [""])[0]
            if pid:
                return {"platform": "netease", "kind": "playlist", "id": pid}
        if "/album" in sp.path:
            aid = (q.get("id") or [""])[0]
            if aid:
                return {"platform": "netease", "kind": "album", "id": aid}
        m = re.search(r"(?:song|playlist|album)/(\d+)", sp.path)
        if m:
            kind = "playlist" if "playlist/" in sp.path else ("album" if "album/" in sp.path else "song")
            return {"platform": "netease", "kind": kind, "id": m.group(1)}

    if host.endswith("y.qq.com") or host.endswith("i.y.qq.com") or host.endswith("c.y.qq.com"):
        m = re.search(r"/songDetail/([0-9A-Za-z]+)", sp.path)
        if m:
            return {"platform": "qqmusic", "kind": "song", "id": m.group(1)}
        m = re.search(r"/albumDetail/([0-9A-Za-z]+)", sp.path)
        if m:
            return {"platform": "qqmusic", "kind": "album", "id": m.group(1)}
        m = re.search(r"/playsquare/([0-9A-Za-z]+)", sp.path) or re.search(r"/playlist/(\d+)", sp.path)
        if m:
            return {"platform": "qqmusic", "kind": "playlist", "id": m.group(1)}
        mid = (q.get("albummid") or [""])[0]
        if mid:
            return {"platform": "qqmusic", "kind": "album", "id": mid}
        sid = (q.get("songmid") or [""])[0]
        if sid:
            return {"platform": "qqmusic", "kind": "song", "id": sid}

    if host.endswith("kugou.com"):
        m = re.search(r"hash=([0-9a-f]{32})", u, re.I)
        if m:
            return {"platform": "kugou", "kind": "song", "id": m.group(1)}
        m = re.search(r"/song/([0-9a-f]{32})", sp.path, re.I)
        if m:
            return {"platform": "kugou", "kind": "song", "id": m.group(1)}

    return None


def parse_url_follow(url):
    """parse_url 的增强版：碰到 163cn.tv 这类短链，先跟一次跳转再认。"""
    info = parse_url(url)
    if info:
        return info
    host = (urllib.parse.urlsplit(_flatten_hash(url)).netloc or "").lower()
    if not any(host.endswith(h) for h in ("163cn.tv", "url.cn", "t.cn")):
        return None
    try:
        with _request(url, follow=True, timeout=12) as r:
            final = r.geturl()
        return parse_url(final)
    except Exception:
        return None


# ---------- 歌单 / 专辑展开 ----------
def _mk(platform, sid, title="", artist=""):
    return {"platform": platform, "id": str(sid or ""), "title": (title or "").strip(),
            "artist": (artist or "").strip()}


def _artists(song):
    """网易云和 QQ 的歌手字段名不一样，统一成「A / B」"""
    arr = song.get("ar") or song.get("artists") or song.get("singer") or []
    names = [str(a.get("name") or "").strip() for a in arr if isinstance(a, dict)]
    return " / ".join([n for n in names if n])


def playlist_tracks(info, limit=200):
    """把歌单 / 专辑链接展开成单曲列表 [{"platform","id","title","artist"}]。
    读不出来抛 RuntimeError，消息里说清卡在哪。单曲链接返回空列表。"""
    if not info:
        return []
    plat, kind, pid = info.get("platform"), info.get("kind"), str(info.get("id") or "")
    if kind not in ("playlist", "album") or not pid:
        return []
    if plat == "netease":
        return _tracks_netease(kind, pid, limit)
    if plat == "qqmusic":
        return _tracks_qq(kind, pid, limit)
    if plat == "kugou":
        raise RuntimeError("酷狗的歌单暂时读不了，贴单曲链接可以下")


def _tracks_netease(kind, pid, limit):
    headers = {"Referer": "https://music.163.com/"}
    if kind == "playlist":
        d = _get_json("https://music.163.com/api/v6/playlist/detail?id=%s" % pid, headers)
        pl = d.get("playlist") or {}
        ids = [t.get("id") for t in (pl.get("trackIds") or []) if t.get("id")]
        if not ids:
            raise RuntimeError("这个歌单是空的，或者不是公开歌单")
        ids = ids[:limit]
        out = []
        # 详情接口一次 100 首，拿名字和歌手，列表里立刻能看懂
        for i in range(0, len(ids), 100):
            chunk = ids[i:i + 100]
            try:
                dd = _get_json("https://music.163.com/api/song/detail?ids=%s"
                               % json.dumps(chunk).replace(" ", ""), headers, timeout=20)
            except Exception:
                continue
            for s in dd.get("songs") or []:
                out.append(_mk("netease", s.get("id"), s.get("name"), _artists(s)))
        if not out:      # 详情接口抽风时退回只有 id，下载时再查
            out = [_mk("netease", i) for i in ids]
        return out

    if kind == "album":
        d = _get_json("https://music.163.com/api/v1/album/%s" % pid, headers)
        songs = d.get("songs") or []
        if not songs:
            raise RuntimeError("这张专辑读不出来，可能已经下架")
        return [_mk("netease", s.get("id"), s.get("name"), _artists(s))
                for s in songs[:limit]]

    raise RuntimeError("网易云只支持歌单和专辑链接")


def _tracks_qq(kind, pid, limit):
    headers = {"Referer": "https://y.qq.com/"}
    if kind == "playlist":
        u = ("https://c.y.qq.com/qzone/fcg-bin/fcg_ucc_getcdinfo_byids_cp.fcg"
             "?type=1&json=1&utf8=1&onlysong=0&format=json&disstid=%s" % pid)
        d = _get_json(u, headers)
        cd = (d.get("cdlist") or [{}])[0] or {}
        songs = cd.get("songlist") or []
        if not songs:
            raise RuntimeError("这个歌单是空的，或者需要登录才能看")
        return [_mk("qqmusic", s.get("songmid") or s.get("mid"),
                    s.get("songname") or s.get("name"), _artists(s))
                for s in songs[:limit]]

    if kind == "album":
        u = "https://c.y.qq.com/v8/fcg-bin/fcg_v8_album_info_cp.fcg?format=json&albummid=%s" % pid
        d = _get_json(u, headers)
        lst = (d.get("data") or {}).get("list") or []
        if not lst:
            raise RuntimeError("这张专辑读不出来")
        return [_mk("qqmusic", s.get("songmid"), s.get("songname"), _artists(s))
                for s in lst[:limit]]

    raise RuntimeError("QQ音乐只支持歌单和专辑链接")


# ---------- 搜索 ----------
def search(platform, keyword, limit=20):
    keyword = (keyword or "").strip()
    if not keyword:
        return []
    if platform == "netease":
        return _search_netease(keyword, limit)
    if platform == "qqmusic":
        return _search_qq(keyword, limit)
    if platform == "kugou":
        return _search_kugou(keyword, limit)
    return []


def _search_netease(kw, limit):
    url = ("https://music.163.com/api/search/get/web?s=%s&type=1&limit=%d&offset=0"
           % (urllib.parse.quote(kw), limit))
    d = _get_json(url, {"Referer": "https://music.163.com/"})
    out = []
    for s in ((d.get("result") or {}).get("songs") or []):
        out.append({
            "platform": "netease", "id": str(s.get("id")),
            "title": s.get("name") or "",
            "artist": ",".join(a.get("name", "") for a in (s.get("artists") or [])),
            "album": (s.get("album") or {}).get("name") or "",
            "cover": (s.get("album") or {}).get("picUrl") or "",
            "duration": int((s.get("duration") or 0) // 1000),
        })
    # 搜索接口只回 picId 不回图，缺封面的批量问一次详情补上（一次请求就够）
    need = [x["id"] for x in out if not x["cover"]]
    if need:
        try:
            det = _get_json("https://music.163.com/api/song/detail?ids=%s"
                            % json.dumps(need).replace(" ", ""),
                            {"Referer": "https://music.163.com/"})
            pic = {}
            for t in (det.get("songs") or []):
                al = t.get("al") or t.get("album") or {}
                u = al.get("picUrl") or al.get("blurPicUrl") or ""
                if u:
                    pic[str(t.get("id"))] = u
            for x in out:
                if not x["cover"]:
                    x["cover"] = pic.get(x["id"], "")
        except Exception:
            pass          # 补不到就空着，前端有占位图兜底
    return out


def _search_qq(kw, limit):
    body = json.dumps({
        "req_1": {"method": "DoSearchForQQMusicDesktop", "module": "music.search.SearchCgiService",
                  "param": {"num_per_page": limit, "page_num": 1, "query": kw, "search_type": 0}},
    }, ensure_ascii=False).encode()
    headers = {"Referer": "https://y.qq.com/", "Content-Type": "application/json"}
    ck = cookie_header("qqmusic")
    if ck:                      # 登录后这个接口才肯多给结果
        headers["Cookie"] = ck
    d = _get_json("https://u.y.qq.com/cgi-bin/musicu.fcg", data=body, headers=headers)
    lst = (((d.get("req_1") or {}).get("data") or {}).get("body") or {}).get("song", {}).get("list") or []
    if not lst:
        return _search_qq_smartbox(kw, limit)   # 未登录时官方搜索只回空，退回联想词
    out = []
    for s in lst:
        album = s.get("album") or {}
        out.append({
            "platform": "qqmusic", "id": s.get("mid") or "",
            "title": s.get("name") or "",
            "artist": ",".join(x.get("name", "") for x in (s.get("singer") or [])),
            "album": album.get("name") or "",
            "cover": "https://y.gtimg.cn/music/photo_new/T002R300x300M000%s.jpg" % album.get("mid", ""),
            "duration": int(s.get("interval") or 0),
        })
    return out


def _search_qq_smartbox(kw, limit):
    """QQ 音乐未登录时 musicu 搜索返回空，联想词接口还能拿到几条。
    结果没有封面和时长，但 songmid 足够下载。"""
    try:
        d = _get_json("https://c.y.qq.com/splcloud/fcgi-bin/smartbox_new.fcg?format=json&key=%s"
                      % urllib.parse.quote(kw), {"Referer": "https://y.qq.com/"})
    except Exception:
        return []
    items = ((d.get("data") or {}).get("song") or {}).get("itemlist") or []
    out = []
    for s in items[:limit]:
        mid = s.get("mid") or s.get("id") or ""
        if not mid:
            continue
        out.append({
            "platform": "qqmusic", "id": mid,
            "title": s.get("name") or "",
            "artist": s.get("singer") or "",
            "album": "", "cover": "", "duration": 0,
        })
    return out


def _search_kugou(kw, limit):
    url = ("https://mobilecdn.kugou.com/api/v3/search/song?format=json&keyword=%s"
           "&page=1&pagesize=%d&showtype=1" % (urllib.parse.quote(kw), limit))
    d = _get_json(url, {"Referer": "https://www.kugou.com/"}, verify=False)
    out = []
    for s in ((d.get("data") or {}).get("info") or []):
        out.append({
            "platform": "kugou", "id": s.get("hash") or "",
            "title": s.get("songname") or "",
            "artist": s.get("singername") or "",
            "album": s.get("album_name") or "",
            "cover": "",
            "duration": int(s.get("duration") or 0),
        })
    return out


# ---------- 直链 ----------
def resolve(platform, sid, quality="best"):
    """拿到可下载地址列表。失败抛 RuntimeError，消息里写清楚卡在哪。"""
    if platform == "netease":
        return _resolve_netease(sid, quality)
    if platform == "qqmusic":
        return _resolve_qq(sid, quality)
    if platform == "kugou":
        return _resolve_kugou(sid)
    raise RuntimeError("不认识这个音乐平台")


def _resolve_netease(sid, quality):
    ck = cookie_header("netease")
    headers = {"Referer": "https://music.163.com/", "User-Agent": UA_PC}
    if ck:
        headers["Cookie"] = ck
    # 先走官方接口（有 Cookie 时能拿到更高码率）
    try:
        d = _get_json("https://music.163.com/api/song/enhance/player/url?ids=[%s]&br=%s"
                      % (sid, 320000 if quality != "720" else 999000), headers,
                      timeout=15)
        item = (d.get("data") or [{}])[0] or {}
        if item.get("url"):
            return [{"url": item["url"], "br": item.get("br") or 0,
                     "size": item.get("size") or 0, "ext": "mp3"}]
    except Exception:
        pass

    # 再走 outer 外链：免费曲目还是能给一条 302
    try:
        r = _request("https://music.163.com/song/media/outer/url?id=%s.mp3" % sid,
                     headers, follow=False, timeout=15)
        r.close()
        raise RuntimeError("没跳转")
    except urllib.error.HTTPError as e:
        loc = e.headers.get("Location") or ""
        if loc and "404" not in loc:
            ext = "m4a" if ".m4a" in loc.lower() else "mp3"
            return [{"url": loc, "br": 0, "size": 0, "ext": ext}]
        raise RuntimeError(
            "网易云这首拿不到直链（多半是 VIP 或无版权）。"
            "可以在设置里登录网易云后再试。")


def _resolve_qq(sid):
    ck = cookie_header("qqmusic")
    if not ck:
        raise RuntimeError("QQ音乐的播放地址必须登录才有。先在设置里登录 QQ音乐，再点重试。")
    uin = ""
    m = re.search(r"\buin=o?(\d+)", ck)
    if m:
        uin = m.group(1)
    body = json.dumps({
        "req_0": {"module": "vkey.GetVkeyServer", "method": "CgiGetVkey",
                  "param": {"guid": "10000", "songmid": [sid], "songtype": [0],
                            "uin": uin or "0", "loginflag": 1, "platform": "20"}}},
        ensure_ascii=False).encode()
    d = _get_json("https://u.y.qq.com/cgi-bin/musicu.fcg", data=body,
                  headers={"Referer": "https://y.qq.com/", "Cookie": ck,
                           "Content-Type": "application/json"})
    info = (((d.get("req_0") or {}).get("data") or {}).get("midurlinfo") or [{}])[0]
    purl = info.get("purl") or ""
    if not purl:
        raise RuntimeError("QQ音乐返回了空地址，通常是没登录成功或这首歌要会员。重新登录试试。")
    base = "https://ws.stream.qqmusic.qq.com/"
    return [{"url": base + purl, "br": 0, "size": 0,
             "ext": "m4a" if ".m4a" in purl.lower() else "mp3"}]


def _resolve_kugou(sid):
    for url, hd in (
        ("https://wwwapi.kugou.com/yy/index.php?r=play/getdata&hash=%s&dfid=-&mid=1&platid=4&appid=1014" % sid,
         {"Referer": "https://www.kugou.com/"}),
        ("http://m.kugou.com/app/i/getSongInfo.php?cmd=playInfo&hash=%s" % sid,
         {"Referer": "http://m.kugou.com/"}),
    ):
        try:
            d = _get_json(url, hd, verify=False, timeout=15)
            data = d.get("data") or d
            link = (data.get("play_url") or data.get("url") or "") if isinstance(data, dict) else ""
            if link:
                return [{"url": link, "br": 0, "size": 0, "ext": "mp3"}]
        except Exception:
            continue
    raise RuntimeError("酷狗这边拿不到播放地址（接口需要登录态），暂时下不了。")


# ---------- 下载 ----------
def song_meta(platform, sid):
    """按 id 反查歌曲信息，用于直接粘贴链接的场景。找不到就返回空壳。"""
    if platform == "netease":
        try:
            d = _get_json("https://music.163.com/api/song/detail?ids=[%s]" % sid,
                          {"Referer": "https://music.163.com/"}, timeout=15)
            s = (d.get("songs") or [{}])[0] or {}
            return {"title": s.get("name") or "", "artist": ",".join(
                a.get("name", "") for a in (s.get("artists") or [])),
                "album": (s.get("album") or {}).get("name") or "",
                "cover": (s.get("album") or {}).get("picUrl") or "",
                "duration": int((s.get("duration") or 0) // 1000)}
        except Exception:
            pass
    if platform == "qqmusic":
        try:
            body = json.dumps({"req_1": {"module": "music.pf_song_detail_svr",
                                         "method": "get_song_detail",
                                         "param": {"song_mid": sid}}}, ensure_ascii=False).encode()
            d = _get_json("https://u.y.qq.com/cgi-bin/musicu.fcg", data=body,
                          headers={"Referer": "https://y.qq.com/",
                                   "Content-Type": "application/json"}, timeout=15)
            t = (((d.get("req_1") or {}).get("data") or {}).get("track_info") or {})
            if t:
                return {"title": t.get("name") or "", "artist": ",".join(
                    x.get("name", "") for x in (t.get("singer") or [])),
                    "album": (t.get("album") or {}).get("name") or "",
                    "cover": (t.get("album") or {}).get("picUrl") or "",
                    "duration": int(t.get("interval") or 0)}
        except Exception:
            pass
    return {}


def safe(s, limit=70):
    s = re.sub(r'[\\/:*?"<>|\r\n\t]', " ", s or "")
    s = re.sub(r"\s+", " ", s).strip(" .")
    return s[:limit].strip(" .-_")


# ---------- ID3 标签（手写 v2.3，不引第三方库） ----------
def _id3_frame(fid, payload):
    return fid.encode("ascii") + len(payload).to_bytes(4, "big") + b"\x00\x00" + payload


def _text_frame(fid, text):
    # encoding=1 → UTF-16LE with BOM，中文不会乱码
    return _id3_frame(fid, b"\x01\xff\xfe" + str(text).encode("utf-16-le"))


def _apic_frame(data, mime):
    payload = b"\x00" + mime.encode() + b"\x00" + b"\x03" + b"\x00" + data
    return _id3_frame("APIC", payload)


def _fetch_cover(url, referer="https://music.163.com/"):
    if not url:
        return None, None
    try:
        with _request(url, {"Referer": referer}, timeout=15) as r:
            data = r.read()
            ct = (r.headers.get("Content-Type") or "").lower()
        if len(data) < 800:
            return None, None
        if "png" in ct:
            return data, "image/png"
        if "webp" in ct:
            return None, None          # ID3 里塞 webp 兼容性差，宁可不要
        return data, "image/jpeg"
    except Exception:
        return None, None


def _strip_id3(data):
    """把文件头已有的 ID3v2 标签剥掉。流媒体返回的 mp3 常有 35 字节的空标签
    （只有编码器信息），留着会和新标签打架。"""
    if len(data) < 10 or data[:3] != b"ID3":
        return data
    size = ((data[6] & 0x7F) << 21) | ((data[7] & 0x7F) << 14) | \
           ((data[8] & 0x7F) << 7) | (data[9] & 0x7F)
    total = 10 + size
    if data[5] & 0x10:          # footer 标志
        total += 10
    return data[total:] if total < len(data) else data


def write_id3(path, meta, referer="https://music.163.com/"):
    """把歌名/歌手/专辑/封面写进 mp3（ID3v2.3）。写不进去就安静地放弃。"""
    low = path.lower()
    if not low.endswith(".mp3"):
        return False                # m4a 是 MP4 盒子结构，另说
    try:
        with open(path, "rb") as f:
            body = _strip_id3(f.read())
    except OSError:
        return False

    frames = b""
    if meta.get("title"):
        frames += _text_frame("TIT2", meta["title"])
    if meta.get("artist"):
        frames += _text_frame("TPE1", meta["artist"])
    if meta.get("album"):
        frames += _text_frame("TALB", meta["album"])
    pic, mime = _fetch_cover(meta.get("cover") or "", referer)
    if pic:
        frames += _apic_frame(pic, mime)
    if not frames:
        return False

    size = len(frames)
    ss = bytes([(size >> 21) & 0x7F, (size >> 14) & 0x7F, (size >> 7) & 0x7F, size & 0x7F])
    try:
        with open(path, "wb") as f:
            f.write(b"ID3\x03\x00\x00" + ss + frames + body)
        return True
    except OSError:
        return False


def _pick_name(meta, fallback):
    title = safe(meta.get("title") or fallback or "未命名")
    artist = safe(meta.get("artist") or "")
    return ("%s - %s" % (title, artist)).strip(" -") if artist else title


def download(url, out_dir, progress, quality="best", cookie_file=None):
    """下载一首。返回文件路径；歌单/专辑在这里只认第一首并给出提示。"""
    info = parse_url(url)
    if not info:
        raise RuntimeError("这不是能识别的音乐链接")

    platform = info["platform"]
    if info["kind"] != "song":
        raise RuntimeError("歌单/专辑请用搜索框逐首挑，或者复制单曲链接。")

    meta = song_meta(platform, info["id"])
    name = _pick_name(meta, info["id"])
    progress(title=meta.get("title") or name, author=meta.get("artist") or platform,
             thumb=meta.get("cover") or "", duration=meta.get("duration") or 0,
             stage="获取地址…")

    cands = resolve(platform, info["id"], quality)
    last = None
    for c in cands:
        try:
            p = _stream(c, os.path.join(out_dir, "%s.%s" % (name, c.get("ext") or "mp3")),
                        progress, platform)
            write_id3(p, meta, _referer(platform))
            return p
        except Exception as e:
            last = e
    raise RuntimeError(str(last or "下载失败"))


def _referer(platform):
    return {"qqmusic": "https://y.qq.com/",
            "kugou": "https://www.kugou.com/"}.get(platform, "https://music.163.com/")


def download_song(platform, sid, out_dir, progress, quality="best",
                  title="", artist="", cover="", duration=0):
    """搜索列表里点下来的一首歌：信息已经在手上，缺封面时再补查一次。"""
    meta = {"title": title, "artist": artist, "cover": cover, "album": ""}
    if not cover or not artist:
        extra = song_meta(platform, sid)
        for k in ("title", "artist", "album", "cover", "duration"):
            if not meta.get(k) and extra.get(k):
                meta[k] = extra[k]
    name = _pick_name(meta, sid)
    progress(title=meta.get("title") or name, author=meta.get("artist") or platform,
             thumb=meta.get("cover") or "", duration=duration or 0, stage="获取地址…")
    cands = resolve(platform, sid, quality)
    last = None
    for c in cands:
        try:
            p = _stream(c, os.path.join(out_dir, "%s.%s" % (name, c.get("ext") or "mp3")),
                        progress, platform)
            write_id3(p, meta, _referer(platform))
            return p
        except Exception as e:
            last = e
    raise RuntimeError(str(last or "下载失败"))


def _stream(cand, out_path, progress, platform):
    from core import unique_path
    out_path = unique_path(out_path)
    hd = {"User-Agent": UA_PC, "Referer": "https://music.163.com/"}
    if platform == "qqmusic":
        hd["Referer"] = "https://y.qq.com/"
    elif platform == "kugou":
        hd["Referer"] = "https://www.kugou.com/"

    with _request(cand["url"], hd, follow=True, timeout=60) as r:
        ctype = (r.headers.get("Content-Type") or "").lower()
        if "text/html" in ctype or "json" in ctype:
            raise RuntimeError("这个地址返回的是网页，不是音频（多半没版权或要登录）")
        total = int(r.headers.get("Content-Length") or 0)
        got = 0
        t0 = time.time()
        with open(out_path, "wb") as f:
            while True:
                chunk = r.read(65536)
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
                if total:
                    progress(done=got, total=total,
                             speed=got / 1048576.0 / max(time.time() - t0, 0.01),
                             percent=got * 100.0 / total)
    if os.path.getsize(out_path) < 30000:
        try:
            os.remove(out_path)
        except OSError:
            pass
        raise RuntimeError("下下来只有几 KB，多半是试听片段")
    return out_path

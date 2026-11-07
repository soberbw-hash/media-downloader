# -*- coding: utf-8 -*-
"""聚合搜索的「来源插件表」。

学的是网上那些资源搜索插件 / 元搜索引擎的做法：
一个来源 = 一个独立适配器，只管「给我关键词，吐出统一格式的卡片」；
谁快谁慢、谁挂了、结果怎么合并去重排序，全交给 aggregate.py，适配器不操心。

统一卡片字段：
  platform / id / kind / title / author / cover / duration / desc
  url       详情页（给人看、可溯源）
  download  真正丢给下载管线的地址（直链或页面链接）
  source / source_name   来自哪个源

只接公开、可合法访问的搜索接口与免费素材站；不碰登录墙、不解析付费内容。
"""
import concurrent.futures as cf
import html as html_mod
import json
import re
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


class SkipSource(Exception):
    """这个源本次不适用（不是故障），说人话告诉用户就行。"""


# ---------- 内置来源 ----------
# priority 越小越靠前；timeout 是单源最长等待时间（秒）
BUILTIN = [
    {"id": "bili", "name": "B站", "kind": "video", "priority": 1, "timeout": 12,
     "group": "中文视频", "note": "中文关键词最准"},
    {"id": "mixkit", "name": "Mixkit 视频", "kind": "video", "priority": 2, "timeout": 14,
     "group": "免费素材", "note": "免费可商用视频，直链可下；只认英文关键词"},
    {"id": "mixkit_sfx", "name": "Mixkit 音效", "kind": "audio", "priority": 3, "timeout": 12,
     "group": "免费音效", "note": "免费可商用音效，wav 直链；只认英文关键词"},
    {"id": "youtube", "name": "YouTube", "kind": "video", "priority": 4, "timeout": 12,
     "group": "海外视频", "note": "需要网络能到 Google"},
    {"id": "freesound", "name": "Freesound", "kind": "audio", "priority": 6, "timeout": 25,
     "group": "音效", "note": "公开音效库，响应较慢，建议点它单独搜", "slow": True},
]


# 中文关键词 → Mixkit 能认的英文分类词。
# 素材站是英文站，中文丢过去只会得到空页，所以先做个常用词映射；
# 映射不出来的中文词就跳过这个源，并提示用户换英文，别假装搜过。
EN_WORDS = [
    ("城市夜景", "city"), ("延时", "timelapse"), ("航拍", "aerial"), ("慢动作", "slow-motion"),
    ("夜景", "night"), ("城市", "city"), ("日落", "sunset"), ("日出", "sunrise"),
    ("星空", "stars"), ("天空", "sky"), ("云", "cloud"), ("雨", "rain"),
    ("雪", "snow"), ("海", "ocean"), ("沙滩", "beach"), ("山", "mountain"),
    ("森林", "forest"), ("花", "flower"), ("水", "water"), ("火", "fire"),
    ("光", "light"), ("霓虹", "neon"), ("烟花", "fireworks"), ("街", "street"),
    ("车", "car"), ("交通", "traffic"), ("人", "people"), ("人群", "crowd"),
    ("猫", "cat"), ("狗", "dog"), ("鸟", "bird"), ("咖啡", "coffee"),
    ("美食", "food"), ("办公室", "office"), ("科技", "technology"),
    ("旅行", "travel"), ("春天", "spring"), ("夏天", "summer"),
    ("秋天", "autumn"), ("冬天", "winter"), ("风", "wind"), ("雾", "fog"),
    ("城市夜景", "city"), ("建筑", "architecture"), ("跑步", "running"),
    ("舞蹈", "dance"), ("运动", "sport"), ("婴儿", "baby"), ("家庭", "family"),
]


def en_keyword(kw):
    """英文原样用；中文挑第一个能映射的词；认不出返回 None。"""
    kw = (kw or "").strip()
    if not kw:
        return None
    if all(ord(c) < 128 for c in kw):
        return kw
    for zh, en in EN_WORDS:
        if zh in kw:
            return en
    return None


# ---------- 站点直达（不抓内容，只帮你一键在各站搜同一个词） ----------
# 都是公开可访问的搜索页，不涉及登录墙、付费内容或盗版站点。
SITE_LINKS = [
    {"id": "aigei", "name": "爱给网", "url": "https://www.aigei.com/s?q={q}&type=video",
     "tip": "国内视频 / 音效素材"},
    {"id": "pexels", "name": "Pexels", "url": "https://www.pexels.com/search/videos/{slug}/",
     "tip": "免费可商用视频"},
    {"id": "pixabay", "name": "Pixabay", "url": "https://pixabay.com/videos/search/{slug}/",
     "tip": "免费可商用视频"},
    {"id": "mixkit_site", "name": "Mixkit", "url": "https://mixkit.co/free-stock-video/{slug}/",
     "tip": "免费可商用视频"},
    {"id": "coverr", "name": "Coverr", "url": "https://coverr.co/search?q={q}",
     "tip": "免费短视频素材"},
    {"id": "vimeo", "name": "Vimeo", "url": "https://vimeo.com/search?q={q}",
     "tip": "创作者视频"},
    {"id": "freesound_site", "name": "Freesound", "url": "https://freesound.org/search/?q={q}",
     "tip": "音效 / 环境声"},
    {"id": "archive_site", "name": "Internet Archive", "url": "https://archive.org/search?query={q}",
     "tip": "公共领域影像"},
    {"id": "douban_site", "name": "豆瓣", "url": "https://movie.douban.com/subject_search?search_text={q}",
     "tip": "影视资料"},
    {"id": "bili_site", "name": "B站", "url": "https://search.bilibili.com/all?keyword={q}",
     "tip": "站内搜索"},
    {"id": "youtube_site", "name": "YouTube", "url": "https://www.youtube.com/results?search_query={q}",
     "tip": "站内搜索"},
    {"id": "bing_site", "name": "必应", "url": "https://cn.bing.com/search?q={q}",
     "tip": "全网找"},
]


def site_url(tpl, kw):
    """把 {q} / {slug} 填进模板。{q} 做完整 URL 编码，{slug} 做路径化。
    英文站的路径用中文映射后的词，别把中文硬塞进 URL 变成「all」。"""
    q = urllib.parse.quote((kw or "").strip(), safe="")
    raw = en_keyword(kw) or kw or ""
    slug = re.sub(r"[^a-z0-9\-]+", "-", raw.lower()).strip("-") or "all"
    return (tpl or "").replace("{q}", q).replace("{slug}", slug)


def site_links(cfg):
    """内置站点 + 用户自己加的站点。"""
    out = [dict(x) for x in SITE_LINKS]
    for s in (cfg or {}).get("custom_sites") or []:
        if not isinstance(s, dict):
            continue
        name = str(s.get("name") or "").strip()
        url = str(s.get("url") or "").strip()
        if not name or not url.startswith("https://") or "{q}" not in url:
            continue
        out.append({"id": "u_" + re.sub(r"\W+", "", name)[:16], "name": name,
                    "url": url, "tip": str(s.get("tip") or "自己加的站点"),
                    "custom": True})
    return out


def allowed_hosts(cfg):
    """给 /api/open_url 用：只允许打开名单里的站点，别变成任意网址跳板。"""
    hosts = set()
    for s in site_links(cfg):
        try:
            h = urllib.parse.urlsplit(s["url"]).hostname or ""
            if h:
                hosts.add(h.lower())
        except Exception:
            pass
    return hosts


# ---------- 来源清单 ----------
def custom_sources(cfg):
    """用户接进来的第三方 JSON 接口。

    配置形如：
      {"id":"my", "name":"我的资源站", "url":"https://x.com/api/s?q={q}&n={limit}",
       "root":"data.list", "fields":{"title":"title","url":"link","cover":"pic"}}
    """
    out = []
    for s in (cfg or {}).get("custom_sources") or []:
        if not isinstance(s, dict):
            continue
        sid = str(s.get("id") or "").strip()
        url = str(s.get("url") or "").strip()
        if not sid or not url.startswith("https://") or "{q}" not in url:
            continue
        out.append({
            "id": "c_" + re.sub(r"\W+", "", sid)[:20],
            "name": str(s.get("name") or sid)[:20],
            "kind": "video", "priority": 5, "timeout": 12,
            "group": "我加的接口", "note": "自己接的第三方接口",
            "custom": True, "url": url, "root": str(s.get("root") or ""),
            "fields": s.get("fields") if isinstance(s.get("fields"), dict) else {},
        })
    return out


def all_sources(cfg):
    """内置 + 自定义，按优先级排好。"""
    return sorted([dict(x) for x in BUILTIN] + custom_sources(cfg),
                  key=lambda x: x["priority"])


def sources_for_ui(cfg):
    return [{"id": s["id"], "name": s["name"], "group": s["group"],
             "note": s.get("note", ""), "kind": s["kind"],
             "slow": bool(s.get("slow")),
             "custom": bool(s.get("custom"))} for s in all_sources(cfg)]


# ---------- 通用小工具 ----------
def _fetch(url, timeout=10, referer=None):
    headers = {"User-Agent": UA, "Accept": "*/*",
               "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}
    if referer:
        headers["Referer"] = referer
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def _txt(s):
    return html_mod.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


def _attr(block, key):
    m = re.search(r'data-%s="([^"]*)"' % re.escape(key), block or "")
    return html_mod.unescape(m.group(1)) if m else ""


def _card(**kw):
    base = {"platform": "", "id": "", "kind": "video", "title": "", "author": "",
            "cover": "", "duration": 0, "desc": "", "url": "", "download": "",
            "page": "", "source": "", "source_name": ""}
    base.update(kw)
    return base


# ---------- 适配器 1：B站 / YouTube（复用原来的解析器） ----------
def _bili(kw, limit, cfg, proxy):
    import search
    items = search.search_bili(kw, limit)
    for x in items:
        x.update(source="bili", source_name="B站", download=x.get("url", ""),
                 page=x.get("url", ""))
    return items


def _youtube(kw, limit, cfg, proxy):
    import search
    items = search.search_youtube(kw, limit, proxy)
    for x in items:
        x.update(source="youtube", source_name="YouTube",
                 download=x.get("url", ""), page=x.get("url", ""))
    return items


# ---------- 适配器 2：Mixkit（免费可商用视频，直链可下） ----------
def _mixkit_slug(kw):
    s = re.sub(r"[^a-z0-9]+", "-", kw.lower()).strip("-")
    return s or "all"


def _mixkit_hq(vid, timeout=8):
    """去下载弹窗里把 1080p 的真实直链抠出来；拿不到就返回空，用预览直链兜底。"""
    u = "https://mixkit.co/free-stock-video/download/%s/?context=sidebar&type=1080p" % vid
    try:
        t = _fetch(u, timeout, referer="https://mixkit.co/")
        m = re.search(r'data-download--modal-url-value="([^"]+)"', t)
        return html_mod.unescape(m.group(1)) if m else ""
    except Exception:
        return ""


def _mixkit(kw, limit, cfg, proxy):
    en = en_keyword(kw)
    if not en:
        raise SkipSource("Mixkit 只认英文关键词，试试 city / rain / night（已跳过）")
    url = "https://mixkit.co/free-stock-video/%s/" % _mixkit_slug(en)
    t = _fetch(url, 12, referer="https://mixkit.co/")
    blocks = re.split(r'class="item-grid-card"', t)[1:limit + 8]
    cards = []
    for b in blocks:
        vid = _attr(b, "item-grid--video-player-item-id-value")
        if not vid:
            m = re.search(r'data-algolia-analytics-object-id="video-(\d+)"', b)
            vid = m.group(1) if m else ""
        if not vid:
            continue
        m = re.search(r'<video\s+src="([^"]+)"', b)
        preview = html_mod.unescape(m.group(1)) if m else ""
        if not preview:
            continue
        mt = re.search(r'item-grid-video-player__overlay-video-title">\s*(.*?)\s*</span>', b, re.S)
        md = re.search(r'item-grid-video-player__overlay-video-description">\s*(.*?)\s*</span>', b, re.S)
        mh = re.search(r'href="(/free-stock-video/[^"]+)"', b)
        mi = re.search(r'<img\s+src="([^"]+)"', b)
        page = "https://mixkit.co" + mh.group(1) if mh else url
        cards.append(_card(
            platform="mixkit", id="mk" + vid, kind="video",
            title=_txt(mt.group(1)) if mt else "Mixkit 素材 " + vid,
            author="Mixkit", cover=html_mod.unescape(mi.group(1)) if mi else "",
            desc=_txt(md.group(1))[:120] if md else "",
            url=page, page=page, download=preview,
            source="mixkit", source_name="Mixkit"))
        if len(cards) >= limit:
            break
    if not cards:
        raise SkipSource("Mixkit 没找到「%s」相关素材，换个英文词试试" % en)
    # 前 10 条并发换成 1080p 直链。失败就留着页面给的预览直链，
    # 至少还能下，不会出现「点了下载却什么都没有」。
    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        hqs = list(ex.map(lambda c: _mixkit_hq(c["id"][2:]), cards[:10]))
    for c, hq in zip(cards[:10], hqs):
        if hq:
            c["download"] = hq
            c["hq"] = True
    return cards


# ---------- 适配器 3：Mixkit 音效（免费可商用，wav 直链） ----------
def _mixkit_sfx_hq(vid, timeout=8):
    """下载弹窗里给的是 wav 原文件，比页面上的 mp3 预览好。"""
    u = "https://mixkit.co/free-sound-effects/download/%s/?context=item+grid" % vid
    try:
        t = _fetch(u, timeout, referer="https://mixkit.co/")
        m = re.search(r'data-download--modal-url-value="([^"]+)"', t)
        return html_mod.unescape(m.group(1)) if m else ""
    except Exception:
        return ""


def _mixkit_sfx(kw, limit, cfg, proxy):
    en = en_keyword(kw)
    if not en:
        raise SkipSource("Mixkit 音效只认英文关键词，试试 rain / wind / whoosh（已跳过）")
    url = "https://mixkit.co/free-sound-effects/%s/" % _mixkit_slug(en)
    t = _fetch(url, 12, referer="https://mixkit.co/")
    cards = []
    # 只按卡片开头切，别把卡片里的 item-grid-card__meta / __title 也切碎
    for b in re.split(r'class="item-grid-card(?=[\s"])', t)[1:limit + 8]:
        m = re.search(r'data-audio-player-preview-url-value="([^"]+)"', b)
        preview = html_mod.unescape(m.group(1)) if m else ""
        mid = re.search(r'data-audio-player-item-id-value="(\d+)"', b)
        if not preview or not mid:
            continue
        vid = mid.group(1)
        mt = re.search(r'class="item-grid-card__title">\s*(.*?)\s*</h2>', b, re.S)
        mh = re.search(r'href="(/free-sound-effects/[^"]*-%s/)"' % re.escape(vid), b)
        page = "https://mixkit.co" + mh.group(1) if mh else url
        cards.append(_card(
            platform="mixkit_sfx", id="ms" + vid, kind="audio",
            title=_txt(mt.group(1)) if mt else "音效 " + vid,
            author="Mixkit", cover="", duration=0,
            desc="免费可商用音效 · 可下 wav 原文件",
            url=page, page=page, download=preview,
            source="mixkit_sfx", source_name="Mixkit 音效"))
        if len(cards) >= limit:
            break
    if not cards:
        raise SkipSource("Mixkit 没找到「%s」相关音效，换个英文词试试" % en)
    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        hqs = list(ex.map(lambda c: _mixkit_sfx_hq(c["id"][2:]), cards[:10]))
    for c, hq in zip(cards[:10], hqs):
        if hq:
            c["download"] = hq
            c["hq"] = True
    return cards


# ---------- 适配器 4：Freesound（音效 / 环境声，比较慢） ----------
def _freesound(kw, limit, cfg, proxy):
    # 这站的内容基本是英文标题，中文词先映射成英文，命中率高得多
    q = en_keyword(kw) or kw
    u = "https://freesound.org/search/?q=" + urllib.parse.quote(q)
    t = _fetch(u, 12, referer="https://freesound.org/")
    out = []
    for m in re.finditer(r'<div\s+class="bw-player"(.*?)>', t, re.S):
        b = m.group(1)
        sid = _attr(b, "sound-id")
        mp3 = _attr(b, "mp3")
        if not sid or not mp3:
            continue
        user = _attr(b, "username") or "Freesound"
        try:
            dur = int(float(_attr(b, "duration") or 0))
        except (TypeError, ValueError):
            dur = 0
        page = "https://freesound.org/people/%s/sounds/%s/" % (user, sid)
        out.append(_card(
            platform="freesound", id="fs" + sid, kind="audio",
            title=_attr(b, "title") or "音效 " + sid,
            author=user, cover=_attr(b, "waveform"),
            duration=dur, desc="公开预览音频 · 完整文件请到原站下载",
            url=page, page=page, download=mp3,
            source="freesound", source_name="Freesound"))
        if len(out) >= limit:
            break
    return out


# ---------- 适配器 4：用户自己接的 JSON 接口 ----------
def _dig(obj, path):
    cur = obj
    for p in (path or "").split("."):
        if not p:
            continue
        if isinstance(cur, dict):
            cur = cur.get(p)
        elif isinstance(cur, list) and p.isdigit() and int(p) < len(cur):
            cur = cur[int(p)]
        else:
            return None
        if cur is None:
            return None
    return cur


def _custom_json(src, kw, limit, cfg, proxy):
    u = site_url(src["url"], kw).replace("{limit}", str(limit))
    t = _fetch(u, src.get("timeout") or 12)
    try:
        d = json.loads(t)
    except Exception:
        raise RuntimeError("返回的不是 JSON，检查接口地址")
    rows = _dig(d, src.get("root")) if src.get("root") else d
    if rows is None:
        rows = d.get("data") if isinstance(d, dict) else d
    if not isinstance(rows, list):
        rows = []
    f = src.get("fields") or {}
    out = []
    for i, row in enumerate(rows[:limit]):
        if not isinstance(row, dict):
            continue
        get = lambda key, default="": str(_dig(row, f.get(key) or key) or default)
        link = get("url") or get("download")
        if not link:
            continue
        if link.startswith("//"):
            link = "https:" + link
        out.append(_card(
            platform=src["id"], id="%s%d" % (src["id"], i), kind="video",
            title=get("title", "未命名"), author=get("author"), cover=get("cover"),
            desc=get("desc")[:120], url=link, page=link, download=get("download") or link,
            source=src["id"], source_name=src["name"]))
    return out


_ADAPTERS = {"bili": _bili, "youtube": _youtube,
             "mixkit": _mixkit, "mixkit_sfx": _mixkit_sfx,
             "freesound": _freesound}


def run_source(src, kw, limit, cfg=None, proxy=None):
    """跑一个来源。失败抛异常，调度器负责转成一句人话。"""
    sid = src["id"]
    if src.get("custom"):
        return _custom_json(src, kw, limit, cfg, proxy)
    fn = _ADAPTERS.get(sid)
    if not fn:
        raise RuntimeError("不认识的来源 " + sid)
    return fn(kw, limit, cfg, proxy)

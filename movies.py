# -*- coding: utf-8 -*-
"""电影条目搜索：把「搜电影」做成资料卡，而不是盗版源。

思路：
  · 豆瓣（免 key）出资料——海报、评分、导演、类型、时长、地区、简介
  · B站影视区（media_ft 电影 / media_bangumi 番剧）出「正版在哪」——
    片子在 B站有没有正版、是不是大会员、正片链接、站内评分
两张表按片名合并成一张卡：左边是资料，右边是正版状态。

只做「找正版 + 查资料」，不碰付费内容；想拿素材就走「找素材」按钮，
去 B站/YouTube 搜二创、解说、花絮这类可下载内容。
"""
import json
import re
import threading
import time
import urllib.parse
import urllib.request

import search as S

UA = S.UA
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _get(url, referer="https://movie.douban.com/", timeout=12):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept": "*/*", "Referer": referer})
    with _opener.open(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


# ---------------- 豆瓣：资料 ----------------

def _douban_suggest(kw, limit=8):
    """搜片名，拿 subject_id。豆瓣这个老接口不要 key，直连就行。"""
    try:
        raw = _get("https://movie.douban.com/j/subject_suggest?q=" +
                   urllib.parse.quote(kw))
        data = json.loads(raw)
    except Exception:
        return []
    out = []
    for it in data or []:
        sid = str(it.get("id") or "")
        title = (it.get("title") or "").strip()
        if not sid or not title:
            continue
        out.append({
            "id": sid,
            "title": title,
            "year": str(it.get("year") or ""),
            "type": it.get("type") or "movie",
            "cover": _douban_img(it.get("img") or ""),
            "url": "https://movie.douban.com/subject/%s/" % sid,
        })
        if len(out) >= limit:
            break
    return out


def _douban_detail(sid):
    """拉单片详情：评分、导演、类型、时长、地区、简介。

    主用 rexxar 接口（字段全，带完整简介），老 abstract 接口做兜底。
    """
    out = {}
    try:
        raw = _get("https://m.douban.com/rexxar/api/v2/movie/" + sid,
                   "https://m.douban.com/movie/subject/%s/" % sid)
        d = json.loads(raw)
        if d.get("title"):
            rating = d.get("rating") or {}
            directors = [x.get("name") for x in (d.get("directors") or []) if x.get("name")]
            actors = [x.get("name") for x in (d.get("actors") or []) if x.get("name")]
            out = {
                "title": (d.get("title") or "").strip(),
                "rating": rating.get("value") or "",
                "rating_count": rating.get("count") or 0,
                "director": " / ".join(directors),
                "actors": " / ".join(actors[:6]),
                "genres": d.get("genres") or [],
                "duration": " / ".join(d.get("durations") or []),
                "region": " / ".join(d.get("countries") or []),
                "year": str(d.get("year") or ""),
                "summary": (d.get("intro") or "").strip(),
                "cover": _douban_img(d.get("cover_url") or ""),
            }
    except Exception:
        pass
    if out:
        return out
    # 兜底：老接口
    try:
        raw = _get("https://movie.douban.com/j/subject_abstract?subject_id=" + sid,
                   "https://movie.douban.com/subject/%s/" % sid)
        d = json.loads(raw)
    except Exception:
        return {}
    s = d.get("subject") or {}
    if not s or not s.get("title"):
        return {}
    desc = s.get("desc") or ""
    if not desc:
        sc = s.get("short_comment") or {}
        if isinstance(sc, dict):
            desc = sc.get("comment") or ""
    actors = s.get("actors") or []
    if isinstance(actors, str):
        actors = [actors]
    return {
        "title": (s.get("title") or "").strip(),
        "rating": s.get("rate") or "",
        "rating_count": 0,
        "director": " / ".join(s.get("directors") or []),
        "actors": " / ".join(str(x) for x in actors[:6]),
        "genres": s.get("types") or [],
        "duration": s.get("duration") or "",
        "region": s.get("region") or "",
        "year": str(s.get("release_year") or ""),
        "summary": desc,
        "cover": "",
    }


# ---------------- B站：正版在哪 ----------------

def _strip_em(s):
    return re.sub(r"<[^>]+>", "", s or "").strip()


def _douban_img(url):
    """豆瓣 suggest 给的是 m_ratio 小图（宽 100px），换 l_ratio（宽 200px）清楚些。"""
    u = (url or "").replace("\\/", "/")
    u = re.sub(r"/view/photo/[a-z]_ratio_poster/", "/view/photo/l_ratio_poster/", u)
    if u and not re.search(r"\.(jpg|jpeg|png|webp)$", u, re.I):
        u += ".jpg"
    return u


def _year_of(ts):
    """B站的 pubtime 是 Unix 时间戳，转成年份字符串。"""
    try:
        ts = int(ts or 0)
        if ts <= 0:
            return ""
        return str(time.localtime(ts)[0])
    except Exception:
        return ""


def _query_variants(kw):
    """生成几个搜索词变体。

    B站影视区对完整片名经常搜不到（「哪吒之魔童降世」0 条，站内叫「哪吒」），
    所以额外给出短前缀（「哪吒」）和去后缀（「流浪地球2」→「流浪地球」）。
    """
    out = []
    kw = (kw or "").strip()
    if not kw:
        return out

    def add(s):
        s = (s or "").strip()
        if s and s not in out:
            out.append(s)

    add(kw)
    # 「三体II：黑暗森林」→ 主标题「三体」
    m = re.split(r"[：:]", kw)[0].strip()
    add(m)
    # 中文片名常见的「X之Y」结构，主名是 X
    m2 = re.split(r"之", kw)[0].strip()
    if len(m2) >= 2:
        add(m2)
    # 前 2 个字（「哪吒之魔童降世」→「哪吒」）
    if len(kw) > 3:
        add(kw[:2])
    return out


def _bili_media(kw, limit=8):
    """搜 B站影视区。media_ft=电影/电视剧，media_bangumi=番剧。

    B站对长片名经常搜不到（「哪吒之魔童降世」0 条，站内叫「哪吒」），
    所以再拿关键词的短前缀补搜一轮，别漏掉正版。
    """
    S._bootstrap()
    seen = set()
    out = []
    for q in _query_variants(kw):
        for st in ("media_ft", "media_bangumi"):
            try:
                sq = S._signed_query({"search_type": st, "keyword": q, "page": "1"})
                raw = S._get("https://api.bilibili.com/x/web-interface/wbi/search/type?" + sq)
                d = json.loads(raw.decode("utf-8", "replace"))
                if d.get("code") != 0:
                    continue
                res = (d.get("data") or {}).get("result") or []
            except Exception:
                continue
            for r in res:
                title = _strip_em(r.get("org_title") or r.get("title"))
                if not title or title in seen:
                    continue
                seen.add(title)
                score = r.get("media_score") or {}
                badges = [b.get("text") for b in (r.get("badges") or []) if b.get("text")]
                disp = [b.get("text") for b in (r.get("display_info") or []) if b.get("text")]
                tags = []
                for x in (badges + disp):
                    if x and x not in tags:
                        tags.append(x)
                cover = (r.get("cover") or "").replace("http://", "https://")
                eps = r.get("eps") or []
                first_ep = ""
                if isinstance(eps, list) and eps:
                    first_ep = (eps[0] or {}).get("url") or ""
                page = r.get("url") or r.get("goto_url") or ""
                # ss 整季页没法直接下，指向首集；电影正片页（ep+theme=movie）本身就能下
                dl_url = page
                if "/bangumi/play/ss" in page and first_ep:
                    dl_url = first_ep
                out.append({
                    "title": title,
                    "cover": cover,
                    "url": page,
                    "dl_url": dl_url,
                    "kind": "番剧" if st == "media_bangumi" else (r.get("season_type_name") or "影视"),
                    "score": score.get("score") or 0,
                    "score_count": score.get("user_count") or 0,
                    "styles": r.get("styles") or "",
                    "areas": r.get("areas") or "",
                    "desc": r.get("desc") or "",
                    "cv": (r.get("cv") or "").split("\n")[0],
                    "staff": (r.get("staff") or "").split("\n")[0],
                    "ep_size": r.get("ep_size") or 0,
                    "pubtime": r.get("fix_pubtime_str") or "",
                    "year": _year_of(r.get("pubtime")),
                    "tags": tags,
                    "vip": any(("大会员" in t or "付费" in t or "会员" in t) for t in tags),
                })
                if len(out) >= limit * 2:
                    return out
    return out


# ---------------- 合并 ----------------

def _clean_title(s):
    """豆瓣标题里混着不可见字符（U+200E 等）和年份后缀，清干净。"""
    s = (s or "").strip()
    s = re.sub(r"[-‏‪-‮﻿]", "", s)
    s = re.sub(r"\s*[(（]\s*(19|20)\d{2}\s*[)）]\s*$", "", s).strip()
    s = re.sub(r"\s+", " ", s)
    return s


def _norm(s):
    """归一化片名用于匹配：去空格标点、去括号补充说明。"""
    s = (s or "").strip().lower()
    s = re.sub(r"[（(\[【].*?[)）\]】]", "", s)
    s = re.sub(r"[\s\-—_·:：,，.。!！?？'\"“”‘’]+", "", s)
    return s


def _match(douban_title, bili_title, douban_year="", bili_year=""):
    """判断两部是不是同一部片子。

    - 完全一致（归一化后）直接算
    - 一方包含另一方：「哪吒」vs「哪吒之魔童降世」
    - 续集要排除：「流浪地球」vs「流浪地球2」不是同一部
    - 前缀不同要排除：「我的三体」vs「三体」不是同一部
    - 年份：只在两边都是「上映/播出年份」时才有意义。
      B站的 pubtime 是资源上线时间，跟豆瓣的上映年对不上（三体 2023 的剧
      2022 年就在 B站上线了），所以这里只当弱信号——差 1 年以内照旧算同一部。
    """
    a, b = _norm(douban_title), _norm(bili_title)
    if not a or not b:
        return False
    year_gap = None
    if douban_year and bili_year:
        try:
            year_gap = abs(int(douban_year) - int(bili_year))
        except Exception:
            year_gap = None
    if a == b:
        # 同名的两部片子（《哪吒》有 79/83/19 多个版本），差得远才排除
        if year_gap is not None and year_gap > 1:
            return False
        return True
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    if len(short) < 2 or short not in long:
        return False
    # 「我的三体」vs「三体」：短串不在长串开头，是另一部片子的标题
    if not long.startswith(short):
        return False
    # 短串是长串的前缀时，长的多出来的是数字（2、3、上）→ 是续集，不是同一部
    tail = long[len(short):]
    if re.match(r"^[0-9]+", tail):
        return False
    # 年份差太远说明是两部不同的片子
    if year_gap is not None and year_gap > 1:
        return False
    return True


def bili_media_info(ep_or_ss_url):
    """按 ep/ss 链接查番剧元信息：片名、导演、主演、封面、集数。

    番剧页 yt-dlp 只给「正片」和一个数字 id，文件名和作者都难看，
    这里补上真实信息。
    """
    s = str(ep_or_ss_url or "")
    m = re.search(r"(ep|ss)(\d+)", s)
    if not m:
        return {}
    key = "ep_id" if m.group(1) == "ep" else "season_id"
    try:
        raw = _get("https://api.bilibili.com/pgc/view/web/season?%s=%s" % (key, m.group(2)),
                   "https://www.bilibili.com/")
        d = json.loads(raw)
    except Exception:
        return {}
    r = d.get("result") or {}
    if not r.get("title"):
        return {}
    staff = (r.get("staff") or "").split("\n")
    director = ""
    # 有的是「导演:」，有的是「总导演:」——都算数
    for line in staff:
        if "导演" in line and ":" in line or "导演" in line and "：" in line:
            name = re.split(r"[:：]", line, 1)[-1].strip()
            name = re.sub(r"^(总)?导演", "", name).strip()
            if name:
                director = name
                break
    actors = [x.strip() for x in (r.get("actors") or "").split("\n") if x.strip()]
    # 番剧的 actors 是「角色：配音」格式，作者位放配音演员更像样
    names = []
    for x in actors[:8]:
        n = re.split(r"[:：]", x, 1)[-1].strip() if re.search(r"[:：]", x) else x
        if n and n not in names:
            names.append(n)
    # 作者位：有导演用导演（电影），番剧没导演就用配音主演
    credit = director or (" / ".join(names[:3]))
    return {
        "title": r.get("title") or "",
        "director": director,
        "actors": " / ".join(names[:5]),
        "credit": credit,
        "cover": (r.get("cover") or "").replace("http://", "https://"),
        "ep_count": len(r.get("episodes") or []),
        "url": r.get("link") or "",
    }


def bili_episodes(url_or_ssid, limit=30):
    """拿番剧的集数列表。

    传 ss 链接（.../bangumi/play/ss28747）或 season_id 都能取到，
    返回 [{id, title, url, cover}]，用户可以挑单集下。
    """
    ssid = str(url_or_ssid or "")
    m = re.search(r"ss(\d+)", ssid)
    if not m:
        m = re.search(r"season_id=(\d+)", ssid)
    if not m:
        return []
    sid = m.group(1)
    try:
        raw = _get("https://api.bilibili.com/pgc/view/web/season?season_id=" + sid,
                   "https://www.bilibili.com/")
        d = json.loads(raw)
    except Exception:
        return []
    eps = ((d.get("result") or {}).get("episodes")) or []
    out = []
    for e in eps[:limit]:
        eid = e.get("id")
        if not eid:
            continue
        out.append({
            "id": eid,
            "title": e.get("long_title") or e.get("title") or str(eid),
            "index": e.get("title") or "",
            "url": "https://www.bilibili.com/bangumi/play/ep%s" % eid,
            "cover": (e.get("cover") or "").replace("http://", "https://"),
        })
    return out


def bili_sources(kw, limit=8):
    """只查 B站影视区：这个片有没有正版、正片链接、是不是大会员。"""
    return _bili_media(kw, limit)


def search_movie(kw, limit=10):
    """搜电影条目。返回 (items, errors)。

    item: kind=movie，含资料字段 + bili（正版信息，可能为空）
    """
    kw = (kw or "").strip()
    if not kw:
        return [], []
    errors = []
    douban, bili = [], []

    def run_db():
        try:
            sug = _douban_suggest(kw, limit)
            for it in sug[:6]:
                det = _douban_detail(it["id"])
                it.update(det or {})
                douban.append(it)
                time.sleep(0.25)     # 别把豆瓣打急了
        except Exception as e:
            errors.append("豆瓣没搜成：%s" % str(e)[:80])

    def run_bili():
        try:
            bili.extend(_bili_media(kw, limit))
        except Exception as e:
            errors.append("B站影视区没搜成：%s" % str(e)[:80])

    ths = [threading.Thread(target=run_db), threading.Thread(target=run_bili)]
    for t in ths:
        t.start()
    for t in ths:
        t.join()

    items = []
    used_bili = set()
    for d in douban:
        d_year = str(d.get("year") or "")
        hit = None
        for i, b in enumerate(bili):
            if i in used_bili:
                continue
            if _match(d.get("title"), b.get("title"), d_year, b.get("year") or ""):
                hit = (i, b)
                break
        if hit:
            used_bili.add(hit[0])
            b = hit[1]
        else:
            b = None
        cover = _douban_img(d.get("cover") or "")
        # 豆瓣没简介时用B站的剧情简介顶上，总比空着强
        summary = (d.get("summary") or "").strip() or ((b or {}).get("desc") or "").strip()
        items.append({
            "kind": "movie",
            "title": _clean_title(d.get("title")) or _clean_title(d.get("org_title")),
            "year": d.get("year") or "",
            "cover": cover or (b or {}).get("cover") or "",
            "rating": d.get("rating") or "",
            "rating_count": d.get("rating_count") or 0,
            "director": d.get("director") or "",
            "actors": d.get("actors") or "",
            "genres": d.get("genres") or [],
            "duration": d.get("duration") or "",
            "region": d.get("region") or "",
            "summary": summary,
            "url": d.get("url") or "",
            "douban_id": d.get("id") or "",
            "bili": b,
        })
        if len(items) >= limit:
            break

    # B站有、豆瓣没搜到的（多见于新片/冷门片）也补上，别漏。
    # 但别喧宾夺主——最多补到 limit 的 1.5 倍，剩下的位置留给豆瓣条目
    cap = max(limit, int(limit * 1.5))
    for i, b in enumerate(bili):
        if i in used_bili or len(items) >= cap:
            continue
        if any(_match(b.get("title"), it.get("title"), b.get("year") or "", it.get("year") or "")
               for it in items):
            continue
        staff = (b.get("staff") or "").split(":")
        items.append({
            "kind": "movie", "title": _clean_title(b.get("title")),
            "year": b.get("pubtime") or b.get("year") or "", "cover": b.get("cover") or "",
            "rating": "", "rating_count": 0,
            "director": staff[-1].strip() if len(staff) > 1 else "",
            "actors": b.get("cv") or "",
            "genres": [x for x in (b.get("styles") or "").split("/") if x],
            "duration": "", "region": b.get("areas") or "",
            "summary": (b.get("desc") or "").strip(),
            "url": "", "douban_id": "", "bili": b,
        })
    return items[:cap], errors

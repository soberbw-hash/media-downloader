# -*- coding: utf-8 -*-
"""聚合调度：把一次关键词同时分发给多个来源，再把结果收拢成一份。

照着成熟聚合搜索工具的那套做法，但按本机这个下载器的体量裁过：
  · 并发工作池，一个源卡住不拖累别人
  · 双级超时：先 6 秒把快的源给用户，慢源在后台接着跑完再补
  · 失败隔离 + 冷却：连不上两次就歇 3 分钟，别每次都白等
  · 结果去重、打分排序、来源混排，每条都标清来自哪儿
  · 内存缓存 10 分钟，同样的话不用再问一遍所有网站
"""
import concurrent.futures as cf
import re
import threading
import time
import uuid

import sources as src_mod

FAST_DEADLINE = 6.0        # 首屏最多等这么久
FULL_DEADLINE = 32.0       # 后台最长允许跑到这么久
CACHE_TTL = 600
COOLDOWN = 120
MAX_FAILS = 2

_LOCK = threading.RLock()
_CACHE = {}        # key -> (ts, payload)
_PENDING = {}      # token -> state
_FAILS = {}        # sid -> 连续失败次数
_COOLDOWN = {}     # sid -> 冷却截止时间戳


# ---------- 归一化 / 去重 / 打分 ----------
def _norm_url(u):
    u = (u or "").split("#")[0].strip().lower()
    u = re.sub(r"^https?://(www\.)?", "", u)
    u = re.sub(r"[?&](utm_[^=]+|spm|from|src|share_source|vd_source|timestamp|_t)=[^&]*", "", u)
    return u.rstrip("/?&")


def _norm_title(t):
    return re.sub(r"[\s\-_·|,，。!！?？\[\]【】()（）]+", "", (t or "").lower())


def _key(x):
    return (x.get("source") or x.get("platform") or "") + "|" + (x.get("id") or "")


def _score(x, kw, priority, rank):
    q = (kw or "").strip().lower()
    title = (x.get("title") or "").lower()
    s = (6 - min(5, priority)) * 12.0
    if q and q in title:
        s += 30
    else:
        for w in re.split(r"[\s,，、]+", q):
            if w and len(w) > 1 and w in title:
                s += 9
    if x.get("cover"):
        s += 5
    if x.get("duration"):
        s += 3
    if x.get("desc"):
        s += 2
    if x.get("hq"):
        s += 4
    s += max(0, 12 - rank)          # 源内越靠前越相关
    return s


def _merge(items, kw, pri_map):
    """去重 → 打分 → 排序 → 同源不连着刷屏。"""
    seen_key, seen_url, seen_title, out = set(), set(), set(), []
    for x in items:
        if not isinstance(x, dict):
            continue
        k, u, t = _key(x), _norm_url(x.get("download") or x.get("url")), _norm_title(x.get("title"))
        if k in seen_key or (u and u in seen_url) or (t and t in seen_title):
            continue
        seen_key.add(k)
        if u:
            seen_url.add(u)
        if t:
            seen_title.add(t)
        out.append(x)
    pri = pri_map or {}
    for i, x in enumerate(out):
        x["_score"] = _score(x, kw, pri.get(x.get("source"), 3), i)
    out.sort(key=lambda x: -x["_score"])
    # 同一个源最多连着出现 2 条，保证「汇集」而不是一家霸屏
    res, used, last, streak = [], set(), "", 0
    while out:
        pick = None
        for i, x in enumerate(out):
            s = x.get("source") or ""
            if i in used:
                continue
            if s == last and streak >= 2:
                continue
            pick = i
            break
        if pick is None:
            pick = next((i for i in range(len(out)) if i not in used), 0)
        x = out[pick]
        used.add(pick)
        s = x.get("source") or ""
        streak = streak + 1 if s == last else 1
        last = s
        res.append(x)
        if len(res) >= len(out):
            break
    return res


def _payload(items, kw, pri_map, limit):
    """按来源轮转取结果：每个搜到的源都得露脸，别让一两家把前 24 条占满，
    不然状态行写着「Freesound 12 条」，列表里却一条都看不到。"""
    merged = _merge(items, kw, pri_map)
    groups, best = {}, {}
    for x in merged:
        s = x.get("source") or ""
        groups.setdefault(s, []).append(x)
        best[s] = max(best.get(s, 0), x.get("_score", 0))
        x.pop("_score", None)
    order = sorted(groups, key=lambda s: -best[s])
    out, i = [], 0
    while len(out) < limit:
        grew = False
        for s in order:
            if i < len(groups[s]):
                out.append(groups[s][i])
                grew = True
                if len(out) >= limit:
                    break
        if not grew:
            break
        i += 1
    return out


# ---------- 单源执行 ----------
def _run(state, src, kw, per, cfg, proxy):
    sid = src["id"]
    t0 = time.time()
    try:
        rows = src_mod.run_source(src, kw, per, cfg, proxy)
        rows = [r for r in rows if isinstance(r, dict) and (r.get("title") or r.get("url"))]
        # 标签跟来源按钮用同一个名字，别出现「按钮叫 A、结果写着 B」
        for r in rows:
            r["source"] = sid
            r["source_name"] = src["name"]
        with _LOCK:
            _FAILS[sid] = 0
            _COOLDOWN.pop(sid, None)
            state["items"].extend(rows)
            state["sources"][sid] = {
                "id": sid, "name": src["name"], "state": "ok",
                "count": len(rows), "took": round(time.time() - t0, 1),
                "msg": "" if rows else "没搜到相关结果",
            }
    except src_mod.SkipSource as e:
        with _LOCK:
            state["sources"][sid] = {
                "id": sid, "name": src["name"], "state": "skip",
                "count": 0, "took": round(time.time() - t0, 1), "msg": str(e),
            }
    except Exception as e:
        msg = str(e) or type(e).__name__
        # 人话：超时、连不上、被拒，别把一长串英文异常丢给用户
        low = msg.lower()
        net_bad = any(k in low for k in (
            "tunnel", "502", "proxyerror", "10051", "10060", "timed out",
            "timeout", "unreachable", "handshake", "握手", "name or service not known"))
        if net_bad:
            msg = "网络到不了这个站（或代理连不上），已跳过"
        elif "429" in msg:
            msg = "对方嫌问得太勤，先歇一会儿再搜"
        elif "403" in msg:
            msg = "对方网站拒绝了访问，已跳过"
        elif "401" in msg or "402" in msg:
            msg = "这个源需要登录，已跳过"
        elif any(k in low for k in ("expecting value", "json", "not json")):
            msg = "返回的内容读不懂，已跳过"
        else:
            msg = "没搜成（" + msg[:60] + "）"
        with _LOCK:
            n = _FAILS.get(sid, 0) + 1
            _FAILS[sid] = n
            # 明确的网络不通就立刻歇一会儿，别每次搜索都白等
            if n >= MAX_FAILS or net_bad:
                _COOLDOWN[sid] = time.time() + COOLDOWN
            state["sources"][sid] = {
                "id": sid, "name": src["name"], "state": "fail",
                "count": 0, "took": round(time.time() - t0, 1),
                "msg": "%s：%s" % (src["name"], msg),
            }
    finally:
        with _LOCK:
            state["done"].add(sid)


def _snapshot(state, kw, limit, token):
    with _LOCK:
        items = list(state["items"])
        srcs = dict(state["sources"])
        done = set(state["done"])
    total = set(state["wanted"])
    pending = sorted(total - done)
    errors = [v["msg"] for v in srcs.values() if v["state"] in ("fail", "skip") and v["msg"]]
    return {
        "ok": True,
        "items": _payload(items, kw, state["pri"], limit),
        "errors": errors,
        "sources": [srcs[k] for k in sorted(srcs, key=lambda x: state["order"].get(x, 99))
                    if k in srcs],
        "token": token,
        "pending": pending,
        "done": not pending,
        "cached": bool(state.get("cached")),
    }


# ---------- 对外入口 ----------
def search(kw, limit=24, source_ids=None, cfg=None, proxy=None, refresh=False):
    """搜一次。返回首屏结果；慢源会在后台继续，前端拿 token 来 /more 补齐。"""
    kw = (kw or "").strip()
    cfg = cfg or {}
    if not kw:
        return {"ok": False, "error": "先输入关键词", "items": []}
    allsrc = src_mod.all_sources(cfg)
    if source_ids:
        wanted = [s for s in allsrc if s["id"] in source_ids]
    else:
        # 「全部」不拉那些十几秒才回的源；想用就点它自己的按钮单独搜
        wanted = [s for s in allsrc if not s.get("slow")]
    if not wanted:
        return {"ok": False, "error": "没有可用的来源", "items": []}

    key = (kw, tuple(s["id"] for s in wanted), limit)
    now = time.time()
    if not refresh:
        with _LOCK:
            hit = _CACHE.get(key)
            if hit and now - hit[0] < CACHE_TTL:
                return dict(hit[1], cached=True)

    # 冷却中的源先不跑，明确告诉用户
    skipped_note = {}
    active = []
    for s in wanted:
        until = _COOLDOWN.get(s["id"], 0)
        if until > now:
            skipped_note[s["id"]] = {
                "id": s["id"], "name": s["name"], "state": "skip", "count": 0,
                "took": 0, "msg": "%s 刚才连不上，先歇一会儿（约 %d 秒后自动再试）"
                                  % (s["name"], int(until - now)),
            }
        else:
            active.append(s)

    token = uuid.uuid4().hex[:16]
    per = min(12, max(6, limit))
    state = {
        "kw": kw, "limit": limit, "pri": {s["id"]: s["priority"] for s in allsrc},
        "order": {s["id"]: i for i, s in enumerate(allsrc)},
        "wanted": [s["id"] for s in active],
        "cache_key": key,
        "items": [], "sources": dict(skipped_note),
        "done": set(skipped_note), "cached": False, "ts": now,
    }
    with _LOCK:
        _PENDING[token] = state
        # 只留最近 30 次查询，别让内存一直涨
        if len(_PENDING) > 30:
            for k in sorted(_PENDING, key=lambda k: _PENDING[k]["ts"])[:-30]:
                _PENDING.pop(k, None)

    if active:
        pool = cf.ThreadPoolExecutor(max_workers=min(8, len(active)))
        futs = [pool.submit(_run, state, s, kw, per, cfg, proxy) for s in active]
        pool.shutdown(wait=False)
        cf.wait(futs, timeout=FAST_DEADLINE)
    out = _snapshot(state, kw, limit, token)
    with _LOCK:
        _CACHE[key] = (now, dict(out, cached=False))
    return out


def more(token):
    """取回慢源后台补出来的新结果。"""
    with _LOCK:
        state = _PENDING.get(token)
    if not state:
        return {"ok": False, "error": "这次搜索已经过期，再搜一次就好", "items": [], "done": True}
    out = _snapshot(state, state["kw"], state["limit"], token)
    with _LOCK:
        if out["done"]:
            # 完成后把整份结果刷新进缓存，下次直接给全的
            _CACHE[state["cache_key"]] = (
                time.time(), dict(out, pending=[], done=True, cached=False))
    return out


def clear_cache():
    with _LOCK:
        _CACHE.clear()

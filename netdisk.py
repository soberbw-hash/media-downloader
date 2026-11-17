# -*- coding: utf-8 -*-
"""网盘分享链接解析 —— 粘贴分享链接，换成直链交给下载引擎。

参考云析 YunX（AGPL-3.0）的接口思路，用 Python 独立重写，不含其代码。
只解析用户自己提供的分享链接，不存储、不上传任何文件。

链路：分享链接/整段文案 → 识别平台+提取码 → 各家解析出文件列表 → 取直链 → 下载。
目前实现：蓝奏云（匿名，单文件/文件夹）。其余平台陆续补。
"""
import gzip
import http.cookiejar
import json
import os
import re
import time
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


# ---------- 平台定义 ----------
P_LANZOU = "lanzou"
P_QUARK = "quark"
P_UC = "uc"
P_XUNLEI = "xunlei"
P_BAIDU = "baidu"
P_C139 = "c139"
P_123 = "123pan"
P_115 = "pan115"
P_GUANGYA = "guangya"

PLATFORM_NAMES = {
    P_LANZOU: "蓝奏云", P_QUARK: "夸克网盘", P_UC: "UC网盘",
    P_XUNLEI: "迅雷网盘", P_BAIDU: "百度网盘", P_C139: "139云盘",
    P_123: "123云盘", P_115: "115网盘", P_GUANGYA: "光鸭云盘",
}

# 已实现解析的平台（其余识别得到但会给出「还没做好」的明确提示）
SUPPORTED = {P_LANZOU}


# ---------- 链接识别 ----------
_URL_RE = re.compile(r"https?://[^\s]+", re.I)
_PWD_URL_RE = re.compile(r"[?&](?:pwd|pass|passcode|password)=([A-Za-z0-9]+)", re.I)
_PWD_TEXT_RE = re.compile(r"(?:提取码|访问码|密码)[：:]\s*([A-Za-z0-9]{4,8})")

# 每种平台：(正则, 提取组, 是否在 share_id 前去掉首字符)
_SHARE_PATTERNS = [
    (P_QUARK, re.compile(r"pan\.quark\.cn/s/([A-Za-z0-9]+)", re.I), 0),
    (P_UC, re.compile(r"drive\.uc\.cn/s/([A-Za-z0-9]+)", re.I), 0),
    (P_XUNLEI, re.compile(r"pan\.xunlei\.com/s/([A-Za-z0-9_-]+)", re.I), 0),
    (P_BAIDU, re.compile(r"pan\.baidu\.com/s/(1[A-Za-z0-9_-]+)", re.I), 1),  # surl 去掉开头 1
    (P_C139, re.compile(r"yun\.139\.com/shareweb/.*?/w/i/([A-Za-z0-9_-]+)", re.I), 0),
    (P_123, re.compile(r"123(?:865|pan)\.(?:com|cn)/s/([A-Za-z0-9]+-[A-Za-z0-9]+)", re.I), 0),
    (P_123, re.compile(r"share\.123pan\.cn/123pan/([A-Za-z0-9-]+)", re.I), 0),
    (P_123, re.compile(r"api/srr\?sk=([A-Za-z0-9-]+)", re.I), 0),
    (P_115, re.compile(r"115(?:cdn|rc)?\.com/s/(sw[A-Za-z0-9]+)", re.I), 0),
    (P_GUANGYA, re.compile(r"guangyapan\.com/s/([A-Za-z0-9_-]+)", re.I), 0),
    # 蓝奏优享版无解析接口，归到 lanzou 后面由解析层给出提示
    (P_LANZOU, re.compile(r"(?:^|[/.])ilanzou\.(?:com|net|org|cn)/s/([A-Za-z0-9_-]+)", re.I), 0),
    (P_LANZOU, re.compile(r"(?:^|[/.])(?:lanzou[a-z0-9-]*|lan[zs]o[ux])\.(?:com|net|org|cn)/([A-Za-z0-9]+)", re.I), 0),
]


def parse_share(text):
    """从分享链接或整段文案里认出平台。返回 dict 或 None。

    {'platform', 'name', 'share_id', 'pwd', 'url'} —— pwd 是提取码（可能为空）。
    """
    if not text or not isinstance(text, str):
        return None
    clean = text.strip()
    m = _URL_RE.search(clean)
    if m:
        url = m.group(0).rstrip("。，,；;)]}'\"")
    else:
        # 允许直接粘不带协议的裸域名链接
        m = re.search(r"(?:pan\.quark\.cn|drive\.uc\.cn|pan\.xunlei\.com|pan\.baidu\.com|"
                      r"yun\.139\.com|115(?:cdn|rc)?\.com|www\.123pan\.com|www\.123865\.com|"
                      r"share\.123pan\.cn|www\.guangyapan\.com|(?:www\.)?lanzou[a-z0-9-]*\."
                      r"(?:com|net|org|cn)|(?:www\.)?lan[zs]o[ux]\.(?:com|net|org|cn))/\S+", clean, re.I)
        if not m:
            return None
        url = "https://" + m.group(0).rstrip("。，,；;)]}'\"")
    for pid, regex, strip_n in _SHARE_PATTERNS:
        hit = regex.search(url)
        if not hit:
            continue
        sid = hit.group(1)
        if strip_n:
            sid = sid[strip_n:]
        pwd = _PWD_URL_RE.search(url) or _PWD_TEXT_RE.search(text)
        return {
            "platform": pid, "name": PLATFORM_NAMES.get(pid, pid),
            "share_id": sid, "pwd": pwd.group(1) if pwd else "",
            "url": url,
        }
    return None


def is_netdisk_url(text):
    return parse_share(text) is not None


# ---------- 通用 HTTP（带 CookieJar，能处理挑战 cookie 注入）----------
def _new_opener():
    cj = http.cookiejar.CookieJar()
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),   # 服从环境里的系统代理，网盘不需要梯子
        urllib.request.HTTPCookieProcessor(cj),
    ), cj


def _set_cookie(cj, name, value, host):
    cj.set_cookie(http.cookiejar.Cookie(
        0, name, value, None, False, host, False, False, "/",
        True, False, None, False, None, None, {}))


def _read_resp(resp):
    raw = resp.read()
    if raw[:2] == b"\x1f\x8b" or (resp.headers.get("Content-Encoding") or "").lower() == "gzip":
        try:
            raw = gzip.decompress(raw)
        except Exception:
            pass
    return raw


def _get(op, url, referer=None):
    h = {"User-Agent": UA}
    if referer:
        h["Referer"] = referer
    r = op.open(urllib.request.Request(url, headers=h), timeout=25)
    return r, _read_resp(r)


# ---------- 蓝奏云 ----------
# acw_sc__v2 人机校验：arg1 固定重排 + 逐字节异或（公开算法，纯字符串变换，不执行 JS）
_ACW_POS = [
    15, 35, 29, 24, 33, 16, 1, 38, 10, 9, 19, 31, 40, 27, 22, 23, 25, 13, 6, 11,
    39, 18, 20, 8, 14, 21, 32, 26, 2, 30, 7, 4, 17, 5, 3, 28, 34, 37, 12, 36,
]
_ACW_MASK = "3000176000856006061501533003690027800375"


def acw_sc_v2(arg1):
    if not arg1 or len(arg1) < 40:
        return ""
    q = [""] * 40
    for x, ch in enumerate(arg1[:40]):
        for z in range(40):
            if _ACW_POS[z] == x + 1:
                q[z] = ch
    u = "".join(q)
    out = []
    for i in range(0, 40, 2):
        try:
            out.append("%02x" % (int(u[i:i + 2], 16) ^ int(_ACW_MASK[i:i + 2], 16)))
        except ValueError:
            return ""
    return "".join(out)


def _is_lanzou_host(host):
    host = (host or "").lower()
    return bool(re.match(r"^(?:[a-z0-9-]+\.)*(?:lanzou[a-z]?|lan[zs]o[ux])\.(?:com|net|org|cn)$", host))


def _normalize_lanzou(url):
    u = url.strip()
    u = re.sub(r"^http://", "https://", u, flags=re.I)
    u = u.split("#")[0]
    return u.rstrip("/")


_LANZOU_HOST_RE = re.compile(
    r"(?:^|[/.])(?:lanzou[a-z0-9-]*|lan[zs]o[ux])\.(?:com|net|org|cn)/([A-Za-z0-9]+)", re.I)


def lanzou_resolve(share_url, pwd="", progress=None):
    """解析蓝奏分享，返回文件列表：[{'name','size','fid'}]。

    抛 RuntimeError 说明链接失效/需要提取码/被风控。progress 可选回调。
    """
    progress = progress or (lambda **kw: None)
    url = _normalize_lanzou(share_url)
    host = urllib.parse.urlparse(url).hostname or ""
    if not _is_lanzou_host(host):
        raise RuntimeError("这不是蓝奏云的分享链接")
    if "ilanzou" in host.lower():
        raise RuntimeError("蓝奏优享版暂时解析不了，请用普通蓝奏云链接")

    op, cj = _new_opener()
    progress(stage="正在打开分享页…")
    r, raw = _get(op, url, url)
    html = raw.decode("utf-8", "ignore")
    if "文件取消分享" in html or "不存在" in html:
        raise RuntimeError("这个分享已经取消或文件不存在了")
    if "请求过于频繁" in html or "稍后再试" in html:
        raise RuntimeError("蓝奏云问得太勤了，过几分钟再试")

    # 人机校验（分享页本身也可能返回 arg1）
    m1 = re.search(r"\barg1\s*=\s*'([0-9A-Fa-f]{40})'", html)
    if m1:
        val = acw_sc_v2(m1.group(1))
        if val:
            _set_cookie(cj, "acw_sc__v2", val, host)
            r, raw = _get(op, url, url)
            html = raw.decode("utf-8", "ignore")

    needs_pwd = any(k in html for k in ("请输入密码", 'id="pwbox"', "input_password", "密码不正确"))
    if needs_pwd and not pwd:
        raise RuntimeError("这个分享需要提取码，填一下再解析")
    if needs_pwd and pwd:
        # 带密码分享：蓝奏用 ajaxfile 校验，先把密码放进会话（后续取链会用到）
        pass

    title_m = re.search(r"<title>([^<]+)</title>", html)
    title = title_m.group(1).replace(" - 蓝奏云", "").strip() if title_m else "蓝奏分享"

    is_folder = any(k in html for k in ("filemoreajax", 'class="filemore"', "file_more"))
    if not is_folder:
        size_m = re.search(r"(?:文件大小|大小)\s*[:：]\s*([\d.]+)\s*([KMGT]?)(?:i?B)?", html, re.I)
        size = _parse_size(size_m.group(1) + size_m.group(2)) if size_m else 0
        return {"title": title, "is_folder": False,
                "files": [{"name": title, "size": size, "fid": ""}]}

    # 文件夹：需要 lx/fid/t/k 参数，逐页列
    def jsvar(name, src=html):
        mm = re.search(r"\bvar\s+" + re.escape(name) + r"\s*=\s*'([^']*)'", src)
        return mm.group(1) if mm else None

    lx, fid, t, k = jsvar("lx"), jsvar("fid"), jsvar("t"), jsvar("k")
    if not (lx and t and k):
        raise RuntimeError("分享页结构变了，暂时解析不了这个文件夹，请换个链接试试")
    fid = fid or urllib.parse.urlparse(url).path.rstrip("/").split("/")[-1]

    files, seen = [], set()
    for pg in range(1, 501):
        progress(stage="正在列文件…", percent=min(90, pg))
        fields = {"lx": lx, "fid": fid, "t": t, "k": k, "pwd": pwd or "", "pg": str(pg)}
        data = urllib.parse.urlencode(fields).encode()
        req = urllib.request.Request(
            "https://" + host + "/filemoreajax.php", data=data,
            headers={"User-Agent": UA, "Referer": url, "Origin": "https://" + host,
                     "Content-Type": "application/x-www-form-urlencoded"})
        try:
            body = json.loads(op.open(req, timeout=25).read().decode("utf-8", "ignore"))
        except Exception:
            if pg == 1:
                raise RuntimeError("蓝奏云列表请求失败，稍后再试")
            break
        zt = int(body.get("zt") or 0)
        if zt == 2:
            break
        if zt == 3:
            raise RuntimeError("提取码不对，检查一下再试")
        if zt == 4:
            time.sleep(0.3)
            continue
        if zt != 1:
            if pg == 1:
                raise RuntimeError("蓝奏云解析失败，请确认链接和提取码")
            break
        arr = body.get("text") or []
        if not arr:
            break
        added = 0
        for item in arr:
            iid = str(item.get("id") or "")
            if not iid or iid in seen:
                continue
            seen.add(iid)
            files.append({"name": item.get("name_all") or item.get("name") or "未命名",
                          "size": _parse_size(str(item.get("size") or "0")), "fid": iid})
            added += 1
        if not added:
            break
    if not files:
        raise RuntimeError("这个分享里没找到可下载的文件")
    return {"title": title, "is_folder": True, "files": files}


def _parse_size(text):
    m = re.search(r"([\d.]+)\s*([KMGT]?)", str(text), re.I)
    if not m:
        return 0
    try:
        n = float(m.group(1))
    except ValueError:
        return 0
    unit = m.group(2).upper()
    return int(n * {"": 1, "K": 1024, "M": 1048576,
                    "G": 1073741824, "T": 1099511627776}.get(unit, 1))


def lanzou_direct(share_url, pwd="", file_info=None, progress=None):
    """把蓝奏分享里的某个文件换成下载直链。返回 (url, filename, size, headers)。

    单文件分享 file_info 传 None；文件夹分享传 lanzou_resolve 里的某一项。
    """
    progress = progress or (lambda **kw: None)
    url = _normalize_lanzou(share_url)
    host = urllib.parse.urlparse(url).hostname or ""
    op, cj = _new_opener()

    # 分享页 -> iframe（下载参数都在 iframe 里）
    r, raw = _get(op, url, url)
    html = raw.decode("utf-8", "ignore")
    m = re.search(r'<iframe[^>]+src=["\']([^"\']+)["\']', html)
    iframe = urllib.parse.urljoin(url, m.group(1)) if m else url
    r, raw = _get(op, iframe, url)
    ih = raw.decode("utf-8", "ignore")

    def jsvar(name):
        mm = re.search(r"\bvar\s+" + re.escape(name) + r"\s*=\s*'([^']*)'", ih)
        return mm.group(1) if mm else None

    file_id = None
    if file_info and file_info.get("fid"):
        file_id = file_info["fid"]
    if not file_id:
        mm = re.search(r"(?:ajaxm|ajaxfile)\.php\?file=(\d+)", ih) or re.search(r"file=(\d+)", ih)
        file_id = mm.group(1) if mm else None
    if not file_id:
        raise RuntimeError("蓝奏解析失败，找不到文件编号，请重新复制链接")

    ajax = (jsvar("domain1") or jsvar("domain2") or
            "https://apifile.woozooo.com/ajaxfile.php?file=" + file_id)
    sign = jsvar("wp_sign") or jsvar("sign")
    ajaxdata = jsvar("ajaxdata") or ""
    if not sign:
        raise RuntimeError("蓝奏分享页缺少下载参数，请刷新后重试")

    progress(stage="正在获取下载地址…")
    fields = {"action": "downprocess", "websignkey": ajaxdata, "signs": ajaxdata,
              "sign": sign, "websign": "", "kd": jsvar("kdns") or "1", "ves": "1"}
    if pwd:
        fields = {"action": "downprocess", "p": pwd, "kd": "1", "sign": sign}
    req = urllib.request.Request(ajax, data=urllib.parse.urlencode(fields).encode(),
        headers={"User-Agent": UA, "Referer": url,
                 "Origin": "https://" + urllib.parse.urlparse(ajax).hostname,
                 "Content-Type": "application/x-www-form-urlencoded"})
    j = json.loads(op.open(req, timeout=25).read().decode("utf-8", "ignore"))
    if str(j.get("zt")) != "1" or not isinstance(j.get("url"), str):
        msg = j.get("url") if isinstance(j.get("url"), str) else ""
        raise RuntimeError(("提取码不对或已过期" if msg else "蓝奏下载地址已失效，请重新解析"))

    dom = j.get("dom") or ""
    rel = j["url"]
    if not dom or rel.startswith("/") or "://" in rel:
        raise RuntimeError("蓝奏下载地址异常，请重新解析")
    node = dom.rstrip("/") + "/file/" + rel
    node_host = urllib.parse.urlparse(node).hostname or ""
    _set_cookie(cj, "down_ip", "1", node_host)

    final = _probe_node(op, cj, node, node_host, url, progress)
    fname = (file_info or {}).get("name") or j.get("inf") or "蓝奏文件"
    return final, fname, int(j.get("size") or (file_info or {}).get("size") or 0), \
        {"Referer": url + "/", "User-Agent": UA}


def _probe_node(op, cj, node, node_host, share_url, progress):
    """跟随下载节点：处理 acw 挑战 + 「验证并下载」确认页，拿到最终文件直链。"""
    acw_done = set()      # 同一节点只处理一次 acw 挑战，避免死循环
    verified = False      # 「验证并下载」也只提交一次
    for _ in range(8):
        r, raw = _get(op, node, share_url)
        ctype = (r.headers.get("Content-Type") or "").lower()
        text = raw.decode("utf-8", "ignore")

        # 已经是文件（不是 html 页面）
        if "text/html" not in ctype and raw[:2] != b"<!":
            return node

        m1 = re.search(r"\barg1\s*=\s*'([0-9A-Fa-f]{40})'", text)
        if m1:
            if node_host in acw_done:
                raise RuntimeError("蓝奏验证没通过，请在网页里打开一次这个链接再试")
            acw_done.add(node_host)
            val = acw_sc_v2(m1.group(1))
            if val:
                _set_cookie(cj, "acw_sc__v2", val, node_host)
                continue
        if "验证并下载" in text or ("data :" in text and "'file'" in text):
            if verified:
                raise RuntimeError("蓝奏验证没通过，请在网页里打开一次这个链接再试")
            verified = True
            progress(stage="正在确认下载…")
            time.sleep(2)   # 页面按钮 2 秒后才可用，与云析一致
            dm = re.search(r"data\s*:\s*\{([^{}]*)\}", text)
            data = dm.group(1) if dm else ""
            fm = re.search(r"'file'\s*:\s*'([^']+)'", data)
            sm = re.search(r"'sign'\s*:\s*'([^']+)'", data)
            if not (fm and sm):
                fm = re.search(r'name=["\']file["\'][^>]*value=["\']([^"\']+)["\']', text)
            if fm and sm:
                ajax = urllib.parse.urljoin(node, "ajax.php")
                vf = {"file": fm.group(1), "el": "2", "sign": sm.group(1)}
                vreq = urllib.request.Request(ajax, data=urllib.parse.urlencode(vf).encode(),
                    headers={"User-Agent": UA, "Referer": node,
                             "Origin": "https://" + node_host,
                             "Content-Type": "application/x-www-form-urlencoded"})
                vj = json.loads(op.open(vreq, timeout=25).read().decode("utf-8", "ignore"))
                if str(vj.get("zt")) != "1" or not isinstance(vj.get("url"), str):
                    raise RuntimeError(str(vj.get("url") or "蓝奏验证失败，请在网页里打开一次再试"))
                u = vj["url"]
                if u.startswith("?SignError") or "SignError" in u:
                    raise RuntimeError("蓝奏验证已过期，请重新解析")
                if u.startswith("/"):
                    u = "https://" + node_host + u
                elif "://" not in u:
                    u = "https://" + node_host + "/" + u
                node = u
                continue
        raise RuntimeError("蓝奏返回的是验证页面，请在网页里打开一次这个链接再试")
    raise RuntimeError("蓝奏下载地址暂时不可用，请稍后再试")


# ---------- 统一入口 ----------
def resolve(text, pwd="", progress=None):
    """解析分享链接，返回 {title, platform, files:[{name,size,fid}]}。"""
    info = parse_share(text)
    if not info:
        raise RuntimeError("没认出这是网盘分享链接")
    pid = info["platform"]
    if pid not in SUPPORTED:
        raise RuntimeError("%s的解析还在做，暂时下不了；目前支持蓝奏云" % info["name"])
    pwd = pwd or info.get("pwd") or ""
    if pid == P_LANZOU:
        res = lanzou_resolve(info["url"], pwd, progress)
        res["platform"] = pid
        res["url"] = info["url"]
        res["pwd"] = pwd
        return res
    raise RuntimeError("这个网盘暂时不支持")


def get_direct(share_info, file_info=None, progress=None):
    """从 resolve() 的结果取某个文件的直链。"""
    pid = share_info.get("platform")
    if pid == P_LANZOU:
        return lanzou_direct(share_info["url"], share_info.get("pwd") or "",
                             file_info, progress)
    raise RuntimeError("这个网盘暂时不支持下载")

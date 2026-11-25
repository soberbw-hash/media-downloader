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
import urllib.error
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
# 夸克列目录已实测可用；取直链要登录转存，见 quark_direct
SUPPORTED = {P_LANZOU, P_BAIDU, P_QUARK}

# 各平台登录态 cookie 的作用域（只在本机 cookies 目录里找这些站点的登录信息）
COOKIE_DOMAINS = {
    P_BAIDU: ".baidu.com", P_QUARK: ".quark.cn", P_UC: ".uc.cn",
    P_115: ".115.com", P_123: ".123pan.com", P_XUNLEI: ".xunlei.com",
    P_C139: ".139.com", P_GUANGYA: ".guangyapan.com", P_LANZOU: ".lanzou.com",
}


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


# ---------- 登录态 ----------
def _cookie_dir():
    try:
        import paths
        return paths.cookie_dir()
    except Exception:
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookies")


def load_cookie_header(pid):
    """读本机 cookies/<平台>.txt（浏览器扩展同步下来的），拼成 Cookie 请求头。
    没有就返回空串 —— 调用方据此提示「先去登录」，别硬着头皮去撞。"""
    parts = []
    try:
        with open(os.path.join(_cookie_dir(), "%s.txt" % pid),
                  "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if line.startswith("#") or not line.strip():
                    continue
                cols = line.rstrip("\n").split("\t")
                if len(cols) >= 7:
                    parts.append("%s=%s" % (cols[5], cols[6]))
    except Exception:
        pass
    return "; ".join(parts)


def has_login(pid):
    return bool(load_cookie_header(pid))


# ---------- 通用 HTTP（带 CookieJar，能处理挑战 cookie 注入）----------
def _new_opener(cookie="", host=""):
    cj = http.cookiejar.CookieJar()
    if cookie and host:
        for kv in cookie.split(";"):
            k, _, v = kv.strip().partition("=")
            if k and v:
                _set_cookie(cj, k.strip(), v.strip(), host)
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


# ---------- 百度网盘 ----------
# 分享页里嵌着 shareid / sign / timestamp / bdstoken，取直链全靠这几个；
# 页面匿名能打开，但列目录和取直链一定要带着自己的登录态。
_BAIDU_PAN = "https://pan.baidu.com"
_BAIDU_DL_UA = "pan.baidu.com"     # 直链只认自家 UA，拿浏览器 UA 去请求会被拒
_BAIDU_NO_LOGIN = ("百度网盘要先登录：设置 → 账号 → 百度网盘点「打开」，"
                   "登完扩展会自动同步过来，再回来贴链接")

_BAIDU_ERR = {
    -9: "这个分享要提取码，把提取码一起贴进来（或写成 链接?pwd=xxxx）",
    2: "百度接口说参数不对（多半是登录态过期或这条分享限制了下载），先在浏览器里打开确认能看到文件，再回来重试",
    112: "百度说页面过期了，重新贴一次链接再试",
    118: "百度要求人工验证，先在浏览器里打开这个分享过一次再来",
    31066: "文件不存在或已经被删了",
}


class _BaiduErr(Exception):
    def __init__(self, errno, msg=""):
        self.errno = errno
        self.msg = msg
        Exception.__init__(self, msg or str(errno))


def _json_object_at(text, idx):
    """从 idx（指向 '{'）开始按花括号配对截一个完整对象，字符串里的括号不算。"""
    depth = 0
    in_str = False
    esc = False
    end = min(len(text), idx + 200000)
    for i in range(idx, end):
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


def _json_at(text, pat):
    m = re.search(pat, text)
    if not m:
        return {}
    obj = _json_object_at(text, m.start(1))
    if not obj:
        return {}
    try:
        return json.loads(obj)
    except Exception:
        return {}


def _baidu_state(html):
    """把分享页里那几个关键字段扒出来。字段藏的位置各版本不一样，多路兜底。"""
    st = {}
    for pat in (r"locals\.mset\(\s*(\{)", r"window\.yunData\s*=\s*(\{)",
                r"window\.__INITIAL_STATE__\s*=\s*(\{)"):
        got = _json_at(html, pat)
        if got:
            st.update(got)
            # 有些版本塞在子对象里
            for v in got.values():
                if isinstance(v, dict) and v.get("shareid"):
                    st.update({k: v[k] for k in v if k not in st or not st[k]})
    if not st.get("shareid"):
        for k in ("shareid", "share_uk", "sign", "timestamp", "bdstoken"):
            m = re.search(r'"%s"\s*:\s*"?([A-Za-z0-9_.\-]+)' % k, html)
            if m:
                st[k] = m.group(1)
    # sign 现在多藏在内嵌 file_list 的每个文件条目上（顶层没有），
    # 取第一个非空 sign 兜底。真正的签名取链时会用 tplconfig 现拿，这里只是备用。
    if not st.get("sign"):
        m = re.search(r'"sign"\s*:\s*"([A-Za-z0-9]{20,})"', html)
        if m:
            st["sign"] = m.group(1)
    if not st.get("timestamp"):
        m = re.search(r'"timestamp"\s*:\s*(\d{9,})', html)
        if m:
            st["timestamp"] = m.group(1)
    return st


def _baidu_verify(op, surl, pwd):
    """过提取码，成功后 CookieJar 里会落下 BDCLND。"""
    ts = int(time.time())
    q = urllib.parse.urlencode({
        "surl": surl, "t": ts, "channel": "chunlei", "web": 1,
        "app_id": 250528, "clienttype": 0,
    })
    body = urllib.parse.urlencode({"pwd": pwd}).encode()
    req = urllib.request.Request(
        "%s/share/verify?%s" % (_BAIDU_PAN, q), data=body,
        headers={"User-Agent": UA, "Referer": "%s/s/1%s" % (_BAIDU_PAN, surl),
                 "Content-Type": "application/x-www-form-urlencoded"})
    try:
        j = json.loads(op.open(req, timeout=25).read().decode("utf-8", "ignore"))
    except Exception:
        raise RuntimeError("百度提取码校验没返回结果，稍后再试")
    if j.get("errno") != 0:
        if j.get("errno") == -12:
            raise RuntimeError("提取码不对，核对一下再试")
        raise RuntimeError("百度不让过提取码（%s），在浏览器里打开这个分享试试"
                           % (j.get("errmsg") or j.get("errno")))
    return j


def _baidu_list(op, uk, shareid, bdstoken, dirpath):
    ts = int(time.time())
    q = urllib.parse.urlencode({
        "uk": uk, "shareid": shareid, "order": "other", "desc": 1,
        "showempty": 0, "web": 1, "page": 1, "num": 100, "dir": dirpath,
        "t": ts, "channel": "chunlei", "app_id": 250528,
        "bdstoken": bdstoken or "", "clienttype": 0,
    })
    _, raw = _get(op, "%s/share/list?%s" % (_BAIDU_PAN, q), _BAIDU_PAN + "/")
    try:
        j = json.loads(raw.decode("utf-8", "ignore"))
    except Exception:
        raise RuntimeError("百度没返回文件列表，稍后再试")
    if j.get("errno"):
        raise _BaiduErr(j.get("errno"), j.get("errmsg") or "")
    return j.get("list") or []


def _baidu_walk(op, uk, shareid, bdstoken, dirpath, depth=0, acc=None):
    """递归摊平目录。百度文件夹能套很多层，超过 3 层就不再往里走。"""
    acc = acc if acc is not None else []
    for it in _baidu_list(op, uk, shareid, bdstoken, dirpath):
        if len(acc) >= 300:
            break
        name = it.get("server_filename") or it.get("filename") or "文件"
        if str(it.get("isdir")) == "1":
            if depth < 3:
                _baidu_walk(op, uk, shareid, bdstoken, it.get("path") or dirpath,
                            depth + 1, acc)
            continue
        acc.append({"name": name, "size": int(it.get("size") or 0),
                    "fid": str(it.get("fs_id") or "")})
    return acc


def _baidu_fresh_sign(op, surl):
    """从 tplconfig 现拿 sign + timestamp（只对登录态有效，约 5 分钟过期）。

    分享页里的 sign 经常会缺（尤其需要提取码的分享，过了码页面才给），
    取链这一步统一现拿，成功率比复用页面里的旧值高得多。
    拿不到就返回空，调用方退回用页面里的值，不把路堵死。
    """
    if not surl:
        return "", ""
    q = urllib.parse.urlencode({
        "surl": surl, "fields": "sign,timestamp", "channel": "chunlei",
        "web": 1, "app_id": 250528, "clienttype": 0,
    })
    req = urllib.request.Request(
        "%s/share/tplconfig?%s" % (_BAIDU_PAN, q),
        headers={"User-Agent": _BAIDU_DL_UA, "Referer": _BAIDU_PAN + "/"})
    try:
        j = json.loads(op.open(req, timeout=25).read().decode("utf-8", "ignore"))
        if not j.get("errno"):
            return str(j.get("sign") or ""), str(j.get("timestamp") or "")
    except Exception:
        pass
    return "", ""


def _baidu_dlink(op, uk, shareid, sign, timestamp, bdstoken, fs_id,
                 randsk="", surl=""):
    fresh_sign, fresh_ts = _baidu_fresh_sign(op, surl)
    if fresh_sign:
        sign, timestamp = fresh_sign, fresh_ts
    if not sign:
        raise RuntimeError("百度这个分享没给出签名，先在浏览器里打开一次这个链接再来")
    q = urllib.parse.urlencode({
        "sign": sign, "timestamp": timestamp or int(time.time()),
        "bdstoken": bdstoken or "",
        "channel": "chunlei", "clienttype": 0, "web": 1, "app_id": 250528,
    })
    form = {
        "encrypt": 0, "product": "share", "uk": uk,
        "primaryid": shareid, "fid_list": json.dumps([int(fs_id)]),
    }
    # 有提取码的分享，verify 下发的 randsk 要作为 sekey 带回去，
    # 否则百度会当没过码（errno -9 / 9019）
    if randsk:
        form["extra"] = json.dumps({"sekey": randsk})
    body = urllib.parse.urlencode(form).encode()
    req = urllib.request.Request(
        "%s/api/sharedownload?%s" % (_BAIDU_PAN, q), data=body,
        headers={"User-Agent": _BAIDU_DL_UA, "Referer": _BAIDU_PAN + "/",
                 "Content-Type": "application/x-www-form-urlencoded"})
    raw = op.open(req, timeout=30).read()
    try:
        j = json.loads(raw.decode("utf-8", "ignore"))
    except Exception:
        raise RuntimeError("百度没给出下载地址，稍后再试")
    err = j.get("errno")
    if err == 9019:
        raise RuntimeError("百度要求账号安全验证，先在浏览器里打开这个分享过一次再来")
    if err:
        raise RuntimeError(_BAIDU_ERR.get(err) or
                           "百度拒绝了取下载地址（%s），可能这条分享限制了下到本地" % err)
    lst = j.get("list") or []
    if not lst or not lst[0].get("dlink"):
        raise RuntimeError("百度没给出下载地址，可能这个文件不让第三方下载")
    return lst[0]["dlink"]


def _baidu_files_from_page(html):
    """从过了提取码的分享页内嵌 file_list 里读文件。

    百度把文件信息直接塞进过码后的页面里，字段和 /share/list 返回基本一致，
    接口被风控挡住时用它兜底。返回空列表表示页面里没有。
    """
    m = re.search(r'"file_list"\s*:\s*(\[)', html)
    if not m:
        return []
    start = m.start(1)
    depth = 0
    in_str = False
    esc = False
    end = None
    for i in range(start, len(html)):
        ch = html[i]
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
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if not end:
        return []
    try:
        arr = json.loads(html[start:end])
    except Exception:
        return []
    out = []
    for it in arr:
        if not isinstance(it, dict) or str(it.get("isdir")) == "1":
            continue
        fs = it.get("fs_id")
        if not fs:
            continue
        out.append({"name": it.get("server_filename") or it.get("filename") or "文件",
                    "size": int(it.get("size") or 0), "fid": str(fs),
                    "sign": it.get("sign") or ""})
    return out


def baidu_resolve(url, pwd="", progress=None):
    cookie = load_cookie_header(P_BAIDU)
    if not cookie:
        raise RuntimeError(_BAIDU_NO_LOGIN)
    op, _cj = _new_opener(cookie, COOKIE_DOMAINS[P_BAIDU])
    if progress:
        progress(stage="正在打开百度分享…")
    _, raw = _get(op, url, _BAIDU_PAN + "/")
    html = raw.decode("utf-8", "ignore")
    if any(m in html for m in _DEAD_MARKS):
        raise RuntimeError("这个百度分享已经失效了，换个还能打开的链接")

    st = _baidu_state(html)
    shareid = st.get("shareid")
    uk = st.get("share_uk") or st.get("uk")
    if not shareid or not uk:
        raise RuntimeError("百度分享页没读出文件信息，可能被要求人工验证；"
                           "在浏览器里打开这个分享过一次再回来贴")
    surl = ""
    m = re.search(r"/s/1([A-Za-z0-9_-]+)", url)
    if m:
        surl = m.group(1)
    bdstoken = st.get("bdstoken") or ""
    randsk = ""

    def _list_root():
        return _baidu_walk(op, uk, shareid, bdstoken, "/")

    def _page_files():
        """列目录接口被挡时，退回页面内嵌的 file_list（含 fs_id / sign）。"""
        return _baidu_files_from_page(html)

    try:
        files = _list_root()
    except _BaiduErr as e:
        # /share/list 现在经常被风控挡回 errno=2/9019，页面里其实有文件。
        # 先试页面兜底，能读就直接往下走，别一上来就报「参数不对」。
        if e.errno != -9:
            files = _page_files()
            if files:
                st = _baidu_state(html) or st
            else:
                raise RuntimeError(_BAIDU_ERR.get(e.errno) or
                                   "百度列不出文件（%s），可能这条分享限制了下到本地" % e.errno)
        else:
            # 要提取码：有就验一次，没有就让用户补
            if not pwd:
                raise RuntimeError(_BAIDU_ERR[-9])
            vj = _baidu_verify(op, surl, pwd)
            randsk = vj.get("randsk") or ""
            # 过了码以后分享页会内嵌 file_list（含 fs_id / sign），
            # 比再打 /share/list 稳——后者现在经常回 errno=2/9019
            _, raw2 = _get(op, url, _BAIDU_PAN + "/")
            html = raw2.decode("utf-8", "ignore")
            st = _baidu_state(html)
            try:
                files = _list_root()
            except _BaiduErr as e2:
                # 列目录接口还是过不去，一律用页面内嵌的文件列表兜底
                files = _page_files()
                if not files:
                    raise RuntimeError(_BAIDU_ERR.get(e2.errno) or
                                       "百度列不出文件（%s）" % e2.errno)

    if not files:
        raise RuntimeError("这个百度分享里没看到文件（空文件夹，或者要提取码）")

    title = ""
    mt = re.search(r"<title>(.*?)</title>", html, re.S)
    if mt:
        title = re.sub(r"_?免费高速下载.*$", "", mt.group(1)).strip()
    return {
        "title": title, "platform": P_BAIDU, "url": url, "pwd": pwd,
        "files": files, "is_folder": len(files) > 1,
        "_uk": uk, "_shareid": shareid, "_sign": st.get("sign") or "",
        "_timestamp": st.get("timestamp") or int(time.time()),
        "_bdstoken": bdstoken, "_randsk": randsk, "_surl": surl,
    }


def _jar_header(cj):
    return "; ".join("%s=%s" % (c.name, c.value) for c in cj)


def baidu_direct(shot, file_info=None, progress=None):
    cookie = load_cookie_header(P_BAIDU)
    if not cookie:
        raise RuntimeError(_BAIDU_NO_LOGIN)
    op, cj = _new_opener(cookie, COOKIE_DOMAINS[P_BAIDU])
    fi = file_info or (shot.get("files") or [{}])[0]
    fid = fi.get("fid")
    if not fid:
        raise RuntimeError("没拿到这个文件在百度那边的编号，重新贴一次链接")
    if progress:
        progress(stage="正在向百度要下载地址…")
    sign = shot.get("_sign")
    if not sign:
        # 页面没给签名不要紧，_baidu_dlink 会去 tplconfig 现拿；
        # 只有连 tplconfig 也拿不到才报这个错
        pass
    dlink = _baidu_dlink(op, shot["_uk"], shot["_shareid"], sign,
                         shot.get("_timestamp"), shot.get("_bdstoken"), fid,
                         randsk=shot.get("_randsk") or "",
                         surl=shot.get("_surl") or "")
    return (dlink, fi.get("name") or "", fi.get("size") or 0,
            {"User-Agent": _BAIDU_DL_UA, "Cookie": _jar_header(cj),
             "Referer": _BAIDU_PAN + "/"})


# ---------- 夸克网盘 ----------
# 实测（2026-11-18）：游客态 token + detail 能列出完整文件树（文件名/大小/fid/
# share_fid_token 都有）；但取直链的 sharepage/download 已下线（404），
# 现在必须登录 + save 转存到自己网盘 + task 轮询 + file/download。
# 所以 resolve 用游客态列目录（真能用），direct 要登录（按公开接口写，
# 本机没夸克账号，端到端未实测，失败会说清楚在哪一步）。
_QCK_API = "https://drive-pc.quark.cn/1/clouddrive"
_QCK_REF = "https://pan.quark.cn/"
_QCK_NO_LOGIN = ("夸克网盘取下载地址要登录：设置 → 账号 → 夸克网盘点「打开」，"
                 "登完扩展会自动同步过来，再贴一次链接")


def _qck_api(op, path, obj=None, params=None):
    """夸克接口统一走 JSON。obj 有值用 POST，否则 GET。返回 data 或抛错。"""
    url = _QCK_API + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    h = {"User-Agent": UA, "Referer": _QCK_REF}
    if obj is not None:
        req = urllib.request.Request(url, data=json.dumps(obj).encode(),
                                     headers=dict(h, **{"Content-Type": "application/json"}))
    else:
        req = urllib.request.Request(url, headers=h)
    try:
        raw = _read_resp(op.open(req, timeout=25))
        j = json.loads(raw.decode("utf-8", "ignore"))
    except Exception:
        raise RuntimeError("夸克没返回数据，稍后再试")
    # 夸克用 status=200 / code=0 表示成功；code 非 0 是业务错
    code = j.get("code")
    if j.get("status") != 200 or (code not in (None, 0)):
        raise RuntimeError(_qck_err(j))
    return j.get("data") or j


def _qck_err(j):
    msg = str(j.get("message") or "")
    code = j.get("code")
    if code in (41002, 41003, 41006) or "密码" in msg or "passcode" in msg.lower():
        return "这个夸克分享要提取码，把提取码一起贴进来（或写成 链接?pwd=xxxx）"
    if code in (31000, 31001, -1) or "login" in msg.lower():
        return _QCK_NO_LOGIN
    return "夸克说：%s（%s）" % (msg or "分享可能失效了", code or j.get("status"))


def _qck_detail(op, pwd_id, stoken, pdir_fid):
    return _qck_api(op, "/share/sharepage/detail", params={
        "pr": "ucpro", "fr": "pc", "pwd_id": pwd_id, "stoken": stoken,
        "pdir_fid": pdir_fid, "force": "0", "_page": 1, "_size": 100,
        "_fetch_total": 1, "_sort": "file_type:asc,updated_at:desc",
    }).get("list") or []


def quark_resolve(url, pwd="", progress=None):
    """游客态列目录：token 拿 stoken + detail 递归拿文件。取链留给 quark_direct。"""
    info = parse_share(url) or {}
    pwd_id = info.get("share_id") or ""
    if not pwd_id:
        raise RuntimeError("没认出夸克分享链接")
    op, _cj = _new_opener()
    if progress:
        progress(stage="正在打开夸克分享…")
    try:
        tok = _qck_api(op, "/share/sharepage/token",
                       {"pwd_id": pwd_id, "passcode": pwd or ""},
                       {"pr": "ucpro", "fr": "pc", "uc_param_str": ""})
    except RuntimeError:
        raise RuntimeError("这个夸克分享打不开或已经失效了，换个链接再试")
    stoken = tok.get("stoken") or ""
    if not stoken:
        raise RuntimeError("夸克没给出访问凭证，稍后再试")
    files = []
    seen = set()

    def walk(fid, depth):
        if depth > 5 or len(files) >= 300:
            return
        for it in _qck_detail(op, pwd_id, stoken, fid):
            f = str(it.get("fid") or "")
            if not f or f in seen:
                continue
            seen.add(f)
            if str(it.get("dir")) in ("1", "True", "true") or it.get("dir") is True:
                walk(f, depth + 1)
            else:
                files.append({
                    "name": it.get("file_name") or "文件",
                    "size": int(it.get("size") or 0), "fid": f,
                    "share_fid_token": it.get("share_fid_token") or "",
                })

    try:
        walk("0", 0)
    except RuntimeError as e:
        if not files:
            raise
        # 部分目录取不到就先给已经拿到的，取链时会再说
    if not files:
        raise RuntimeError("这个夸克分享里没看到文件（可能要提取码，或已经被删了）")
    return {
        "title": tok.get("title") or "夸克分享", "platform": P_QUARK,
        "url": url, "pwd": pwd, "files": files, "is_folder": len(files) > 1,
        "_pwd_id": pwd_id, "_stoken": stoken,
    }


def quark_direct(share_info, file_info=None, progress=None):
    """取直链：必须登录。save 转存 → task 轮询 → file/download。

    save 会把文件复制进用户自己的网盘（夸克的硬要求），完成后不删——
    夸克的 task 里删文件要另走接口，贸然删可能把用户自己的同名文件删掉。
    端到端需要真实夸克登录态，本机没有，代码按公开接口写，失败会说清哪一步。
    """
    cookie = load_cookie_header(P_QUARK)
    if not cookie:
        raise RuntimeError(_QCK_NO_LOGIN)
    op, _cj = _new_opener(cookie, COOKIE_DOMAINS[P_QUARK])
    fi = file_info or (share_info.get("files") or [{}])[0]
    fid = fi.get("fid")
    if not fid:
        raise RuntimeError("没拿到这个文件在夸克那边的编号，重新贴一次链接")
    if progress:
        progress(stage="正在转存到你的夸克网盘…")
    pwd_id = share_info.get("_pwd_id") or ""
    stoken = share_info.get("_stoken") or ""
    # 1) 转存到自己网盘根目录
    try:
        save = _qck_api(op, "/share/sharepage/save", {
            "fid_list": [fid],
            "fid_token_list": [fi.get("share_fid_token") or ""],
            "to_pdir_fid": "0", "pwd_id": pwd_id, "stoken": stoken,
            "pdir_fid": "0", "scene": "link",
        }, {"pr": "ucpro", "fr": "pc", "uc_param_str": ""})
    except RuntimeError as e:
        raise RuntimeError("夸克转存失败：%s" % str(e))
    task_id = save.get("task_id") if isinstance(save, dict) else ""
    if not task_id:
        raise RuntimeError("夸克没给转存任务号，取下载地址失败，稍后再试")
    # 2) 轮询任务，拿转存后的新 fid
    new_fid = ""
    for _ in range(20):
        try:
            t = _qck_api(op, "/task", params={
                "pr": "ucpro", "fr": "pc", "task_id": task_id, "retry_index": 0})
        except RuntimeError:
            break
        if str(t.get("status")) == "2":   # 2 = 完成
            info = t.get("save_as") or t.get("data") or {}
            fids = info.get("save_as_top_fids") or info.get("fid_list") or []
            if fids:
                new_fid = str(fids[0])
            break
        if str(t.get("status")) in ("3", "4"):   # 失败/取消
            break
        time.sleep(1)
    if not new_fid:
        raise RuntimeError("夸克转存还没完成或失败了，稍后再贴一次链接重试")
    # 3) 拿直链
    if progress:
        progress(stage="正在取下载地址…")
    try:
        got = _qck_api(op, "/file/download", {"fids": [new_fid]},
                       {"pr": "ucpro", "fr": "pc", "uc_param_str": ""})
    except RuntimeError as e:
        raise RuntimeError("夸克取下载地址失败：%s" % str(e))
    items = got if isinstance(got, list) else (got.get("list") or [])
    dlink = ""
    for it in items:
        if isinstance(it, dict) and (it.get("download_url") or it.get("url")):
            dlink = it.get("download_url") or it.get("url")
            break
    if not dlink:
        raise RuntimeError("夸克没给出下载地址，可能这个文件不让下，稍后再试")
    return (dlink, fi.get("name") or "", fi.get("size") or 0,
            {"User-Agent": UA, "Referer": _QCK_REF})


# ---------- 统一入口 ----------
# 没实现的平台，先花两秒看看分享本身还在不在：
# 链接失效和「我们没做」是两回事，混成一句话用户只会觉得软件坏了。
_DEAD_MARKS = ("页面不存在", "你访问的页面不存在", "分享的文件已经被取消",
               "链接错误", "来晚了", "分享已过期", "文件已经删除", "该分享已失效")
# 页面打得开、但取直链要登录态的站；如实说清缺什么，别让人以为点两下就能好
_NEED_ACCOUNT = {
    P_XUNLEI: "要登录迅雷账号才能下",
    P_C139: "要登录 139 账号才能下",
    P_115: "要登录 115 账号才能下",
    P_123: "123 的取链接口换过一版，还在重写",
    P_GUANGYA: "要登录光鸭账号才能下",
    P_UC: "目录能列出来，但拿下载地址必须登录 UC 账号",
}

# 已经能真下的平台（文案里用来告诉用户「现在能下什么」）
CAN_DOWNLOAD = "蓝奏云、百度网盘（百度要登录）、夸克（列目录免登录，下载要登录）"

# 平台名后面已经带了站名，文案里别再重复一遍「百度网盘：百度网盘…」


def _probe_share(info):
    """看看分享页还活着没。返回 'dead' / 'alive' / 'unknown'，不抛异常。"""
    url = info.get("url") or ""
    try:
        op, _ = _new_opener()
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        try:
            r = op.open(req, timeout=15)
            raw = _read_resp(r)
            code = getattr(r, "status", 200)
        except urllib.error.HTTPError as e:
            raw = e.read() or b""
            code = e.code
        html = raw.decode("utf-8", "ignore")
        if any(m in html for m in _DEAD_MARKS):
            return "dead"
        # 百度/夸克这类失效页会直接给 404；其余站 404 也可能是路径变了，按 unknown 保守处理
        if code == 404 and info.get("platform") in (P_BAIDU, P_QUARK, P_UC):
            return "dead"
        if 200 <= code < 400:
            return "alive"
    except Exception:
        pass
    return "unknown"


def resolve(text, pwd="", progress=None):
    """解析分享链接，返回 {title, platform, files:[{name,size,fid}]}。"""
    info = parse_share(text)
    if not info:
        raise RuntimeError("没认出这是网盘分享链接")
    pid = info["platform"]
    if pid not in SUPPORTED:
        name = info["name"]
        # 先分清「链接没了」和「我们没做」，这两种给用户的出路完全不同
        if _probe_share(info) == "dead":
            raise RuntimeError("%s这个分享已经失效了（对方页面说页面不存在/已取消），"
                               "换个还能打开的链接再试" % name)
        why = _NEED_ACCOUNT.get(pid)
        if why:
            raise RuntimeError("%s：%s，暂时还下不了；现在能下的是%s"
                               % (name, why, CAN_DOWNLOAD))
        raise RuntimeError("%s的解析还在做，暂时下不了；现在能下的是%s"
                           % (name, CAN_DOWNLOAD))
    pwd = pwd or info.get("pwd") or ""
    if pid == P_LANZOU:
        res = lanzou_resolve(info["url"], pwd, progress)
        res["platform"] = pid
        res["url"] = info["url"]
        res["pwd"] = pwd
        return res
    if pid == P_BAIDU:
        return baidu_resolve(info["url"], pwd, progress)
    if pid == P_QUARK:
        res = quark_resolve(info["url"], pwd, progress)
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
    if pid == P_BAIDU:
        return baidu_direct(share_info, file_info, progress)
    if pid == P_QUARK:
        return quark_direct(share_info, file_info, progress)
    raise RuntimeError("这个网盘暂时不支持下载")

# -*- coding: utf-8 -*-
"""音视频下载器 - 核心引擎

平台识别 / 抖音解析 / yt-dlp 封装 / 任务队列
"""
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib

import paths

try:
    import yt_dlp
except ImportError:
    yt_dlp = None

UA_M = ("Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1")
UA_PC = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
         "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

BASE = os.path.dirname(os.path.abspath(__file__))

# ---------- 平台定义 ----------
# level: full = 开箱可用 / cookie = 需要登录 cookies / experimental = 实验性
#        unsupported = 无公开接口
PLATFORMS = [
    {"id": "douyin", "name": "抖音", "level": "full",
     "hosts": ["douyin.com", "iesdouyin.com"]},
    {"id": "bilibili", "name": "B站", "level": "full",
     "hosts": ["bilibili.com", "b23.tv"]},
    {"id": "xiaohongshu", "name": "小红书", "level": "cookie",
     "hosts": ["xiaohongshu.com", "xhslink.com", "xhslink.cn"]},
    {"id": "kuaishou", "name": "快手", "level": "experimental",
     "hosts": ["kuaishou.com", "gif.kuaishou.com", "kwai.com", "chenzhongtech.com"]},
    {"id": "weibo", "name": "微博", "level": "full",
     "hosts": ["weibo.com", "weibo.cn", "t.cn"]},
    {"id": "ixigua", "name": "西瓜视频", "level": "full",
     "hosts": ["ixigua.com"]},
    {"id": "acfun", "name": "AcFun", "level": "full",
     "hosts": ["acfun.cn"]},
    {"id": "xinpianchang", "name": "新片场", "level": "experimental",
     "hosts": ["xinpianchang.com"]},
    {"id": "tiktok", "name": "TikTok", "level": "full",
     "hosts": ["tiktok.com"]},
    {"id": "instagram", "name": "Instagram", "level": "cookie",
     "hosts": ["instagram.com", "instagr.am"]},
    {"id": "youtube", "name": "YouTube", "level": "full",
     "hosts": ["youtube.com", "youtu.be"]},
    {"id": "twitter", "name": "X", "level": "full",
     "hosts": ["twitter.com", "x.com", "t.co"]},
    {"id": "vimeo", "name": "Vimeo", "level": "full",
     "hosts": ["vimeo.com"]},
    {"id": "twitch", "name": "Twitch", "level": "full",
     "hosts": ["twitch.tv"]},
    {"id": "reddit", "name": "Reddit", "level": "full",
     "hosts": ["reddit.com", "redditmedia.com", "redd.it"]},
    {"id": "weixin", "name": "视频号", "level": "needs-api",
     "hosts": ["channels.weixin.qq.com", "weixin.qq.com", "finder.video.qq.com",
               "findermp.video.qq.com", "wxapp.tc.qq.com"]},
    # —— 音乐：走 music.py，不进 yt-dlp ——
    {"id": "netease", "name": "网易云音乐", "level": "full", "music": True,
     "hosts": ["music.163.com"]},
    {"id": "qqmusic", "name": "QQ音乐", "level": "cookie", "music": True,
     "hosts": ["y.qq.com", "i.y.qq.com", "c.y.qq.com"]},
    {"id": "kugou", "name": "酷狗音乐", "level": "experimental", "music": True,
     "hosts": ["kugou.com", "m.kugou.com", "wwwapi.kugou.com"]},
    # —— AI 音乐：贴 Suno 分享链接下载，走 suno.py ——
    {"id": "suno", "name": "Suno", "level": "full",
     "hosts": ["suno.com", "suno.ai", "studio-api.prod.suno.com",
               "cdn1.suno.ai", "cdn2.suno.ai"]},
    # —— 直链 / HLS：没有固定域名，按后缀和参数判断，走 hls.py ——
    {"id": "direct", "name": "直链 / HLS", "level": "full", "hosts": []},
]
PINFO = {p["id"]: p for p in PLATFORMS}

#: (平台, 歌曲 id) → 一条能被 classify 认出来的链接
MUSIC_URL_TPL = {
    "netease": "https://music.163.com/song?id=%s",
    "qqmusic": "https://y.qq.com/n/ryqq/songDetail/%s",
    "kugou": "https://www.kugou.com/song/#hash=%s",
}


def music_song_url(platform, sid):
    tpl = MUSIC_URL_TPL.get(platform)
    if not tpl or not sid:
        return ""
    return tpl % sid


_ALL_HOSTS = sorted({h for p in PLATFORMS for h in p["hosts"]}, key=len, reverse=True)

# RFC3986 可见字符，只吃 ASCII。老的 \w 会把后面的中文一起吞进链接
_UCH = r"A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%"
URL_RE = re.compile(r"https?://[%s]+" % _UCH, re.I)
_BARE_RE = re.compile(
    "(?<![A-Za-z0-9@._%/-])((?:[A-Za-z0-9-]+\\.)*(?:"
    + "|".join(re.escape(h) for h in _ALL_HOSTS)
    + r")(?::\d+)?(?:/[" + _UCH + r"]*)?)",
    re.I)

_TRAIL = ".,;:!?)]}'\"<>，。、；：！？）】》」』…–—"


def _split(url):
    u = url if re.match(r"^[a-z][a-z0-9+.-]*://", url or "", re.I) else "https://" + (url or "")
    try:
        return urllib.parse.urlsplit(u)
    except Exception:
        return urllib.parse.urlsplit("https://example.invalid")


def _normalize(u):
    u = u.strip().rstrip(_TRAIL)
    if not re.match(r"^[a-z][a-z0-9+.-]*://", u, re.I):
        u = "https://" + u
    return u


def find_urls(text):
    """从任意粘贴文本里抽出所有链接（含不带 http:// 的裸域名），按出现顺序去重"""
    text = text or ""
    spans, found = [], []
    for m in URL_RE.finditer(text):
        spans.append((m.start(), m.end()))
        found.append((m.start(), _normalize(m.group(0))))
    for m in _BARE_RE.finditer(text):
        # 已经被 http(s):// 那条规则覆盖的，跳过
        if any(a <= m.start() < b for a, b in spans):
            continue
        found.append((m.start(), _normalize(m.group(1))))
    out = []
    for _, u in sorted(found, key=lambda x: x[0]):
        if u not in out:
            out.append(u)
    return out


def host_of(url):
    return (_split(url).hostname or "").lower()


def detect_platform(url):
    """按域名精确匹配，避免 t.co 误伤 test.com 这类子串问题"""
    h = host_of(url)
    for p in PLATFORMS:
        for d in p["hosts"]:
            if h == d or h.endswith("." + d):
                return p["id"], p["name"], p["level"]
    # 域名不认识不代表下不了：m3u8 / mp4 这类直链单独处理，
    # 否则会掉进 yt-dlp 的通用模式，既没有码率选择也不会带对 Referer
    try:
        import hls
        if hls.looks_like(url):
            return "direct", "直链 / HLS", "full"
    except Exception:
        pass
    return "unknown", "未知平台", "unknown"


# ---------- 视频身份 & 链接分类 ----------
# 同一个视频的短链 / 带参数链接，尽量归一到同一个 key，用来去重
KEY_PATTERNS = {
    "douyin": [r"/video/(\d+)", r"/note/(\d+)", r"modal_id=(\d+)", r"/share/(?:video|note)/(\d+)"],
    "bilibili": [r"(BV[0-9A-Za-z]{10})", r"/av(\d+)"],
    "xiaohongshu": [r"/(?:explore|discovery/item)/([0-9A-Za-z]{16,})"],
    "kuaishou": [r"/short-video/([\w-]+)", r"/photo/([\w-]+)", r"photoId=([\w-]+)"],
    "weibo": [r"/status/(\w+)", r"/tv/show/([\w:]+)", r"fid=(\d+:\d+)"],
    "tiktok": [r"/video/(\d+)"],
    "ixigua": [r"/video/(\d+)"],
    "acfun": [r"/v/(ac\d+)", r"/bangumi/(aa\d+)"],
    "xinpianchang": [r"/a(\d+)"],
    "vimeo": [r"vimeo\.com/(\d+)"],
    "twitch": [r"/videos/(\d+)"],
    "instagram": [r"/(?:p|reel|tv)/([\w-]+)"],
    "youtube": [r"(?:[?&]v=|youtu\.be/|/shorts/|/embed/)([\w-]{11})"],
    "twitter": [r"/status(?:es)?/(\d+)"],
    "reddit": [r"/comments/([a-z0-9]+)", r"redd\.it/([a-z0-9]+)"],
    "weixin": [r"/sph/([A-Za-z0-9_-]{4,})", r"encfilekey=([A-Za-z0-9]{16,})"],
    # 音乐：不补这组的话，所有网易云的歌都会算成同一个 key（play=id 在 query 里）
    "netease": [r"/song.*?[?&#]id=(\d+)", r"/song/(\d+)"],
    "qqmusic": [r"songDetail/([0-9A-Za-z]+)", r"songmid=([0-9A-Za-z]+)"],
    "kugou": [r"hash=([0-9a-fA-F]{32})", r"/song/([0-9a-fA-F]{32})"],
    "suno": [r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"],
}

# 这些平台的服务器在国内直连不到，需要走代理
OVERSEAS = {"youtube", "twitter", "tiktok", "instagram", "vimeo", "twitch", "reddit",
            "suno"}

# 明显不是「单个视频」的链接，早点说清楚，别让用户等一轮解析
NOT_VIDEO = {
    "douyin": [
        (r"/user/", "这是抖音主页，不是单条作品"),
        (r"/(?:collection|mix)", "这是合集 / 收藏页，请贴单条作品链接"),
        (r"/(?:discover|hot|search|jingxuan|follow)", "这是抖音的推荐 / 搜索页，不是单条作品"),
    ],
    "bilibili": [
        (r"//space\.bilibili\.com", "这是 UP 主空间页，不是单个视频"),
        (r"//live\.bilibili\.com", "B站直播间，不是视频"),
        (r"//search\.bilibili\.com", "这是搜索页"),
        (r"/space/", "这是 UP 主空间页，不是单个视频"),
        (r"/list/", "这是播放列表，请贴单集视频链接"),
        # 番剧/影视正片页能正常下（yt-dlp 走 B站 pgc 接口）。
        # 只拦整季页（ss 开头的）——那是一堆集数，得让用户挑单集
        (r"/bangumi/play/ss\d+", "这是番剧整季页，请从电影卡里挑单集下"),
    ],
    "xiaohongshu": [
        (r"/user/profile/", "这是小红书主页，不是单篇笔记"),
        (r"/search", "这是搜索结果页"),
    ],
    "kuaishou": [
        (r"/profile/", "这是快手主页，不是单个作品"),
    ],
    "weibo": [
        (r"weibo\.com/u/", "这是微博主页，不是单条微博"),
        (r"weibo\.com/n/", "这是微博话题 / 榜单页"),
    ],
    "weixin": [
        (r"/web/pages/(?:home|profile|search)", "这是视频号首页 / 个人页，不是单条视频"),
        (r"channels\.weixin\.qq\.com/platform", "这是视频号助手后台，不是单条视频"),
    ],
    "youtube": [
        (r"/@", "这是 YouTube 频道主页"),
        (r"/channel/", "这是 YouTube 频道主页"),
        (r"/playlist", "这是播放列表，请贴单个视频链接"),
        (r"/results", "这是搜索结果页"),
    ],
    "twitter": [
        (r"//(?:www\.)?(?:x|twitter)\.com/(?:home|explore|search|notifications|messages)\b",
         "这是 X 的首页 / 搜索页"),
    ],
    "tiktok": [
        (r"/@[\w.-]+/?$", "这是 TikTok 主页"),
    ],
    "instagram": [
        (r"instagram\.com/(?!p/|reel/|tv/|stories/)[\w.]+/?$", "这是 Instagram 主页"),
    ],
    "reddit": [
        (r"/r/[\w-]+/?$", "这是 Reddit 版块列表页，请贴具体帖子链接"),
        (r"/r/[\w-]+/(?:hot|new|top|rising|controversial|best|gilded)/?(?:[?#].*)?$",
         "这是 Reddit 版块列表页，请贴具体帖子链接"),
        (r"/user/[\w-]+/?$", "这是 Reddit 用户主页"),
        (r"/u/[\w-]+/?$", "这是 Reddit 用户主页"),
        (r"/(?:search|wiki|settings|chat|message|submit|login|register)\b",
         "这是 Reddit 的功能页，不是帖子"),
        (r"^https?://(?:www\.)?reddit\.com/?$", "这是 Reddit 首页，请贴具体帖子链接"),
    ],
}


def video_key(url):
    pid, _, _ = detect_platform(url)
    if pid == "unknown":
        p = _split(url)
        segs = [s for s in p.path.split("/") if s]
        return "generic:%s/%s" % ((p.hostname or "").lower(), "/".join(segs[-2:]))
    for pat in KEY_PATTERNS.get(pid, []):
        m = re.search(pat, url or "", re.I)
        if m:
            return "%s:%s" % (pid, m.group(1).lower())
    segs = [s for s in _split(url).path.split("/") if s]
    token = "/".join(segs[-2:]) if segs else host_of(url)
    return "%s:%s" % (pid, token.lower())


def classify(url):
    """返回 (pid, 平台名, level, 不能下的原因)。原因空字符串表示可以下。"""
    pid, pname, level = detect_platform(url)
    if pid == "unknown":
        # 不认识不代表下不了：交给 yt-dlp 的通用识别去试一遍
        return pid, "其它站点", "generic", ""
    if level == "unsupported":
        return pid, pname, level, ("这个站没有公开接口，下不了。"
                                   "能下的话，手机端保存到相册或录屏。")
    for pat, why in NOT_VIDEO.get(pid, []):
        if re.search(pat, url, re.I):
            return pid, pname, level, why
    return pid, pname, level, ""



def safe_name(s, limit=60):
    s = re.sub(r'[\\/:*?"<>|\r\n\t]', " ", s or "")
    s = re.sub(r"#\S+", "", s)
    s = re.sub(r"\s+", " ", s).strip(" .")
    return s[:limit].strip(" .-_") or "video"


def unique_path(path):
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    i = 2
    while os.path.exists("%s (%d)%s" % (base, i, ext)):
        i += 1
    return "%s (%d)%s" % (base, i, ext)


#: 命名模板里能用的占位符。空模板 = 沿用各解析器自己的命名。
NAME_TOKENS = ("title", "author", "platform", "date", "time", "id", "kind")
DEFAULT_NAME_TPL = ""


def render_name(tpl, task):
    """把命名模板渲染成文件名（不含扩展名）。不认识的占位符原样留着，方便用户自己判断。"""
    if not (tpl or "").strip():
        return ""
    ctx = {
        "title": getattr(task, "title", "") or "未命名",
        "author": getattr(task, "author", "") or "未知作者",
        "platform": getattr(task, "platform_name", "") or getattr(task, "platform", "") or "其他",
        "date": time.strftime("%Y%m%d", time.localtime(getattr(task, "created", time.time()))),
        "time": time.strftime("%H%M%S", time.localtime(getattr(task, "created", time.time()))),
        "id": getattr(task, "id", ""),
        "kind": {"images": "图集", "audio": "音乐"}.get(getattr(task, "kind", ""), "视频"),
    }
    out = tpl
    for k, v in ctx.items():
        out = out.replace("{" + k + "}", str(v))
    out = re.sub(r'[\\/:*?"<>|\r\n\t]', " ", out)
    out = re.sub(r"\s+", " ", out).strip(" .")
    return out[:120].strip(" .-_")


COVER_EXTS = (".jpg", ".jpeg", ".webp", ".png")


def save_cover(thumb_url, base_path, headers=None):
    """把封面图存到同名 .cover.jpg 旁边。失败就当没这回事，绝不影响主任务。"""
    if not thumb_url:
        return ""
    for ext in COVER_EXTS:
        if os.path.exists(base_path + ext):
            return base_path + ext
    try:
        req = urllib.request.Request(thumb_url, headers=headers or {"User-Agent": UA_PC})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=20) as r:
            data = r.read()
            ctype = (r.headers.get("Content-Type") or "").lower()
        if len(data) < 800:
            return ""
        ext = ".jpg"
        if "webp" in ctype:
            ext = ".webp"
        elif "png" in ctype:
            ext = ".png"
        with open(base_path + ext, "wb") as f:
            f.write(data)
        return base_path + ext
    except Exception:
        return ""


# ---------- B站弹幕 ----------
_BILI_HDR = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
    "Referer": "https://www.bilibili.com/",
}


def bili_cid(url):
    """拿 B 站视频的 cid（弹幕按这个编号）。分P视频按 ?p= 选对那一页。拿不到返回 0。"""
    url = url or ""
    m = re.search(r"(BV[0-9A-Za-z]{10})", url)
    q = {"bvid": m.group(1)} if m else None
    if not q:
        m = re.search(r"/av(\d+)", url)
        if m:
            q = {"aid": m.group(1)}
    if not q:
        # b23.tv 这类短链里没有 BV 号，跟一次跳转再看
        try:
            r = http_get(url, _BILI_HDR, timeout=12)
            final = r.geturl() or url
            r.close()
        except Exception:
            return 0
        m = re.search(r"(BV[0-9A-Za-z]{10})", final) or re.search(r"/av(\d+)", final)
        if not m:
            return 0
        q = {"bvid": m.group(1)} if m.group(0).startswith("BV") else {"aid": m.group(1)}
        url = final
    p = re.search(r"[?&]p=(\d+)", url)
    want = int(p.group(1)) if p else 1
    try:
        api = "https://api.bilibili.com/x/web-interface/view?%s" % urllib.parse.urlencode(q)
        r = http_get(api, _BILI_HDR, timeout=15)
        d = json.loads(r.read().decode("utf-8", "replace") or "{}")
        r.close()
    except Exception:
        return 0
    data = d.get("data") or {}
    pages = data.get("pages") or []
    for pg in pages:
        if int(pg.get("page") or 0) == want:
            return int(pg.get("cid") or 0)
    return int(data.get("cid") or 0)


def save_danmaku(url, base_path):
    """把 B 站弹幕存成同名 .xml。失败就当没这回事，绝不影响主任务。"""
    out = base_path + ".xml"
    if os.path.exists(out):
        return out
    cid = bili_cid(url)
    if not cid:
        return ""
    try:
        r = http_get("https://comment.bilibili.com/%d.xml" % cid, _BILI_HDR, timeout=25)
        raw = r.read()
        r.close()
    except Exception:
        return ""
    # 接口给的是 raw deflate（Content-Encoding: deflate 但没包 zlib 头）
    try:
        text = zlib.decompress(raw).decode("utf-8", "replace")
    except Exception:
        try:
            text = zlib.decompress(raw, -15).decode("utf-8", "replace")
        except Exception:
            text = raw.decode("utf-8", "replace")
    if "<d " not in text and "<i>" not in text:
        return ""                    # 不是弹幕 XML，别存垃圾
    out = base_path + ".xml"
    try:
        with open(out, "w", encoding="utf-8") as f:
            f.write(text)
        return out
    except OSError:
        return ""


def save_desc_txt(task, path):
    """标题 / 作者 / 链接 / 简介写成同名 .txt，方便日后翻。失败返回 ""。"""
    t = task
    dur = int(getattr(t, "duration", 0) or 0)
    lines = [
        "标题：%s" % (getattr(t, "title", "") or os.path.splitext(os.path.basename(path))[0]),
        "作者：%s" % (getattr(t, "author", "") or "未知"),
        "平台：%s" % (getattr(t, "platform_name", "") or ""),
        "链接：%s" % (getattr(t, "url", "") or ""),
    ]
    if dur:
        lines.append("时长：%d 分 %d 秒" % (dur // 60, dur % 60) if dur >= 60
                     else "时长：%d 秒" % dur)
    try:
        size = os.path.getsize(path)
        lines.append("大小：%s" % (("%.1f MB" % (size / 1048576.0)) if size >= 1048576
                                  else ("%d KB" % (size // 1024))))
    except OSError:
        pass
    lines.append("下载时间：%s" % time.strftime("%Y-%m-%d %H:%M"))
    desc = (getattr(t, "desc", "") or "").strip()
    if desc:
        lines += ["", "简介：", desc]
    out = os.path.splitext(path)[0] + ".txt"
    try:
        with open(out, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        return out
    except OSError:
        return ""


#: 附件的类型（封面/字幕/弹幕/简介），归拢时只认这些，绝不误伤同名视频音频
SIDE_EXTS = (".txt", ".xml", ".srt", ".vtt", ".ass", ".sub", ".lrc",
             ".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp")


def gather_side_files(path, old_stem):
    """封面/字幕/弹幕/简介这些附件收进同名文件夹，文件夹名跟视频一致（同一套命名模板）。
    视频自己留在外面——下载目录里还是一排视频，附件不碍眼。
    命名模板改名前写下的附件（比如 yt-dlp 的字幕）顺手换成新名。
    返回文件夹路径；没东西可收返回 ""。"""
    d = os.path.dirname(path)
    stem = os.path.splitext(os.path.basename(path))[0]
    folder = os.path.join(d, stem)
    made = False
    try:
        names = os.listdir(d)
    except OSError:
        return ""
    for f in names:
        full = os.path.join(d, f)
        if not os.path.isfile(full) or os.path.abspath(full) == os.path.abspath(path):
            continue
        if f.startswith(stem + "."):
            newname = f
        elif old_stem and old_stem != stem and f.startswith(old_stem + "."):
            newname = stem + f[len(old_stem):]      # 改名前的字幕等，跟着换新名
        else:
            continue
        tail = newname[len(stem):].lower()
        if ".part" in tail or not tail.endswith(SIDE_EXTS):
            continue
        if not made:
            try:
                os.makedirs(folder, exist_ok=True)
            except OSError:
                return ""
            made = True
        try:
            os.replace(full, os.path.join(folder, newname))
        except OSError:
            pass            # 文件被占着就留在外面，不值当报错
    return folder if made else ""


def http_get(url, headers=None, data=None, timeout=40):
    req = urllib.request.Request(url, data=data, headers=headers or {})
    return urllib.request.urlopen(req, timeout=timeout)


# ---------- 代理 ----------
def _mac_proxy():
    """mac 的系统代理不走环境变量，只能问 scutil。输出形如：
        <dictionary> {
          HTTPSEnable : 1
          HTTPSProxy : 127.0.0.1
          HTTPSPort : 7890
        }
    优先级：HTTPS > HTTP > SOCKS。
    """
    try:
        r = subprocess.run(["scutil", "--proxy"], capture_output=True, text=True, timeout=5)
    except Exception:
        return None
    if r.returncode != 0:
        return None
    vals = {}
    for line in (r.stdout or "").splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        vals[k.strip()] = v.strip()
    for name, scheme in (("HTTPS", "http"), ("HTTP", "http"), ("SOCKS", "socks5")):
        if vals.get(name + "Enable") != "1":
            continue
        host = vals.get(name + "Proxy") or ""
        port = vals.get(name + "Port") or ""
        if host and port:
            return "%s://%s:%s" % (scheme, host, port)
    return None


def system_proxy():
    """读系统代理：Windows 读 IE/WinHTTP 设置，mac 读 scutil --proxy。拿不到返回 None"""
    if sys.platform == "darwin":
        return _mac_proxy()
    try:
        import winreg
    except ImportError:
        return None
    try:
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                           r"Software\Microsoft\Windows\CurrentVersion\Internet Settings")
        try:
            on, _ = winreg.QueryValueEx(k, "ProxyEnable")
        except OSError:
            on = 0
        if not on:
            return None
        try:
            server, _ = winreg.QueryValueEx(k, "ProxyServer")
        except OSError:
            return None
    except OSError:
        return None
    finally:
        try:
            winreg.CloseKey(k)
        except Exception:
            pass
    if not server:
        return None
    server = str(server).strip()
    # 形如 "http=1.2.3.4:8080;https=1.2.3.4:8080" 时优先取 https
    if "=" in server:
        parts = {}
        for seg in server.split(";"):
            if "=" in seg:
                a, b = seg.split("=", 1)
                parts[a.strip().lower()] = b.strip()
        server = parts.get("https") or parts.get("http") or ""
    if not server:
        return None
    if "://" not in server:
        server = "http://" + server
    return server


# 常见梯子客户端在本机开的 HTTP 代理端口。用户不会去设置里填端口，
# 但客户端默认就在这几个上：Clash 7890 / Clash Verge 7897 / v2rayN 10809 /
# Surge 6152 / ClashX 7890·8889 / V2rayU 1087。
LOCAL_PROXY_PORTS = (7890, 7897, 7891, 1087, 10809, 2080, 6152, 8889)

_proxy_cache = {"at": 0.0, "url": ""}


def _proxy_works(host, port, timeout=1.5, connect_timeout=0.35):
    """这个地址是不是一个真能出去的 HTTP 代理：发一次 CONNECT 看它回不回 200。
    光看端口开着不够——7890 也可能被别的程序占着，用错了反而把下载搞挂。"""
    try:
        s = socket.create_connection((host, port), timeout=connect_timeout)
    except OSError:
        return False
    try:
        s.settimeout(timeout)
        s.sendall(b"CONNECT www.youtube.com:443 HTTP/1.1\r\n"
                  b"Host: www.youtube.com:443\r\n\r\n")
        head = s.recv(64)
        first = head.split(b"\r\n", 1)[0]
        return first.startswith(b"HTTP/") and b" 200" in first
    except OSError:
        return False
    finally:
        try:
            s.close()
        except OSError:
            pass


def local_proxy():
    """系统代理没开时（有的客户端只监听端口、不接管系统代理），
    顺手看一眼本机常见端口上有没有能用的代理。命中就用，用户什么都不用填。

    并行探、每个只等 0.12 秒：本机安全软件会把连不上的端口挂满超时，
    一个个等下来能拖好几秒，全放到线程里一起等就只剩一次的钱。"""
    now = time.time()
    if now - _proxy_cache["at"] < 300:
        return _proxy_cache["url"]
    hits = {}

    def probe(port):
        if _proxy_works("127.0.0.1", port, connect_timeout=0.12):
            hits[port] = 1

    ts = [threading.Thread(target=probe, args=(p,), daemon=True)
          for p in LOCAL_PROXY_PORTS]
    for t in ts:
        t.start()
    for t in ts:
        t.join(2.0)
    url = ""
    for port in LOCAL_PROXY_PORTS:          # 按偏好顺序取第一个通的
        if port in hits:
            url = "http://127.0.0.1:%d" % port
            break
    _proxy_cache["at"] = now
    _proxy_cache["url"] = url
    return url


def proxy_reaches(proxy_url):
    """这个代理本身能不能到境外。系统代理经常是公司装的那种（存在但出不去），
    所以不能「有代理就当能通」，得真握一次手。Socks 的认不出来，先当好用。"""
    try:
        u = urllib.parse.urlparse(proxy_url or "")
    except ValueError:
        return False
    if not u.hostname:
        return False
    if (u.scheme or "").lower().startswith("socks"):
        return True
    return _proxy_works(u.hostname, u.port or 8080)


def reach_overseas(timeout=1.2):
    """现在这台机器能不能到境外站。开了 TUN 模式 / 全局代理的，直连就通。"""
    for host in ("www.youtube.com", "www.google.com"):
        try:
            s = socket.create_connection((host, 443), timeout=timeout)
            s.close()
            return True
        except OSError:
            continue
    return False


def resolve_proxy(platform, cfg_proxy):
    """决定这条任务走哪个代理。
    - 设置里填了就用设置的（老配置，界面上已经没有这一项了）
    - 境外平台：系统代理 -> 本机常见端口上真能用的代理 -> 直连（TUN 模式）
    - 其余情况返回 ""，表示直连，且要显式屏蔽环境变量里的代理
    """
    cfg_proxy = (cfg_proxy or "").strip()
    if cfg_proxy:
        return cfg_proxy
    if platform in OVERSEAS:
        return system_proxy() or local_proxy() or ""
    return ""


def _rm_quiet(path):
    if path and os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass


def _read_ckpt(src):
    """读 .part.src：第一行是文件全长，第二行是上次用的地址。返回 (total, url)。"""
    try:
        with open(src, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
        total = int(lines[0].strip() or 0)
        url = lines[1].strip() if len(lines) > 1 else ""
        return total, url
    except (OSError, ValueError, IndexError):
        return 0, ""


def resumable_download(url, out_path, progress, open_fn=None, headers=None,
                       min_bytes=0, timeout=60, label="下载中", chunk=262144):
    """带断点续传的单地址下载：写 out_path + ".part"，下完再原子改名。

    中断 / 失败 / 取消都**保留** .part，重试时用 HTTP Range 从断点接着下。
    所以半截文件永远不会顶着正式名字出现在下载目录里。

    能不能续不看地址——CDN / 网盘的签名地址每次都变（网易云就是），
    看的是**文件全长对不对**：全长一致当同一个文件接着下，对不上就重头来。
    open_fn(url, headers, timeout) 返回能 read()/close() 的响应对象，
    各平台的证书、代理差异由调用方自己封好传进来。
    """
    open_fn = open_fn or (lambda u, h, t: http_get(u, h, timeout=t))
    part = out_path + ".part"
    src = out_path + ".part.src"
    have = os.path.getsize(part) if os.path.exists(part) else 0
    old_total, old_url = _read_ckpt(src)

    if have and not old_total and not old_url:
        _rm_quiet(part)              # 这半截来历不明，不敢往上拼
        have = 0

    def _open_with_range():
        h = dict(headers or {})
        if have:
            h["Range"] = "bytes=%d-" % have
        return open_fn(url, h, timeout)

    def _total_of(r, base):
        cr = r.headers.get("Content-Range") or ""
        m = re.search(r"/(\d+)\s*$", cr)
        if m:
            return int(m.group(1))
        try:
            return int(r.headers.get("Content-Length") or 0) + base
        except ValueError:
            return 0

    try:
        r = _open_with_range()
    except urllib.error.HTTPError as e:
        # 416 = 断点已经顶到文件尾，上次其实下完了
        if e.code == 416 and have and (old_total == have or not old_total):
            e.close()
            os.replace(part, out_path)
            _rm_quiet(src)
            progress(done=have, total=have, speed=0, percent=100, stage=label)
            return out_path
        raise

    code = getattr(r, "status", None) or getattr(r, "code", 0) or 200
    if code != 206:                  # 服务器不认 Range、整份重发 → 丢掉半截重写
        have = 0
    total = _total_of(r, have)

    if have and old_total and total and total != old_total:
        # 同名不同文件（换了个源 / 换了档位），半截的不能往上拼
        try:
            r.close()
        except Exception:
            pass
        _rm_quiet(part)
        have = 0
        r = _open_with_range()
        code = getattr(r, "status", None) or getattr(r, "code", 0) or 200
        if code != 206:
            have = 0
        total = _total_of(r, have)

    try:
        with open(src, "w", encoding="utf-8") as f:
            f.write("%d\n%s" % (total, url))
    except OSError:
        pass

    got = 0
    t0 = time.time()
    try:
        with open(part, "ab" if have else "wb") as f:
            while True:
                b = r.read(chunk)
                if not b:
                    break
                f.write(b)
                got += len(b)
                done = have + got
                now = time.time()
                progress(done=done, total=total or done,
                         speed=done / 1048576.0 / max(now - t0, 0.01),
                         percent=min(100.0, done * 100.0 / total) if total else 0.0,
                         stage=label)
    except Exception:
        if not have and not got:
            _rm_quiet(part)          # 一个字节没写就别留垃圾
        # 其余情况留着 .part，重试能接着下
        raise
    finally:
        try:
            r.close()
        except Exception:
            pass

    size = os.path.getsize(part) if os.path.exists(part) else 0
    # 连接硬断时 read() 可能安静地返回空串，光看循环结束判断不出来
    if total and size < total:
        raise RuntimeError("下载中断了（%d/%d 字节）" % (size, total))
    if not size:
        _rm_quiet(part)
        _rm_quiet(src)
        raise RuntimeError("下下来是空的")
    if min_bytes and size < min_bytes:
        _rm_quiet(part)
        _rm_quiet(src)
        raise RuntimeError("文件过小")
    os.replace(part, out_path)       # 下完才顶正式名字，半截文件永远不叫成品名
    _rm_quiet(src)
    return out_path


def stream_download(urls, out_path, progress, headers=None, min_bytes=102400):
    """按候选地址依次尝试下载，成功返回 (路径, 实际地址)。支持断点续传。"""
    headers = headers or {"User-Agent": UA_M}
    last = None

    def _open(u, h, t):
        r = http_get(u, h, timeout=t)
        code = getattr(r, "status", 0) or 200
        ctype = (r.headers.get("Content-Type") or "").lower()
        # 续传回包（206）不看类型；其余只拦「明显不是媒体」的网页/JSON
        if code != 206 and ctype and not any(
                k in ctype for k in ("video", "audio", "octet-stream")):
            r.close()
            raise RuntimeError("返回的不是视频")
        return r

    for u in urls:
        if not u:
            continue
        try:
            path = resumable_download(u, out_path, progress, _open, headers,
                                      min_bytes=min_bytes, timeout=60)
            return path, u
        except Exception as e:
            last = e
            continue
    raise RuntimeError("所有播放源都失败：%s" % last)


# ---------- 图集（图文作品）----------
def _img_ext(url, ctype=""):
    path = (urllib.parse.urlparse(url).path or "").lower()
    m = re.search(r"\.(jpe?g|png|webp|gif|heic|bmp|avif)$", path)
    if m:
        e = m.group(1)
        return ".jpg" if e == "jpeg" else "." + e
    c = (ctype or "").lower()
    for key, ext in (("jpeg", ".jpg"), ("jpg", ".jpg"), ("png", ".png"),
                     ("webp", ".webp"), ("gif", ".gif"),
                     ("heic", ".heic"), ("avif", ".avif"), ("bmp", ".bmp")):
        if key in c:
            return ext
    return ".jpg"


def download_images(urls, folder, progress, headers=None, tag="图片"):
    """逐张下到 folder，文件名 01、02…（保留原始顺序）。返回 [{i,name,size}]"""
    os.makedirs(folder, exist_ok=True)
    total = max(1, len(urls))
    files = []
    for i, u in enumerate(urls):
        progress(stage="%s %d/%d" % (tag, i + 1, total),
                 percent=round(i * 100.0 / total, 1))
        data, ext = b"", ".jpg"
        cands = [u]
        if "!" in u:            # 小红书图片地址带 !nd_dft_xxx 后缀，去掉可拿原图
            cands.append(u.split("!")[0])
        for cu in cands:
            for _ in range(3):  # CDN 偶发中断（IncompleteRead），重试几次
                try:
                    r = http_get(cu, headers or {}, timeout=30)
                    data = r.read()
                    ext = _img_ext(cu, r.headers.get("Content-Type"))
                except Exception:
                    data = b""
                if data:
                    break
                time.sleep(0.35)
            if data:
                break
        if not data:
            continue
        name = "%02d%s" % (i + 1, ext)
        with open(os.path.join(folder, name), "wb") as f:
            f.write(data)
        files.append({"i": len(files), "name": name, "size": len(data)})
    if not files:
        raise RuntimeError("一张图片都没下下来，可能需要登录，或链接已失效")
    return files


# ---------- 抖音专用解析（yt-dlp 需要 cookie，这里走公开分享页）----------
def _douyin_ttwid():
    body = json.dumps({
        "region": "cn", "aid": 1768, "needFid": False, "service": "www.ixigua.com",
        "migrate_info": {"ticket": "", "source": "node"},
        "cbUrlProtocol": "https", "union": True,
    }).encode()
    r = http_get("https://ttwid.bytedance.com/ttwid/union/register/",
                 {"Content-Type": "application/json", "User-Agent": UA_PC}, data=body, timeout=20)
    for c in r.headers.get_all("Set-Cookie") or []:
        m = re.search(r"ttwid=([^;]+)", c)
        if m:
            return m.group(1)
    return None


def _douyin_item(url):
    """返回 (作品 ID, item)。item 里可能带 video，也可能只有 images（图文）。"""
    r = http_get(url, {"User-Agent": UA_M}, timeout=30)
    final = r.geturl()
    r.read(0)
    m = (re.search(r"/(?:video|note)/(\d+)", final)
         or re.search(r"modal_id=(\d+)", final)
         or re.search(r"/(?:video|note)/(\d+)", url))
    if not m:
        raise RuntimeError("没拿到抖音作品 ID，链接可能不是单条作品")
    aid = m.group(1)

    ttwid = _douyin_ttwid()
    if not ttwid:
        raise RuntimeError("获取 ttwid 失败，稍后重试")
    item = _douyin_share_item(aid, ttwid)
    return aid, item


def _douyin_share_item(aid, ttwid):
    """分享页 SSR。视频走 /share/video/，图文走 /share/note/，两种都试一遍。"""
    hdr = {"User-Agent": UA_M, "Cookie": "ttwid=" + ttwid,
           "Referer": "https://www.douyin.com/"}
    last = ""
    for kind in ("video", "note"):
        try:
            html = http_get("https://www.iesdouyin.com/share/%s/%s/" % (kind, aid),
                            hdr, timeout=30).read().decode("utf-8", "ignore")
        except Exception as e:
            last = str(e)
            continue
        mm = re.search(r"_ROUTER_DATA\s*=\s*(\{.*?\});?\s*</script>", html, re.S)
        if not mm:
            last = "分享页没有返回数据"
            continue
        try:
            loader = json.loads(mm.group(1)).get("loaderData") or {}
        except Exception:
            last = "分享页数据解析失败"
            continue
        for v in loader.values():
            if not isinstance(v, dict):
                continue
            items = (v.get("videoInfoRes") or {}).get("item_list") or []
            if items:
                return items[0]
    raise RuntimeError("分享页没有返回数据，可能被风控，稍后重试（%s）" % (last or "空"))


def douyin_images(item):
    """从图文作品里取图片地址。

    注意别用 download_url_list：那批地址的 tplv 参数里带 -water，是被打过
    「抖音号」水印的版本。url_list 是不带水印的，优先用它。
    """
    out = []
    for im in item.get("images") or []:
        urls = [u for u in (im.get("url_list") or []) if u]
        if not urls:
            urls = [u for u in (im.get("download_url_list") or [])
                    if u and "-water" not in u]
        if not urls:
            continue
        best = (next((u for u in urls if "p3-pc-sign" in u), "")
                or next((u for u in urls if "-sign" in u), "")
                or urls[0])
        out.append(best)
    return out


def is_image_post(item):
    """图文作品：有 images，且没有真正的视频时长（图文那个 video 字段是占位的）"""
    if not douyin_images(item):
        return False
    if item.get("aweme_type") in (2, 68):
        return True
    dur = (item.get("video") or {}).get("duration") or 0
    return dur <= 0


def douyin_probe(url):
    aid, item = _douyin_item(url)
    v = item.get("video") or {}
    cover = ""
    for c in (v.get("cover") or {}).get("url_list") or []:
        cover = c
        break
    return {
        "platform": "douyin", "platform_name": "抖音",
        "title": (item.get("desc") or "").replace("\n", " ").strip(),
        "author": (item.get("author") or {}).get("nickname") or "",
        "duration": (v.get("duration") or 0) // 1000,
        "thumb": cover,
        "urls": _douyin_play_urls(v),
    }


def _douyin_play_urls(v):
    out = []
    for u in (v.get("play_addr") or {}).get("url_list") or []:
        base = u.replace("/playwm/", "/play/")
        out.append(re.sub(r"ratio=[^&]*", "ratio=1080p", base))
        out.append(re.sub(r"ratio=[^&]*", "ratio=720p", base))
    return out


def douyin_download(url, out_dir, progress, quality="best"):
    aid, item = _douyin_item(url)
    v = item.get("video") or {}
    title = (item.get("desc") or "").replace("\n", " ").strip() or "抖音作品"
    author = (item.get("author") or {}).get("nickname") or "未知作者"
    cover = next(iter((v.get("cover") or {}).get("url_list") or []), "")
    progress(title=title, author=author, thumb=cover,
             duration=(v.get("duration") or 0) // 1000)

    # 图文作品：有 images，且 video 里没有真正的时长
    if is_image_post(item):
        imgs = douyin_images(item)
        folder = unique_path(os.path.join(
            out_dir, "%s - %s（图集）" % (safe_name(title), safe_name(author))))
        files = download_images(imgs, folder, progress,
                                {"User-Agent": UA_M, "Referer": "https://www.douyin.com/"})
        return folder, files

    name = "%s - %s.mp4" % (safe_name(title), safe_name(author))
    out_path = unique_path(os.path.join(out_dir, name))

    urls = _douyin_play_urls(v)
    if quality == "720":
        urls = [u for u in urls if "1080p" not in u] or urls

    path, _ = stream_download(urls, out_path, progress,
                              {"User-Agent": UA_M, "Referer": "https://www.douyin.com/"})
    return path


# ---------- yt-dlp 通用引擎 ----------
def ytdlp_probe(url, proxy=None, cookies_file=None):
    if yt_dlp is None:
        raise RuntimeError("下载组件没装好，重新解压一次这个文件夹就行（yt-dlp 未安装）")
    opts = {"quiet": True, "no_warnings": True, "skip_download": True,
            "noplaylist": True, "socket_timeout": 20,
            # 显式给值：空字符串 = 直连，避免 yt-dlp 自己捡到环境变量里的代理
            "proxy": proxy or ""}
    if cookies_file and os.path.exists(cookies_file):
        opts["cookiefile"] = cookies_file
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    if info.get("_type") == "playlist":
        info = (info.get("entries") or [{}])[0]
    return {
        "title": info.get("title") or "",
        "author": info.get("uploader") or info.get("channel") or "",
        "duration": int(info.get("duration") or 0),
        "thumb": info.get("thumbnail") or "",
        "platform": None, "platform_name": None, "urls": [],
    }


# ---------- Mac 播放兼容 ----------
# QuickTime 只吃 H.264 8bit 4:2:0 + AAC 的 mp4/mov。yt-dlp 的 best 现在常常挑到
# AV1 / VP9 / HEVC 10bit / 4:2:2，硬塞进 mp4 容器后 QuickTime 就弹
# 「此文件包含与 QuickTime Player 不兼容的部分媒体」。
# Mac 版下完自动探一次：不兼容才转成能双击播的 mp4，已经能播的一个字节都不动。
_QT_VIDEO_EXTS = (".mp4", ".m4v", ".mov", ".mkv", ".webm", ".flv", ".avi", ".ts")
_QT_MP4_EXTS = (".mp4", ".m4v", ".mov")
_QT_AUDIO_OK = ("", "aac", "mp3", "alac")


def format_for(quality, mac=False):
    """选流表达式。Mac 优先 H.264 + AAC；站点没有就一路兜底，别为了兼容让人下不了。"""
    if quality == "audio":
        return "bestaudio/best"
    h = {"1080": "[height<=1080]", "720": "[height<=720]"}.get(quality, "")
    if not mac:
        if not h:
            return "bestvideo*+bestaudio/best"
        return "bestvideo%s+bestaudio/best%s/best" % (h, h)
    v = "bestvideo[vcodec^=avc1]%s" % h
    # 最后两级必须退回原始的 bestvideo*+bestaudio：有些站点只有 AV1 / VP9 分离流，
    # 没有 avc1、也没有合并好的 mp4，光靠 best 会选出空列表，直接下不了。
    # 那种情况照旧下下来，再交给 ensure_mac_playable 转码。
    return ("%s+bestaudio[acodec^=mp4a]/%s+bestaudio/best[ext=mp4]%s/"
            "bestvideo*%s+bestaudio/best" % (v, v, h, h))


def _probe_streams(path):
    """不依赖 ffprobe（Mac 随包的静态 ffmpeg 没带它），从 `ffmpeg -i` 的 stderr 读流信息。"""
    ff = paths.ffmpeg_path()
    if not ff or not os.path.isfile(path):
        return None
    try:
        r = subprocess.run([ff, "-hide_banner", "-i", path],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
    except Exception:
        return None
    txt = (r.stderr or b"").decode("utf-8", "replace") + \
          (r.stdout or b"").decode("utf-8", "replace")
    v = re.search(r"Stream #\d+:\d+[^\n]*?Video:\s*([A-Za-z0-9_]+)([^\n]*)", txt)
    if not v:
        return None
    a = re.search(r"Stream #\d+:\d+[^\n]*?Audio:\s*([A-Za-z0-9_]+)", txt)
    return {"vcodec": v.group(1).lower(), "vrest": v.group(2).lower(),
            "acodec": a.group(1).lower() if a else ""}


def quicktime_compatible(path, info=None):
    """这个文件双击能不能被 QuickTime 正常播。探不出来就当能播，别乱动用户的文件。"""
    ext = os.path.splitext(path)[1].lower()
    if ext not in _QT_VIDEO_EXTS:
        return True                    # 音频 / 图集不归这儿管
    info = info if info is not None else _probe_streams(path)
    if not info:
        return True
    if ext not in _QT_MP4_EXTS:
        return False
    if not info["vcodec"].startswith(("h264", "avc1")):
        return False
    # yuv420p10le / yuv422p（相机原片）都不算，\b 正好把 10bit 排掉
    if not re.search(r"\byuv(?:j)?420p\b", info["vrest"]):
        return False
    return info["acodec"] in _QT_AUDIO_OK


def _h264_encoder_args(ff):
    """随包静态 ffmpeg 不一定编了 libx264，按可用编码器挑一套参数。"""
    try:
        r = subprocess.run([ff, "-hide_banner", "-encoders"],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
        s = (r.stdout or b"").decode("utf-8", "replace")
    except Exception:
        s = ""
    if "libx264" in s:
        return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]
    if "h264_videotoolbox" in s:
        return ["-c:v", "h264_videotoolbox", "-b:v", "8M"]
    return ["-c:v", "mpeg4", "-q:v", "3"]


def ensure_mac_playable(path, progress=None):
    """把 QuickTime 播不了的视频转成能双击播的 H.264/AAC mp4。

    返回 (最终路径, 备注)。任何一步失败都保留原文件 —— 下好的东西不能因为
    一次「兼容性优化」弄丢，也不能把成功的任务变成失败。
    """
    if not path or not os.path.isfile(path):
        return path, ""
    ext = os.path.splitext(path)[1].lower()
    if ext not in _QT_VIDEO_EXTS:
        return path, ""
    ff = paths.ffmpeg_path()
    if not ff:
        return path, "QuickTime 可能播不了，用 IINA 打开"
    info = _probe_streams(path)
    if not info or quicktime_compatible(path, info):
        return path, ""
    stem = os.path.splitext(path)[0]
    tmp = stem + ".compat.mp4"
    target = stem + ".mp4"
    cmd = [ff, "-y", "-hide_banner", "-loglevel", "error", "-i", path,
           "-map", "0:v:0", "-map", "0:a:0?", "-sn", "-dn"] + \
          _h264_encoder_args(ff) + \
          ["-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
           "-movflags", "+faststart", tmp]
    try:
        if progress:
            progress(stage="正在转成 Mac 能播的格式…")
        r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=900)
        if r.returncode != 0 or not os.path.isfile(tmp) or os.path.getsize(tmp) < 4096:
            raise RuntimeError("转码没成功")
        os.replace(tmp, target)
        if target != path:
            try:
                os.remove(path)
            except OSError:
                pass
        return target, ""
    except Exception:
        try:
            if os.path.isfile(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return path, "QuickTime 可能播不了，用 IINA 打开"


def _seg_percent(seg, raw):
    """DASH 是「视频流 + 音频流」一段一段下的，给后面几段留点位置。

    以前第一段下完就 100%，第二段还在下时进度条和文案都定住，
    看着像卡死。这里按段压缩：第一段 0~95，第二段 95~99.5，
    第三段 99.5~99.95……永远走不到 100，100 留给真正完成的那一刻。
    """
    lo = 0.0 if seg == 0 else 100.0 - 5.0 * (0.1 ** (seg - 1))
    hi = 100.0 - 5.0 * (0.1 ** seg)
    return lo + max(0.0, min(100.0, raw)) / 100.0 * (hi - lo)


def ytdlp_download(url, out_dir, progress, quality="best", proxy=None, cookies_file=None,
                   subs=False, title_hint=""):
    if yt_dlp is None:
        raise RuntimeError("yt-dlp 未安装")
    # Mac 上优先 H.264 + AAC：QuickTime 认这个，AV1/VP9/10bit 会弹「不兼容的部分媒体」。
    # Windows 保持原来的 best，编导那边的播放器不挑格式，画质优先。
    fmt = format_for(quality, mac=(sys.platform == "darwin"))

    state = {"path": "", "last": 0.0, "seg": 0}

    def hook(d):
        if d.get("filename"):
            state["path"] = d["filename"]
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done = d.get("downloaded_bytes") or 0
            raw = (done * 100.0 / total) if total else 0
            # 按「第几段」压缩，段与段之间不倒退，也不会提前冲到 100%
            p = max(_seg_percent(state["seg"], raw), state["last"])
            state["last"] = p
            progress(done=done, total=total,
                     speed=(d.get("speed") or 0) / 1048576.0,
                     percent=p)
        elif d.get("status") == "finished":
            state["path"] = d.get("filename") or ""
            state["seg"] += 1
            # 只有真的下过两段才说「合并」；单段文件只是收尾
            progress(speed=0, stage="正在合并音视频…" if state["seg"] >= 2
                     else "正在处理…")
        elif d.get("status") == "error":
            progress(stage="下载出错")

    opts = {
        "format": fmt,
        # 原来带 %(uploader,channel,id) 后缀，是为了区分同名视频；
        # 但 B站番剧页没有 uploader，会退化成数字 id，文件名变成「正片 - 744327」。
        # 这里只留标题，重名由后面的命名模板 / 唯一化处理
        "outtmpl": os.path.join(out_dir, "%(title).80s.%(ext)s"),
        "progress_hooks": [hook],
        "quiet": True, "no_warnings": True, "noplaylist": True,
        "noprogress": True,       # 进度走自己的回调，别往控制台刷
        "merge_output_format": "mp4",
        "restrictfilenames": False,
        "windowsfilenames": True,
        "concurrent_fragment_downloads": 4,
        "retries": 5,
        "fragment_retries": 5,
        # 不要 nopart：yt-dlp 自己写 .part 检查点，中断后重试会用 Range 接着下
        "continuedl": True,
        "socket_timeout": 25,
        # 同上：显式关闭环境变量代理，空字符串即直连
        "proxy": proxy or "",
    }
    # Mac 版随包带了 ffmpeg（没装 Homebrew 也能合并音视频 / 转 mp3）。
    # Windows 保持原样，交给 yt-dlp 自己找，免得改坏桌面便携版。
    if sys.platform == "darwin":
        ff_dir = paths.ffmpeg_dir()
        if ff_dir:
            opts["ffmpeg_location"] = ff_dir
    if quality == "audio":
        opts["postprocessors"] = [{"key": "FFmpegExtractAudio",
                                   "preferredcodec": "mp3", "preferredquality": "192"}]
    if cookies_file and os.path.exists(cookies_file):
        opts["cookiefile"] = cookies_file
    if subs:
        # B站/YouTube 的字幕单独存成 .srt，不嵌进视频（编导要的是文本，不是字幕轨）
        opts.update({
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": ["zh-Hans", "zh-CN", "zh", "ai-zh", "en"],
            "subtitlesformat": "srt/best",
        })

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            # 先只解析不下载：标题/作者/时长/封面/简介立刻递回去，
            # 下载的这几分钟里任务卡片就有内容，简介也能写进 .txt
            info = ydl.extract_info(url, download=False)
            if info.get("_type") == "playlist":
                info = (info.get("entries") or [{}])[0]
            # B站番剧/影视正片页 yt-dlp 给的标题是「正片」，没信息量。
            # 用户贴链接时带的标题（电影卡下正片）更准，用它顶着
            raw_title = (info.get("title") or "").strip()
            if raw_title in ("正片", "预告", "PV", "PV1", "片头", "default"):
                raw_title = ""
            if title_hint:
                raw_title = title_hint
            if raw_title:
                try:
                    info["title"] = raw_title
                except Exception:
                    pass
            try:
                progress(title=raw_title,
                         author=info.get("uploader") or info.get("channel") or "",
                         duration=int(info.get("duration") or 0),
                         thumb=info.get("thumbnail") or "",
                         desc=info.get("description") or "")
            except Exception:
                pass            # 被取消了就让下面的下载自己抛
            info = ydl.process_ie_result(info, download=True)
            path = ydl.prepare_filename(info)
    except Exception:
        # 取消 / 失败：什么都别删。.part 是断点检查点，重试能接着下；
        # 唯独空文件顺手清掉，免得占着正式名字
        for p in (state.get("path") or "", (state.get("path") or "") + ".part"):
            if p and os.path.exists(p) and os.path.getsize(p) == 0:
                try:
                    os.remove(p)
                except OSError:
                    pass
        raise
    if not os.path.exists(path):
        cand = state.get("path") or ""
        if cand and os.path.exists(cand):
            path = cand
        else:
            base = os.path.splitext(path)[0]
            for ext in (".mp4", ".mkv", ".webm", ".mp3", ".m4a"):
                if os.path.exists(base + ext):
                    path = base + ext
                    break
    if not os.path.exists(path):
        raise RuntimeError("下载完成了但找不到文件")
    return path


# ---------- 任务管理 ----------
# ---------- 失败原因：把底层异常翻成人话 ----------
# 前端不再拿英文异常去猜，这里先认一遍。认不出来就原样返回，不硬编。
_ERR_RULES = [
    (("login", "sign in", "cookies", "未登录", "需要登录", "vip", "会员"),
     "需要登录", "打开「设置 → 账号」登一次，再重试"),
    (("403", "forbidden"), "被平台拒绝了", "链接可能过期，重新复制一次分享链接"),
    # ffmpeg 打不开输入：多半是下到了网页 / 加密片段，别再骗用户说「内容不存在」
    (("invalid data found", "error opening input", "moov atom not found"),
     "拿到的不是音视频", "可能只下到加密片段或网页，登录对应账号后重试"),
    (("404", "not found", "不存在"), "内容不存在", "作品可能被删了或设成了私密"),
    (("private", "私密", "仅粉丝"), "私密内容", "作者设了权限，登录对应账号才看得到"),
    (("412", "risk", "verify", "风控", "验证码"), "触发了风控", "等几分钟再试，别一次贴太多条"),
    (("429", "too many", "频繁"), "被限速了", "把「同时下载数」调到 2，歇会儿再下"),
    (("timed out", "timeout", "超时", "connection reset"), "网络超时", "重试一次通常就好；境外站点要先开梯子（VPN）"),
    (("ssl", "certificate", "cert verify"), "证书校验失败", "系统时间不对，或者代理在劫持 https"),
    (("unsupported url", "no suitable", "不支持", "无法解析"), "这个链接解析不了", "换分享里的完整短链再试"),
    (("no space", "磁盘", "disk full"), "磁盘空间不足", "换个下载目录，或者清点空间"),
    (("permission", "access is denied", "拒绝访问", "winerror 5"), "写不进这个目录", "安全软件可能锁了文件，换个目录试试"),
    (("ffmpeg",), "缺少 ffmpeg", "合并音视频要用它，装一个再下"),
    (("region", "country", "地区", "geo"), "地区限制", "要先开梯子（VPN）才能下"),
    (("connection refused", "max retries", "连接", "network is unreachable"), "连不上", "先看看梯子（VPN）开没开，再重试"),
]


def explain_error(msg):
    """返回 (短标题, 建议)。认不出来的返回 ('', '')，调用方用原始信息兜底。"""
    m = (msg or "").strip()
    if not m:
        return "", ""
    # 已经中文化过的（下面 _run_task 里生成的）就别再套规则了：
    # 「视频不可用了：可能已删除、设成私密」会被 "私密" 命中，反而说得更差
    cn = sum(1 for ch in m if "\u4e00" <= ch <= "\u9fff")
    if cn >= 6:
        return "", ""
    low = m.lower()
    for keys, title, tip in _ERR_RULES:
        if any(k in low for k in keys):
            return title, tip
    return "", ""


class Task(object):
    def __init__(self, tid, url, platform, platform_name, batch=0, key=""):
        self.id = tid
        self.url = url
        self.platform = platform
        self.platform_name = platform_name
        self.key = key
        self.batch = batch
        self.title = ""
        self.author = ""
        self.thumb = ""
        self.duration = 0
        self.desc = ""
        # 提交时带的片名（电影卡下正片时给的）。解析器给的标题是「正片」那种
        # 没信息量的值时，用它顶上，文件名才不会变成「正片 - 744327」
        self.title_hint = ""
        # 用户/搜索界面指定的名字（音效那种直链，解析器只会给个网址当标题）。
        # 锁上之后解析阶段不再改标题，否则文件名会变成 assets_xxx_2393
        self.title_locked = False
        self.status = "queued"      # queued / running / done / error / canceled
        self.percent = 0.0
        self.speed = 0.0
        self.size = 0
        self.stage = "排队中"
        self.error = ""
        self.filepath = ""
        self.proxy = ""
        self.kind = "video"         # video / images
        self.images = []            # [{i, name, size}]
        self.created = time.time()

    def to_dict(self):
        d = {
            "id": self.id, "url": self.url, "platform": self.platform,
            "platform_name": self.platform_name, "title": self.title,
            "author": self.author, "thumb": self.thumb, "duration": self.duration,
            "status": self.status, "percent": round(self.percent, 1),
            "speed": round(self.speed, 2), "size": self.size,
            "stage": self.stage, "error": self.error, "filepath": self.filepath,
            "batch": self.batch, "kind": self.kind,
            "images": self.images, "count": len(self.images),
        }
        if self.status == "error" and self.error:
            # 允许「标题 \n 建议 \n 详情」三段式：
            # 卡片上只显示标题 + 一句短建议，长细节塞进 tooltip，
            # 否则一行几十个字会把卡片撑成两行，列表看着参差不齐
            lines = [x.strip() for x in self.error.split("\n")]
            head = lines[0]
            tip = lines[1] if len(lines) > 1 else ""
            more = " ".join(x for x in lines[2:] if x)
            title, hint = explain_error(head)
            if title:
                tip = tip or hint
            else:
                title = head
            if title:
                d["err_title"] = title
                d["err_tip"] = tip
                d["error"] = "" if (tip and not more) else (more or self.error)
        return d


class Manager(object):
    HIST_MAX = 800

    def __init__(self, config):
        self.cfg = config
        self.tasks = {}
        self.lock = threading.Lock()
        self.seq = 0
        self.batch_seq = 0
        self.max_workers = 3
        self.running = 0
        paths.ensure_data_dir()
        self.history_file = paths.history_path()
        self.history = []
        self._load_history()
        self._apply_cfg()

    # ---------- 历史（关掉软件也还在） ----------
    def _load_history(self):
        try:
            with open(self.history_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                self.history = [x for x in data if isinstance(x, dict)][:self.HIST_MAX]
        except Exception:
            self.history = []
        # 老记录没有 key（去重是后来加的），补算一遍，不然重下同一首拦不住
        patched = False
        for x in self.history:
            if not x.get("key") and x.get("url"):
                x["key"] = video_key(x["url"])
                patched = True
        if patched:
            self._save_history()

    def _save_history(self):
        try:
            tmp = self.history_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.history, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.history_file)
        except Exception:
            pass

    def _push_history(self, t):
        rec = {
            "id": t.id, "url": t.url, "title": t.title, "author": t.author,
            "platform": t.platform, "platform_name": t.platform_name,
            "path": t.filepath, "size": t.size, "duration": t.duration,
            "thumb": t.thumb, "kind": t.kind, "count": len(t.images),
            "key": t.key, "created": int(t.created), "done_at": int(time.time()),
        }
        with self.lock:
            self.history.insert(0, rec)
            del self.history[self.HIST_MAX:]
        self._save_history()

    def history_snapshot(self, q="", limit=200):
        with self.lock:
            items = list(self.history)
        q = (q or "").strip().lower()
        if q:
            items = [x for x in items
                     if q in ("%s %s %s" % (x.get("title", ""), x.get("author", ""),
                                            x.get("platform_name", ""))).lower()]
        return items[:limit]

    def clear_history(self):
        with self.lock:
            self.history = []
        self._save_history()

    def _apply_cfg(self):
        try:
            n = int(self.cfg.get("concurrency") or 3)
        except (TypeError, ValueError):
            n = 3
        self.max_workers = max(1, min(n, 8))

    def set_config(self, cfg):
        self.cfg = cfg
        self._apply_cfg()

    def snapshot(self):
        with self.lock:
            items = sorted(self.tasks.values(), key=lambda t: t.created, reverse=True)
            return [t.to_dict() for t in items]

    def next_batch(self):
        with self.lock:
            self.batch_seq += 1
            return self.batch_seq

    def add(self, url, batch=0):
        """加入一条任务，返回 (task, 跳过原因)"""
        key = video_key(url)
        pid, pname, level, why = classify(url)
        start = False
        with self.lock:
            if key:
                for x in self.tasks.values():
                    if x.key == key and x.status in ("queued", "running", "done"):
                        return None, "这条已经在列表里了"
                # 以前下过、文件还在 → 别重复下。文件被删了则照常重下
                for h in self.history:
                    if h.get("key") == key:
                        p = h.get("path") or ""
                        if p and os.path.exists(p):
                            return None, "这条以前下过了，历史里能找到"
                        break
            self.seq += 1
            t = Task(str(self.seq), url, pid, pname, batch=batch, key=key)
            if why:
                t.status = "error"
                t.stage = "不支持"
                t.error = why
            else:
                start = True
            self.tasks[t.id] = t
        if start:
            threading.Thread(target=self._run_wrapper, args=(t,), daemon=True).start()
        return t, ""

    def find_task(self, url):
        """按 url 找同一条已有的任务（在下 / 已下好），找不到返回 None"""
        key = video_key(url)
        if not key:
            return None
        with self.lock:
            for x in self.tasks.values():
                if x.key == key:
                    return x
        return None

    def cancel_all(self):
        n = 0
        with self.lock:
            for t in self.tasks.values():
                if t.status in ("queued", "running"):
                    t.status = "canceled"
                    t.stage = "已取消"
                    n += 1
        return n

    def _wait_slot(self):
        while True:
            with self.lock:
                if self.running < self.max_workers:
                    self.running += 1
                    return
            time.sleep(0.4)

    def _run_wrapper(self, t):
        self._wait_slot()
        try:
            with self.lock:
                if t.status == "canceled":   # 排队期间就被取消了，别再开跑
                    return
            self._run(t)
        except Exception as e:
            raw = str(e)[:200]
            with self.lock:
                # 用户点了取消，别把它改写成「失败」
                if t.status == "canceled":
                    return
            msg = raw
            # 卡片上只有一行位置，原因说清楚就够，建议跟一句，别写成小作文
            if t.platform == "kuaishou":
                msg = "快手要登录才能解析。去「设置 → 账号」登一次再重试。（%s）" % raw
            elif t.platform == "xiaohongshu":
                # cn_sites 的报错已经写清原因和出路，别再一律安「要登录」把人带偏
                msg = raw if any(k in raw for k in
                                 ("登录", "重试", "反馈", "分享", "删除", "图片")) \
                    else "小红书解析失败，重试一次看看。（%s）" % raw
            elif t.platform == "instagram":
                msg = "Instagram 要登录 cookies + 代理，配好再试。"
            elif t.platform == "reddit":
                if ("403" in raw or "Blocked" in raw or "authentication" in raw.lower()):
                    msg = "Reddit 拒绝了这台机器的出口 IP，换个代理再试。（%s）" % raw
                else:
                    msg = "连不上 Reddit，先把梯子（VPN）打开再试。（%s）" % raw
            elif t.platform in ("tiktok", "youtube", "twitter", "vimeo", "twitch"):
                # 以前一律说"代理没开"，但链接失效、要登录也是同样的报错，会把方向带偏
                low = raw.lower()
                if any(k in low for k in ("unavailable", "removed", "not available",
                                          "deleted", "no longer")):
                    msg = "视频不可用：已删除、私密或地区限制。（%s）" % raw
                elif any(k in low for k in ("sign in", "login", "log in", "members-only",
                                            "confirm your age", "cookies", "age-restricted")):
                    msg = "要登录才能看，设置里登录后重试。（%s）" % raw
                elif any(k in low for k in ("timed out", "timeout", "temporary failure")):
                    msg = "网络超时，重试一次通常就好。（%s）" % raw
                elif any(k in low for k in ("geo", "region", "country", "not made this video available")):
                    msg = "地区限制，换个出口再试。（%s）" % raw
                else:
                    msg = "连不上。这个站在境外，先把梯子（VPN）打开再试。（%s）" % raw
            elif t.platform == "unknown":
                msg = "这个站点解析不了，可能要登录或用强加密。（%s）" % raw
            elif "Unsupported URL" in raw:
                msg = "页面结构识别不了，可能是合集页或要登录。"
            with self.lock:
                t.status = "error"
                t.error = msg
                t.stage = "失败"
        finally:
            with self.lock:
                self.running -= 1

    def _run(self, t):
        out_dir = self.cfg.get("out_dir") or os.path.join(os.path.expanduser("~"), "Downloads")
        os.makedirs(out_dir, exist_ok=True)
        proxy = resolve_proxy(t.platform, self.cfg.get("proxy"))
        cookies = (self.cfg.get("cookies_file") or "").strip() or None
        quality = self.cfg.get("quality") or "best"

        # 设置里没填就用手动登录存下来的那份
        if not cookies:
            try:
                import login as login_mod
                cookies = login_mod.cookie_file(t.platform)
            except Exception:
                cookies = None

        with self.lock:
            t.status = "running"
            t.stage = "解析中…"
            t.proxy = proxy

        def progress(**kw):
            with self.lock:
                if t.status == "canceled":
                    raise RuntimeError("已取消")
                if kw.get("title") and not t.title_locked:
                    t.title = kw["title"]
                if kw.get("author"):
                    t.author = kw["author"]
                if kw.get("thumb"):
                    t.thumb = kw["thumb"]
                if kw.get("duration"):
                    t.duration = int(kw["duration"])
                if kw.get("desc"):
                    t.desc = kw["desc"]
                if "percent" in kw:
                    t.percent = kw["percent"]
                if "speed" in kw:
                    t.speed = kw["speed"]
                if "total" in kw and kw["total"]:
                    t.size = kw["total"]
                if kw.get("stage"):
                    t.stage = kw["stage"]
                elif t.status == "running":
                    t.stage = "下载中"

        if t.platform == "douyin":
            res = douyin_download(t.url, out_dir, progress, quality)
        elif t.platform in ("netease", "qqmusic", "kugou"):
            import music as music_mod       # 延迟导入，避免循环依赖
            with self.lock:
                t.kind = "audio"
            res = music_mod.download(t.url, out_dir, progress, quality, cookies)
        elif t.platform == "suno":
            import suno  # 延迟导入，避免循环依赖
            with self.lock:
                t.kind = "audio"
            res = suno.download(t.url, out_dir, progress, quality, proxy)
        elif t.platform == "direct":
            import hls  # 延迟导入，避免循环依赖
            if hls.is_audio_name(t.url):
                with self.lock:
                    t.kind = "audio"
            res = hls.download(t.url, out_dir, progress, quality, proxy)
        elif t.platform == "weixin":
            import sph  # 延迟导入，避免循环依赖
            res = sph.download(t.url, out_dir, progress, quality,
                               self.cfg.get("sph_api") or "",
                               self.cfg.get("sph_token") or "")
        elif t.platform in ("kuaishou", "xiaohongshu"):
            import cn_sites  # 延迟导入，避免循环依赖
            if t.platform == "kuaishou":
                res = cn_sites.kuaishou_download(t.url, out_dir, progress, quality, cookies)
            else:
                res = cn_sites.xhs_download(t.url, out_dir, progress, quality, cookies)
        else:
            # B站番剧/影视正片页 yt-dlp 只给「正片」，片名和作者都空着。
            # 先用 B站 pgc 接口把真名查出来，文件名和简介才像样
            if t.platform == "bilibili" and "/bangumi/play/" in t.url:
                try:
                    import movies as movies_mod
                    mi = movies_mod.bili_media_info(t.url) or {}
                    if mi.get("title") and not t.title_hint:
                        t.title_hint = mi["title"]
                        t.title = mi["title"]
                    if mi.get("credit") and not t.author:
                        t.author = mi["credit"]
                    if mi.get("director") and not t.author:
                        t.author = mi["director"]
                    if mi.get("cover") and not t.thumb:
                        t.thumb = mi["cover"]
                except Exception:
                    pass
            res = ytdlp_download(t.url, out_dir, progress, quality, proxy, cookies,
                                 subs=bool(self.cfg.get("down_subs")),
                                 title_hint=t.title_hint)

        # 图集返回 (目录, 图片列表)，视频返回单个文件路径
        if isinstance(res, tuple):
            path, imgs = res
        else:
            path, imgs = res, []

        # Mac 专属：QuickTime 播不了的格式，下完顺手转成能双击播的 mp4。
        # 放这儿而不是某个下载器里，直链 / HLS / 抖音 / B站 走哪条路都覆盖到。
        compat_note = ""
        if sys.platform == "darwin" and not imgs:
            path, compat_note = ensure_mac_playable(path, progress)

        with self.lock:
            t.filepath = path
            if imgs:
                t.kind = "images"
            elif t.kind != "audio":
                t.kind = "video"
            t.images = imgs or []
            t.status = "done"
            t.stage = "已完成" if not imgs else "已完成 · %d 张" % len(imgs)
            if compat_note:
                t.stage += " · " + compat_note
            t.percent = 100.0
            t.speed = 0
            if imgs:
                t.size = sum(int(x.get("size") or 0) for x in imgs)
            elif not t.size and os.path.isfile(path):
                t.size = os.path.getsize(path)
            if not t.title:
                t.title = os.path.splitext(os.path.basename(path))[0]

        # 收尾：按模板改名、顺手存封面。都是锦上添花，失败了也不影响任务本身。
        try:
            new_path = self._post_process(t, path)
            if new_path and new_path != path:
                with self.lock:
                    t.filepath = new_path
        except Exception:
            pass
        self._push_history(t)

    def _post_process(self, t, path):
        if not path or not os.path.exists(path):
            return path
        is_dir = os.path.isdir(path)

        old_stem = os.path.splitext(os.path.basename(path))[0]
        tpl = (self.cfg.get("name_tpl") or "").strip()
        if tpl and not is_dir:
            newbase = render_name(tpl, t)
            if newbase:
                ext = os.path.splitext(path)[1]
                target = unique_path(os.path.join(os.path.dirname(path), newbase + ext))
                if target != path:
                    try:
                        os.replace(path, target)
                        path = target
                    except OSError:
                        pass       # DLP 锁着文件时改不动，保留原名就算了

        # 音频的封面已经写进 ID3 了，不用再单独存一张
        if (self.cfg.get("save_cover") and not is_dir and t.thumb
                and t.kind != "audio"):
            save_cover(t.thumb, os.path.splitext(path)[0] + ".cover")

        # 弹幕（B站）与简介/元信息：同名 .xml / .txt，失败不影响任务。
        # 先写本地的 .txt，再拉要走网络的弹幕——状态已经显示「已完成」了，
        # 本地文件越早落地越好
        base = os.path.splitext(path)[0]
        if self.cfg.get("save_desc", False) and not is_dir:
            try:
                save_desc_txt(t, path)
            except Exception:
                pass
        if (self.cfg.get("save_danmaku", False) and not is_dir
                and t.platform == "bilibili"):
            try:
                save_danmaku(t.url, base)
            except Exception:
                pass

        # 封面/字幕/弹幕/简介收进同名文件夹，文件夹跟视频用同一套命名
        if not is_dir:
            try:
                gather_side_files(path, old_stem)
            except Exception:
                pass
        return path

    def cancel(self, tid):
        with self.lock:
            t = self.tasks.get(tid)
            if t and t.status in ("queued", "running"):
                t.status = "canceled"
                t.stage = "已取消"
                return True
        return False

    def retry_failed(self):
        """把当前所有失败的重新排一遍。断网/风控这类失败，一次性重试最省事。"""
        with self.lock:
            ids = [t.id for t in self.tasks.values() if t.status == "error"]
        for tid in ids:
            self.retry(tid)
        return len(ids)

    def retry(self, tid):
        with self.lock:
            t = self.tasks.get(tid)
            if not t:
                return False
            t.status = "queued"
            t.error = ""
            t.percent = 0
            t.speed = 0
            t.stage = "排队中"
        threading.Thread(target=self._run_wrapper, args=(t,), daemon=True).start()
        return True

    def clear_done(self):
        with self.lock:
            for tid in [k for k, v in self.tasks.items() if v.status in ("done", "canceled")]:
                del self.tasks[tid]


def _expand_music(url):
    """歌单 / 专辑链接展开成单曲列表；单曲、普通视频返回 None。
    读不出来抛 RuntimeError，由调用方记成一条 skipped。"""
    try:
        import music as music_mod        # 延迟导入，避免循环依赖
    except Exception:
        return None
    try:
        info = music_mod.parse_url_follow(url)
    except Exception:
        return None
    if not info or info.get("kind") not in ("playlist", "album"):
        return None
    return music_mod.playlist_tracks(info)


def add_blocks(manager, blocks):
    """每一块 = 一次粘贴。一块里如果有多条链接，全部拆开，但归到同一个批次号。
    音乐的歌单 / 专辑会先展开成单曲，再一条条进队。"""
    added, skipped, batches = [], [], []
    for b in blocks or []:
        text = (b.get("text") or "").strip()
        if not text:
            continue
        urls = find_urls(text)
        if not urls:
            skipped.append({"url": text[:80], "reason": "这段文字里没找到链接"})
            continue

        # 先把明确下不了的挑出来，别让它们白白占一个批次号
        seen, usable = set(), []
        for u in urls:
            k = video_key(u)
            if k:
                if k in seen:
                    continue
                seen.add(k)
            pid, pname, level, why = classify(u)
            if why:
                skipped.append({"url": u, "platform": pname, "reason": why})
                continue

            # 音乐：一条歌单链接可能变成几十首，展开后再进队
            try:
                tracks = _expand_music(u)
            except RuntimeError as e:
                skipped.append({"url": u, "platform": pname, "reason": str(e)})
                continue
            if tracks is not None:
                before = len(usable)
                for tr in tracks:
                    mu = music_song_url(tr.get("platform"), tr.get("id"))
                    if not mu:
                        continue
                    mk = video_key(mu)
                    if mk and mk in seen:
                        continue
                    if mk:
                        seen.add(mk)
                    mname = (PINFO.get(tr.get("platform")) or {}).get("name") or pname
                    usable.append((mu, mname, tr.get("title") or "", tr.get("artist") or ""))
                if len(usable) == before:
                    skipped.append({"url": u, "platform": pname,
                                    "reason": "这个歌单里没有能下的歌"})
                continue

            usable.append((u, pname, "", ""))
        if not usable:
            continue

        # 这一块自己带的标题提示（比如电影卡下正片，番剧页 yt-dlp 只给「正片」）
        hint_title = (b.get("title") or "").strip()
        batch = manager.next_batch()
        n = 0
        for u, pname, mtitle, martist in usable:
            t, msg = manager.add(u, batch)
            if not t:
                skipped.append({"url": u, "platform": pname, "reason": msg})
                continue
            # 歌单里已经知道歌名和歌手，先写上，列表里立刻能看懂
            if mtitle:
                t.title = mtitle
            elif hint_title:
                t.title = hint_title
                t.title_hint = hint_title
            if martist:
                t.author = martist
            added.append(t.to_dict())
            n += 1
        batches.append({"no": batch, "count": n, "found": len(urls)})
    return {"ok": bool(added), "added": added, "skipped": skipped, "batches": batches}


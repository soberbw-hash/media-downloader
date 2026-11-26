# -*- coding: utf-8 -*-
"""多引擎回退：一条链接在后台按「最靠谱 → 兜底」的顺序挨个试。

前端只看到一个任务、一根进度条。哪个引擎成了就用哪个，中途失败不打扰用户，
只在 logs/engines.log 里留一行痕迹（出问题时能查，平时不用管）。

两条铁律：
1. 用户取消、磁盘满、写不进目录 —— 立刻停，不许换引擎继续折腾。
2. 链接本身就是错的（主页、已删除、私密）—— 也不试第二个，
   否则用户要干等好几轮才看到一句「这链接不对」。
"""
import os
import time

# 整条链的总时长上限。单引擎内部还有自己的超时，这里是最后的兜底，
# 防止某个源半死不活地把用户吊在那儿。
BUDGET = 240.0


class Canceled(RuntimeError):
    """用户点了取消。任何引擎抛它都必须立刻停。"""


class Engine(object):
    """一个下载引擎。fn(t, out_dir, progress, opt) -> path 或 (dir, files)"""

    __slots__ = ("name", "label", "fn")

    def __init__(self, name, label, fn):
        self.name = name
        self.label = label
        self.fn = fn

    def __repr__(self):
        return "<Engine %s>" % self.name


# ---------------------------------------------------------------- 错误判定
# 命中这些词就别再试下一个引擎了：再试也是同一个结果，只会让用户多等。
_FATAL_HINTS = (
    # 用户自己的动作 / 本机环境
    "已取消", "取消了", "no space", "disk full", "磁盘空间", "空间不足",
    "write protected", "拒绝访问", "permission denied", "winerror 5",
    "写不进", "只读",
    # 链接本身不指向一条可下的作品
    "这是", "不是单条", "请贴", "请从", "主页", "合集", "播放列表",
    "整季页", "直播间", "搜索结果页", "话题", "榜单页",
    "不支持", "下不了", "没有公开接口",
    # 内容没了 / 看不了
    "已删除", "被删", "不存在", "失效", "设为私密", "私密内容", "仅粉丝",
    "没有提取码", "提取码不对", "提取码错误", "分享已过期",
)

# 这些词说明「换个来源可能就行」，明确不算终止条件（覆盖 _FATAL_HINTS 里的宽词）
_RETRY_HINTS = (
    "需要登录", "登录过期", "未登录", "要登录", "登录墙",
    "风控", "验证码", "滑块", "300012", "存在风险",
    "签名", "sign", "参数不对", "token", "解析不了", "读不出来",
    "超时", "timeout", "连接", "网络", "429", "412", "403",
    "拿到的不是音视频", "没找到文件",
)


def is_fatal(exc):
    """这个失败值不值得再换一个引擎试？False = 值得试。"""
    if isinstance(exc, Canceled):
        return True
    msg = str(exc or "").lower()
    if not msg:
        return False
    for hint in _RETRY_HINTS:
        if hint.lower() in msg:
            return False
    for hint in _FATAL_HINTS:
        if hint.lower() in msg:
            return True
    return False


# ---------------------------------------------------------------- 半成品清理
def _snapshot(out_dir):
    try:
        return set(os.listdir(out_dir))
    except OSError:
        return set()


def _cleanup(out_dir, before, started_at):
    """只清这次新产生的「空壳」：0 字节文件、空目录。

    为什么不敢多删：`.part` / `.ytdl` 是断点续传的凭据 —— 用户点「继续下载」
    就是靠它接着下，删了等于让他从头再来。正常名字的文件更可能是别的任务
    刚下完的，或者用户自己放进来的。删错一个的代价，比留一点垃圾大得多。
    """
    for name in _snapshot(out_dir) - before:
        path = os.path.join(out_dir, name)
        try:
            if os.path.getmtime(path) < started_at - 2:
                continue                    # 不是这次动过的，别碰
            if os.path.isdir(path):
                if not os.listdir(path):
                    os.rmdir(path)
            elif os.path.getsize(path) == 0:
                os.remove(path)
        except OSError:
            pass


# ---------------------------------------------------------------- 诊断日志
_LOG_MAX = 512 * 1024


def _log(line):
    try:
        import paths
        os.makedirs(paths.log_dir(), exist_ok=True)
        path = os.path.join(paths.log_dir(), "engines.log")
    except Exception:
        return
    try:
        if os.path.exists(path) and os.path.getsize(path) > _LOG_MAX:
            os.replace(path, path + ".1")
        with open(path, "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (time.strftime("%m-%d %H:%M:%S"), line))
    except OSError:
        pass


# ---------------------------------------------------------------- 主流程
def run(chain, t, out_dir, progress, opt):
    """按顺序试 chain 里的引擎，返回第一个成功的结果。

    全挂了就把「最像根因」的那个异常抛出去 —— 优先不是「网络超时」这种
    最没信息量的失败，而是第一个真正说明问题的。
    """
    os.makedirs(out_dir, exist_ok=True)
    deadline = time.time() + BUDGET
    errors = []
    best = None                              # 最有信息量的失败（第一个「不是网络问题」的）

    for i, eng in enumerate(chain):
        if time.time() > deadline:
            errors.append((eng.name, "整条链超时"))
            break
        before = _snapshot(out_dir)
        started = time.time()
        try:
            if i:                            # 换引擎时把进度收回去，别让用户看见 47% 卡住
                progress(stage="解析中…", percent=0, speed=0)
            res = eng.fn(t, out_dir, progress, opt)
            _log("OK   %-14s %s" % (eng.name, _short(t.url)))
            return res
        except Exception as exc:             # noqa: BLE001 —— 一个引擎怎么挂都不能拖垮整条链
            if isinstance(exc, Canceled) or type(exc).__name__ == "DownloadCanceled":
                raise
            _cleanup(out_dir, before, started)
            errors.append((eng.name, str(exc)[:300]))
            _log("FAIL %-14s %-22s %s" % (eng.name, _short(t.url), str(exc)[:200]))
            fatal = is_fatal(exc)
            if best is None:                 # 第一个失败最有信息量，就用它报错
                best = (eng, exc)
            if fatal:
                raise

    # 全挂了
    if errors:
        _log("ALL  %-22s %s" % (_short(t.url), " | ".join("%s:%s" % e for e in errors)))
    if best is not None:
        raise best[1]
    raise RuntimeError("这条链接没能解析出来，换分享里的完整短链再试一次")


def _short(url, limit=48):
    url = (url or "").replace("\n", " ")
    return url if len(url) <= limit else url[:limit] + "…"


# ================================================================ 各个引擎
# 约定：fn(t, out_dir, progress, opt) -> 单个文件路径，或 (目录, 文件列表)
# 抛异常就算这个引擎失败，由 run() 决定要不要换下一个。

def _ytdlp(t, out_dir, progress, opt):
    import core
    return core.ytdlp_download(
        t.url, out_dir, progress, opt["quality"], opt["proxy"], opt["cookies"],
        subs=bool(opt.get("subs")), title_hint=t.title_hint)


def _douyin(t, out_dir, progress, opt):
    import core
    return core.douyin_download(t.url, out_dir, progress, opt["quality"])


def _xhs(t, out_dir, progress, opt):
    import cn_sites
    return cn_sites.xhs_download(t.url, out_dir, progress, opt["quality"], opt["cookies"])


def _kuaishou(t, out_dir, progress, opt):
    import cn_sites
    return cn_sites.kuaishou_download(t.url, out_dir, progress, opt["quality"], opt["cookies"])


def _bili_web(t, out_dir, progress, opt):
    import bili_web
    return bili_web.download(t.url, out_dir, progress, opt["quality"],
                             opt["proxy"], opt["cookies"])


def _weibo(t, out_dir, progress, opt):
    import weibo
    return weibo.download(t.url, out_dir, progress, opt["quality"],
                          opt["proxy"], opt["cookies"])


def _sph(t, out_dir, progress, opt):
    import sph
    return sph.download(t.url, out_dir, progress, opt["quality"],
                        opt.get("sph_api") or "", opt.get("sph_token") or "")


def _hls(t, out_dir, progress, opt):
    import hls
    if hls.is_audio_name(t.url):
        t.kind = "audio"
    return hls.download(t.url, out_dir, progress, opt["quality"], opt["proxy"])


def _netdisk(t, out_dir, progress, opt):
    import core
    return core._netdisk_download(t, out_dir, progress)


def _music(t, out_dir, progress, opt):
    import music
    t.kind = "audio"
    return music.download(t.url, out_dir, progress, opt["quality"], opt["cookies"])


def _suno(t, out_dir, progress, opt):
    import suno
    t.kind = "audio"
    return suno.download(t.url, out_dir, progress, opt["quality"], opt["proxy"])


# 页面里直接躺着 mp4 / m3u8 的站（yt-dlp 不认识的居多）。
# 只做「找地址 → 丢给 hls 下」，不下重手，免得白耗时间。
_PAGE_MEDIA_RE = None


def _page_media_urls(html, base):
    global _PAGE_MEDIA_RE
    import re
    if _PAGE_MEDIA_RE is None:
        _PAGE_MEDIA_RE = re.compile(
            r"""(?:https?:)?//[^\s"'<>\\]+?\.(?:mp4|m3u8|flv)(?:\?[^\s"'<>\\]*)?""", re.I)
    # 页面里的地址常被转义成 https:\/\/cdn…，不还原就一个都匹配不上
    text = (html or "").replace("\\/", "/")
    out, seen = [], set()
    for raw in _PAGE_MEDIA_RE.findall(text):
        u = raw
        if u.startswith("//"):
            u = ("https:" if base.startswith("https") else "http:") + u
        if u in seen:
            continue
        # 广告位、统计用的假 mp4 常见，太短的名字跳过
        if len(u.split("?")[0].split("/")[-1]) < 6:
            continue
        seen.add(u)
        out.append(u)
    return out


def _page_direct(t, out_dir, progress, opt):
    import core
    import hls
    progress(stage="解析中…")
    html = core.http_get(t.url, {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    }, timeout=20).read().decode("utf-8", "ignore")
    urls = _page_media_urls(html, t.url)
    if not urls:
        raise RuntimeError("页面里没找到视频地址，换个来源再试")
    # m3u8 优先（多数是完整流），其次 mp4
    urls.sort(key=lambda u: 0 if ".m3u8" in u.lower() else 1)
    return hls.download(urls[0], out_dir, progress, opt["quality"], opt["proxy"])


# ================================================================ 平台 → 链
def chain_for(platform):
    """按「成功率高、画质好」排序。第一个挂了才轮到后面，用户看不到这些名字。"""
    if platform == "douyin":
        return [Engine("douyin", "抖音分享页", _douyin),
                Engine("ytdlp", "yt-dlp", _ytdlp)]
    if platform == "xiaohongshu":
        return [Engine("xhs", "小红书分享页", _xhs),
                Engine("ytdlp", "yt-dlp", _ytdlp)]
    if platform == "kuaishou":
        return [Engine("kuaishou", "快手分享页", _kuaishou),
                Engine("page", "网页直链", _page_direct)]
    if platform == "bilibili":
        return [Engine("ytdlp", "yt-dlp", _ytdlp),
                Engine("bili_web", "B站播放接口", _bili_web)]
    if platform == "weibo":
        return [Engine("ytdlp", "yt-dlp", _ytdlp),
                Engine("weibo", "微博移动接口", _weibo)]
    if platform == "weixin":
        return [Engine("sph", "视频号接口", _sph)]
    if platform == "direct":
        return [Engine("hls", "直链 / HLS", _hls),
                Engine("ytdlp", "yt-dlp", _ytdlp)]
    if platform.startswith("netdisk:"):
        return [Engine("netdisk", "网盘", _netdisk)]
    if platform in ("netease", "qqmusic", "kugou"):
        return [Engine("music", "音乐源", _music)]
    if platform == "suno":
        return [Engine("suno", "Suno", _suno)]
    if platform == "unknown":
        return [Engine("ytdlp", "yt-dlp", _ytdlp),
                Engine("page", "网页直链", _page_direct)]
    # 剩下的是 yt-dlp 覆盖得不错的站（YouTube / TikTok / X / Instagram …）
    return [Engine("ytdlp", "yt-dlp", _ytdlp)]

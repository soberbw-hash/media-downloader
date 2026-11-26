# -*- coding: utf-8 -*-
"""多引擎回退：一条链接在后台挨个试，用户只看到一次下载。

这里守三件事，任何一件坏了都是「用户明明贴的是好链接却下不了」：
1. 前一个引擎挂了，后一个能接上；
2. 链接本身错了（主页 / 已删 / 私密）别傻试，立刻报；
3. 用户点了取消，绝不许换引擎继续跑。

    python tests/test_engines.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import core        # noqa: E402
import engines     # noqa: E402


def log(*a):
    print(*a, flush=True)


def check(ok, msg):
    if not ok:
        raise AssertionError(msg)
    log("✓", msg)


def _task(url="https://example.com/v", plat="douyin"):
    return core.Task("1", url, plat, plat)


def _opt():
    return {"quality": "best", "proxy": None, "cookies": None, "subs": False}


def _engine(name, fn):
    return engines.Engine(name, name, fn)


# ---------------------------------------------------------------- 链定义
def test_chains():
    expect = {
        "douyin": ["douyin", "ytdlp"],
        "xiaohongshu": ["xhs", "ytdlp"],
        "bilibili": ["ytdlp", "bili_web"],
        "weibo": ["ytdlp", "weibo"],
        "direct": ["hls", "ytdlp"],
        "unknown": ["ytdlp", "page"],
        "kuaishou": ["kuaishou"],
        "youtube": ["ytdlp"],
        "netease": ["music"],
        "netdisk:baidu": ["netdisk"],
    }
    for plat, names in expect.items():
        got = [e.name for e in engines.chain_for(plat)]
        check(got == names, "%s 的引擎链是 %s" % (plat, names))

    # 每个平台都得有兜底，不能出现空链
    from core import PLATFORMS
    for p in PLATFORMS:
        chain = engines.chain_for(p["id"])
        check(len(chain) >= 1, "%s 至少有一个引擎" % p["id"])


# ---------------------------------------------------------------- 回退
def test_fallback():
    out = tempfile.mkdtemp(prefix="eng_fb_")
    calls = []

    def first(t, d, progress, opt):
        calls.append("first")
        open(os.path.join(d, "半截.part"), "w").write("x")   # 模拟下了半截
        open(os.path.join(d, "空壳.mp4"), "w").close()        # 0 字节，纯垃圾
        raise RuntimeError("没拿到播放地址")

    def second(t, d, progress, opt):
        calls.append("second")
        p = os.path.join(d, "成品.mp4")
        open(p, "w").write("video")
        return p

    res = engines.run([_engine("a", first), _engine("b", second)],
                      _task(), out, lambda **k: None, _opt())
    check(calls == ["first", "second"], "第一个失败后自动换第二个")
    check(os.path.basename(res) == "成品.mp4", "拿到的是第二个引擎的成果")
    left = os.listdir(out)
    check("半截.part" in left, "断点续传的 .part 留着（点重试要接着下）")
    check("空壳.mp4" not in left, "0 字节的空壳被清掉")


def test_progress_reset_between_engines():
    """换引擎时进度要收回 0，否则进度条停在 47% 让人以为卡死。"""
    out = tempfile.mkdtemp(prefix="eng_pg_")
    seen = []

    def first(t, d, progress, opt):
        progress(percent=47, stage="下载中")
        raise RuntimeError("网络超时")

    def second(t, d, progress, opt):
        progress(percent=100)
        p = os.path.join(d, "ok.mp4")
        open(p, "w").write("v")
        return p

    def prog(**kw):
        if "percent" in kw:
            seen.append(kw["percent"])

    engines.run([_engine("a", first), _engine("b", second)], _task(), out, prog, _opt())
    check(seen[:3] == [47, 0, 100], "进度 47 → 换引擎归零 → 100，不会吊在半路")


# ---------------------------------------------------------------- 该不该换
def test_fatal_stops():
    out = tempfile.mkdtemp(prefix="eng_ft_")
    calls = []

    def bad_link(t, d, progress, opt):
        calls.append("bad")
        raise RuntimeError("分享已经失效了")

    def ok_engine(t, d, progress, opt):
        calls.append("ok")
        p = os.path.join(d, "x.mp4")
        open(p, "w").write("v")
        return p

    try:
        engines.run([_engine("bad", bad_link), _engine("ok", ok_engine)],
                    _task(), out, lambda **k: None, _opt())
        raise AssertionError("失效链接应该直接报错")
    except AssertionError:
        raise
    except RuntimeError as e:
        check("失效" in str(e), "链接失效直接报错，不白试第二个引擎")
    check(calls == ["bad"], "只试了一次，没有多余重试")


def test_cancel_stops():
    out = tempfile.mkdtemp(prefix="eng_cc_")
    calls = []

    def user_cancel(t, d, progress, opt):
        calls.append("cancel")
        raise core.DownloadCanceled("已取消")

    def ok_engine(t, d, progress, opt):
        calls.append("ok")
        return os.path.join(d, "x.mp4")

    try:
        engines.run([_engine("c", user_cancel), _engine("ok", ok_engine)],
                    _task(), out, lambda **k: None, _opt())
        raise AssertionError("取消应该抛出去")
    except core.DownloadCanceled:
        log("✓ 取消异常原样抛给上层")
    check(calls == ["cancel"], "取消之后绝不换引擎")


def test_login_wall_switches():
    """登录墙算「换个来源可能就行」，不许当成终止条件。"""
    out = tempfile.mkdtemp(prefix="eng_lg_")
    calls = []

    def need_login(t, d, progress, opt):
        calls.append("login")
        raise RuntimeError("需要登录才能看")

    def ok_engine(t, d, progress, opt):
        calls.append("ok")
        p = os.path.join(d, "y.mp4")
        open(p, "w").write("v")
        return p

    engines.run([_engine("l", need_login), _engine("ok", ok_engine)],
                _task(), out, lambda **k: None, _opt())
    check(calls == ["login", "ok"], "要登录 ≠ 没救了，换下一个引擎再试")


def test_fatal_keep_user_file():
    """清理只碰空壳，同名成品、用户文件、续传凭据都不能动。"""
    out = tempfile.mkdtemp(prefix="eng_keep_")
    keep = os.path.join(out, "用户原来的.mp4")
    open(keep, "w").write("mine")
    before = engines._snapshot(out)
    started = 0                     # 很久以前就开始，条件最宽松
    open(os.path.join(out, "new.part"), "w").write("x")     # 续传凭据
    open(os.path.join(out, "new.mp4"), "w").write("x")      # 正常成品
    open(os.path.join(out, "empty.tmp"), "w").close()       # 空壳
    engines._cleanup(out, before, started)
    left = sorted(os.listdir(out))
    check("用户原来的.mp4" in left, "用户原有文件不动")
    check("new.mp4" in left, "正常名字的成品不清（宁可留也不误删）")
    check("new.part" in left, "续传用的 .part 不清")
    check("empty.tmp" not in left, "0 字节空壳清掉")


def test_all_failed():
    out = tempfile.mkdtemp(prefix="eng_all_")

    def a(t, d, progress, opt):
        raise RuntimeError("没解析出播放地址")

    def b(t, d, progress, opt):
        raise RuntimeError("签名不对")

    try:
        engines.run([_engine("a", a), _engine("b", b)], _task(), out,
                    lambda **k: None, _opt())
        raise AssertionError("全挂应该抛错")
    except RuntimeError as e:
        check("播放地址" in str(e) or "签名" in str(e),
              "全挂时报第一个失败原因：%s" % e)


def test_fatal_table():
    fatal = [
        "分享已经失效了", "作品可能被删了", "这是抖音主页，不是单条作品",
        "磁盘空间不足", "拒绝访问", "这个站没有公开接口，下不了",
    ]
    retry = [
        "需要登录才能看", "登录过期了", "触发了风控（300012）",
        "网络超时", "签名不对", "参数不对", "解析不出笔记内容",
    ]
    for msg in fatal:
        check(engines.is_fatal(RuntimeError(msg)) is True, "终止：%s" % msg)
    for msg in retry:
        check(engines.is_fatal(RuntimeError(msg)) is False, "可重试：%s" % msg)


def test_page_media_regex():
    html = ('<script>var a="https:\\/\\/cdn.x.com\\/v\\/abc123.mp4?k=1";'
            'var b="//cdn.y.com/live/index.m3u8";var c="https://t.co/a.mp4"</script>')
    urls = engines._page_media_urls(html, "https://example.com/page")
    check("https://cdn.x.com/v/abc123.mp4?k=1" in urls, "页面里转义的 mp4 地址能认出来")
    check("https://cdn.y.com/live/index.m3u8" in urls, "// 开头的 m3u8 补成 https")
    check(all("t.co" not in u for u in urls), "太短的文件名当作广告位跳过")


if __name__ == "__main__":
    test_chains()
    test_fallback()
    test_progress_reset_between_engines()
    test_fatal_stops()
    test_cancel_stops()
    test_login_wall_switches()
    test_fatal_keep_user_file()
    test_all_failed()
    test_fatal_table()
    test_page_media_regex()
    log("ALL PASS")

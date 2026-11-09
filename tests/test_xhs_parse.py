# -*- coding: utf-8 -*-
"""小红书解析回归：脏 JSON 不许再炸，报错文案不许往「要登录」带偏。

背景：explore 页的 __INITIAL_STATE__ 里混着 JS 语法（new Map([]) / undefined），
以前 json.loads 直接抛，被包装成「笔记数据解析失败，可能需要登录 cookies」——
用户明明登着录也下不了，还被往登录方向带偏。

    python tests/test_xhs_parse.py
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import cn_sites  # noqa: E402


def log(*a):
    print(*a, flush=True)


# 页面里实测见过的脏样子：noteDetailMap 旁挂 new Map([])，还有 undefined
DIRTY_HTML = (
    '<html><script>window.__INITIAL_STATE__='
    '{"note":{"noteDetailMap":{"64a1b2c3d4e5f6a7b8c9d0e1":{"note":'
    '{"title":"测试标题","desc":"d","user":{"nickname":"作者"},"video":{}}}}},'
    '"AiNoteDetailStore":{"noteDetailMap":new Map([])},'
    '"SomeStore":{"set":new Set([])},"x":undefined}</script></html>'
)


def test_sanitize():
    s = cn_sites._sanitize_state_json('{"a":new Map([]),"b":new Set([]),"c":undefined}')
    assert s == '{"a":{},"b":[],"c":null}', s
    # 带空白的写法也要认
    s2 = cn_sites._sanitize_state_json('{"a":new Map( [ ] )}')
    assert s2 == '{"a":{}}', s2
    log("✓ 清洗：new Map([]) / new Set([]) / undefined 都洗成合法 JSON")


def test_parse_dirty_state():
    note = cn_sites._xhs_note_from_html(DIRTY_HTML, "64a1b2c3d4e5f6a7b8c9d0e1")
    assert note.get("title") == "测试标题", note
    assert (note.get("user") or {}).get("nickname") == "作者"
    log("✓ 解析：混着 new Map([])/undefined 的 __INITIAL_STATE__ 能抠出笔记")


def test_error_wording():
    # JSON 真解析不了：不许再提登录/cookies（那是把人带偏的老文案）
    try:
        cn_sites._xhs_note_from_html(
            '<html><script>__INITIAL_STATE__={bad json}</script></html>', "x")
        assert False, "应该抛错"
    except RuntimeError as e:
        msg = str(e)
        assert "登录" not in msg and "cookies" not in msg, msg
        assert "反馈" in msg or "重试" in msg, msg
        log("✓ 文案：解析失败 →", msg)

    # 页面里根本没有 state：说清楚多半是没登录，给「设置 → 账号」出路
    try:
        cn_sites._xhs_note_from_html("<html>no state</html>", "x")
        assert False, "应该抛错"
    except RuntimeError as e:
        msg = str(e)
        assert "登录" in msg and "设置" in msg, msg
        log("✓ 文案：没 state →", msg)

    # state 在但笔记抠不出来：删除/私密/登录三个可能都说清
    try:
        cn_sites._xhs_note_from_html(
            '<html><script>__INITIAL_STATE__={"note":{"noteDetailMap":{}}}</script></html>',
            "x")
        assert False, "应该抛错"
    except RuntimeError as e:
        msg = str(e)
        assert "删除" in msg, msg
        log("✓ 文案：空笔记 →", msg)


def test_video_urls_new_codec_keys():
    """编码键从 h264/h265 改名 EF4/EF5 之后，播放地址还得收得到。"""
    note = {
        "type": "video",
        "video": {
            "media": {"stream": {
                # 新名字（页面现状）
                "EF4": [{"masterUrl": "http://a/258.mp4", "width": 720, "height": 1280,
                         "videoBitrate": 2284639, "backupUrls": ["http://bak/258.mp4"]}],
                "EF5": [{"masterUrl": "http://a/520.mp4", "width": 720, "height": 1280,
                         "videoBitrate": 1146799}],
                "EF6": [],
                # 旧名字也得认（老页面/缓存）
                "h264": [{"masterUrl": "http://a/old.mp4", "width": 1920, "height": 1080,
                          "videoBitrate": 999}],
            }},
            "mediaV2": json.dumps({"stream": {
                "EF5": [{"master_url": "http://a/v2.mp4", "width": 720, "height": 1280,
                         "videoBitrate": 1867524, "backup_urls": ["http://bak/v2.mp4"]}]
            }}),
        },
    }
    urls = cn_sites._xhs_video_urls(note, "best")
    # 码率最高的 258.mp4 应排第一（1080p 的旧地址像素更高、排它前面）
    assert urls[0] == "http://a/old.mp4", urls
    assert "http://a/258.mp4" in urls and "http://a/520.mp4" in urls
    assert "http://a/v2.mp4" in urls          # mediaV2 里的也要
    assert "http://bak/258.mp4" in urls       # 备用地址跟在主地址后
    assert urls.index("http://a/258.mp4") < urls.index("http://bak/258.mp4")
    log("✓ 取址：EF4/EF5 新编码键 + mediaV2 + 备用地址都收得到")

    # 720p 档：短边 > 720 的（1920x1080 横版短边是 1080）让位
    urls720 = cn_sites._xhs_video_urls(note, "720")
    assert "http://a/old.mp4" not in urls720, urls720
    assert urls720[0] in ("http://a/258.mp4", "http://a/v2.mp4"), urls720
    log("✓ 取址：quality=720 只留 720p 档")


def test_core_wrapper_not_misleading():
    """core._run_wrapper 不许把非登录错误改写成「要登录」。"""
    os.environ["VDL_DATA_DIR"] = tempfile.mkdtemp(prefix="vdl_xhs_")
    os.environ["VDL_NO_OPEN"] = "1"
    import core

    class FakeTask(object):
        def __init__(self):
            self.id = 1
            self.url = "https://www.xiaohongshu.com/explore/64a1b2c3d4e5f6a7b8c9d0e1"
            self.platform = "xiaohongshu"
            self.platform_name = "小红书"
            self.status = "queued"
            self.stage = ""
            self.error = ""
            self.title = ""
            self.title_locked = False
            self.title_hint = ""
            self.author = ""
            self.thumb = ""
            self.duration = 0
            self.desc = ""
            self.percent = 0
            self.speed = ""
            self.size = 0
            self.kind = "video"
            self.images = []
            self.filepath = ""
            self.proxy = ""
            self.created = 0
            self.key = ""

    mgr = core.Manager({"out_dir": tempfile.mkdtemp(prefix="vdl_out_")})
    real = cn_sites.xhs_download

    # 场景 1：解析失败（非登录原因）—— 原样透传，别安「要登录」
    def boom_parse(*a, **k):
        raise RuntimeError("小红书页面数据读不出来。重试一次，还不行就反馈")
    cn_sites.xhs_download = boom_parse
    t = FakeTask()
    mgr._run_wrapper(t)
    assert t.status == "error", (t.status, t.error)
    assert "要登录" not in t.error, t.error
    assert "读不出来" in t.error, t.error
    log("✓ core：非登录错误原样透传 →", t.error)

    # 场景 2：真的是登录问题 —— 照旧给出路
    def boom_login(*a, **k):
        raise RuntimeError("小红书没给笔记数据，一般是没登录。去「设置 → 账号」登录后重试")
    cn_sites.xhs_download = boom_login
    t2 = FakeTask()
    mgr._run_wrapper(t2)
    assert "登录" in t2.error and "设置" in t2.error, t2.error
    log("✓ core：登录问题仍有出路 →", t2.error)

    # 场景 3：裸网络异常兜底也说人话
    def boom_net(*a, **k):
        raise RuntimeError("urlopen error timed out")
    cn_sites.xhs_download = boom_net
    t3 = FakeTask()
    mgr._run_wrapper(t3)
    assert "重试" in t3.error, t3.error
    log("✓ core：裸异常兜底 →", t3.error)

    cn_sites.xhs_download = real


if __name__ == "__main__":
    test_sanitize()
    test_parse_dirty_state()
    test_error_wording()
    test_video_urls_new_codec_keys()
    test_core_wrapper_not_misleading()
    print("ALL PASS", flush=True)

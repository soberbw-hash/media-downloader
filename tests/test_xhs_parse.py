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


def test_expired_short_link():
    """失效短链跳 404 时，必须当场报错，不能返回空 ID 含糊过去。"""
    real = cn_sites.http_get
    try:
        class R:
            def geturl(self):
                return "https://www.xiaohongshu.com/404/sec_x?source=xhs_sec_server"

            def read(self, *a):
                return b""

        cn_sites.http_get = lambda *a, **k: R()
        try:
            cn_sites.xhs_resolve("https://xhslink.com/o/deadlink")
            raise AssertionError("失效短链应该抛错")
        except RuntimeError as e:
            assert "失效" in str(e) or "重新" in str(e), str(e)
            log("✓ 失效短链 →", str(e))

        # 短链打开了却没跳到笔记页，也要说清楚
        class R2:
            def geturl(self):
                return "https://www.xiaohongshu.com/explore"

            def read(self, *a):
                return b""

        cn_sites.http_get = lambda *a, **k: R2()
        try:
            cn_sites.xhs_resolve("https://xhslink.cn/o/weird")
            raise AssertionError("没笔记ID的短链应该抛错")
        except RuntimeError as e:
            assert "重新" in str(e) or "失效" in str(e), str(e)
            log("✓ 短链没指向笔记 →", str(e))
    finally:
        cn_sites.http_get = real


def test_login_expired_wording():
    """页面正常、但 loggedIn=false 且无笔记数据 → 明确说登录过期。"""
    html = (
        '<html><script>window.__INITIAL_STATE__='
        '{"user":{"loggedIn":false},"note":{"noteDetailMap":{}}}'
        '</script></html>'
    )
    try:
        cn_sites._xhs_note_from_html(html, "6a7c4784000000003300a1bc")
        raise AssertionError("登录墙应该抛错")
    except RuntimeError as e:
        assert "登录过期" in str(e) and "设置" in str(e), str(e)
        log("✓ 登录墙 →", str(e))


# ---------- 2026-11 免登录改造：App 分享页那条路 ----------
MOBILE_HTML = (
    '<html><script>window.__INITIAL_STATE__={"noteData":{"data":{"noteData":'
    '{"noteId":"6a9d0eb2000000002a024480","type":"video","title":"视频标题","desc":"第一行\\n第二行",'
    '"user":{"nickName":"阿空"},'
    '"imageList":[{"url":"http://img/1!h5_1080jpg"}],'
    '"video":{"media":{"stream":{"EF4":[{"masterUrl":"http://v/1.mp4","width":1080,'
    '"height":1920,"videoBitrate":900}]}}}},'
    '"commentData":{}},"normalNotePreloadData":{"imagesList":[{"urlSizeLarge":"http://img/1large"}]}}}'
    '</script></html>'
)


def test_mobile_share_structure():
    """手机分享页把笔记放在 noteData.data.noteData，不是 noteDetailMap。"""
    note = cn_sites._xhs_note_from_html(MOBILE_HTML, "6a9d0eb2000000002a024480")
    assert note["title"] == "视频标题", note
    assert (note.get("user") or {}).get("nickName") == "阿空"
    urls = cn_sites._xhs_video_urls(note)
    assert urls == ["http://v/1.mp4"], urls
    imgs = cn_sites.xhs_images(note)
    # imageList 有图就用它，preload 那批只在前者缺失时兜底（不然会下一堆重复图）
    assert imgs == ["http://img/1!h5_1080jpg"], imgs
    only_pre = {"imageList": [], "_preloadImages": [{"urlSizeLarge": "http://img/2large"}]}
    assert cn_sites.xhs_images(only_pre) == ["http://img/2large"]
    log("✓ 分享页：noteData.data.noteData 里的标题/作者/视频/图片都取得到")


def test_token_hidden_in_login_redirect():
    """桌面 UA 打开短链会被踢到 /login?redirectPath=…，token 藏在里面，不许丢。"""
    import urllib.parse

    real = cn_sites.http_get
    target = ("http://www.xiaohongshu.com/discovery/item/67a64f1e000000001800ade1"
              "?xsec_source=app_share&type=normal&xsec_token=CBCMxHh3%2FHLz%2Btok%3D")
    login = ("https://www.xiaohongshu.com/login?redirectPath="
             + urllib.parse.quote(target, safe=""))

    class R:
        def geturl(self):
            return login

        def read(self, *a):
            return b""

    try:
        cn_sites.http_get = lambda *a, **k: R()
        nid, token, final = cn_sites.xhs_resolve("https://xhslink.cn/o/9UWBdRy7GUA")
        assert nid == "67a64f1e000000001800ade1", nid
        assert token == "CBCMxHh3/HLz+tok=", token
        assert "discovery/item" in final and "xsec_token" in final, final
        log("✓ token 还原：登录跳转里的 redirectPath 被拆开了")
    finally:
        cn_sites.http_get = real


def test_candidates_and_anonymous_order():
    """候选顺序要对：先原样分享链（手机 UA + app_share），并且匿名先试。"""
    calls = []

    class R:
        def __init__(self, body):
            self.body = body

        def geturl(self):
            return ""

        def read(self, *a):
            return self.body

    def fake_get(url, hdr=None, **k):
        calls.append((url, dict(hdr or {})))
        if "discovery/item" in url:
            return R(MOBILE_HTML.encode())
        return R(b'<html><script>window.__INITIAL_STATE__={"user":{"loggedIn":false},'
                 b'"note":{"noteDetailMap":{}}}</script></html>')

    real = cn_sites.http_get
    try:
        cn_sites.http_get = fake_get
        nid, token, final = cn_sites.xhs_resolve(
            "https://www.xiaohongshu.com/discovery/item/6a9d0eb2000000002a024480"
            "?xsec_token=TOK%3D&xsec_source=app_share")
        cands = cn_sites._xhs_candidates(nid, token, final)
        assert "xsec_source=app_share" in cands[0][0], cands[0][0]
        assert "iPhone" in cands[0][1], cands[0][1]

        note = cn_sites._xhs_fetch_note(nid, token, final, None, lambda **k: None)
        assert note["title"] == "视频标题", note
        assert calls and "Cookie" not in calls[0][1], calls[0][1]
        log("✓ 请求顺序：先原样分享链 + 手机 UA，且不带 cookie")
    finally:
        cn_sites.http_get = real


def test_risk_control_wording():
    """300012 是网络风控，不是没登录 —— 别再把用户往登录页赶。"""
    html = '<html><body>安全限制 当前网络存在风险，请切换可靠网络后重试 300012</body></html>'
    try:
        cn_sites._xhs_note_from_html(html, "x")
        raise AssertionError("风控页应该抛错")
    except RuntimeError as e:
        msg = str(e)
        assert "300012" in msg, msg
        assert "设置 → 账号" not in msg and "登录后" not in msg, msg
        log("✓ 风控 →", msg)


def test_truncated_state_regex_fallback():
    """state 被截断也别放弃：正则兜底还能捞到播放地址。"""
    html = ('<html><meta property="og:title" content="截断的笔记">'
            '<script>window.__INITIAL_STATE__={"note":{"noteDetailMap":{"x":{"note":'
            '{"title":"半截')
    note = cn_sites._xhs_note_from_regex(
        html + '"video":{"media":{"stream":{"EF4":[{"masterUrl":"http://v/cut.mp4"}]}}}}')
    assert note and cn_sites._xhs_video_urls(note) == ["http://v/cut.mp4"], note
    log("✓ 截断兜底：正则从残页里捞出了播放地址")



if __name__ == "__main__":
    test_sanitize()
    test_parse_dirty_state()
    test_error_wording()
    test_video_urls_new_codec_keys()
    test_core_wrapper_not_misleading()
    test_expired_short_link()
    test_login_expired_wording()
    test_mobile_share_structure()
    test_token_hidden_in_login_redirect()
    test_candidates_and_anonymous_order()
    test_risk_control_wording()
    test_truncated_state_regex_fallback()
    print("ALL PASS", flush=True)

# -*- coding: utf-8 -*-
"""B 站网页接口兜底：地址识别、wbi 签名、番剧分支。

    python tests/test_bili_web.py
"""
import os
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bili_web  # noqa: E402


def log(*a):
    print(*a, flush=True)


def check(ok, msg):
    if not ok:
        raise AssertionError(msg)
    log("✓", msg)


def test_ids():
    cases = {
        "https://www.bilibili.com/video/BV1NMW86XEdA/": ("BV1NMW86XEdA", "", 1),
        "https://www.bilibili.com/video/BV1NMW86XEdA?p=3": ("BV1NMW86XEdA", "", 3),
        "https://www.bilibili.com/video/av170001": ("", "170001", 1),
        "https://m.bilibili.com/video/BV1xx411c7mD?spm=1": ("BV1xx411c7mD", "", 1),
    }
    for url, want in cases.items():
        got = bili_web._ids(url)
        check(got == want, "%s → %s" % (url[:46], got))
    try:
        bili_web._ids("https://www.bilibili.com/bangumi/play/ep123")
        raise AssertionError("番剧页不该走普通视频分支")
    except AssertionError:
        raise
    except RuntimeError as e:
        check("单个视频" in str(e), "番剧页交给 _episode，不在这里硬猜")


def test_quality_map():
    check(bili_web._QUALITY_DESC["best"] == 80, "best 对应 1080P（qn=80）")
    check(bili_web._QUALITY_DESC["480p"] == 32, "480p 对应 qn=32")


def test_mixin_key():
    # 真实 nav 接口给的就是两个图片地址，文件名（去掉扩展名）拼起来 64 位
    img = "https://i0.hdslb.com/bfs/wbi/7cd084941338484aae1ad9425b84077c.png"
    sub = "https://i0.hdslb.com/bfs/wbi/4932caff0ff746eab6f01bf08b70ac45.png"
    key = bili_web._mixin_key(img, sub)
    check(len(key) == 32, "重排后取前 32 位：%s" % key)
    check(set(key) <= set("0123456789abcdef"), "结果是一串十六进制字符")
    check(bili_web._mixin_key("", "") == "", "拿不到密钥时返回空串，不炸")


def test_signed():
    query = bili_web._signed({"cid": 1, "bvid": "BV1xx411c7mD", "qn": 80}, "abcdefghijklmnopqrstuvwxyz012345")
    qs = urllib.parse.parse_qs(query)
    check("w_rid" in qs and len(qs["w_rid"][0]) == 32, "签名带上 w_rid")
    check("wts" in qs, "签名带上时间戳")
    check(qs["bvid"][0] == "BV1xx411c7mD", "参数原样带着")
    # 同样的参数两次签名，wts 变了签名也该变 —— 这里只确认不会撞固定值
    check(bili_web._signed({"a": "1!'()*"}, "k").find("%21") == -1, "特殊字符不参与签名")


def test_episode_branch():
    """番剧页要先问 pgc 接口要 cid，拿到就走同一条 playurl。"""
    real = bili_web._get_json

    def fake(url, op, cookie="", **kw):
        if "pgc/view/web/season" in url:
            return {"title": "测试番", "episodes": [
                {"id": 111, "bvid": "BV1aaa", "aid": 222, "cid": 333},
                {"id": 112, "bvid": "BV1bbb", "aid": 223, "cid": 334},
            ]}
        return {}

    try:
        bili_web._get_json = fake
        got = bili_web._episode("https://www.bilibili.com/bangumi/play/ep112", None)
        check(got == ("BV1bbb", 223, 334, "112"), "按 ep 号挑到对应那一集：%s" % (got,))
        check(bili_web._episode("https://www.bilibili.com/video/BV1xx", None) is None,
              "普通视频不走番剧分支")
    finally:
        bili_web._get_json = real


def test_playurl_params():
    """番剧必须带 ep_id，普通视频不能带 —— 带上会 404。"""
    real = bili_web._get_json
    seen = []

    def fake(url, op, cookie="", **kw):
        seen.append(url)
        return {"durl": [{"url": "https://cdn/x.mp4"}], "quality": 64}

    try:
        bili_web._get_json = fake
        # 没有 wbi 密钥时走无签名分支
        bili_web._wbi_keys = lambda *a, **k: ""
        bili_web._playurl("BV1xx", "", 333, 80, None, "", "112")
        check("ep_id=112" in seen[-1], "番剧地址带上 ep_id")
        bili_web._playurl("BV1xx", "", 333, 80, None, "")
        check("ep_id" not in seen[-1], "普通视频不带 ep_id")
    finally:
        bili_web._get_json = real


if __name__ == "__main__":
    test_ids()
    test_quality_map()
    test_mixin_key()
    test_signed()
    test_episode_branch()
    test_playurl_params()
    log("ALL PASS")

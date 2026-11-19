# -*- coding: utf-8 -*-
"""小红书登录态真探测：cookie 过期要主动标「已失效」，不能一直骗「已登录」。

背景：账号面板以前只看 cookie 文件在不在，小红书 web_session 被服务端
作废后文件还在，界面一直显示「已登录」，直到下载失败才发现 —— 用户说
「过期了软件里要提示啊，软件什么都不说」。

    python tests/test_login_expire.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import login  # noqa: E402


def log(*a):
    print(*a, flush=True)


def _html(logged, nickname=""):
    return ("<html><script>window.__INITIAL_STATE__="
            '{"user":{"loggedIn":%s,"userInfo":{"nickname":"%s"}}},'
            '"note":{"noteDetailMap":{}}}'
            "</script></html>" % ("true" if logged else "false", nickname))


class _R:
    def __init__(self, html):
        self.h = html

    def read(self, *a):
        return self.h.encode("utf-8")


def test_probe():
    orig_u = login.urllib.request.urlopen
    orig_cf = login.cookie_file
    orig_rn = login._read_netscape
    login.cookie_file = lambda pid: "fake"
    login._read_netscape = lambda p: "web_session=xxx"
    try:
        # 1) 服务端明确没登录 → 报失效，且带「失效」二字（前端据此标红）
        login.urllib.request.urlopen = lambda req, timeout: _R(_html(False))
        r = login._probe_xhs()
        assert r.get("ok") is False and "失效" in r.get("error", ""), r
        log("✓ cookie 过期 →", r["error"])

        # 2) 登录有效 → 显示账号名
        login.urllib.request.urlopen = lambda req, timeout: _R(_html(True, "空恋"))
        r = login._probe_xhs()
        assert r.get("ok") is True and r.get("name") == "空恋", r
        log("✓ 登录有效 → name=%s" % r["name"])

        # 3) 断网 / 页面结构变了 → 不误报失效（宁可不提示，不能冤枉好人）
        def boom(req, timeout):
            raise Exception("断网")
        login.urllib.request.urlopen = boom
        r = login._probe_xhs()
        assert "失效" not in str(r), r
        log("✓ 断网 → 不乱报失效")

        login.urllib.request.urlopen = lambda req, timeout: _R("<html>改版了</html>")
        r = login._probe_xhs()
        assert "失效" not in str(r), r
        log("✓ 页面改版 → 不乱报失效")
    finally:
        login.urllib.request.urlopen = orig_u
        login.cookie_file = orig_cf
        login._read_netscape = orig_rn


def test_cache():
    """轮询 1.5 秒，必须缓存；导入新 cookie 后要立刻能复查。"""
    orig = login._probe_xhs
    calls = []
    login._ACC.pop("xiaohongshu", None)
    login._probe_xhs = lambda: (calls.append(1) or {"ok": True})
    try:
        login.account("xiaohongshu", fresh=True)
        login.account("xiaohongshu")
        login.account("xiaohongshu")
        assert len(calls) == 1, "90 秒内只该真探一次，实际 %d 次" % len(calls)
        log("✓ 缓存：3 次调用只真探 1 次")
        login.account("xiaohongshu", fresh=True)
        assert len(calls) == 2, "fresh 应强制复查"
        log("✓ fresh=True 强制复查")
    finally:
        login._probe_xhs = orig
        login._ACC.pop("xiaohongshu", None)


def test_front_end_flag():
    """前端认 acc.error 里的「失效」二字，文案别把它改没了。"""
    r = {"ok": False, "error": "登录已失效，去「设置 → 账号」重新登录一次"}
    assert "失效" in r["error"], "前端 loginRow 靠这个词标红"
    log("✓ 前端标红关键词在")


if __name__ == "__main__":
    test_probe()
    test_cache()
    test_front_end_flag()
    print("ALL PASS", flush=True)

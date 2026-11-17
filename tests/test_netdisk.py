# -*- coding: utf-8 -*-
"""网盘解析回归测试。单测不联网，实链路单独用 LIVE=1 打开。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import netdisk

PASS = [0]


def ok(cond, name):
    assert cond, name
    PASS[0] += 1
    print("  OK", name)


def test_parse():
    print("=== 链接识别 ===")
    cases = [
        ("https://pan.quark.cn/s/abc123 提取码: xk9d", "quark", "abc123", "xk9d"),
        ("https://drive.uc.cn/s/AbCdEf?pwd=1234", "uc", "AbCdEf", "1234"),
        ("pan.xunlei.com/s/FOO_bar-1 访问码：abcd", "xunlei", "FOO_bar-1", "abcd"),
        ("https://pan.baidu.com/s/1abCD12_x?pwd=8abc", "baidu", "abCD12_x", "8abc"),
        ("https://www.123pan.com/s/2785Vv-T4Ded 提取码: 1234", "123pan", "2785Vv-T4Ded", "1234"),
        ("https://115.com/s/swabcd1234?password=a1b2", "pan115", "swabcd1234", "a1b2"),
        ("https://www.guangyapan.com/s/abcDEF-1?pwd=xy12", "guangya", "abcDEF-1", "xy12"),
        ("https://wwt.lanzouw.com/inAe108hg9de", "lanzou", "inAe108hg9de", ""),
        ("https://www.lanzoup.com/abcd1234?pwd=abcd", "lanzou", "abcd1234", "abcd"),
    ]
    for text, pid, sid, pwd in cases:
        got = netdisk.parse_share(text)
        assert got, "没识别: " + text
        ok(got["platform"] == pid and got["share_id"] == sid and got["pwd"] == pwd,
           "%s -> %s/%s/pwd=%s" % (text[:34], pid, sid, pwd))
    ok(netdisk.parse_share("随便一段没有链接的文字") is None, "普通文字不误判")
    ok(netdisk.is_netdisk_url("https://pan.quark.cn/s/x1"), "is_netdisk_url")


def test_acw():
    print("=== acw_sc__v2 算法 ===")
    # 云析源码里的固定变换，用实测抓到的 arg1 验证输出稳定
    arg1 = "0B94EA9F37BADD5EAADDA7CB7683FC6BD2A71D02"
    a = netdisk.acw_sc_v2(arg1)
    b = netdisk.acw_sc_v2(arg1)
    ok(len(a) == 40 and a == b, "输出 40 位且稳定: " + a)
    ok(netdisk.acw_sc_v2("short") == "", "arg1 太短返回空")


def test_size():
    print("=== 大小解析 ===")
    ok(netdisk._parse_size("2.8 M") == int(2.8 * 1048576), "2.8 M")
    ok(netdisk._parse_size("1.5G") == int(1.5 * 1073741824), "1.5G")
    ok(netdisk._parse_size("999") == 999, "裸数字")


def test_unsupported():
    print("=== 未实现平台的提示 ===")
    info = netdisk.parse_share("https://pan.quark.cn/s/abc123")
    try:
        netdisk.resolve(info["url"])
        raise AssertionError("应该抛错")
    except RuntimeError as e:
        msg = str(e)
        # 要么说清「缺账号/没调通」，要么说清「链接失效」，最后都要给出现在能下谁
        ok(("蓝奏云" in msg) and ("登录" in msg or "失效" in msg or "还在做" in msg
                                  or "没调通" in msg or "列不出" in msg),
           "说清原因 + 当前支持谁: " + msg)

    print("=== 失效链接不能说成「还在做」 ===")
    # 不联网，直接打桩探测结果，钉死两条分支的文案
    saved = netdisk._probe_share
    try:
        netdisk._probe_share = lambda info: "dead"
        try:
            netdisk.resolve("https://pan.quark.cn/s/abc123")
            raise AssertionError("应该抛错")
        except RuntimeError as e:
            ok("失效" in str(e) and "还在做" not in str(e),
               "失效链接说失效，不甩「还在做」: " + str(e))

        netdisk._probe_share = lambda info: "alive"
        try:
            netdisk.resolve("https://pan.quark.cn/s/abc123")
            raise AssertionError("应该抛错")
        except RuntimeError as e:
            ok("登录" in str(e), "能打开但缺登录态要说清: " + str(e))
    finally:
        netdisk._probe_share = saved

    print("=== 百度网盘：没登录就先说登录，别去撞它的接口 ===")
    try:
        netdisk.resolve("https://pan.baidu.com/s/1abcDEFghiJKLmnoPQR")
        raise AssertionError("应该抛错")
    except RuntimeError as e:
        msg = str(e)
        ok("登录" in msg and "点「打开」" in msg, "指到具体入口: " + msg)


def test_baidu_cookie():
    print("=== 百度登录态：读文件 → 注入 CookieJar → 带进请求头 ===")
    import tempfile, urllib.request
    tmp = tempfile.mkdtemp(prefix="vdl_bd_ck_")
    old = os.environ.get("VDL_DATA_DIR")
    os.environ["VDL_DATA_DIR"] = tmp
    try:
        p = os.path.join(netdisk._cookie_dir(), "baidu.txt")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write("# Netscape HTTP Cookie File\n")
            f.write(".baidu.com\tTRUE\t/\tTRUE\t0\tBDUSS\tfake_bduss\n")
            f.write(".baidu.com\tTRUE\t/\tFALSE\t0\tSTOKEN\tfake_stoken\n")
        hdr = netdisk.load_cookie_header("baidu")
        ok("BDUSS=fake_bduss" in hdr and "STOKEN=fake_stoken" in hdr,
           "读成 Cookie 头: " + hdr)
        ok(netdisk.has_login("baidu"), "has_login 认得百度")
        _, cj = netdisk._new_opener(hdr, netdisk.COOKIE_DOMAINS["baidu"])
        ok(sorted(c.name for c in cj) == ["BDUSS", "STOKEN"],
           "注入 CookieJar: %s" % [c.name for c in cj])
        req = urllib.request.Request("https://pan.baidu.com/s/1abc")
        cj.add_cookie_header(req)
        got = req.get_header("Cookie") or ""
        ok("BDUSS=" in got, "会自动带进请求头: " + got)
        req2 = urllib.request.Request("https://d.pcs.baidu.com/file/x")
        cj.add_cookie_header(req2)
        ok("BDUSS=" in (req2.get_header("Cookie") or ""), "子域 d.pcs 也带得上")
    finally:
        if old is None:
            os.environ.pop("VDL_DATA_DIR", None)
        else:
            os.environ["VDL_DATA_DIR"] = old


LIVE_URL = "https://wwt.lanzouw.com/inAe108hg9de"   # 公开测试分享：配音文件.mp3

def _resolve_live():
    res = netdisk.resolve(LIVE_URL, progress=lambda **kw: None)
    ok(res["platform"] == "lanzou", "平台")
    ok(res["title"] == "配音文件.mp3", "标题: " + res["title"])
    ok(len(res["files"]) == 1, "单文件")
    return res


def test_live():
    print("=== 蓝奏真实链路（LIVE）===")
    res = _resolve_live()
    direct, name, size, hdr = netdisk.get_direct(res, progress=lambda **kw: None)
    ok(direct.startswith("http"), "拿到直链: " + direct[:50])
    import urllib.request
    req = urllib.request.Request(direct, headers=dict(hdr, Range="bytes=0-2047"))
    raw = urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=25).read()
    ok(raw[:2] >= b"\xff\xe0" or raw[:3] == b"ID3", "真实音频字节: " + raw[:4].hex())


def test_e2e():
    """用真实 Manager 跑一遍：识别→解析→下载→任务完成。"""
    print("=== 端到端下载（LIVE）===")
    import tempfile, time
    os.environ["VDL_DATA_DIR"] = tempfile.mkdtemp(prefix="vdl_nd_data_")
    out = tempfile.mkdtemp(prefix="vdl_nd_out_")
    import core
    mgr = core.Manager({"out_dir": out, "quality": "best"})
    t, msg = mgr.add(LIVE_URL)
    ok(t is not None, "任务入队: " + msg)
    done = None
    for _ in range(90):
        time.sleep(1)
        d = t.to_dict()
        if d["status"] in ("done", "error", "canceled"):
            done = d
            break
    ok(done is not None, "任务在时限内结束")
    ok(done["status"] == "done", "下载完成: %s %s" % (done.get("err_title"), done.get("error")))
    ok(done.get("filepath") and os.path.isfile(done["filepath"]), "文件落地")
    ok(os.path.getsize(done["filepath"]) > 100000, "大小合理: %d" % os.path.getsize(done["filepath"]))
    ok(not done["filepath"].endswith(".mp3.mp3"), "后缀没重复")


def test_detect_integration():
    print("=== 接入 core 识别 ===")
    import core
    for u, pid in ((LIVE_URL, "netdisk:lanzou"),
                   ("https://pan.quark.cn/s/abc123", "netdisk:quark")):
        got, name, level = core.detect_platform(u)
        ok(got == pid, "detect_platform %s -> %s" % (u[:30], pid))
    # 提取码从整段文案补进 URL
    class FakeMgr:
        def next_batch(self): return 1
        def add(self, url, batch):
            ok("pwd=abcd" in url, "提取码补进 URL: " + url)
            class T:
                def to_dict(self): return {}
            return T(), ""
    core.add_blocks(FakeMgr(), [{"text": LIVE_URL + " 提取码: abcd"}])


if __name__ == "__main__":
    test_parse()
    test_acw()
    test_size()
    test_unsupported()
    test_baidu_cookie()
    test_detect_integration()
    if os.environ.get("LIVE") == "1":
        test_live()
        test_e2e()
    print("\n通过 %d 项" % PASS[0], flush=True)

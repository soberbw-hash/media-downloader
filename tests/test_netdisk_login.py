# -*- coding: utf-8 -*-
"""网盘登录通道：面板里要能看到各家网盘，扩展旧了要说「要更新」。

跑法：python tests/test_netdisk_login.py

不依赖外网、不碰 8765：自己起隔离服务（VDL_PORT_FILE + VDL_DATA_DIR），
只读 /api/login/state 和打一次模拟的扩展上报。
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WANT = ["baidu", "quark", "uc", "xunlei", "123pan", "pan115", "c139", "guangya"]


def log(*a):
    print(*a, flush=True)


def find_port(pf):
    for _ in range(120):
        if os.path.isfile(pf):
            try:
                p = int(open(pf, encoding="utf-8").read().strip() or 0)
            except (OSError, ValueError):
                p = 0
            if p:
                return p
        time.sleep(0.15)
    return 0


def get_json(url):
    return json.loads(urllib.request.urlopen(url, timeout=20).read())


def post_json(url, body):
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "X-VDL-Ext": "1"})
    return json.loads(urllib.request.urlopen(req, timeout=30).read())


def main():
    log("=== 1. 源码里各网盘都登记了 ===")
    sys.path.insert(0, ROOT)
    import login
    for pid in WANT:
        assert pid in login.SITES, "login.SITES 少了 %s" % pid
    log("  OK 账号面板会列出：%s" % "、".join(login.SITES[p]["name"] for p in WANT))

    log("=== 2. 扩展清单和 manifest 对得上 ===")
    ext = os.path.join(ROOT, "browser-extension")
    man = json.load(open(os.path.join(ext, "manifest.json"), encoding="utf-8"))
    hosts = " ".join(man.get("host_permissions") or [])
    for dom in ("baidu.com", "quark.cn", "uc.cn", "xunlei.com",
                "123pan.com", "115.com", "139.com", "guangyapan.com"):
        assert dom in hosts, "manifest 少了 %s 的权限" % dom
    bg = open(os.path.join(ext, "background.js"), encoding="utf-8").read()
    for pid in WANT:
        assert '"%s"' % pid in bg, "background.js 没同步 %s" % pid
    log("  OK 扩展权限与站点清单都含各家网盘")

    log("=== 3. 服务端：面板能看到网盘 + 认得出扩展旧了 ===")
    tmp = tempfile.mkdtemp(prefix="vdl_nd_login_")
    env = os.environ.copy()
    env.update({"VDL_FORCE_NEW": "1", "VDL_NO_OPEN": "1",
                "VDL_NO_AUTO_UPDATE": "1", "VDL_DATA_DIR": tmp})
    pf = os.path.join(tmp, "port.txt")
    env["VDL_PORT_FILE"] = pf
    logf = open(os.path.join(tmp, "srv.log"), "wb")
    srv = subprocess.Popen([sys.executable, "server.py", "none"], cwd=ROOT,
                           env=env, stdout=logf, stderr=subprocess.STDOUT)
    try:
        port = find_port(pf)
        assert port, "没起带新代码的服务（可能被旧桌面版占了）"
        base = "http://127.0.0.1:%d" % port
        st = get_json(base + "/api/login/state")
        ids = [s["id"] for s in st.get("sites") or []]
        for pid in WANT:
            assert pid in ids, "账号面板没有 %s：%s" % (pid, ids)
        log("  OK 面板列了 %d 个站点，网盘全在" % len(ids))

        # 旧扩展：清单里没有网盘 → 必须提示「扩展要更新」
        old = ["xiaohongshu", "instagram", "douyin", "kuaishou",
               "bilibili", "weibo", "music_netease", "music_qqmusic", "suno"]
        post_json(base + "/api/login/extension",
                  {"ext_version": "1.0.0", "platforms": old, "items": []})
        info = get_json(base + "/api/login/state").get("extension") or {}
        assert info.get("stale") is True, "旧扩展没被认出来：%s" % info
        assert "百度网盘" in (info.get("missing") or []), info
        log("  OK 旧扩展 → stale=True，缺：%s" % "、".join(info["missing"][:4]))

        # 新扩展：报全了清单 → 不再提示
        new = old + WANT
        post_json(base + "/api/login/extension",
                  {"ext_version": "1.1.0", "platforms": new, "items": []})
        info = get_json(base + "/api/login/state").get("extension") or {}
        assert info.get("stale") is False, "新扩展被误判成旧的：%s" % info
        assert info.get("version") == "1.1.0", info
        log("  OK 新扩展 → stale=False，version=%s" % info.get("version"))

        log("=== 4. 扩展把百度登录态送来 → 面板立刻变已登录 ===")
        post_json(base + "/api/login/extension", {
            "ext_version": "1.1.0", "platforms": new,
            "items": [{"platform": "baidu", "cookies": [
                {"name": "BDUSS", "value": "fake-bduss-for-test",
                 "domain": ".baidu.com", "path": "/", "secure": True},
                {"name": "STOKEN", "value": "fake-stoken",
                 "domain": ".pan.baidu.com", "path": "/", "secure": True},
            ]}],
        })
        st = get_json(base + "/api/login/state")
        baidu = [s for s in st.get("sites") or [] if s["id"] == "baidu"][0]
        assert baidu.get("logged_in") is True, "百度没被认成已登录：%s" % baidu
        log("  OK 百度显示已登录")

        sys.path.insert(0, ROOT)
        import netdisk
        netdisk._cookie_dir = lambda: os.path.join(tmp, "cookies")
        hdr = netdisk.load_cookie_header("baidu")
        assert "BDUSS=" in hdr and "STOKEN=" in hdr, hdr
        assert netdisk.has_login("baidu") is True
        log("  OK 网盘解析读得到百度 cookie：%s" % hdr[:40])
    finally:
        srv.terminate()
        try:
            srv.wait(timeout=6)
        except subprocess.TimeoutExpired:
            srv.kill()
        logf.close()

    log("网盘登录通道 —— 全部通过")


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""网盘入口：首屏「支持」行要看得到、粘贴框要说、贴链接要认得出。

跑法：python tests/test_netdisk_entry.py

不依赖外网、不碰 8765（用户桌面版在用）：自己起隔离服务（VDL_PORT_FILE），
只查 /api/state 的平台列表和前端静态文案，不提交下载任务。
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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


def main():
    log("=== 1. 前端静态文案 ===")
    html = open(os.path.join(ROOT, "web", "index.html"), encoding="utf-8").read()
    js = open(os.path.join(ROOT, "web", "app.js"), encoding="utf-8").read()
    assert "网盘分享都认" in html, "粘贴框没提网盘分享"
    assert '"netdisk"' in js and "PLAT_SHOW_MAX = 14" in js, \
        "支持行没把网盘排进去，或者最多显示数没跟上"
    log("  OK 粘贴框提到网盘，支持行排序里有 netdisk")

    log("=== 2. 隔离服务的平台列表 ===")
    tmp = tempfile.mkdtemp(prefix="vdl_nd_entry_")
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
        state = json.loads(urllib.request.urlopen(
            "http://127.0.0.1:%d/api/state" % port, timeout=20).read())
        plats = state.get("platforms") or []
        nd = [p for p in plats if p.get("id") == "netdisk"]
        assert len(nd) == 1, "平台列表里没有网盘：%s" % plats
        assert "蓝奏云" in nd[0].get("name", ""), "只列真能下的那家：%s" % nd[0]
        assert nd[0].get("level") == "full", "能下的平台不该标成待登录"
        log("  OK /api/state 里有：%s" % nd[0])

        log("=== 3. 贴网盘链接能认出平台 ===")
        sys.path.insert(0, ROOT)
        import core
        pid, name, level = core.detect_platform("https://wwt.lanzouw.com/inAe108hg9de")
        assert pid.startswith("netdisk:") and "蓝奏云" in name, (pid, name, level)
        log("  OK 识别为 %s / %s" % (pid, name))
    finally:
        srv.terminate()
        try:
            srv.wait(timeout=6)
        except subprocess.TimeoutExpired:
            srv.kill()
        logf.close()

    log("网盘入口回归 —— 全部通过")


if __name__ == "__main__":
    main()

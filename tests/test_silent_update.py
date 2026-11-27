# -*- coding: utf-8 -*-
"""无感自动更新：真起服务 + 本地假发布页，把「自己下好、等没人的时候悄悄换」跑通。

跑法：python tests/test_silent_update.py
不碰线上 Release，也不碰真源码（替换目标是一份临时副本）。
"""
import hashlib
import io
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import updater  # noqa: E402

PASS = [0]
FAIL = []


def check(cond, label):
    if cond:
        PASS[0] += 1
        print("  ok  " + label)
    else:
        FAIL.append(label)
        print("  FAIL " + label)


def log(*a):
    print(*a, flush=True)


NEW_VER = "9.9.9"
OP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def copy_code(dst):
    """把当前代码复制一份当「安装目录」，只带上跑得起来的那些。"""
    for name in os.listdir(ROOT):
        src = os.path.join(ROOT, name)
        if os.path.isfile(src) and name.endswith((".py", ".txt")):
            shutil.copy2(src, os.path.join(dst, name))
    for d in ("web", "browser-extension", "assets"):
        s = os.path.join(ROOT, d)
        if os.path.isdir(s):
            shutil.copytree(s, os.path.join(dst, d))
    # 这里保持原样（旧版）；要造新版包时再单独把版本号抬上去
    p = os.path.join(dst, "server.py")
    s = open(p, encoding="utf-8").read()
    assert 'APP_VERSION = "1.24.12"' in s, "复制的代码不是当前版本"


def bump_version(dst, ver):
    p = os.path.join(dst, "server.py")
    s = open(p, encoding="utf-8").read()
    s2 = re.sub(r'APP_VERSION = "[^"]+"', 'APP_VERSION = "%s"' % ver, s, count=1)
    assert s2 != s, "server.py 里的版本号没改到"
    open(p, "w", encoding="utf-8", newline="\n").write(s2)
    open(os.path.join(dst, "version.txt"), "w", encoding="utf-8", newline="\n").write(ver + "\n")


def make_zip(app_dir):
    buf = io.BytesIO()
    root = "VideoDL-%s-Windows/" % NEW_VER
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for dirpath, _dirs, files in os.walk(app_dir):
            for f in files:
                full = os.path.join(dirpath, f)
                rel = os.path.relpath(full, app_dir).replace(os.sep, "/")
                if rel.endswith((".pyc",)) or "/__pycache__/" in "/" + rel or rel.startswith("__pycache__/"):
                    continue
                with open(full, "rb") as fh:
                    z.writestr(root + "程序/" + rel, fh.read())
        z.writestr(root + "使用说明.txt", "说明\n".encode("utf-8"))
    return buf.getvalue()


class Handler(BaseHTTPRequestHandler):
    ZIP = b""

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/octet-stream"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError):
            pass

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/releases/latest":
            payload = {
                "tag_name": "v" + NEW_VER,
                "assets": [{
                    "name": "VideoDL-%s-Windows.zip" % NEW_VER,
                    "browser_download_url": "http://127.0.0.1:%d/dl.zip" % self.server.server_address[1],
                    "size": len(self.ZIP),
                    "digest": "sha256:" + hashlib.sha256(self.ZIP).hexdigest(),
                }],
            }
            return self._send(200, json.dumps(payload).encode(), "application/json")
        if path == "/content/version.txt":
            return self._send(200, (NEW_VER + "\n").encode(), "text/plain")
        if path == "/dl.zip":
            rng = self.headers.get("Range") or ""
            m = re.match(r"bytes=(\d+)-(\d*)", rng)
            if m:
                s = int(m.group(1))
                e = int(m.group(2)) if m.group(2) else len(self.ZIP) - 1
                e = min(e, len(self.ZIP) - 1)
                body = self.ZIP[s:e + 1]
                self.send_response(206)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Content-Range", "bytes %d-%d/%d" % (s, e, len(self.ZIP)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionAbortedError):
                    pass
                return
            return self._send(200, self.ZIP)
        self._send(404, b"nope")


def get_json(url, timeout=5):
    with OP.open(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def post(url, timeout=5):
    req = urllib.request.Request(url, data=b"{}", method="POST",
                                headers={"Content-Type": "application/json"})
    with OP.open(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def wait_port(pf, limit=40):
    for _ in range(int(limit / 0.2)):
        try:
            v = open(pf, encoding="utf-8").read().strip()
            if v:
                return int(v)
        except (OSError, ValueError):
            pass
        time.sleep(0.2)
    return 0


def main():
    work = tempfile.mkdtemp(prefix="vdl_silent_")
    app = os.path.join(work, "app")
    os.makedirs(app)
    copy_code(app)                     # 安装目录里是旧版
    newer = os.path.join(work, "newer")
    shutil.copytree(app, newer)
    bump_version(newer, NEW_VER)       # 发布包才是新版
    Handler.ZIP = make_zip(newer)
    log("临时安装目录:", app, "| 假包", len(Handler.ZIP), "字节")

    web = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    web.daemon_threads = True
    threading.Thread(target=web.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d" % web.server_address[1]

    data = os.path.join(work, "data")
    pf = os.path.join(work, "port.txt")
    env = dict(os.environ)
    env.update({
        "VDL_DATA_DIR": data,
        "VDL_PORT_FILE": pf,
        "VDL_NO_OPEN": "1",
        "VDL_UPDATE_API": base + "/releases/latest",
        "VDL_UPDATE_VERSION_TXT": base + "/content/version.txt",
        "VDL_UPDATE_TARGET": app,
        "VDL_UPDATE_QUIET": "4",       # 4 秒没人动就算走了（真机是 10 分钟）
        "VDL_UPDATE_FIRST": "1",       # 1 秒后首次检查
        "VDL_UPDATE_GAP": "99999",     # 只查这一次，别在测试期间反复查
        "VDL_FORCE_NEW": "1",          # 别复用 8765 上用户正在用的那个实例
        "VDL_NO_AUTO_UPDATE": "",
    })
    logf = open(os.path.join(work, "srv.log"), "wb")
    srv = subprocess.Popen([sys.executable, "server.py", "none"], cwd=app, env=env,
                           stdout=logf, stderr=subprocess.STDOUT)
    try:
        port = wait_port(pf)
        check(bool(port), "隔离服务起来了（端口 %s）" % port)
        if not port:
            return

        st = get_json("http://127.0.0.1:%d/api/state" % port)
        check(st.get("version") == "1.24.12", "换之前是旧版（%s）" % st.get("version"))

        log("=== 1. 后端自己发现新版并下好 ===")
        waited = 0.0
        upd = {}
        while waited < 60:
            upd = get_json("http://127.0.0.1:%d/api/update/status" % port)["update"]
            if upd.get("state") in ("waiting", "applying", "done"):
                break
            time.sleep(0.5)
            waited += 0.5
        log("   状态", upd.get("state"), upd.get("percent"), upd.get("detail"))
        check(upd.get("state") == "waiting", "下好后停在 waiting，没有立刻重启")
        check(upd.get("latest") == NEW_VER, "认出的新版是 " + NEW_VER)

        log("=== 2. 有人在用的时候绝不换 ===")
        for _ in range(10):                       # 4 秒内持续报「在用」
            post("http://127.0.0.1:%d/api/touch" % port)
            time.sleep(0.4)
        st = get_json("http://127.0.0.1:%d/api/state" % port)
        check(st.get("version") != NEW_VER, "用户一直在动，没有偷偷重启")
        upd = get_json("http://127.0.0.1:%d/api/update/status" % port)["update"]
        check(upd.get("state") == "waiting", "包还在手里等着，没丢")

        log("=== 3. 人走了以后自己装上 ===")
        gone = False
        # 重启后很可能又抢回同一个端口，所以别盯着端口变没变，直接看版本号。
        for _ in range(150):                      # 最多等 60 秒
            time.sleep(0.4)
            try:
                p2 = int(open(pf, encoding="utf-8").read().strip() or 0) or port
            except (OSError, ValueError):
                p2 = port
            try:
                st = get_json("http://127.0.0.1:%d/api/state" % p2, timeout=2)
            except Exception:
                continue                          # 正处在「旧的刚退、新的还没起」的缝里
            if st.get("version") == NEW_VER:
                gone = True
                port = p2
                break
        check(gone, "无人操作后自动换到新版并重新拉起")
        if gone:
            st = get_json("http://127.0.0.1:%d/api/state" % port, timeout=5)
            check(st.get("version") == NEW_VER, "重启后接口版本是 %s" % NEW_VER)
            src = open(os.path.join(app, "server.py"), encoding="utf-8").read()
            check('APP_VERSION = "%s"' % NEW_VER in src, "安装目录里的代码真的换了")
            cfg = os.path.join(app, "config.json")
            check(not os.path.exists(cfg) or NEW_VER not in open(cfg, encoding="utf-8").read(),
                  "用户配置没被新版覆盖")

        log("=== 4. 界面不会自己弹提示（源码层面确认） ===")
        js = open(os.path.join(ROOT, "web", "app.js"), encoding="utf-8").read()
        check("toast(\"新版装好了" not in js, "更新装好不再弹 toast")
        check("location.reload()" in js, "版本号变了会自动刷新前端")
        check("/api/touch" in js, "前端会上报真实操作")
        check("restoreSearch()" in js, "重启后会把上次搜的一屏摆回来")
        log("=== 5. 关软件时顺手换上：下次打开就是新版 ===")
        app2 = os.path.join(work, "app2")
        os.makedirs(app2)
        copy_code(app2)
        pf2 = os.path.join(work, "port2.txt")
        env2 = dict(env)
        env2.update({"VDL_DATA_DIR": os.path.join(work, "data2"),
                     "VDL_PORT_FILE": pf2,
                     "VDL_UPDATE_TARGET": app2,
                     "VDL_UPDATE_QUIET": "9999"})   # 这次别自动装，专测退出那条路
        logf2 = open(os.path.join(work, "srv2.log"), "wb")
        srv2 = subprocess.Popen([sys.executable, "server.py", "none"], cwd=app2,
                                env=env2, stdout=logf2, stderr=subprocess.STDOUT)
        try:
            p3 = wait_port(pf2)
            check(bool(p3), "第二个隔离实例起来了（端口 %s）" % p3)
            waited = 0.0
            while waited < 60:
                u2 = get_json("http://127.0.0.1:%d/api/update/status" % p3)["update"]
                if u2.get("state") in ("waiting", "error", "done"):
                    break
                time.sleep(0.5)
                waited += 0.5
            check(u2.get("state") == "waiting", "包也下好了（%s）" % u2.get("state"))
            # 模拟用户关窗
            post("http://127.0.0.1:%d/api/quit" % p3)
            try:
                srv2.wait(timeout=20)
            except Exception:
                pass
            time.sleep(2)
            src2 = open(os.path.join(app2, "server.py"), encoding="utf-8").read()
            check('APP_VERSION = "%s"' % NEW_VER in src2, "退出时把代码换成了新版")
            still = True
            try:
                get_json("http://127.0.0.1:%d/api/state" % p3, timeout=2)
            except Exception:
                still = False
            check(not still, "没有自作主张再拉起一个（用户就是要关）")
        finally:
            try:
                srv2.terminate()
            except Exception:
                pass

    finally:
        # 第 3 步拉起来的新版进程不归 srv 管，单独通知它退出
        for p in {port, locals().get("p3")}:
            if not p:
                continue
            try:
                post("http://127.0.0.1:%d/api/quit_now" % p, timeout=2)
            except Exception:
                pass
        try:
            srv.terminate()
        except Exception:
            pass
        try:
            srv.wait(timeout=5)
        except Exception:
            srv.kill()

    log("")
    log("通过 %d 项，失败 %d 项" % (PASS[0], len(FAIL)))
    if FAIL:
        for f in FAIL:
            log("  - " + f)
        sys.exit(1)


if __name__ == "__main__":
    main()

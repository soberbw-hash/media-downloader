# -*- coding: utf-8 -*-
"""自动更新回归：本地假发布页 + 带 Range 的 zip，把整条链路真跑一遍。

跑法：python tests/test_updater.py
不碰线上 Release，也不碰真源码（替换目标用临时目录）。
"""
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
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


VER = "9.9.9"
PREFIX = "VideoDL-%s-Windows/程序/" % VER
NEW_SERVER = '# -*- coding: utf-8 -*-\nAPP_VERSION = "%s"\nprint("new")\n' % VER
NEW_CORE = "# new core\nVALUE = 42\n"
NEW_APPJS = "console.log('new ui');\n"
NEW_UPDATER = "# new updater module\n"


def make_zip(version=VER, broken_py=False):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        root = "VideoDL-%s-Windows/" % version
        p = root + "程序/"
        z.writestr(root + "使用说明.txt", "说明\n".encode("utf-8"))
        z.writestr(root + "音视频下载器.exe", b"MZ" + b"\0" * 200)
        z.writestr(p + "server.py",
                   ("# syntax error here !!(\n" if broken_py else NEW_SERVER))
        z.writestr(p + "core.py", NEW_CORE)
        z.writestr(p + "updater.py", NEW_UPDATER)
        z.writestr(p + "version.txt", version + "\n")
        z.writestr(p + "web/app.js", NEW_APPJS)
        z.writestr(p + "web/index.html", "<html>new</html>\n")
        z.writestr(p + "browser-extension/manifest.json", '{"version":"2"}\n')
        z.writestr(p + "config.json", '{"out_dir":"FAKE_USER_DATA"}')
        z.writestr(p + "history.json", "[]")
        z.writestr(p + "cookies/bilibili.txt", "SESSDATA=fake")
        z.writestr(p + "runtime/python.exe", b"MZ" + b"x" * 3000)
    return buf.getvalue()


ZIP = make_zip()
DIGEST = "sha256:" + hashlib.sha256(ZIP).hexdigest()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/octet-stream", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError):
            pass

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/releases/latest":
            payload = {
                "tag_name": "v" + VER,
                "assets": [{
                    "name": "VideoDL-%s-Windows.zip" % VER,
                    "browser_download_url": "http://127.0.0.1:%d/VideoDL-%s-Windows.zip" % (
                        self.server.server_address[1], VER),
                    "size": len(ZIP), "digest": DIGEST,
                }],
            }
            return self._send(200, json.dumps(payload).encode(), "application/json")
        if path == "/content/version.txt":
            return self._send(200, (VER + "\n").encode(), "text/plain")
        if path.endswith(".zip"):
            total = len(ZIP)
            rng = self.headers.get("Range") or ""
            m = re.match(r"bytes=(\d+)-(\d*)", rng)
            if m:
                s = int(m.group(1))
                e = int(m.group(2)) if m.group(2) else total - 1
                e = min(e, total - 1)
                if s > e:
                    return self._send(416, b"")
                body = ZIP[s:e + 1]
                return self._send(206, body, extra={
                    "Content-Range": "bytes %d-%d/%d" % (s, e, total)})
            return self._send(200, ZIP)
        if path == "/bad":
            return self._send(500, b"no")
        self._send(404, b"nope")


def main():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    base = "http://127.0.0.1:%d" % port
    tmp = tempfile.mkdtemp(prefix="vdl_upd_test_")
    updater.API_LATEST = base + "/releases/latest"
    updater.VERSION_TXT = base + "/content/version.txt"
    updater.MANUAL_URL = base + "/manual"
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    log("本地假发布页:", base, "| zip", len(ZIP), "字节")

    try:
        log("=== 1. 查版本 / 挑包 ===")
        r = updater.check("1.24.3", op=op)
        check(r["ok"] and r["has_update"] and r["latest"] == VER, "老版本能查到新版")
        check(r["asset"]["size"] == len(ZIP) and r["asset"]["digest"] == DIGEST,
              "资产大小与 sha256 都带上了")
        check(r["platform"] == "win" and r["can_auto"], "Windows 支持自动更新")
        check(updater.check(VER, op=op)["has_update"] is False, "同版本不误报更新")
        old_api = updater.API_LATEST
        updater.API_LATEST = base + "/bad"
        rb = updater.check("1.24.3", op=op)
        check(rb["ok"] and rb["has_update"] and not rb["can_auto"],
              "Release API 挂了还能退回 version.txt，只给手动出路")
        updater.API_LATEST = old_api
        updater.API_LATEST = base + "/bad"
        updater.VERSION_TXT = base + "/bad"
        rn = updater.check("1.24.3", op=op)
        check(not rn["ok"] and "连不上" in rn["error"], "全挂了如实报错，不假装是最新")
        updater.API_LATEST = old_api
        updater.VERSION_TXT = base + "/content/version.txt"

        log("=== 2. 远端 zip 只读需要的字节 ===")
        rz = updater.RemoteZip(base + "/VideoDL-%s-Windows.zip" % VER, len(ZIP), op)
        ents = rz.read_dir()
        check(len(ents) > 10, "中央目录解析出 %d 个条目" % len(ents))
        check(any(e["name"].endswith("使用说明.txt") for e in ents), "中文文件名没乱码")
        pairs = updater.code_entries(rz)
        rels = [rel for _, rel in pairs]
        check("server.py" in rels and "web/app.js" in rels and "updater.py" in rels,
              "代码文件都挑上了（含新增模块）")
        check(not any(x in rels for x in ("config.json", "history.json")),
              "用户配置 / 历史绝不被覆盖")
        check(not any(x.startswith(("runtime/", "cookies/")) for x in rels),
              "runtime / cookies 被排除")
        got = rz.read_many([e for e, _ in pairs])
        check(len(got) == len(pairs), "合并 Range 后每个文件都取回来了")
        check(got[PREFIX + "server.py"].decode() == NEW_SERVER, "取回的字节和原文件一致")

        log("=== 3. 热更：只下 0.1MB 的代码 ===")
        info = updater.check("1.24.3", op=op)
        info["asset"]["url"] = base + "/VideoDL-%s-Windows.zip" % VER
        prog = []
        staged, n = updater.fetch_hot(info, tmp, lambda **kw: prog.append(kw), op=op)
        check(n == len(pairs) and os.path.isfile(os.path.join(staged, "server.py")),
              "热更落地 %d 个文件" % n)
        check(not os.path.exists(os.path.join(staged, "config.json")), "staged 里没有用户数据")
        check(prog and prog[-1]["done"] >= prog[-1]["total"], "进度回调走到 100%")
        bad = dict(info)
        bad["latest"] = "8.8.8"
        try:
            updater.fetch_hot(bad, os.path.join(tmp, "bad"), op=op)
            check(False, "版本号对不上必须拒绝")
        except updater.UnsupportedZip:
            check(True, "版本号对不上会拒绝，不拿半截包去覆盖")

        log("=== 4. 整包下载 + sha256 + 续传 ===")
        full = updater.fetch_full(info, os.path.join(tmp, "full"), op=op)
        check(open(full, "rb").read() == ZIP, "整包下载字节完全一致")
        badinfo = dict(info)
        badinfo["asset"] = dict(info["asset"], digest="sha256:" + "0" * 64)
        try:
            updater.fetch_full(badinfo, os.path.join(tmp, "full_bad"), op=op)
            check(False, "校验不通过必须拒绝")
        except IOError:
            check(True, "sha256 不对就丢弃，不拿去覆盖")
        part_dir = os.path.join(tmp, "resume")
        os.makedirs(part_dir, exist_ok=True)
        name = info["asset"]["name"]
        half = len(ZIP) // 2
        with open(os.path.join(part_dir, name + ".part"), "wb") as f:
            f.write(ZIP[:half])
        with open(os.path.join(part_dir, name + ".part.src"), "w", encoding="utf-8") as f:
            f.write("%d\n%s\n" % (len(ZIP), info["asset"]["url"]))
        resumed = updater.fetch_full(info, part_dir, op=op)
        check(open(resumed, "rb").read() == ZIP, "断掉的整包能接着下完")
        staged2, files2 = updater.hot_from_archive(full, os.path.join(tmp, "expand"))
        check("server.py" in files2 and "config.json" not in files2,
              "整包兜底也只挑代码，不碰用户数据")

        log("=== 5. 替换 / 备份 / 回滚 ===")
        target = os.path.join(tmp, "target")
        os.makedirs(target, exist_ok=True)
        os.makedirs(os.path.join(target, "web"), exist_ok=True)
        with open(os.path.join(target, "server.py"), "w", encoding="utf-8") as f:
            f.write('APP_VERSION = "1.24.3"\n')
        with open(os.path.join(target, "config.json"), "w", encoding="utf-8") as f:
            f.write('{"out_dir":"MY_REAL_DIR"}')
        with open(os.path.join(target, "web", "app.js"), "w", encoding="utf-8") as f:
            f.write("old ui\n")
        plan = {"target": target, "staged": staged,
                "backup": os.path.join(tmp, "backup"), "files": [
                    "server.py", "core.py", "updater.py", "version.txt",
                    "web/app.js", "web/index.html", "browser-extension/manifest.json"]}
        n = updater.apply_hot(plan)
        check(n == 7, "7 个文件都替换了")
        check(VER in open(os.path.join(target, "server.py"), encoding="utf-8").read(),
              "主程序换成了新版")
        check(os.path.isfile(os.path.join(target, "updater.py")), "新增模块也装上了")
        check("MY_REAL_DIR" in open(os.path.join(target, "config.json"), encoding="utf-8").read(),
              "用户配置纹丝不动")
        check(os.path.isfile(os.path.join(tmp, "backup", "server.py")), "旧文件有备份")
        broken = make_zip(broken_py=True)
        with open(os.path.join(staged, "core.py"), "wb") as f:
            f.write(b"def ((( broken\n")
        try:
            updater.apply_hot(plan)
            check(False, "语法坏的新版必须整体拒绝")
        except IOError:
            check(True, "语法坏的新版被拦下")
        check(VER in open(os.path.join(target, "server.py"), encoding="utf-8").read(),
              "失败后还是能跑的旧版")
        # 恢复一个正常文件，顺便验证语法自检本身
        with open(os.path.join(staged, "core.py"), "wb") as f:
            f.write(NEW_CORE.encode())
        check(updater._compile_ok(os.path.join(staged, "core.py")), "语法自检能识别正常文件")
        check(not updater._compile_ok(os.path.join(staged, "web", "index.html")),
              "非 Python 文件不参与语法自检")

        log("=== 6. 替换计划 + 助手进程 ===")
        plan_dir = os.path.join(tmp, "plan")
        os.makedirs(plan_dir, exist_ok=True)
        pp = updater.make_plan(plan_dir, VER, target, staged,
                               ["server.py", "core.py", "version.txt"],
                               {"cmd": [], "cwd": tmp}, no_restart=True, no_wait=True)
        saved = json.load(open(pp, encoding="utf-8"))
        check(saved["no_restart"] and saved["no_wait"] and saved["version"] == VER,
              "计划文件内容正确")
        ok, msg = updater.run_helper(pp)
        check(ok, "助手能独立完成替换（不重启模式）：" + msg)
        mac = updater._mac_helper_script(
            {"target": "/Applications/音视频下载器.app/Contents/MacOS",
             "archive": "/tmp/a b.dmg", "port": 8765}, plan_dir)
        script = open(mac, encoding="utf-8").read()
        check(all(x in script for x in ("hdiutil attach", "ditto", "xattr", "open -n")),
              "Mac 助手脚本包含挂载 / 替换 / 去隔离 / 重启")
        check("'/tmp/a b.dmg'" in script, "带空格路径被正确加引号")
        check("mv \"$OLD\" \"$APP\"" in script, "替换失败能把旧包挪回来")
        launch = updater.default_launch("server", ROOT)
        check(launch["cmd"] and any("server.py" in x for x in launch["cmd"]),
              "源码运行时的重启命令正确")
        real_exe = updater.sys.executable
        try:
            updater.sys.executable = "/Applications/Test.app/Contents/MacOS/VideoDL"
            check(updater.mac_app_path("/tmp/nothing") ==
                  os.path.abspath("/Applications/Test.app"),
                  "Mac 从可执行文件定位 .app，不靠临时解压目录")
        finally:
            updater.sys.executable = real_exe

        log("=== 7. 服务端整条链路（隔离实例，不碰真源码） ===")
        env = os.environ.copy()
        env.update({
            "VDL_FORCE_NEW": "1", "VDL_NO_OPEN": "1", "VDL_NO_AUTO_UPDATE": "1",
            "VDL_DATA_DIR": os.path.join(tmp, "srvdata"),
            "VDL_UPDATE_TARGET": target,
            "VDL_UPDATE_API": base + "/releases/latest",
            "VDL_UPDATE_VERSION_TXT": base + "/content/version.txt",
            "VDL_UPDATE_MANUAL": base + "/manual",
        })
        logf = open(os.path.join(tmp, "srv.log"), "wb")
        proc = subprocess.Popen([sys.executable, "server.py", "none"], cwd=ROOT,
                                env=env, stdout=logf, stderr=subprocess.STDOUT)
        local = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            sport = 0
            for _ in range(60):
                for p in range(8765, 8786):
                    try:
                        h = local.open("http://127.0.0.1:%d/" % p, timeout=0.5).read().decode(
                            "utf-8", "replace")
                    except Exception:
                        continue
                    if 'id="updBar"' in h:
                        sport = p
                        break
                if sport:
                    break
                time.sleep(0.2)
            check(bool(sport), "带更新界面的隔离服务起来了")
            if sport:
                u = "http://127.0.0.1:%d" % sport
                st = json.load(local.open(u + "/api/update/status", timeout=5))
                check(st["ok"] and st["update"]["state"] == "idle", "初始状态是 idle")
                with open(os.path.join(target, "server.py"), "w", encoding="utf-8") as f:
                    f.write('APP_VERSION = "1.24.3"\n')
                req = urllib.request.Request(
                    u + "/api/update/start", data=b"{}",
                    headers={"Content-Type": "application/json"}, method="POST")
                local.open(req, timeout=10).read()
                state = ""
                for _ in range(80):
                    st = json.load(local.open(u + "/api/update/status", timeout=5))["update"]
                    state = st["state"]
                    if state in ("done", "error"):
                        break
                    time.sleep(0.25)
                check(state == "done", "检查→下载→替换全程自动跑完（状态 %s）" % state)
                check(VER in open(os.path.join(target, "server.py"), encoding="utf-8").read(),
                      "服务端真的把新版换上了")
                check("MY_REAL_DIR" in open(os.path.join(target, "config.json"),
                                            encoding="utf-8").read(),
                      "整条链路下来用户配置仍然没动")
                stt = json.load(local.open(u + "/api/state", timeout=5))
                check(stt.get("update", {}).get("state") == "done",
                      "状态搭 /api/state 的轮询车，前端不用另开定时器")
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
            logf.close()

        log("=== 8. 前端与文档静态断言 ===")
        h = open(os.path.join(ROOT, "web/index.html"), encoding="utf-8").read()
        j = open(os.path.join(ROOT, "web/app.js"), encoding="utf-8").read()
        c = open(os.path.join(ROOT, "web/style.css"), encoding="utf-8").read()
        rd = open(os.path.join(ROOT, "README.md"), encoding="utf-8").read()
        bp = open(os.path.join(ROOT, "tools/build_portable.py"), encoding="utf-8").read()
        check(all(x in h for x in ('id="updBar"', 'id="updTip"', 'id="cfgAutoUpd"')),
              "界面有进度条 / VPN 提示 / 自动更新开关")
        check("renderUpdateStatus" in j and "/api/update/start" in j
              and "/api/update/apply" in j, "前端接了自动更新接口")
        check("VPN" in j and "VPN" in h, "下载处会提示开 VPN")
        check(".upd-bar" in c and ".upd-tip" in c, "样式齐了")
        check("VPN" in rd and "自动" in rd, "README 写清了代理与自动更新")
        check('"updater.py"' in bp, "新模块进了打包清单")

        log("\n通过 %d 项" % PASS[0])
        if FAIL:
            log("失败 %d 项：" % len(FAIL))
            for x in FAIL:
                log("  - " + x)
            sys.exit(1)
        log("全部通过")
    finally:
        srv.shutdown()
        srv.server_close()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()

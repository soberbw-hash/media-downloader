# -*- coding: utf-8 -*-
"""听歌识曲：录哪儿能选（麦克风 / 电脑里的声音）。真机 Chrome。

跑法：python tests/test_rec_source.py
自己起隔离服务（VDL_PORT_FILE），用假设备放行 getUserMedia；
getDisplayMedia 在页面里换成假实现（真弹窗没法自动点），
只验「走没走对那条路、提示对不对」。
"""
import os
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXE = r"C:/Program Files/Google/Chrome/Application/chrome.exe"


def log(*a):
    print(*a, flush=True)


def check(cond, msg):
    global ok
    assert cond, msg
    ok += 1
    log("  OK", msg)


ok = 0

log("=== 1. 界面上能选 ===")
html = open(os.path.join(ROOT, "web", "index.html"), encoding="utf-8").read()
bar = html[html.index('id="recBar"'):html.index('id="mSrc"')]
check(bar.count('data-r="') == 2 and 'class="rsrc"' in bar,
      "识曲条上有两个来源")
check('data-r="mic"' in bar and 'data-r="sys"' in bar, "麦克风和系统声音各一个")
check("麦克风" in bar and "系统声音" in bar, "按钮上是人话，不是 mic/loopback")
check("复制" not in bar and "粘贴" not in bar, "识曲条里没有再提复制粘贴")

js = open(os.path.join(ROOT, "web", "app.js"), encoding="utf-8").read()
check("getDisplayMedia" in js, "系统声音走 getDisplayMedia（浏览器里唯一的路）")
check("分享音频" in js, "拿不到声音时告诉用户勾「分享音频」")


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
    tmp = tempfile.mkdtemp(prefix="vdl_rec_")
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
        assert port, "没起带新代码的服务"
        log("=== 2. 两条路都走通（隔离服务 %d）===" % port)

        from playwright.sync_api import sync_playwright
        args = ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"]
        with sync_playwright() as p:
            b = p.chromium.launch(executable_path=EXE, headless=True, args=args)
            pg = b.new_page(viewport={"width": 1204, "height": 900})
            errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)))
            pg.route("**/api/music/recognize",
                     lambda r: r.fulfill(status=200, content_type="application/json",
                                         body='{"ok": false, "error": "测试不真识别"}'))
            pg.goto("http://127.0.0.1:%d/" % port, wait_until="networkidle",
                    timeout=30000)
            pg.evaluate("() => { document.querySelector('#musicPanel').hidden = false; }")
            pg.evaluate("() => { document.querySelector('#mRec').click(); }")
            pg.wait_for_timeout(300)

            assert pg.locator("#recBar").is_visible(), "识曲条没出来"
            assert pg.locator("#recSrc .rs").count() == 2, "来源没显示"
            on = pg.locator("#recSrc .rs.on").inner_text()
            check(on == "麦克风", "默认还是麦克风（老用户不用改习惯）")

            # 装两个探针：记住走了哪条路，顺便给系统声音造一条带声音的流
            pg.evaluate("window.__calls = []")
            pg.evaluate("""() => {
              const md = navigator.mediaDevices;
              const real = md.getUserMedia.bind(md);
              md.getUserMedia = (c) => { window.__calls.push("mic"); return real(c); };
              md.getDisplayMedia = async (c) => {
                window.__calls.push("sys");
                window.__displayOpts = c;
                const ac = new (window.AudioContext || window.webkitAudioContext)();
                const osc = ac.createOscillator();
                const dst = ac.createMediaStreamDestination();
                osc.connect(dst);
                osc.start();
                window.__ac = ac;
                const cv = document.createElement("canvas");
                cv.width = cv.height = 8;
                const vs = cv.captureStream(2);
                window.__vid = vs.getVideoTracks()[0];
                return new MediaStream([...dst.stream.getAudioTracks(),
                                        ...vs.getVideoTracks()]);
              };
            }""")

            pg.locator("#recAct").click()
            pg.wait_for_timeout(600)
            calls = pg.evaluate("window.__calls")
            check(calls == ["mic"], "麦克风模式只碰 getUserMedia")
            check("正在听…" in pg.inner_text("#recTip"), "录音提示是「正在听」")
            pg.evaluate("() => recCleanup()")
            pg.wait_for_timeout(200)

            pg.locator('#recSrc .rs[data-r="sys"]').click()
            pg.wait_for_timeout(200)
            check(pg.locator("#recSrc .rs.on").inner_text() == "系统声音",
                  "点了就切到系统声音")
            check("分享音频" in pg.inner_text("#recTip"), "顺手说了要在弹窗里勾分享音频")
            check(pg.evaluate("localStorage.getItem('vdl.rec.src')") == "sys",
                  "选的来源记下来了，下次不用再点")

            pg.locator("#recAct").click()
            pg.wait_for_timeout(700)
            calls = pg.evaluate("window.__calls")
            check(calls[-1] == "sys" and "mic" not in calls[1:],
                  "系统声音模式走 getDisplayMedia，不去抢麦克风")
            opts = pg.evaluate("window.__displayOpts")
            check(opts.get("audio", {}).get("echoCancellation") is False,
                  "关了回声消除（不然录出来的歌会被削掉一块）")
            check(pg.evaluate("window.__vid.readyState") == "ended",
                  "画面轨道立刻停掉，只留声音")
            check("正在听电脑的声音" in pg.inner_text("#recTip"),
                  "提示说清在听电脑的声音")
            pg.evaluate("() => recCleanup()")
            pg.wait_for_timeout(200)

            # 选了窗口但没勾分享音频 —— 最常见的坑，得说清怎么办
            pg.evaluate("""() => {
              navigator.mediaDevices.getDisplayMedia = async () => {
                const cv = document.createElement("canvas");
                cv.width = cv.height = 8;
                return cv.captureStream(2);
              };
            }""")
            pg.locator("#recAct").click()
            pg.wait_for_timeout(500)
            tip = pg.inner_text("#recTip")
            check("分享音频" in tip and "整个屏幕" in tip,
                  "没勾音频时说清选哪个、勾什么：%s" % tip)
            check(pg.locator("#recAct").inner_text() == "录一段", "失败后能马上重来")

            # 用户自己关掉选择器：不该弹红字骂人
            pg.evaluate("""() => {
              navigator.mediaDevices.getDisplayMedia = async () => {
                const e = new Error("Permission denied");
                e.name = "NotAllowedError";
                throw e;
              };
            }""")
            pg.locator("#recAct").click()
            pg.wait_for_timeout(400)
            dot = pg.locator("#recDot").get_attribute("class")
            check("err" not in (dot or ""), "用户取消不算错，不亮红点")

            # 刷新后还记得上次选的系统声音
            pg.reload(wait_until="networkidle")
            pg.evaluate("() => { document.querySelector('#musicPanel').hidden = false; }")
            pg.evaluate("() => { document.querySelector('#mRec').click(); }")
            pg.wait_for_timeout(300)
            check(pg.locator("#recSrc .rs.on").inner_text() == "系统声音",
                  "重开还是上次选的来源")
            assert not errs, "脚本报错: %s" % errs[:2]
            b.close()
    finally:
        srv.terminate()
        try:
            srv.wait(timeout=5)
        except Exception:
            srv.kill()
        logf.close()
    log("\n全部通过：%d 项" % ok)


if __name__ == "__main__":
    main()

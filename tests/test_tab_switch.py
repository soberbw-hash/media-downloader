# -*- coding: utf-8 -*-
"""切页稳定性回归：反复快速切换后，当前面板必须可见、且两个面板都不许残留动画。

背景（2026-11-26）：旧的切页是「先淡出旧面板，105ms 后再换」。淡出动画带
fill:both，一旦被取消/丢帧，面板会卡在 opacity:0 的末帧上——位置还占着，
内容却画不出来，用户看到的就是「整块空了，反复切换也不回来」。
现在改成「显隐一次到位 + 只给新面板做淡入」，基准状态可见，任何一步被打断
都不会留空白。这个测试守的就是这条不变式。
"""
import json
import os
import random
import subprocess
import sys
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]

PROBE = """() => {
    const one = (s) => {
        const el = document.querySelector(s);
        if (!el) return null;
        const cs = getComputedStyle(el);
        return {hidden: !!el.hidden, display: cs.display,
                opacity: +(+cs.opacity).toFixed(3),
                anims: el.getAnimations().length};
    };
    return {tabMusic: document.body.classList.contains('tab-music'),
            switching: document.body.classList.contains('tab-switching'),
            box: one('#composer'), mus: one('#musicPanel')};
}"""


def main():
    with tempfile.TemporaryDirectory(prefix="vdl_tab_") as folder:
        tmp = Path(folder)
        portfile = tmp / "port"
        (tmp / "config.json").write_text(json.dumps({"out_dir": str(tmp / "downloads")}),
                                         encoding="utf-8")
        env = dict(os.environ, VDL_DATA_DIR=str(tmp), VDL_PORT_FILE=str(portfile),
                   VDL_FORCE_NEW="1", VDL_NO_OPEN="1", VDL_NO_AUTO_UPDATE="1")
        srv = subprocess.Popen([sys.executable, str(ROOT / "server.py"), "--server"], cwd=ROOT,
                               env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            port = None
            for _ in range(150):
                if portfile.exists():
                    port = portfile.read_text().strip()
                    if port:
                        break
                import time as _t
                _t.sleep(0.2)
            assert port, "服务没起来"
            url = "http://127.0.0.1:%s/" % port

            with sync_playwright() as pw:
                br = pw.chromium.launch(channel="chrome")
                pg = br.new_page(viewport={"width": 1320, "height": 800})
                errs = []
                pg.on("pageerror", lambda e: errs.append(str(e)))
                pg.goto(url, wait_until="load")
                pg.wait_for_timeout(1200)

                random.seed(20261126)
                for rnd in range(8):
                    for _ in range(random.randint(6, 14)):
                        name = random.choice(["video", "music"])
                        pg.click('.tab[data-tab="%s"]' % name, timeout=5000)
                        pg.wait_for_timeout(random.randint(15, 70))
                    pg.wait_for_timeout(700)

                    st = pg.evaluate(PROBE)
                    assert st["box"] and st["mus"], "两个面板必须都在 DOM 里"
                    assert not st["switching"], "切换结束后 tab-switching 还挂着"

                    for key, is_music in (("box", False), ("mus", True)):
                        el = st[key]
                        visible = (not el["hidden"]) and el["display"] != "none"
                        should = (st["tabMusic"] == is_music)
                        assert visible == should, "%s 显隐不对（应在%s）: %s" % (
                            key, "显示" if should else "隐藏", el)
                        # 藏起来的面板不许留动画痕迹：留着就会卡成空白
                        assert el["anims"] == 0, "%s 上还挂着动画: %s" % (key, el)

                    active = st["mus"] if st["tabMusic"] else st["box"]
                    assert not active["hidden"] and active["display"] != "none", \
                        "当前面板被藏起来了: %s / %s" % (st, active)
                    assert active["opacity"] > 0.9, \
                        "当前面板是透明的，用户看到空白: %s" % (active,)
                    assert active["anims"] == 0, "切页完成后当前面板还挂着动画: %s" % (active,)
                    print("round %d ok  %s" % (rnd, "搜索" if st["tabMusic"] else "链接下载"),
                          flush=True)

                assert not errs, "界面脚本报错: %s" % errs[:3]
                br.close()
            print("全部通过", flush=True)
        finally:
            srv.terminate()
            try:
                srv.wait(6)
            except Exception:
                srv.kill()


if __name__ == "__main__":
    main()

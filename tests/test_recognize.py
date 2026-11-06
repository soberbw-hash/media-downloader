# -*- coding: utf-8 -*-
"""听歌识曲：从点按钮到出搜索结果，整条链路验一遍。

做法：把一段已知歌曲的片段当成「用户选的文件」喂进页面，
看它能不能认出来、认完会不会自动把歌搜出来。
"""
import os
import sys

from playwright.sync_api import sync_playwright

URL = "http://127.0.0.1:8765/"
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "rtest")
SRC = os.path.join("D:/Projects/视频下载器/downloads", "告白气球 - Xai小爱.mp3")
CLIP = os.path.join(OUT, "clip_ui.mp3")


def log(*a):
    print(*a, flush=True)


def chrome():
    for p in ("C:/Program Files/Google/Chrome/Application/chrome.exe",
              "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"):
        if os.path.exists(p):
            return p
    return None


def prep_clip():
    """截 12 秒出来当上传素材。整首 8MB 传着慢，没必要。"""
    os.makedirs(OUT, exist_ok=True)
    if os.path.isfile(CLIP) and os.path.getsize(CLIP) > 10240:
        return CLIP
    import subprocess
    ff = "ffmpeg"
    subprocess.run([ff, "-y", "-v", "quiet", "-ss", "30", "-t", "12",
                    "-i", SRC, "-ac", "1", "-ar", "44100", CLIP], check=True)
    return CLIP


def main():
    clip = prep_clip()
    log("素材:", clip, os.path.getsize(clip), "字节")

    with sync_playwright() as p:
        kw = {"headless": True,
              "args": ["--mute-audio", "--use-fake-ui-for-media-stream"]}
        c = chrome()
        if c:
            kw["executable_path"] = c
        br = p.chromium.launch(**kw)
        ctx = br.new_context(viewport={"width": 1000, "height": 800},
                             permissions=["microphone"])
        pg = ctx.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(URL, wait_until="networkidle")
        pg.wait_for_timeout(600)
        pg.click('#tabs .tab[data-tab="music"]')
        pg.wait_for_timeout(300)

        # 1) 麦克风按钮在，点开后出现识曲条
        assert pg.query_selector("#mRec"), "找不到识曲按钮"
        assert pg.evaluate("() => document.querySelector('#recBar').hidden") is True, \
            "识曲条一开始就该是收着的"
        pg.click("#mRec")
        pg.wait_for_timeout(400)
        st = pg.evaluate("""() => ({
            open: !document.querySelector('#recBar').hidden,
            tip: document.querySelector('#recTip').textContent,
            act: document.querySelector('#recAct').textContent,
            pickHidden: document.querySelector('#recPick').hidden,
        })""")
        log("A) 点开识曲条:", st)
        assert st["open"], "点了麦克风识曲条没出来"
        assert st["act"] == "录一段", "默认按钮应该是「录一段」"

        # 2) 选文件 → 识别 → 自动搜索
        pg.set_input_files("#recFile", clip)
        pg.wait_for_timeout(1200)
        mid = pg.evaluate("() => document.querySelector('#recTip').textContent")
        log("B) 上传后提示:", mid)

        for i in range(40):
            pg.wait_for_timeout(1000)
            rows = pg.evaluate("() => document.querySelectorAll('.msong').length")
            if rows:
                log("B) 第 %d 秒出结果：%d 条" % (i + 1, rows))
                break
        else:
            raise AssertionError("识曲之后没搜出结果；提示=%r"
                                 % pg.evaluate("() => document.querySelector('#mHint').textContent"))

        st = pg.evaluate("""() => ({
            q: document.querySelector('#mQuery').value,
            hint: document.querySelector('#mHint').textContent,
            rows: document.querySelectorAll('.msong').length,
            barHidden: document.querySelector('#mBar').hidden,
            recBarHidden: document.querySelector('#recBar').hidden,
            firstTitle: (document.querySelector('.msong .t')||{}).textContent,
        })""")
        log("C) 识别后:", st)
        assert "告白气球" in st["q"], "搜索框没被填上歌名，实际是 %r" % st["q"]
        assert st["rows"] > 0, "识别完没出搜索结果"
        assert st["recBarHidden"], "识别完识曲条应该收起来"
        assert not st["barHidden"], "有结果了批量条该露出来"

        pg.screenshot(path=os.path.join(OUT, "rec_done.png"))

        # 3) 取消不该留下东西
        pg.click("#mRec")
        pg.wait_for_timeout(300)
        pg.click("#recX")
        pg.wait_for_timeout(300)
        assert pg.evaluate("() => document.querySelector('#recBar').hidden"), "点取消后识曲条没收起来"

        # 4) 切到电影来源，麦克风该消失（识曲只跟音乐有关）
        pg.click('#mSrc .plat[data-s="movie"]')
        pg.wait_for_timeout(400)
        assert pg.evaluate("() => document.querySelector('#mRec').hidden"), \
            "电影来源下不该显示识曲按钮"
        pg.click('#mSrc .plat[data-s="music"]')
        pg.wait_for_timeout(400)
        assert not pg.evaluate("() => document.querySelector('#mRec').hidden"), \
            "切回音乐后识曲按钮该回来"

        log("页面错误:", errs if errs else "无")
        br.close()
    log("\n>>> 通过：识曲 → 认出来 → 自动搜出结果")
    return 0


if __name__ == "__main__":
    sys.exit(main())

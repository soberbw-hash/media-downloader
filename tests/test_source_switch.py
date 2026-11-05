# -*- coding: utf-8 -*-
"""搜索页状态一致性：计数条、列表、提示语三者必须对得上。

要防的 bug：换来源之后上面还写着「共 24 条」，下面却是另一个来源的空列表。
"""
import os
import sys

from playwright.sync_api import sync_playwright

URL = "http://127.0.0.1:8765/"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rtest")


def chrome():
    for p in ("C:/Program Files/Google/Chrome/Application/chrome.exe",
              "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"):
        if os.path.exists(p):
            return p
    return None


def snap(pg, tag):
    st = pg.evaluate("""() => {
      const bar = document.querySelector('#mBar');
      const cnt = document.querySelector('#mCount');
      const res = document.querySelector('#mResults');
      const on = [...document.querySelectorAll('#mSrc .plat')].find(x=>x.classList.contains('on'));
      return {
        mode: on ? on.dataset.s : null,
        q: document.querySelector('#mQuery').value,
        barHidden: bar ? bar.hidden : null,
        cnt: cnt ? cnt.textContent : null,
        hint: document.querySelector('#mHint').textContent,
        rows: res.querySelectorAll('.msong,.mmovie').length,
        empty: (res.querySelector('.mempty')||{}).textContent || null,
      };
    }""")
    print("[%s] %s" % (tag, st))
    return st


def check(st, where):
    """不变式：计数条显示 ⟺ 列表里有东西。"""
    bar_shown = st["barHidden"] is False
    if bar_shown and st["rows"] == 0:
        raise AssertionError("%s：计数条还显示着「%s」，但列表是空的" % (where, st["cnt"]))
    if not bar_shown and st["rows"] > 0:
        raise AssertionError("%s：列表有 %d 条，计数条却藏着" % (where, st["rows"]))
    return True


def main():
    os.makedirs(OUT, exist_ok=True)
    with sync_playwright() as p:
        kw = {"headless": True, "args": ["--mute-audio"]}
        c = chrome()
        if c:
            kw["executable_path"] = c
        br = p.chromium.launch(**kw)
        pg = br.new_page(viewport={"width": 1000, "height": 800})
        pg.goto(URL, wait_until="networkidle")
        pg.wait_for_timeout(600)
        pg.click('#tabs .tab[data-tab="music"]')
        pg.wait_for_timeout(300)

        # 1) 音乐来源搜一批
        pg.fill("#mQuery", "告白气球")
        pg.click("#mGo")
        for _ in range(30):
            pg.wait_for_timeout(1000)
            if pg.evaluate("()=>document.querySelectorAll('.msong').length"):
                break
        pg.wait_for_timeout(1200)
        st = snap(pg, "搜完音乐")
        check(st, "搜完音乐")
        assert st["rows"] > 0, "音乐搜索没出结果"

        # 2) 切到电影来源，搜个搜不到的
        pg.click('#mSrc .plat[data-s="movie"]')
        pg.wait_for_timeout(400)
        pg.fill("#mQuery", "zzz查无此片")
        pg.click("#mGo")
        pg.wait_for_timeout(9000)
        st = snap(pg, "电影搜不到")
        check(st, "电影搜不到")

        # 3) 清空搜索框，再切回音乐（这就是用户截图那一步）
        pg.fill("#mQuery", "")
        pg.wait_for_timeout(600)
        pg.click('#mSrc .plat[data-s="music"]')
        pg.wait_for_timeout(800)
        st = snap(pg, "切回音乐")
        check(st, "切回音乐")
        assert st["barHidden"] is True, "切回音乐后计数条还露着（用户截图里的毛病）"
        assert not (st["empty"] or "").startswith("没搜到这部片子"), \
            "列表还留着电影来源的空态文案"

        pg.screenshot(path=OUT + "/m_mismatch.png")

        # 4) 再搜一次，确认改完还能正常出结果
        pg.fill("#mQuery", "告白气球")
        pg.click("#mGo")
        for _ in range(30):
            pg.wait_for_timeout(1000)
            if pg.evaluate("()=>document.querySelectorAll('.msong').length"):
                break
        pg.wait_for_timeout(1200)
        st = snap(pg, "再搜一次")
        check(st, "再搜一次")
        assert st["rows"] > 0, "改完之后搜不出东西了"

        print("\n>>> 通过：计数条和列表始终对得上")
        br.close()
        return 0


if __name__ == "__main__":
    sys.exit(main())

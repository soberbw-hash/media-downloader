# -*- coding: utf-8 -*-
"""「全部」档混排：搜一个裸歌名，音乐和视频一起给，不再只出视频。

要防的 bug：搜「告白气球」被识别成视频，列表里只有 B站 / YouTube，
歌一条都看不见；或者混排之后音乐段被素材站的无关条目糊住。

    python tests/test_mixed_search.py        # 需要 8765 上有服务
"""
import os

from playwright.sync_api import sync_playwright

URL = os.environ.get("VDL_TEST_URL", "http://127.0.0.1:8765/")


def chrome():
    for p in ("C:/Program Files/Google/Chrome/Application/chrome.exe",
              "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"):
        if os.path.exists(p):
            return p
    return None


STATE = """() => {
  const rows = [...document.querySelectorAll('#mResults .msong')];
  const groups = [...document.querySelectorAll('#mResults .mgroup')].map(e => e.textContent);
  const srcs = {};
  rows.forEach(r => {
    const p = r.querySelector('.psrc')?.dataset.p || r.querySelector('.cov')?.dataset.p || '?';
    srcs[p] = (srcs[p] || 0) + 1;
  });
  return {hint: document.querySelector('#mHint').textContent,
          n: rows.length, groups, srcs,
          songFirst: rows.length ? !!rows[0].querySelector('.pv') : null};
}"""


def log(*a):
    print(*a, flush=True)


def check(ok, msg):
    if not ok:
        raise AssertionError(msg)
    log("+", msg)


def wait_done(pg, tries=40):
    st = {}
    for _ in range(tries):
        pg.wait_for_timeout(900)
        st = pg.evaluate(STATE)
        if "首歌" in st["hint"] or "条视频" in st["hint"] \
                or "没搜到" in st["hint"] or "没连上" in st["hint"]:
            return st
    return st


def main():
    exe = chrome()
    kw = {"headless": True}
    if exe:
        kw["executable_path"] = exe
    with sync_playwright() as p:
        b = p.chromium.launch(**kw)
        pg = b.new_page(viewport={"width": 1204, "height": 841})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(URL, wait_until="networkidle")
        pg.click('#tabs .tab[data-tab="music"]')
        pg.wait_for_timeout(400)

        # 1｜裸歌名：两类都要搜，音乐排前面
        pg.fill("#mQuery", "告白气球")
        pg.click("#mGo")
        st = wait_done(pg)
        log("   ", st)
        check("识别为视频" not in st["hint"], "不再写「识别为视频」")
        check(st["n"] > 0, "搜出 %d 条" % st["n"])
        check(st["groups"][:1] == ["音乐"], "第一段是「音乐」：%s" % st["groups"])
        check(st["songFirst"] is True, "第一行能直接试听（是歌）")
        check(any(k in ("netease", "qqmusic", "kugou") for k in st["srcs"]),
              "结果里有音乐平台：%s" % st["srcs"])
        # 视频那边可能因为网络到不了而空着，空着就必须说明白
        if "视频" in st["groups"]:
            check(any(k in ("bili", "youtube") for k in st["srcs"]),
                  "视频段来自 B站 / YouTube：%s" % st["srcs"])
            check("mixkit" not in st["srcs"],
                  "混排不带只认英文的素材站：%s" % st["srcs"])
        else:
            check("视频" in st["hint"], "视频那段空着时提示说明了原因：%r" % st["hint"])

        # 2｜带提示词的照旧单类，不该出现分组标题
        pg.fill("#mQuery", "周杰伦")
        pg.click("#mGo")
        st2 = wait_done(pg, 30)
        log("   ", st2)
        check(st2["n"] > 0, "「周杰伦」搜出 %d 条" % st2["n"])
        check(not st2["groups"], "单类结果不摆分组标题：%s" % st2["groups"])
        check("识别为音乐" in st2["hint"], "提示写明是音乐：%r" % st2["hint"])

        # 3｜音效词仍然按音效走
        pg.fill("#mQuery", "雨声")
        pg.click("#mGo")
        st3 = wait_done(pg, 30)
        log("   ", st3)
        check("识别为音效" in st3["hint"], "音效词仍按音效搜：%r" % st3["hint"])
        check(not st3["groups"], "音效不摆分组标题")

        check(not errs, "控制台无报错 %s" % (errs or ""))
        b.close()
    log("\n全部通过")


if __name__ == "__main__":
    main()

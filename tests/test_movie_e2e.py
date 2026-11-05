# -*- coding: utf-8 -*-
"""端到端：电影搜索 → 拿正版链接 → 丢进下载管线 → 真下 → 落盘。"""
import os
import shutil
import sys
import time

sys.path.insert(0, r"D:\Projects\视频下载器")
import core
import movies

TMP = r"D:\Projects\视频下载器\Temp\movie_e2e"


def main():
    if os.path.isdir(TMP):
        shutil.rmtree(TMP, ignore_errors=True)
    os.makedirs(TMP)

    print("=== 1. 搜电影 ===")
    items, errs = movies.search_movie("流浪地球2", 5)
    if errs:
        print("  提示:", errs)
    hit = None
    for it in items:
        b = it.get("bili") or {}
        print("  《%s》 %s | 豆瓣 %s | B站 %s | vip=%s" %
              (it["title"], it.get("year"), it.get("rating"),
               b.get("kind"), b.get("vip")))
        if b.get("dl_url") and hit is None:
            hit = (it, b)
    if not hit:
        print("没找到带正版链接的条目")
        return 1
    it, b = hit
    print("  选中:", it["title"], "->", b["dl_url"])

    print()
    print("=== 2. 走下载管线 ===")
    cfg = {"out_dir": TMP, "quality": "480", "max_workers": 1,
           "proxy": "", "name_tpl": "{date}_{platform}_{author}_{title}",
           "save_desc": True, "save_danmaku": False, "save_cover": False}
    m = core.Manager(cfg)
    # 走 add_blocks：电影卡会把查到的片名一起带下来
    res = core.add_blocks(m, [{"text": b["dl_url"], "title": it["title"]}])
    added = res.get("added") or []
    if not added:
        print("  add 失败:", res.get("skipped"))
        return 1
    tid = added[0]["id"]
    print("  任务:", tid, "| 平台:", added[0].get("platform_name"),
          "| 标题:", added[0].get("title"))
    deadline = time.time() + 600
    while time.time() < deadline:
        tt = m.tasks.get(tid)
        if tt and tt.status in ("done", "error", "canceled"):
            break
        time.sleep(1)
    tt = m.tasks.get(tid)
    print("  状态:", tt.status, "| 阶段:", tt.stage, "| 错误:", tt.error)
    print("  最终标题:", tt.title, "| 作者:", tt.author)
    if tt.status != "done":
        return 1

    print()
    print("=== 3. 落盘检查 ===")
    files = sorted(os.listdir(TMP))
    for f in files:
        sz = os.path.getsize(os.path.join(TMP, f))
        print("  %-60s %8.1f MB" % (f, sz / 1048576))
    vids = [f for f in files if f.endswith((".mp4", ".mkv", ".flv", ".webm"))]
    if not vids:
        print("  没出视频文件")
        return 1
    p = os.path.join(TMP, vids[0])
    print("  视频大小: %.1f MB" % (os.path.getsize(p) / 1048576))
    with open(p, "rb") as f:
        head = f.read(32)
    print("  文件头:", head[:12])
    print("  简介 txt:", [f for f in files if f.endswith(".txt")])

    # 别把几十兆的测试视频留在 Temp 里
    shutil.rmtree(TMP, ignore_errors=True)
    print()
    print("== 端到端通过 ==")
    return 0


if __name__ == "__main__":
    sys.exit(main())

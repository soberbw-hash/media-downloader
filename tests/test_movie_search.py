# -*- coding: utf-8 -*-
"""电影条目搜索实测：真连豆瓣 + B站影视区。"""
import json
import os
import sys

sys.path.insert(0, r"D:\Projects\视频下载器")
import movies

CASES = ["哪吒之魔童降世", "流浪地球", "肖申克的救赎", "三体"]

ok = 0
for kw in CASES:
    items, errs = movies.search_movie(kw, limit=5)
    print("=" * 60)
    print("搜索：%s -> %d 条" % (kw, len(items)))
    if errs:
        print("  错误：", errs)
    for it in items:
        b = it.get("bili") or {}
        print("  《%s》 %s" % (it["title"], it.get("year") or ""))
        print("     豆瓣评分:", it.get("rating") or "-",
              "| 导演:", it.get("director") or "-",
              "| 类型:", "/".join(it.get("genres") or []) or "-",
              "| 时长:", it.get("duration") or "-",
              "| 地区:", it.get("region") or "-")
        print("     简介:", (it.get("summary") or "")[:60])
        if b:
            print("     B站正版:", b.get("kind"), "| 站内分:", b.get("score"),
                  "| 大会员:", b.get("vip"), "| 标签:", b.get("tags"),
                  "| 集数:", b.get("ep_size"))
            print("     正片链接:", b.get("url"))
        else:
            print("     B站正版: 没找到")
        if it.get("url"):
            print("     豆瓣链接:", it["url"])
    if items:
        ok += 1

print()
print("== %d/%d 通过 ==" % (ok, len(CASES)))
sys.exit(0 if ok == len(CASES) else 1)

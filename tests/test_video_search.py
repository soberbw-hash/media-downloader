# -*- coding: utf-8 -*-
"""视频搜索实测：B站必须搜得到；YouTube 网络不通时明确说清，不算失败。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import search


def main():
    fails = []

    # —— B站（直连，必须成）
    items, errs = search.search_video("猫咪 搞笑", limit=6, platform="bili")
    print("B站：%d 条" % len(items))
    for it in items[:3]:
        print("   -", it["title"][:40], "|", it["author"], "|", it["url"])
    if not items:
        fails.append("B站没搜到结果：%s" % errs)
    else:
        it = items[0]
        for k in ("platform", "id", "title", "author", "cover", "duration", "url"):
            if it.get(k) in (None, ""):
                fails.append("B站结果缺字段 %s：%s" % (k, it))
        if not it["url"].startswith("https://www.bilibili.com/video/BV"):
            fails.append("B站链接形态不对：%s" % it["url"])

    # —— YouTube（可能要代理；网络不通只提示）
    try:
        items2, errs2 = search.search_video("lofi hip hop", limit=6, platform="youtube")
        print("YouTube：%d 条" % len(items2))
        for it in items2[:3]:
            print("   -", it["title"][:40], "|", it["author"], "|", it["url"])
        if items2:
            it = items2[0]
            if not it["url"].startswith("https://"):
                fails.append("YouTube链接形态不对：%s" % it["url"])
        else:
            print("   （没结果：%s）" % "；".join(errs2))
    except Exception as e:
        print("YouTube：网络不通，跳过（%s）" % str(e)[:100])

    if fails:
        print("\n失败：")
        for f in fails:
            print(" -", f)
        print("\n== 失败 ==")
        return 1
    print("\n== 通过 ==")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

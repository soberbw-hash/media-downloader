# -*- coding: utf-8 -*-
"""品牌图标一致性回归：桌面母版、扩展图标、favicon 必须是同一张下载箭头。

以前三处各画各的：桌面是黑底白箭头、favicon 是细线托盘、扩展是灰色中括号，
在任务栏和插件卡片里又糊又丑。现在都从 app-icon.svg 母版出，这里防止再分叉。

跑法：python tests/test_icon_brand.py
"""
import os
import re
import urllib.parse

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ok = 0


def t(cond, msg):
    global ok
    assert cond, msg
    ok += 1
    print("  OK", msg)


def center(im):
    return im.convert("RGBA").getpixel((im.width // 2, im.height // 2))


def main():
    print("=== 品牌图标一致性 ===")

    # 1) 母版：中心黑箭头、四分之一处白内框、四角透明
    master = Image.open(os.path.join(ROOT, "tools", "assets", "icon.png")).convert("RGBA")
    mc = center(master)
    t(mc[3] == 255 and max(mc[:3]) < 60, "母版中心是实心深色箭头：%s" % (mc,))
    t(master.getpixel((5, 5))[3] == 0, "母版四角透明（圆角外不带白块）")

    # 2) 扩展图标四档：都在、尺寸对、中心是深色箭头（不是旧的灰色中括号）
    d = os.path.join(ROOT, "browser-extension", "icons")
    for s in (16, 32, 48, 128):
        p = os.path.join(d, "icon%d.png" % s)
        t(os.path.isfile(p), "扩展图标 icon%d.png 存在" % s)
        im = Image.open(p).convert("RGBA")
        t(im.size == (s, s), "icon%d.png 尺寸 %s" % (s, im.size))
        # 旧灰中括号：整张图几乎全是 87/83/78 一档灰；新图有深底(28)、
        # 浅内框(250)、深箭头三档。用小尺寸里最暗/最亮像素是否拉开来判断。
        px = [q for q in im.getdata() if q[3] > 200]
        darkest = min(sum(q[:3]) / 3 for q in px)
        lightest = max(sum(q[:3]) / 3 for q in px)
        t(darkest < 80 and lightest > 200,
          "icon%d.png 有深底+浅内框（非旧灰图）：暗%.0f 亮%.0f" % (s, darkest, lightest))

    # 3) manifest 确实引用了这几档
    mf = open(os.path.join(ROOT, "browser-extension", "manifest.json"),
              encoding="utf-8").read()
    for s in (16, 32, 48, 128):
        t(("icon%d.png" % s) in mf, "manifest 引用 icon%d.png" % s)
    t("[+]" not in mf, "扩展描述里没有旧中括号图形残留")

    # 4) favicon：改用真正的多档 PNG 位图（内联 SVG 在 --app 窗口小尺寸下
    #    会被 Chrome 低分辨率栅格化，任务栏发糊），让系统直接取现成尺寸。
    html = open(os.path.join(ROOT, "web", "index.html"), encoding="utf-8").read()
    for s in (16, 32, 48, 64):
        t(("sizes=\"%dx%d\"" % (s, s)) in html
          and ("favicon-%d.png" % s) in html,
          "index.html 引用 %dx%d PNG favicon" % (s, s))
    t("data:image/svg+xml" not in html.split("<body")[0],
      "favicon 不再是内联 SVG（避免任务栏栅格化发糊）")
    imgd = os.path.join(ROOT, "web", "img")
    for s in (16, 32, 48, 64):
        p = os.path.join(imgd, "favicon-%d.png" % s)
        t(os.path.isfile(p), "favicon-%d.png 存在" % s)
        im = Image.open(p).convert("RGBA")
        t(im.size == (s, s), "favicon-%d.png 尺寸 %s" % (s, im.size))
        px = [q for q in im.getdata() if q[3] > 200]
        darkest = min(sum(q[:3]) / 3 for q in px)
        lightest = max(sum(q[:3]) / 3 for q in px)
        t(darkest < 80 and lightest > 200,
          "favicon-%d.png 有深底+浅内框（母版配色）：暗%.0f 亮%.0f"
          % (s, darkest, lightest))

    print("ALL PASS (%d)" % ok)


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""生成桌面图标：黑底圆角 + 白色下载箭头。输出 icon.ico（多尺寸）和预览 png。

用法：python tools/make_icon.py
"""
import os
from PIL import Image, ImageDraw

BASE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(BASE, "assets")
S = 1024          # 先按 4 倍画，缩下去更顺

BG = (17, 17, 19, 255)        # 近黑
FG = (255, 255, 255, 255)     # 白
ACCENT = (255, 106, 61, 255)  # 珊瑚色小圆点，唯一彩色，屏幕上一眼能认出来


def rounded(draw, box, r, fill):
    draw.rounded_rectangle(box, radius=r, fill=fill)


def build():
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # 底：圆角方块（留 6% 边距，符合 Windows 图标习惯）
    pad = int(S * 0.055)
    rounded(d, (pad, pad, S - pad, S - pad), int(S * 0.22), BG)

    # 下载箭头：竖杆 + 三角头 + 底部托盘
    cx = S // 2
    shaft_w = int(S * 0.118)
    shaft_top = int(S * 0.245)
    shaft_bot = int(S * 0.535)
    head_w = int(S * 0.285)
    head_top = int(S * 0.495)
    head_bot = int(S * 0.755)
    radius = shaft_w // 2

    # 竖杆（两端圆头）
    rounded(d, (cx - shaft_w // 2, shaft_top, cx + shaft_w // 2, shaft_bot),
            radius, FG)
    # 箭头（三角 + 小圆角近似：用多边形）
    d.polygon([(cx - head_w // 2, head_top), (cx + head_w // 2, head_top),
               (cx, head_bot)], fill=FG)

    # 托盘：一条圆头横线
    tray_y = int(S * 0.845)
    tray_h = int(S * 0.092)
    tray_w = int(S * 0.50)
    rounded(d, (cx - tray_w // 2, tray_y - tray_h // 2,
                cx + tray_w // 2, tray_y + tray_h // 2), tray_h // 2, FG)

    # 右上角小圆点（唯一彩色）
    r = int(S * 0.066)
    dx, dy = int(S * 0.735), int(S * 0.255)
    d.ellipse((dx - r, dy - r, dx + r, dy + r), fill=ACCENT)

    os.makedirs(OUT_DIR, exist_ok=True)
    png = os.path.join(OUT_DIR, "icon.png")
    img.resize((512, 512), Image.LANCZOS).save(png)

    ico_sizes = [(s, s) for s in (16, 20, 24, 32, 40, 48, 64, 128, 256)]
    ico = os.path.join(OUT_DIR, "icon.ico")
    img.save(ico, format="ICO", sizes=ico_sizes)

    # 单独导出 256 给网页 favicon 之类的场景
    img.resize((256, 256), Image.LANCZOS).save(
        os.path.join(OUT_DIR, "icon-256.png"))

    # 小尺寸检查：把 16/32/48/64 按 4 倍最近邻放大拼一排，肉眼看会不会糊
    strip = Image.new("RGBA", (4 * 64 * 4 + 5 * 12, 64 * 4 + 24), (245, 245, 245, 255))
    x = 12
    for s in (16, 32, 48, 64):
        small = img.resize((s, s), Image.LANCZOS)
        strip.paste(small.resize((s * 4, s * 4), Image.NEAREST), (x, 12))
        x += s * 4 + 12
    strip.save(os.path.join(OUT_DIR, "icon-small-preview.png"))
    return ico, png


if __name__ == "__main__":
    ico, png = build()
    print("ico ->", ico, os.path.getsize(ico), "bytes")
    print("png ->", png)

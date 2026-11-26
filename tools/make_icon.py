# -*- coding: utf-8 -*-
"""从 SVG 母版生成应用图标：icon.ico / icon.png / icon.icns。

母版：tools/assets/app-icon.svg（唯一真源，改图标只改这个文件）
输出：tools/assets/icon.ico（16~256 九档）、icon.png（512）、icon-256.png、icon.icns
      以及 browser-extension/icons/icon16|32|48|128.png（扩展图标跟 app 同一张脸）

用法：python tools/make_icon.py

原理：SVG 只能靠浏览器精确栅格化，所以调用本机 Chrome/Edge 的 headless
截图得到 1024px 透明母版，再用 Pillow 逐档 LANCZOS 缩放。
找不到浏览器就报错退出——不会悄悄画出别的图标。
"""
import os
import platform
import shutil
import subprocess
import tempfile

from PIL import Image

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE)          # 项目根（BASE 是 tools/，别拿它当根用）
ASSETS = os.path.join(BASE, "assets")
SVG = os.path.join(ASSETS, "app-icon.svg")
S = 1024                      # 母版边长
ICO_SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]
ICNS_SIZES = [16, 32, 64, 128, 256, 512, 1024]


def find_browser():
    """定位本机 Chromium 系浏览器（能 headless 截图的那种）"""
    env = os.environ.get("VDL_BROWSER", "")
    if env and os.path.isfile(env):
        return env
    cands = []
    if platform.system() == "Windows":
        for root in (os.environ.get("PROGRAMFILES", r"C:\Program Files"),
                     os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
                     os.environ.get("LOCALAPPDATA", "")):
            for rel in (r"Google\Chrome\Application\chrome.exe",
                        r"Microsoft\Edge\Application\msedge.exe",
                        r"Chromium\Application\chrome.exe"):
                if root:
                    cands.append(os.path.join(root, rel))
    elif platform.system() == "Darwin":
        cands += ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                  "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                  "/Applications/Chromium.app/Contents/MacOS/Chromium"]
    else:
        for n in ("google-chrome", "chromium", "chromium-browser", "microsoft-edge"):
            p = shutil.which(n)
            if p:
                cands.append(p)
    for p in cands:
        if p and os.path.isfile(p):
            return p
    return ""


def render_master(svg_path, px, out_png):
    """把 SVG 渲染成 px×px 透明 PNG"""
    browser = find_browser()
    if not browser:
        raise RuntimeError("找不到 Chrome / Edge，没法把 SVG 渲染成图标。"
                           "装一个浏览器，或用环境变量 VDL_BROWSER 指定路径。")
    svg = open(svg_path, encoding="utf-8").read()
    tmp = tempfile.mkdtemp(prefix="vdl_icon_")
    try:
        html = ('<!DOCTYPE html><html><head><meta charset="utf-8"><style>'
                'html,body{margin:0;padding:0;background:transparent;overflow:hidden}'
                'svg{display:block;width:%dpx;height:%dpx}</style></head><body>%s</body></html>'
                % (px, px, svg))
        hp = os.path.join(tmp, "r.html")
        with open(hp, "w", encoding="utf-8") as f:
            f.write(html)
        args = [browser, "--headless=new", "--disable-gpu", "--no-sandbox",
                "--hide-scrollbars", "--force-device-scale-factor=1",
                "--default-background-color=00000000",
                "--window-size=%d,%d" % (px, px),
                "--virtual-time-budget=4000",
                "--screenshot=" + os.path.abspath(out_png),
                "file:///" + hp.replace("\\", "/")]
        r = subprocess.run(args, capture_output=True, text=True, timeout=180)
        if not os.path.isfile(out_png):
            raise RuntimeError("浏览器截图失败：%s" % (r.stderr or "")[-300:])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return out_png


def build():
    if not os.path.isfile(SVG):
        raise RuntimeError("找不到图标母版：" + SVG)
    os.makedirs(ASSETS, exist_ok=True)

    raw = os.path.join(ASSETS, "_master.png")
    render_master(SVG, S, raw)
    img = Image.open(raw).convert("RGBA")
    try:
        os.remove(raw)
    except OSError:
        pass

    if img.size != (S, S):
        img = img.resize((S, S), Image.LANCZOS)
    if img.getpixel((2, 2))[3] != 0:
        # 圆角外必须是透明的，不然后面各档都会带一块白角
        raise RuntimeError("母版四角不透明，渲染没吃到透明背景参数")

    ico = os.path.join(ASSETS, "icon.ico")
    frames = [img.resize((s, s), Image.LANCZOS) for s in ICO_SIZES]
    frames[-1].save(ico, format="ICO", sizes=[(s, s) for s in ICO_SIZES],
                    append_images=frames[:-1])

    png = os.path.join(ASSETS, "icon.png")
    img.resize((512, 512), Image.LANCZOS).save(png)
    img.resize((256, 256), Image.LANCZOS).save(os.path.join(ASSETS, "icon-256.png"))

    icns = os.path.join(ASSETS, "icon.icns")
    iframes = [img.resize((s, s), Image.LANCZOS) for s in ICNS_SIZES]
    iframes[-1].save(icns, format="ICNS", append_images=iframes[:-1])

    # 浏览器扩展图标：跟桌面 app 用同一张母版，别再各画各的。
    # Chrome 会按 16/32/48/128 原样显示，圆角外的透明边在插件卡片里很干净。
    ext_icons = os.path.join(ROOT, "browser-extension", "icons")
    if os.path.isdir(ext_icons):
        for s in (16, 32, 48, 128):
            img.resize((s, s), Image.LANCZOS).save(
                os.path.join(ext_icons, "icon%d.png" % s))

    # 网页 favicon：index.html 里按 16/32/48/64 引用，也要跟母版一致
    web_img = os.path.join(ROOT, "web", "img")
    if os.path.isdir(web_img):
        for s in (16, 32, 48, 64):
            img.resize((s, s), Image.LANCZOS).save(
                os.path.join(web_img, "favicon-%d.png" % s))

    # 小尺寸检查条：16/32/48/64 各放大 4 倍
    strip = Image.new("RGBA", (4 * 64 * 4 + 5 * 12, 64 * 4 + 24), (245, 245, 245, 255))
    x = 12
    for s in (16, 32, 48, 64):
        small = img.resize((s, s), Image.LANCZOS)
        strip.paste(small.resize((s * 4, s * 4), Image.NEAREST), (x, 12))
        x += s * 4 + 12
    strip.save(os.path.join(ASSETS, "icon-small-preview.png"))
    return ico, png, icns


if __name__ == "__main__":
    ico, png, icns = build()
    for p in (ico, png, icns):
        print(os.path.basename(p), "->", os.path.getsize(p), "bytes")

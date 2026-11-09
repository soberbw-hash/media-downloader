# -*- mode: python ; coding: utf-8 -*-
"""macOS .app 打包配置（GitHub Actions 的 macOS runner 上执行）。

运行环境：工作目录 = 仓库根目录；VDL_FFMPEG = 静态 ffmpeg 可执行文件；
VDL_VERSION = 版本号。生成物在 dist/音视频下载器.app。
"""
import os
from PyInstaller.utils.hooks import collect_submodules

ROOT = os.path.abspath(os.getcwd())
FFMPEG = os.environ.get("VDL_FFMPEG", "")
VERSION = os.environ.get("VDL_VERSION", "1.24.0")
ICON = os.path.join(ROOT, "tools", "assets", "icon.icns")

datas = [
    (os.path.join(ROOT, "web"), "web"),
    (os.path.join(ROOT, "browser-extension"), "browser-extension"),
]
# 随包带一个静态 ffmpeg：朋友不用装 Homebrew，Suno 转 mp3 / 识曲 / 合并都能用
binaries = [(FFMPEG, "bin")] if FFMPEG and os.path.isfile(FFMPEG) else []

local_mods = [
    "server", "core", "paths", "login", "picker", "picker_win",
    "music", "suno", "hls", "recognize", "search", "movies",
    "sources", "aggregate", "sph", "cn_sites", "updater",
]
# yt-dlp 的站点解析器是运行时按名字动态导入的，必须整个收进来，
# 否则打出来的包只能下少数几个站，还不会报错。
hidden = local_mods + [
    "yt_dlp", "shazamio", "shazamio_core",
] + collect_submodules("yt_dlp")

a = Analysis(
    [os.path.join(ROOT, "tools", "mac_entry.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # imageio_ffmpeg 只用来取二进制，运行时不需要；playwright 已不再使用
    excludes=["imageio_ffmpeg", "playwright", "tkinter", "PyQt5", "PySide2"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="音视频下载器",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe, a.binaries, a.datas,
    strip=False, upx=False, upx_exclude=[],
    name="音视频下载器",
)
app = BUNDLE(
    coll,
    name="音视频下载器.app",
    icon=ICON if os.path.isfile(ICON) else None,
    info_plist={
        "CFBundleName": "音视频下载器",
        "CFBundleDisplayName": "音视频下载器",
        "CFBundleIdentifier": "cn.videodl.desktop",
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        "LSMinimumSystemVersion": "11.0",
        "NSHighResolutionCapable": True,
        "NSRequiresAquaSystemAppearance": False,
    },
)

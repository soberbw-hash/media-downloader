# -*- coding: utf-8 -*-
"""程序「读资源」和「写数据」的位置，统一在这里决定。

为什么需要它：macOS 的 .app 是只读的（签名后改动还会破坏签名），
config.json / history.json / cookies / logs 不能写在程序包里；
下载目录也不该藏在 /Applications 里。Windows 便携版保持原来的行为不变。

优先级：VDL_DATA_DIR 环境变量 > macOS 用户目录 > 程序目录（Windows / 源码运行）。
"""
import os
import shutil
import sys

APP_DIR = os.path.dirname(os.path.abspath(__file__))
IS_MAC = sys.platform == "darwin"
IS_WIN = os.name == "nt"


def data_dir():
    """用户数据根目录。调用方写文件前应先 ensure_data_dir()。"""
    env = (os.environ.get("VDL_DATA_DIR") or "").strip()
    if env:
        return os.path.abspath(os.path.expanduser(env))
    if IS_MAC:
        return os.path.join(os.path.expanduser("~"), "Library",
                            "Application Support", "VideoDL")
    # Windows：继续用程序目录，便携版复制过来就能用
    return APP_DIR


def ensure_data_dir():
    d = data_dir()
    os.makedirs(d, exist_ok=True)
    return d


def cookie_dir():
    return os.path.join(data_dir(), "cookies")


def log_dir():
    return os.path.join(data_dir(), "logs")


def config_path():
    return os.path.join(data_dir(), "config.json")


def history_path():
    return os.path.join(data_dir(), "history.json")


def default_downloads():
    if IS_MAC:
        return os.path.join(os.path.expanduser("~"), "Downloads", "音视频下载器")
    return os.path.join(APP_DIR, "downloads")


def ffmpeg_names():
    return ("ffmpeg.exe", "ffmpeg") if IS_WIN else ("ffmpeg",)


def ffmpeg_path():
    """找 ffmpeg：随包 bin/ → 程序目录 → PATH → imageio-ffmpeg 自带的静态版。

    Mac 版打包时会把 imageio-ffmpeg 的二进制放进 bundle 的 bin/ffmpeg，
    所以朋友机器上不用装 Homebrew，也不用装 ffmpeg。
    """
    for d in (APP_DIR, os.path.join(APP_DIR, "bin"),
              os.path.join(APP_DIR, "runtime")):
        for n in ffmpeg_names():
            p = os.path.join(d, n)
            if os.path.isfile(p):
                return p
    p = shutil.which("ffmpeg")
    if p:
        return p
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe() or ""
    except Exception:
        return ""


def ffmpeg_dir():
    """yt-dlp 的 ffmpeg_location 要的是目录。"""
    p = ffmpeg_path()
    return os.path.dirname(p) if p else ""

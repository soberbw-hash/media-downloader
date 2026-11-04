# -*- coding: utf-8 -*-
"""音视频下载器 - 启动器（被打包成 音视频下载器.exe）

只做一件事：找到同目录下的 程序\runtime\python.exe，用无窗口方式拉起
程序\server.py --app，然后自己退出。

找不到运行时或者启动失败时弹一个中文提示框，而不是默默没反应。
"""
import ctypes
import os
import subprocess
import sys

CREATE_NO_WINDOW = 0x08000000

TITLE = "音视频下载器"


def say(msg):
    try:
        ctypes.windll.user32.MessageBoxW(None, msg, TITLE, 0x00000040)
    except Exception:
        pass


def find_app(here):
    """按顺序找 程序 目录：exe 旁边 → exe 下一层 → 桌面上的标准安装位置。

    这样同一个 exe 既能放在安装目录里当主入口，也能单独放桌面当图标。
    """
    cands = [
        os.path.join(here, "程序"),
        os.path.join(here, "音视频下载器", "程序"),
        r"D:\Desktop\音视频下载器\程序",
    ]
    for c in cands:
        if os.path.isfile(os.path.join(c, "server.py")):
            return c
    return None


def main():
    # PyInstaller 单文件模式下 sys.executable 是 exe 自身，取它所在目录
    here = os.path.dirname(os.path.abspath(sys.executable))
    app = find_app(here)

    if app is None:
        say("没找到程序本体（程序 文件夹）。\n\n"
            "如果你把这个图标单独拖到了别处，请把它放回\n"
            "D:\\Desktop\\音视频下载器\\\n"
            "或者直接把整个文件夹一起复制。")
        return 1

    py = os.path.join(app, "runtime", "python.exe")
    entry = os.path.join(app, "server.py")

    if not os.path.isfile(py):
        say("没找到运行环境：\n%s\n\n这个文件夹应该是完整复制过来的，"
            "别单独把 exe 拖走。" % py)
        return 1
    if not os.path.isfile(entry):
        say("没找到程序本体：\n%s" % entry)
        return 1

    subprocess.Popen(
        [py, "server.py", "--app"],
        cwd=app,
        creationflags=CREATE_NO_WINDOW,
        close_fds=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

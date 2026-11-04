# -*- coding: utf-8 -*-
r"""把源码目录打成一个「别人拿到就能双击用」的免安装文件夹。

产物结构：
    音视频下载器\
        音视频下载器.exe         ← 带图标的启动器（家用）
        音视频下载器-公司.lnk    ← 绕过 exe 拦截的入口（公司电脑，带图标、无黑框）
        下载的文件\              ← 下载保存位置
        使用说明.txt
        程序\                   ← 代码 + 内置 Python（隐藏）
            音视频下载器-公司.bat   ← 备份入口（快捷方式失效时用）
            诊断.bat                ← 出问题排查用（不是启动入口）

用法（在有 Pillow、PyInstaller 的环境里跑）：
    python tools/build_portable.py --dst "D:\\Desktop\\音视频下载器" \
        --runtime "C:\\Users\\xxx\\AppData\\Local\\Programs\\WorkBuddy\\resources\\vendor\\python"

--runtime 是拿来当内置运行时的 Python 目录；不传则保留 dst 里已有的 程序\\runtime。
"""
import argparse
import os
import shutil
import subprocess
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CODE_FILES = [
    "server.py", "core.py", "cn_sites.py", "sph.py", "music.py", "login.py",
    "picker.py", "picker_win.py", "version.txt", "README.md", "LICENSE",
    "requirements.txt", "start.bat", "诊断.bat", "启动.command",
]
CODE_DIRS = ["web"]
DATA_FILES = ["config.json", "history.json"]
DATA_DIRS = ["cookies"]

# 打包时按平台剔掉用不上的启动脚本，免得用户打开「程序」看到一堆不认识的东西
#   Windows 包：启动.command 是 mac 的，start.bat 是旧的开发用壳，都不带
#   mac 包：两个 bat 都是 Windows 专用，不带
IS_WIN = os.name == "nt"
SKIP_ON_WIN = {"start.bat", "启动.command"}
SKIP_ON_MAC = {"start.bat", "诊断.bat"}


def copytree(src, dst, skip=("__pycache__",)):
    if not os.path.isdir(src):
        return
    for root, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d not in skip]
        rel = os.path.relpath(root, src)
        target = os.path.join(dst, rel) if rel != "." else dst
        os.makedirs(target, exist_ok=True)
        for f in files:
            if f.endswith(".pyc"):
                continue
            shutil.copy2(os.path.join(root, f), os.path.join(target, f))


def build_exe(runtime_env):
    """用 PyInstaller 打启动器，返回 exe 路径"""
    assets = os.path.join(BASE, "tools", "assets")
    ico = os.path.join(assets, "icon.ico")
    if not os.path.isfile(ico):
        subprocess.run([sys.executable, os.path.join(BASE, "tools", "make_icon.py")],
                       check=True)
    dist = os.path.join(BASE, "tools", "dist")
    cmd = [
        sys.executable, "-m", "PyInstaller", "--onefile", "--windowed",
        "--name", "音视频下载器", "--icon", ico,
        "--distpath", dist,
        "--workpath", os.path.join(BASE, "tools", "build"),
        "--specpath", os.path.join(BASE, "tools"),
        os.path.join(BASE, "tools", "launcher.py"),
    ]
    subprocess.run(cmd, check=True, cwd=BASE)
    return os.path.join(dist, "音视频下载器.exe")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dst", required=True, help="目标文件夹")
    ap.add_argument("--runtime", default="", help="当作内置运行时的 Python 目录")
    ap.add_argument("--no-exe", action="store_true", help="跳过 exe 打包")
    a = ap.parse_args()

    dst = os.path.abspath(a.dst)
    app = os.path.join(dst, "程序")
    os.makedirs(app, exist_ok=True)

    skip = SKIP_ON_WIN if IS_WIN else SKIP_ON_MAC
    for f in CODE_FILES + DATA_FILES:
        if f in skip:
            continue
        p = os.path.join(BASE, f)
        if os.path.isfile(p):
            shutil.copy2(p, os.path.join(app, f))
    # 顺带清掉上一版留下来的、这一版不该有的启动脚本
    for f in skip:
        old = os.path.join(app, f)
        if os.path.isfile(old):
            os.remove(old)
    for d in CODE_DIRS + DATA_DIRS:
        copytree(os.path.join(BASE, d), os.path.join(app, d))

    if a.runtime:
        copytree(a.runtime, os.path.join(app, "runtime"))
    elif not os.path.isdir(os.path.join(app, "runtime")):
        print("!! 目标里没有 程序\\runtime，也没给 --runtime，打出来的包跑不了")

    os.makedirs(os.path.join(dst, "下载的文件"), exist_ok=True)

    if IS_WIN and not a.no_exe:
        exe = build_exe(a.runtime)
        shutil.copy2(exe, os.path.join(dst, "音视频下载器.exe"))

    if IS_WIN:
        # 公司电脑专用入口：exe 被端点安全拦下时走它
        #   · 根目录生成带图标的快捷方式（.lnk 才有地方存图标，bat 没有）
        #   · bat 本体放「程序」里当备份，换电脑后快捷方式失效时还能用
        subprocess.run([sys.executable, os.path.join(BASE, "tools", "company_bat.py"),
                        app], check=False)
        subprocess.run([sys.executable, os.path.join(BASE, "tools", "make_shortcut.py"),
                        dst, dst], check=False)
        # 隐藏 程序，桌面看起来才干净
        subprocess.run(["attrib", "+h", app], check=False)
    print("打包完成:", dst)


if __name__ == "__main__":
    main()

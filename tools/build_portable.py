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
            bin\ffmpeg.exe          ← 转 mp3 用（Suno），约 97MB

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
    "server.py", "core.py", "cn_sites.py", "sph.py", "music.py", "suno.py",
    "hls.py", "login.py", "search.py", "movies.py", "recognize.py",
    "sources.py", "aggregate.py",
    "picker.py", "picker_win.py", "version.txt", "README.md", "LICENSE",
    "requirements.txt", "start.bat", "诊断.bat", "启动.command",
]
CODE_DIRS = ["web", "browser-extension"]
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


#: ffmpeg 的常见安装位置（PATH 里找不到时按这些兜）
FFMPEG_HINTS = [
    "C:/ffmpeg/bin/ffmpeg.exe",
    "C:/Program Files/ffmpeg/bin/ffmpeg.exe",
]


def find_sys_ffmpeg():
    """找一个能用的 ffmpeg.exe，用来打进便携版（Suno 转 mp3 要用）。"""
    p = shutil.which("ffmpeg")
    if p and os.path.isfile(p):
        return p
    for h in FFMPEG_HINTS:
        if os.path.isfile(h):
            return h
    import glob
    for pat in ("C:/Users/*/AppData/Local/Microsoft/WinGet/Packages/*ffmpeg*/**/bin/ffmpeg.exe",
                "C:/Users/*/scoop/apps/ffmpeg*/**/bin/ffmpeg.exe"):
        hit = glob.glob(pat, recursive=True)
        if hit:
            return hit[0]
    return ""


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
    ap.add_argument("--reset-data", action="store_true",
                    help="连 config.json / history.json 一起覆盖（默认保留目标里的）")
    a = ap.parse_args()

    dst = os.path.abspath(a.dst)
    app = os.path.join(dst, "程序")
    os.makedirs(app, exist_ok=True)

    skip = SKIP_ON_WIN if IS_WIN else SKIP_ON_MAC
    missing = []
    fresh = {}       # 哪些用户数据是这次才从源码带过去的
    for f in CODE_FILES + DATA_FILES:
        if f in skip:
            continue
        p = os.path.join(BASE, f)
        if not os.path.isfile(p):
            missing.append(f)
            continue
        tgt = os.path.join(app, f)
        if f in DATA_FILES:
            fresh[f] = not os.path.isfile(tgt)
        # 配置和历史是用户数据：目标里已经有了就留着，别用开发机的覆盖过去
        if f in DATA_FILES and os.path.isfile(tgt) and not a.reset_data:
            print("保留已有:", f)
            continue
        shutil.copy2(p, tgt)
    if missing:
        raise SystemExit("源码里少了这些文件，打包中止：%s" % "、".join(missing))
    # 顺带清掉上一版留下来的、这一版不该有的启动脚本
    for f in skip:
        old = os.path.join(app, f)
        if os.path.isfile(old):
            os.remove(old)
    for d in CODE_DIRS + DATA_DIRS:
        # cookies 目录里的 _profile 是登录用的浏览器配置，几十 MB，
        # 而且带的是本机账号痕迹，绝不能进分发包
        sk = ("__pycache__", "_profile") if d == "cookies" else ("__pycache__",)
        copytree(os.path.join(BASE, d), os.path.join(app, d), sk)
        if d == "cookies":
            stale = os.path.join(app, d, "_profile")
            if os.path.isdir(stale):
                shutil.rmtree(stale, ignore_errors=True)

    if a.runtime:
        copytree(a.runtime, os.path.join(app, "runtime"))
    elif not os.path.isdir(os.path.join(app, "runtime")):
        print("!! 目标里没有 程序\\runtime，也没给 --runtime，打出来的包跑不了")

    # 内置 ffmpeg：Suno 的 mp4 抽音轨转 mp3 靠它，打包进去别人不用自己装
    ff = find_sys_ffmpeg()
    if ff:
        bindir = os.path.join(app, "bin")
        os.makedirs(bindir, exist_ok=True)
        shutil.copy2(ff, os.path.join(bindir, "ffmpeg.exe"))
        print("内置 ffmpeg:", ff, "->", os.path.join(bindir, "ffmpeg.exe"))
    elif not os.path.isfile(os.path.join(app, "bin", "ffmpeg.exe")):
        print("!! 没找到 ffmpeg，Suno 只能存 mp4。装一个再打包（winget install Gyan.FFmpeg）")

    out_dir = os.path.join(dst, "下载的文件")
    os.makedirs(out_dir, exist_ok=True)

    # 源码的 config.json 指向开发机路径，照搬过去别人会「下完找不到文件」；
    # 只在路径明显不对时改，用户自己设过的目录不动。
    import json
    cfg_path = os.path.join(app, "config.json")
    try:
        with open(cfg_path, encoding="utf-8") as f:
            c = json.load(f)
        old = (c.get("out_dir") or "").replace("/", "\\").rstrip("\\").lower()
        good = out_dir.replace("/", "\\").rstrip("\\").lower()
        dev = BASE.replace("/", "\\").rstrip("\\").lower()
        if old != good and (not old or old.startswith(dev)
                            or not os.path.isdir(c.get("out_dir") or "")):
            c["out_dir"] = out_dir
            c["out_dir_set"] = True
            with open(cfg_path, "w", encoding="utf-8") as f:
                json.dump(c, f, ensure_ascii=False, indent=2)
            print("默认下载目录 ->", out_dir)
    except Exception as e:
        print("!! 没能校正默认下载目录:", e)

    # 第一次打包别把开发机上的下载记录带过去（用户自己的记录不碰）
    if fresh.get("history.json"):
        try:
            with open(os.path.join(app, "history.json"), "w", encoding="utf-8") as f:
                json.dump([], f)
            print("下载记录 -> 空")
        except Exception as e:
            print("!! 没能清空下载记录:", e)

    # 根目录说明跟着当前版本走，别留着几版之前的旧文档
    doc = os.path.join(BASE, "使用说明.txt")
    if os.path.isfile(doc):
        try:
            with open(doc, encoding="utf-8") as f:
                text = f.read()
            with open(os.path.join(BASE, "version.txt"), encoding="utf-8") as f:
                ver = f.read().strip()
            with open(os.path.join(dst, "使用说明.txt"), "w", encoding="utf-8-sig") as f:
                f.write(text.replace("{VER}", ver))
        except Exception as e:
            print("!! 没能写使用说明:", e)

    # 产物自检：每个代码文件都得真的落到目标里。
    # 以前清单漏了一个文件，打出来的包就少个模块，运行时报 ImportError，
    # 但打包过程一声不吭——这里直接把话说出来
    absent = [f for f in CODE_FILES
              if f not in skip and not os.path.isfile(os.path.join(app, f))]
    if absent:
        raise SystemExit("打包不完整，缺少：%s" % "、".join(absent))
    print("自检通过：%d 个代码文件齐全" % len(CODE_FILES))

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

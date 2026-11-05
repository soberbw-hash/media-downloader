# -*- coding: utf-8 -*-
r"""一键生成「干净副本」：能直接发给别人的那一份。

以桌面上那份便携版为底（runtime / exe / ffmpeg 都在里头，不用重打），
再把代码刷新到最新，最后清掉不该外传的东西：

  1. 不带 cookies\            —— 登录态是你自己的账号，不能跟文件一起发出去
  2. 不带 下载的文件\          —— 你下的视频是你的素材，不外传（空目录保留）
  3. history.json 清空        —— 你的下载记录不外传
  4. config.json 的下载目录指向副本自己的「下载的文件」，别人一开就能用
  5. 快捷方式按新路径重新生成 —— 旧快捷方式指向你这台机器的绝对路径，过去就失效

用法：
    python tools/make_clean_copy.py                        # 默认放桌面
    python tools/make_clean_copy.py --dst "D:\Desktop\某目录"
    python tools/make_clean_copy.py --no-zip               # 只出文件夹，不压 zip
    python tools/make_clean_copy.py --src "D:\Desktop\音视频下载器"

默认产物：桌面\音视频下载器-副本\  和  桌面\音视频下载器-副本.zip
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COPY_SKIP = {"cookies", "下载的文件", "__pycache__", "logs"}


def default_desktop():
    """桌面在哪：先问注册表（可能被重定向到 D 盘），拿不到就用用户目录下的 Desktop。"""
    if os.name == "nt":
        try:
            import winreg
            k = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders")
            v, _ = winreg.QueryValueEx(k, "Desktop")
            p = os.path.expandvars(v)
            if os.path.isdir(p):
                return p
        except Exception:
            pass
    return os.path.join(os.path.expanduser("~"), "Desktop")


def copy_base(src, dst):
    """把现有便携版整份拷过来，去掉登录态和下载的文件。"""
    for root, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d not in COPY_SKIP]
        rel = os.path.relpath(root, src)
        target = os.path.join(dst, rel) if rel != "." else dst
        os.makedirs(target, exist_ok=True)
        for f in files:
            if f.endswith(".pyc"):
                continue
            shutil.copy2(os.path.join(root, f), os.path.join(target, f))
    os.makedirs(os.path.join(dst, "下载的文件"), exist_ok=True)


def refresh_code(dst):
    """用源码目录把副本里的代码刷成最新。runtime/exe/ffmpeg 是拷来的，不动。"""
    cmd = [sys.executable, os.path.join(BASE, "tools", "build_portable.py"),
           "--dst", dst, "--no-exe", "--reset-data"]
    r = subprocess.run(cmd, check=False)
    return r.returncode == 0


def clean(dst):
    """把副本里的账号痕迹和私人记录清掉，配置改成指向自己。"""
    app = os.path.join(dst, "程序")
    # 1) 登录态一个字节都不能带出去（刷新代码那步会把开发机的 cookies 拷进来）
    ck = os.path.join(app, "cookies")
    if os.path.isdir(ck):
        shutil.rmtree(ck, ignore_errors=True)
        print("已清空 cookies（登录态不外传）")
    # 2) 下载记录清空
    hist = os.path.join(app, "history.json")
    try:
        with open(hist, "w", encoding="utf-8") as f:
            f.write("[]")
        print("已清空下载历史")
    except OSError as e:
        print("!! 历史没清成：", e)
    # 3) 下载目录指向副本自己的「下载的文件」
    cfg = os.path.join(app, "config.json")
    data = {}
    if os.path.isfile(cfg):
        try:
            with open(cfg, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}
    data["out_dir"] = os.path.join(dst, "下载的文件")
    data["out_dir_set"] = True
    data["music_dir"] = ""
    data["cookies_file"] = ""
    os.makedirs(data["out_dir"], exist_ok=True)
    tmp = cfg + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, cfg)
    print("下载目录已指向：", data["out_dir"])


def make_zip(dst, zip_path):
    """整包压成 zip，方便直接发出去。"""
    root = os.path.dirname(os.path.abspath(dst))
    name = os.path.basename(os.path.abspath(dst))
    if os.path.exists(zip_path):
        os.remove(zip_path)
    print("正在压缩（包不小，几分钟）…")
    out = shutil.make_archive(os.path.splitext(zip_path)[0], "zip",
                              root_dir=root, base_dir=name)
    mb = os.path.getsize(out) / 1048576.0
    print("zip 完成：%.1f MB -> %s" % (mb, out))
    return out


def main():
    ap = argparse.ArgumentParser(description="生成一份干净的、可以发给别人的副本")
    ap.add_argument("--dst", default="", help="副本放到哪（默认：桌面\\音视频下载器-副本）")
    ap.add_argument("--src", default="",
                    help="以哪份便携版为底（默认：桌面\\音视频下载器）")
    ap.add_argument("--no-zip", action="store_true", help="只出文件夹，不压 zip")
    a = ap.parse_args()

    desk = default_desktop()
    dst = (a.dst or "").strip().strip('"') or os.path.join(desk, "音视频下载器-副本")
    dst = os.path.abspath(dst)
    src = (a.src or "").strip().strip('"') or os.path.join(desk, "音视频下载器")
    src = os.path.abspath(src)

    if not os.path.isdir(os.path.join(src, "程序")):
        print("!! 没找到便携版：", src)
        print("   先做一份再生成副本：python tools/build_portable.py --dst \"%s\"" % src)
        return 1
    if os.path.abspath(dst) == src:
        print("!! 副本不能跟源目录是同一个")
        return 1

    print("底包：", src)
    copy_base(src, dst)
    print("已复制（不含 cookies / 下载的文件）")

    if not refresh_code(dst):
        print("!! 刷新代码失败，副本可能是旧版。看上面的报错。")
        return 1
    clean(dst)

    # 快捷方式按新路径重新生成（拷过来的那份指向源目录，是死链）
    if os.name == "nt":
        subprocess.run([sys.executable, os.path.join(BASE, "tools", "make_shortcut.py"),
                        dst, dst], check=False)
        subprocess.run(["attrib", "+h", os.path.join(dst, "程序")], check=False)

    total = sum(os.path.getsize(os.path.join(r_, f))
                for r_, _, fs in os.walk(dst) for f in fs)
    print("副本完成：%.1f MB -> %s" % (total / 1048576.0, dst))

    if not a.no_zip:
        make_zip(dst, dst + ".zip")
    print("\n把 %s（或那个 zip）发给对方，解压后双击「音视频下载器.exe」或" % os.path.basename(dst))
    print("「音视频下载器-公司.lnk」就能用，什么都不用装。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

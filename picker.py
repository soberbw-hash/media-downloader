# -*- coding: utf-8 -*-
"""系统「选择文件夹」对话框（跨平台入口）。

Windows -> picker_win.py，Win32 的 IFileOpenDialog，外观和资源管理器
           右键「选择文件夹」完全一致。
macOS   -> AppleScript 唤原生选择面板（不依赖 pyobjc / tkinter）。
其它    -> tkinter 兜底，实在没有就返回空字符串（前端会退化成手填路径）。
"""

import os
import subprocess
import sys

_IS_WIN = os.name == "nt"
_IS_MAC = sys.platform == "darwin"


def pick_folder(initial=None, title="选择下载目录", parent=None):
    """弹系统文件夹选择框。返回绝对路径；用户取消或失败返回空字符串。"""
    if _IS_WIN:
        try:
            import picker_win
        except Exception:
            return _pick_tk(initial, title)
        return picker_win.pick_folder(initial=initial, title=title, parent=parent)
    if _IS_MAC:
        return _pick_mac(initial, title)
    return _pick_tk(initial, title)


def _as_str(s):
    """AppleScript 字符串字面量：先把反斜杠和引号转义掉。"""
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _pick_mac(initial, title):
    bits = ["POSIX path of (choose folder with prompt " + _as_str(title)]
    if initial and os.path.isdir(initial):
        bits.append(" default location POSIX file " + _as_str(initial))
    bits.append(")")
    try:
        r = subprocess.run(["osascript", "-e", "".join(bits)],
                           capture_output=True, text=True, timeout=1800)
    except Exception:
        return _pick_tk(initial, title)

    if r.returncode != 0:
        err = (r.stderr or "")
        # -128 = 用户点了取消；这种情况不该再弹第二个框
        if "-128" in err or "User canceled" in err or "用户已取消" in err:
            return ""
        return _pick_tk(initial, title)

    p = (r.stdout or "").strip()
    if not p:
        return ""
    # choose folder 返回的路径带结尾斜杠，去掉（根目录 "/" 保留）
    return p.rstrip("/") or "/"


def _pick_tk(initial, title):
    """最后的兜底：tkinter 自带的目录选择框（有些 python 构建没带 tkinter）。"""
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception:
        return ""
    try:
        root = tk.Tk()
    except Exception:
        return ""
    try:
        root.withdraw()
        try:
            root.attributes("-topmost", True)
        except Exception:
            pass
        got = filedialog.askdirectory(
            initialdir=initial or os.path.expanduser("~"),
            title=title, mustexist=True)
        return got or ""
    except Exception:
        return ""
    finally:
        try:
            root.destroy()
        except Exception:
            pass

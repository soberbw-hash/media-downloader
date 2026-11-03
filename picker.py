# -*- coding: utf-8 -*-
"""系统「选择文件夹」对话框。

用 Windows 原生的 IFileOpenDialog（FOS_PICKFOLDERS），
外观和资源管理器里右键 → 选择文件夹完全一致，不是老掉牙的树形控件。
没装 tkinter 也能用，只依赖系统自带的 ole32 / shell32。
"""

import ctypes
import os
import threading
import time
from ctypes import (POINTER, Structure, WINFUNCTYPE, byref, c_ubyte, c_ulong,
                    c_ushort, c_void_p, c_wchar_p)

ole32 = ctypes.WinDLL("ole32")
shell32 = ctypes.WinDLL("shell32")
user32 = ctypes.WinDLL("user32")

_LOCK = threading.Lock()          # 同一时间只弹一个


class GUID(Structure):
    _fields_ = [("Data1", c_ulong), ("Data2", c_ushort),
                ("Data3", c_ushort), ("Data4", c_ubyte * 8)]


def _g(d1, d2, d3, *rest):
    g = GUID()
    g.Data1, g.Data2, g.Data3 = d1, d2, d3
    for i, x in enumerate(rest):
        g.Data4[i] = x
    return g


CLSID_FileOpenDialog = _g(0xDC1C5A9C, 0xE88A, 0x4DDE,
                          0xA5, 0xA1, 0x60, 0xF8, 0x2A, 0x20, 0xAE, 0xF7)
IID_IFileOpenDialog = _g(0xD57C7288, 0xD4AD, 0x4768,
                         0xBE, 0x02, 0x9D, 0x96, 0x95, 0x32, 0xD9, 0x60)
IID_IShellItem = _g(0x43826D1E, 0xE718, 0x42EE,
                    0xBC, 0x55, 0xA1, 0xE2, 0x61, 0xC3, 0x7B, 0xFE)

FOS_PICKFOLDERS = 0x00000020
FOS_FORCEFILESYSTEM = 0x00000040
FOS_PATHMUSTEXIST = 0x00000800
SIGDN_FILESYSPATH = 0x80058000
CLSCTX_INPROC_SERVER = 0x1
S_OK = 0
S_FALSE = 1
RPC_E_CHANGED_MODE = -2147417850

# vtable 下标
_VT_RELEASE = 2
_VT_SHOW = 3                    # IModalWindow::Show
_VT_SET_OPTIONS = 9
_VT_GET_OPTIONS = 10
_VT_SET_DEFAULT_FOLDER = 11     # IFileDialog::SetDefaultFolder
_VT_SET_FOLDER = 12             # IFileDialog::SetFolder
_VT_SET_TITLE = 17
_VT_GET_RESULT = 20
_VT_ITEM_GET_DISPLAY_NAME = 5   # IShellItem::GetDisplayName

# 窗口枚举相关声明（64 位下不声明的话回调指针会被截断）
_ENUM_PROC = WINFUNCTYPE(ctypes.c_bool, c_void_p, c_void_p)
user32.EnumWindows.argtypes = [_ENUM_PROC, c_void_p]
user32.EnumWindows.restype = ctypes.c_bool
user32.GetClassNameW.argtypes = [c_void_p, ctypes.c_wchar_p, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [c_void_p, POINTER(c_ulong)]
user32.ShowWindow.argtypes = [c_void_p, ctypes.c_int]
user32.SetForegroundWindow.argtypes = [c_void_p]
user32.BringWindowToTop.argtypes = [c_void_p]
user32.PostMessageW.argtypes = [c_void_p, ctypes.c_uint, c_void_p, c_void_p]


def _fn(ptr, idx, restype, *argtypes):
    """取 COM 对象第 idx 个虚函数。"""
    vtbl = ctypes.cast(ptr, POINTER(POINTER(c_void_p))).contents
    proto = WINFUNCTYPE(restype, c_void_p, *argtypes)
    return proto(vtbl[idx])


def _release(ptr):
    try:
        _fn(ptr, _VT_RELEASE, c_ulong)(ptr)
    except Exception:
        pass


_SHCreateItemFromParsingName = shell32.SHCreateItemFromParsingName
_SHCreateItemFromParsingName.restype = ctypes.c_long
_SHCreateItemFromParsingName.argtypes = [
    c_wchar_p, c_void_p, ctypes.POINTER(GUID), ctypes.POINTER(c_void_p)]


def _shell_item(path):
    if not path:
        return None
    p = c_void_p()
    hr = _SHCreateItemFromParsingName(path, None, byref(IID_IShellItem), byref(p))
    return p if hr == S_OK and p else None


def _find_own_dialog(pid):
    """找本进程弹出的 #32770 窗口（标准对话框类名）。"""
    found = []

    def cb(hwnd, _):
        try:
            wpid = c_ulong()
            user32.GetWindowThreadProcessId(hwnd, byref(wpid))
            if wpid.value != pid:
                return True
            buf = ctypes.create_unicode_buffer(64)
            user32.GetClassNameW(hwnd, buf, 64)
            if buf.value == "#32770":
                found.append(hwnd)
                return False
        except Exception:
            pass
        return True

    user32.EnumWindows(_ENUM_PROC(cb), None)
    return found[0] if found else None


def _raise_later(pid):
    """服务在后台跑，对话框可能弹到浏览器后面 —— 找到就提到最前。"""
    for _ in range(20):
        time.sleep(0.15)
        h = _find_own_dialog(pid)
        if h:
            try:
                user32.ShowWindow(h, 5)               # SW_SHOW
                user32.SetForegroundWindow(h)
                user32.BringWindowToTop(h)
            except Exception:
                pass
            return


def pick_folder(initial=None, title="选择下载目录", parent=None):
    """弹系统文件夹选择框。返回绝对路径；用户取消返回空字符串。

    COM 全在独立线程里初始化 / 释放，不碰调用方的 COM 状态 ——
    服务端是 HTTP 线程池，混在一起偶尔会崩。
    """
    if not _LOCK.acquire(blocking=False):
        return ""
    box = {}

    def run():
        com = ole32.CoInitialize(None)
        try:
            box["path"] = _pick(initial, title, parent)
        except Exception:
            box["path"] = ""
        finally:
            if com in (S_OK, S_FALSE):
                ole32.CoUninitialize()

    try:
        t = threading.Thread(target=run, daemon=True)
        t.start()
        # 服务在后台跑，对话框可能弹到后面 —— 找到就提到最前
        threading.Thread(target=_raise_later, args=(os.getpid(),), daemon=True).start()
        t.join()
        return box.get("path", "")
    finally:
        _LOCK.release()


def _pick(initial, title, parent):
    dlg = c_void_p()
    hr = ole32.CoCreateInstance(byref(CLSID_FileOpenDialog), None,
                                CLSCTX_INPROC_SERVER,
                                byref(IID_IFileOpenDialog), byref(dlg))
    if hr != S_OK or not dlg:
        return ""

    try:
        # 对话框按钮写「选择文件夹」，并让它有资格抢到前台
        try:
            user32.AllowSetForegroundWindow(ctypes.c_uint(-1))
        except Exception:
            pass

        opts = c_ulong()
        if _fn(dlg, _VT_GET_OPTIONS, ctypes.c_long, POINTER(c_ulong))(dlg, byref(opts)) == S_OK:
            _fn(dlg, _VT_SET_OPTIONS, ctypes.c_long, c_ulong)(
                dlg, opts.value | FOS_PICKFOLDERS | FOS_FORCEFILESYSTEM | FOS_PATHMUSTEXIST)

        if title:
            _fn(dlg, _VT_SET_TITLE, ctypes.c_long, c_wchar_p)(dlg, title)

        start = initial if initial and os.path.isdir(initial) else os.path.expanduser("~")
        item = _shell_item(start)
        if item:
            _fn(dlg, _VT_SET_DEFAULT_FOLDER, ctypes.c_long, c_void_p)(dlg, item)
            _fn(dlg, _VT_SET_FOLDER, ctypes.c_long, c_void_p)(dlg, item)
            _release(item)

        if _fn(dlg, _VT_SHOW, ctypes.c_long, c_void_p)(dlg, parent) != S_OK:
            return ""                       # 用户取消 / 关掉了

        res = c_void_p()
        if _fn(dlg, _VT_GET_RESULT, ctypes.c_long, POINTER(c_void_p))(dlg, byref(res)) != S_OK or not res:
            return ""
        try:
            name = c_wchar_p()
            if _fn(res, _VT_ITEM_GET_DISPLAY_NAME, ctypes.c_long,
                   c_ulong, POINTER(c_wchar_p))(res, SIGDN_FILESYSPATH, byref(name)) != S_OK:
                return ""
            return name.value or ""
        finally:
            _release(res)
    finally:
        _release(dlg)

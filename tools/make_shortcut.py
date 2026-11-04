# -*- coding: utf-8 -*-
"""生成带图标的「音视频下载器-公司.lnk」快捷方式。

为什么要有这个东西：
    .bat 文件本身没有地方存图标资源，Windows 只会给它那个难看的默认图标，
    改不了（除非动注册表把所有 .bat 都换掉，那是污染系统）。
    快捷方式（.lnk）可以自带自定义图标，而且能直接指向 pythonw.exe，
    连「黑框闪一下」都省了。

注意：
    .lnk 里存的是绝对路径，只适合自己这台电脑用。
    要拷给别人，仍然用文件夹里的 音视频下载器-公司.bat。

单独用：
    python tools/make_shortcut.py "D:\\Desktop\\音视频下载器" "D:\\Desktop"
"""
import ctypes
import os
import sys
import uuid
from ctypes import (POINTER, byref, c_int, c_ubyte, c_ulong, c_ushort,
                    c_void_p, c_wchar_p)

ole32 = ctypes.oledll.ole32
CLSCTX_INPROC_SERVER = 1
HRESULT = ctypes.c_long


class GUID(ctypes.Structure):
    _fields_ = [("Data1", c_ulong), ("Data2", c_ushort), ("Data3", c_ushort),
                ("Data4", c_ubyte * 8)]


def _guid(s):
    b = uuid.UUID(s).bytes_le
    return GUID(c_ulong.from_buffer_copy(b[0:4]).value,
                c_ushort.from_buffer_copy(b[4:6]).value,
                c_ushort.from_buffer_copy(b[6:8]).value,
                (c_ubyte * 8).from_buffer_copy(b[8:16]))


CLSID_ShellLink = _guid("00021401-0000-0000-C000-000000000046")
IID_IShellLinkW = _guid("000214F9-0000-0000-C000-000000000046")
IID_IPersistFile = _guid("0000010B-0000-0000-C000-000000000046")


def _method(p, index, restype, *argtypes):
    """按 vtable 序号取方法，返回可直接调用的函数"""
    vtbl = ctypes.cast(p, POINTER(POINTER(c_void_p))).contents
    proto = ctypes.WINFUNCTYPE(restype, c_void_p, *argtypes)
    return proto(vtbl[index])


def create_lnk(path, target, args="", cwd="", icon="", icon_index=0,
               desc="", show=1):
    ole32.CoInitialize(None)
    punk = c_void_p()
    ole32.CoCreateInstance(byref(CLSID_ShellLink), None, CLSCTX_INPROC_SERVER,
                           byref(IID_IShellLinkW), byref(punk))
    if not punk:
        raise RuntimeError("CoCreateInstance 失败")

    _method(punk, 20, HRESULT, c_wchar_p)(punk, target)                   # SetPath
    if args:
        _method(punk, 11, HRESULT, c_wchar_p)(punk, args)                 # SetArguments
    if cwd:
        _method(punk, 9, HRESULT, c_wchar_p)(punk, cwd)                   # SetWorkingDirectory
    if desc:
        _method(punk, 7, HRESULT, c_wchar_p)(punk, desc)                  # SetDescription
    if icon:
        _method(punk, 17, HRESULT, c_wchar_p, c_int)(punk, icon, icon_index)
    _method(punk, 15, c_int, c_int)(punk, show)                           # SetShowCmd

    pf = c_void_p()
    _method(punk, 0, HRESULT, c_void_p, POINTER(c_void_p))(
        punk, byref(IID_IPersistFile), byref(pf))
    if not pf:
        raise RuntimeError("QueryInterface(IPersistFile) 失败")
    _method(pf, 6, HRESULT, c_wchar_p, c_int)(pf, os.path.abspath(path), 1)  # Save
    _method(pf, 2, c_ulong)(pf)
    _method(punk, 2, c_ulong)(punk)
    return os.path.abspath(path)


def build(app_dir, out_dir=None, name="音视频下载器-公司"):
    """app_dir = 便携版里的「程序」文件夹；out_dir = lnk 生成到哪儿"""
    app_dir = os.path.abspath(app_dir)
    out_dir = os.path.abspath(out_dir or os.path.dirname(app_dir))
    pyw = os.path.join(app_dir, "runtime", "pythonw.exe")
    ico = os.path.join(app_dir, "assets", "icon.ico")
    if not os.path.isfile(pyw):
        raise SystemExit("没找到 Python 运行环境: %s" % pyw)
    out = os.path.join(out_dir, name + ".lnk")
    return create_lnk(out, target=pyw, args="server.py --app", cwd=app_dir,
                      icon=ico if os.path.isfile(ico) else "",
                      desc="音视频下载器（公司电脑入口）")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    app = sys.argv[1]
    if not app.lower().endswith("程序"):
        app = os.path.join(app, "程序")
    out = sys.argv[2] if len(sys.argv) > 2 else None
    print("written:", build(app, out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

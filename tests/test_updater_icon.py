# -*- coding: utf-8 -*-
"""更新后图标刷新回归：换了 icon.ico 必须让桌面重读，否则别人更新还是旧图。

    python tests/test_updater_icon.py
"""
import ctypes
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import updater  # noqa: E402
import ctypes as _ctypes  # 与函数内 import 共享同一模块对象


def log(*a):
    print(*a, flush=True)


def test_refresh_finds_both_shortcuts():
    """target 是程序目录，要能找到上级和桌面的两个快捷方式并刷时间戳。"""
    tmp = tempfile.mkdtemp(prefix="vdl_icon_")
    try:
        app = os.path.join(tmp, "音视频下载器", "程序")
        parent = os.path.dirname(app)
        desktop = tmp
        os.makedirs(app, exist_ok=True)
        lnk1 = os.path.join(parent, "音视频下载器-公司.lnk")
        lnk2 = os.path.join(desktop, "音视频下载器-公司.lnk")
        for p in (lnk1, lnk2):
            open(p, "wb").write(b"fake")
            os.utime(p, (100000, 100000))        # 推到很久以前

        # 打桩 SHChangeNotify：测试环境不真广播，但要确认它被调到
        called = []
        real_is_win = updater.IS_WIN
        if real_is_win:
            orig = _ctypes.windll.shell32.SHChangeNotify
            _ctypes.windll.shell32.SHChangeNotify = (
                lambda *a: called.append(a))
        try:
            updater.IS_WIN = True               # 强制走 Windows 分支验证逻辑
            n = updater.refresh_icon_cache(app)
        finally:
            updater.IS_WIN = real_is_win
            if real_is_win:
                _ctypes.windll.shell32.SHChangeNotify = orig

        assert n == 2, "应刷到 2 个快捷方式，实际 %d" % n
        for p in (lnk1, lnk2):
            assert os.path.getmtime(p) > 100000, "时间戳没推进: " + p
        if real_is_win:
            assert called, "SHChangeNotify 应被调到"
        log("✓ 找到上级+桌面两个快捷方式并刷时间戳")
        log("✓ SHChangeNotify 广播已发出" if real_is_win
            else "✓ (非 Windows，跳过 SHChangeNotify 断言)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_no_match_no_crash():
    """没有快捷方式（Mac/干净安装）时不能报错。"""
    tmp = tempfile.mkdtemp(prefix="vdl_icon_")
    try:
        app = os.path.join(tmp, "程序")
        os.makedirs(app, exist_ok=True)
        n = updater.refresh_icon_cache(app)
        assert n == 0, n
        log("✓ 找不到快捷方式时安静返回 0")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_notify_failure_does_not_raise():
    """SHChangeNotify 出问题（老系统/权限）绝对不能让刷新抛异常。"""
    tmp = tempfile.mkdtemp(prefix="vdl_icon_")
    try:
        app = os.path.join(tmp, "音视频下载器", "程序")
        os.makedirs(app, exist_ok=True)
        lnk = os.path.join(os.path.dirname(app), "音视频下载器-公司.lnk")
        open(lnk, "wb").write(b"fake")

        real_is_win = updater.IS_WIN
        orig = None
        if real_is_win:
            orig = _ctypes.windll.shell32.SHChangeNotify

            def boom(*a):
                raise OSError("通知失败")
            _ctypes.windll.shell32.SHChangeNotify = boom
        try:
            updater.IS_WIN = True
            n = updater.refresh_icon_cache(app)   # 不应抛
        finally:
            updater.IS_WIN = real_is_win
            if real_is_win:
                _ctypes.windll.shell32.SHChangeNotify = orig
        assert n == 1, n
        log("✓ SHChangeNotify 失败被吞掉，不影响更新")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_non_windows_noop():
    """非 Windows 直接跳过，不碰 ctypes.windll。"""
    real = updater.IS_WIN
    try:
        updater.IS_WIN = False
        n = updater.refresh_icon_cache("whatever")
        assert n == 0, n
        log("✓ 非 Windows 空操作")
    finally:
        updater.IS_WIN = real


if __name__ == "__main__":
    test_refresh_finds_both_shortcuts()
    test_no_match_no_crash()
    test_notify_failure_does_not_raise()
    test_non_windows_noop()
    print("ALL PASS", flush=True)

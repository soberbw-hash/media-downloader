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


def test_hashed_icon_and_repoint():
    """换图标要换路径：Windows 按路径缓存，同名覆盖后桌面还是旧图（实测过）。

    这里盯三件事：① 复制成 icon-<指纹>.ico ② 旧指纹副本清掉 ③ 快捷方式真指过去
    """
    tmp = tempfile.mkdtemp(prefix="vdl_icon_")
    try:
        app = os.path.join(tmp, "音视频下载器", "程序")
        assets = os.path.join(app, "assets")
        os.makedirs(assets, exist_ok=True)
        ico = os.path.join(assets, "icon.ico")
        open(ico, "wb").write(b"\x00\x01" * 40)
        stale = os.path.join(assets, "icon-deadbeef.ico")
        open(stale, "wb").write(b"old")

        p = updater.hashed_icon_path(assets)
        assert os.path.isfile(p) and p.endswith(".ico"), p
        assert os.path.basename(p) != "icon.ico", "还是指着 icon.ico，缓存不会失效"
        assert os.path.getsize(p) == 80
        assert not os.path.exists(stale), "旧指纹的副本没清掉"
        assert updater.hashed_icon_path(assets) == p, "同样内容算出了两个路径"

        if not updater.IS_WIN:
            log("✓ 图标复制成带指纹的副本（非 Windows 跳过快捷方式部分）")
            return

        from tools.make_shortcut import create_lnk
        lnk = os.path.join(os.path.dirname(app), "音视频下载器-公司.lnk")
        create_lnk(lnk, target=sys.executable, args="server.py --app", cwd=app,
                   icon=ico)
        assert updater._set_lnk_icon(lnk, p), "改快捷方式图标失败"
        raw = open(lnk, "rb").read()
        assert os.path.basename(p).encode("utf-16-le") in raw, "快捷方式没指到新图标"
        assert "icon.ico".encode("utf-16-le") not in raw.replace(
            os.path.basename(p).encode("utf-16-le"), b""), "快捷方式还留着老路径"
        assert "server.py --app".encode("utf-16-le") in raw or b"server.py" in raw, \
            "参数被改坏了"

        # 走一遍真入口：refresh_icon_cache 应该顺手把图标换掉
        n = updater.refresh_icon_cache(app)
        assert n == 1, n
        assert os.path.basename(p).encode("utf-16-le") in open(lnk, "rb").read()
        log("✓ 快捷方式被指到带指纹的图标，参数原样保留")
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
    test_hashed_icon_and_repoint()
    test_non_windows_noop()
    print("ALL PASS", flush=True)

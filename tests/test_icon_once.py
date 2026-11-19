# -*- coding: utf-8 -*-
"""每个版本首次启动补刷图标：同版本只一次、新版本会刷、失败不炸启动。

只测 updater.refresh_icon_once 的判定逻辑，不真去动桌面快捷方式——
用打桩把 refresh_icon_cache 和问题目录隔离掉。
"""
import os
import sys
import tempfile
import shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import updater  # noqa: E402

ok = 0


def t(cond, msg):
    global ok
    assert cond, msg
    ok += 1
    print("  OK", msg)


def main():
    print("=== 版本首次启动补刷图标 ===")
    # 非 Windows：直接跳过，不打桩也能过（本机是 Windows，这里只断言不抛异常）
    saved_win = updater.IS_WIN
    saved_refresh = updater.refresh_icon_cache
    tmp = tempfile.mkdtemp(prefix="vdl_icon_once_")
    saved_env = os.environ.get("VDL_DATA_DIR")
    calls = []

    try:
        # 让 paths 把数据目录指到临时目录，别污染真实用户目录
        os.environ["VDL_DATA_DIR"] = tmp
        # 强制走 Windows 分支来测判定逻辑（本机本来就是 Windows）
        updater.IS_WIN = True
        updater.refresh_icon_cache = lambda target: calls.append(target) or 2

        # 1) 第一次：没有标记 → 刷一次，写标记
        n = updater.refresh_icon_once("9.9.1")
        t(n == 2, "首启触发刷新，返回刷了 2 个")
        t(len(calls) == 1, "刷新函数被调用 1 次")
        marker = os.path.join(tmp, ".icon_refresh_9.9.1")
        t(os.path.isfile(marker), "写入了版本标记")

        # 2) 同一版本再启动：标记在 → 不刷
        n = updater.refresh_icon_once("9.9.1")
        t(n == 0 and len(calls) == 1, "同版本重启不再刷（避免图标闪烁）")

        # 3) 换版本：新标记 → 刷一次
        n = updater.refresh_icon_once("9.9.2")
        t(n == 2 and len(calls) == 2, "升到新版本再刷一次")
        t(os.path.isfile(os.path.join(tmp, ".icon_refresh_9.9.2")), "新版本标记也写入")

        # 4) 非 Windows：不刷
        updater.IS_WIN = False
        n = updater.refresh_icon_once("9.9.3")
        t(n == 0 and len(calls) == 2, "非 Windows 不刷")

        # 5) 刷新函数抛异常：吞掉，不影响启动
        updater.IS_WIN = True
        def boom(target):
            raise RuntimeError("刷图标炸了")
        updater.refresh_icon_cache = boom
        n = updater.refresh_icon_once("9.9.4")
        t(n == 0, "刷新异常被吞掉，返回 0")
        t(not os.path.exists(os.path.join(tmp, ".icon_refresh_9.9.4")),
          "刷新失败不写标记（下次还能重试）")

    finally:
        updater.IS_WIN = saved_win
        updater.refresh_icon_cache = saved_refresh
        if saved_env is None:
            os.environ.pop("VDL_DATA_DIR", None)
        else:
            os.environ["VDL_DATA_DIR"] = saved_env
        shutil.rmtree(tmp, ignore_errors=True)

    print("ALL PASS (%d)" % ok)


if __name__ == "__main__":
    main()

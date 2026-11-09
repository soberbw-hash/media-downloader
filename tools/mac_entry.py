# -*- coding: utf-8 -*-
"""macOS .app 的入口（PyInstaller 冻结后用）。

冻成 app 之后 __file__ 指向包内，资源在 sys._MEIPASS；而 .app 是只读的，
配置 / 历史 / cookie / 下载记录必须写到用户目录。server.py 里的 paths 模块
在 macOS 上本来就会这么做，这里再显式设一次环境变量，防止以后改路径逻辑
时不小心把数据又写回只读的包里。
"""
import os
import sys
import traceback


def _alert(msg):
    """没有控制台窗口，出错时用系统弹窗说人话，别让人以为双击没反应。"""
    try:
        import subprocess
        esc = str(msg).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")
        subprocess.run(["osascript", "-e",
                        'display alert "音视频下载器" message "%s" as critical' % esc],
                       capture_output=True, timeout=60)
    except Exception:
        pass


def main():
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", "")
        if meipass and meipass not in sys.path:
            sys.path.insert(0, meipass)
        os.environ.setdefault(
            "VDL_DATA_DIR",
            os.path.join(os.path.expanduser("~"), "Library",
                         "Application Support", "VideoDL"))

    import server

    # 双击 .app 默认开无地址栏的独立窗口；也可以由命令行传 web / none
    args = [a for a in sys.argv[1:] if not a.startswith("-psn_")]
    if not args:
        args = ["--app"]
    sys.argv = [sys.argv[0]] + args
    server.main()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        traceback.print_exc()
        _alert("启动失败：%s" % str(e)[:200])
        sys.exit(1)

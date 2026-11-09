#!/bin/sh
# 音视频下载器 - macOS 源码运行入口（双击即可）
# DMG 里的 .app 是打包好的，不用这个；这是给直接拿源码跑的人用的。
# 依赖装进用户目录，不往程序文件夹里写东西（DMG 是只读的）。

cd "$(dirname "$0")" || exit 1

export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

pause_exit() {
    printf '\n按回车关闭这个窗口…'
    read -r _
    exit 1
}

if ! command -v python3 >/dev/null 2>&1; then
    printf '没找到 python3。\n\n装一个，二选一：\n  brew install python@3.12\n  或者去 python.org 下载 macOS 安装包\n'
    pause_exit
fi

if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
    printf 'Python 版本太旧，至少要 3.9。\n当前：'
    python3 -V
    pause_exit
fi

VENV="$HOME/Library/Application Support/VideoDL/venv"
if [ ! -x "$VENV/bin/python" ]; then
    printf '第一次运行，正在准备运行环境（只要一次，约 1 分钟）…\n'
    mkdir -p "$HOME/Library/Application Support/VideoDL" || pause_exit
    python3 -m venv "$VENV" || pause_exit
    "$VENV/bin/python" -m pip install --quiet --upgrade pip || pause_exit
    "$VENV/bin/python" -m pip install --quiet \
        -r requirements.txt imageio-ffmpeg || pause_exit
fi

exec "$VENV/bin/python" server.py --app

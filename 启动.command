#!/bin/sh
# 音视频下载器 - macOS 启动入口（双击运行）
# 和 Windows 的「启动.bat」等价：起本地服务，再开一个没有地址栏的独立窗口

cd "$(dirname "$0")" || exit 1

# 从 Finder 里双击时 PATH 是很干净的（只有 /usr/bin:/bin:...），
# Homebrew 装的东西不在里面，这里补上两个常见位置
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

pause_exit() {
    printf '\n按回车关闭这个窗口…'
    read -r _
    exit 1
}

if ! command -v python3 >/dev/null 2>&1; then
    printf '没找到 python3。\n\n先装一个，二选一：\n  brew install python@3.12\n  或者去 python.org 下载 macOS 安装包\n'
    pause_exit
fi

if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
    printf 'Python 版本太旧，至少要 3.9。\n当前：'
    python3 -V
    pause_exit
fi

if ! python3 -c 'import yt_dlp' 2>/dev/null; then
    printf '缺下载内核 yt-dlp，先装：\n\n  python3 -m pip install --user yt-dlp\n'
    printf '\n如果报 externally-managed-environment，改用：\n\n  python3 -m pip install --user yt-dlp --break-system-packages\n'
    pause_exit
fi

exec python3 server.py --app

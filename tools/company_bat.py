# -*- coding: utf-8 -*-
"""生成「音视频下载器-公司.bat」——打包 exe 会被公司端点安全拦下时用的入口。

不经过 exe，直接用文件夹里自带的 runtime\\pythonw.exe 起服务。
pythonw 没有控制台窗口，所以 bat 起完进程就立刻退出，黑框只闪一下、不会留在桌面上。

bat 里的中文必须存成 GBK，否则 cmd 窗口一律乱码；换行必须是 CRLF。

单独用：
    python tools/company_bat.py "D:\\Desktop\\音视频下载器"
"""
import io
import os
import sys

TEMPLATE = r"""@echo off
rem ==========================================================
rem  音视频下载器 · 公司电脑用这个入口
rem  打包过的 exe 会被端点安全软件拦下，这个入口不经过 exe，
rem  直接用文件夹里自带的 Python 启动，双击一下就行。
rem  黑框只会闪一下，那是启动时的正常现象，不用管。
rem  想关掉软件：关掉「音视频下载器」窗口即可。
rem ==========================================================

rem 找程序本体：先看自己所在目录（bat 就放在 程序 里），再看同目录，
rem 再看下一层子文件夹，最后兜到默认位置
set "APP="
if exist "%~dp0server.py" set "APP=%~dp0"
if not defined APP if exist "%~dp0程序\server.py" set "APP=%~dp0程序"
if not defined APP if exist "%~dp0音视频下载器\程序\server.py" set "APP=%~dp0音视频下载器\程序"
if not defined APP if exist "D:\Desktop\音视频下载器\程序\server.py" set "APP=D:\Desktop\音视频下载器\程序"

if not defined APP goto no_app

set "PY=%APP%\runtime\python.exe"
set "PYW=%APP%\runtime\pythonw.exe"
if not exist "%PY%" goto no_py

rem 工作目录切到程序目录，config / cookies / logs 都按它定位
cd /d "%APP%"

rem 优先 pythonw：无窗口、任务栏不留控制台，起完这个黑框立刻消失
if exist "%PYW%" (
  start "" "%PYW%" server.py --app
  exit /b 0
)

rem 没有 pythonw 才退回 python，会留一个最小化的窗口
start "" /min "%PY%" server.py --app
exit /b 0

:no_app
echo.
echo   没找到「程序」文件夹。
echo.
echo   这个文件要和「程序」文件夹放在一起，把整个
echo   音视频下载器 文件夹一起拷过来就行，别单独拖走。
echo.
pause
exit /b 1

:no_py
echo.
echo   没找到内置的 Python 运行环境：
echo     %PY%
echo.
echo   说明「程序」文件夹不完整，请重新拷一份整个文件夹。
echo.
pause
exit /b 1
"""

NAME = "音视频下载器-公司.bat"


def write_to(folder):
    """往 folder 里写一份 bat，返回路径"""
    path = os.path.join(folder, NAME)
    data = TEMPLATE.replace("\r\n", "\n").replace("\n", "\r\n")
    io.open(path, "w", encoding="gbk", newline="").write(data)
    return path


def main():
    if len(sys.argv) < 2:
        print("用法: python tools/company_bat.py <目标文件夹>")
        return 1
    folder = os.path.abspath(sys.argv[1])
    if not os.path.isdir(folder):
        print("文件夹不存在:", folder)
        return 1
    print("written:", write_to(folder))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

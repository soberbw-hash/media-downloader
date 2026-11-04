@echo off
rem ==========================================================
rem  这不是正常的启动入口，出问题排查时才用。
rem  前台运行，报错会留在窗口里，方便截图。
rem  平时请双击文件夹里的「音视频下载器.exe」
rem  或「音视频下载器-公司.lnk」，不用管这个。
rem
rem  直接双击 = 独立窗口；带参数 web = 普通浏览器标签
rem ==========================================================
cd /d "%~dp0"
title 音视频下载器 - 诊断（排查用，关掉此窗口即退出）

set "PY="
rem 1) 文件夹里自带的运行环境
if exist "%~dp0runtime\python.exe" set "PY=%~dp0runtime\python.exe"
rem 2) 系统 PATH 里的 python
if not defined PY for /f "delims=" %%P in ('where python 2^>nul') do if not defined PY set "PY=%%P"
rem 3) 常见安装位置
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"

if not defined PY goto no_py

rem 挡掉 Microsoft Store 的假 python，以及低于 3.11 的版本
"%PY%" -c "import sys; sys.exit(0 if sys.version_info>=(3,11) else 1)" >nul 2>&1
if errorlevel 1 goto no_py

echo.
echo   诊断模式：下面的信息可以截图发给维护的人。
echo.
"%PY%" -V
echo   程序目录：%CD%
echo.

if /i "%~1"=="web" goto run_web
"%PY%" server.py --app
goto done

:run_web
"%PY%" server.py --web
goto done

:no_py
echo.
echo   没找到能用的 Python（需要 3.11 以上）。
echo.
echo   这不影响正常使用——双击文件夹里的
echo   音视频下载器.exe 或 音视频下载器-公司.lnk 即可。
echo   只有「程序」文件夹里的东西被删过，才会走到这里。
echo.
pause
exit /b 1

:done
echo.
echo   服务已停止。按任意键关闭。
pause >nul

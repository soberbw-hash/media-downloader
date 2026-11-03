@echo off
cd /d "%~dp0"

rem  usage:  start.bat       -> standalone window (no address bar)  [default]
rem          start.bat web   -> fallback: normal browser tab
set "MODE=%~1"
if /i "%MODE%"=="web" goto mode_ready
set "MODE=app"

:mode_ready
if /i "%MODE%"=="app" (title AV Downloader - running) else (title AV Downloader)

set "PY="
rem  1) portable runtime next to the scripts (put python.exe in .\runtime to keep it self-contained)
if exist "%~dp0runtime\python.exe" set "PY=%~dp0runtime\python.exe"
rem  2) whatever "python" resolves to on PATH
if not defined PY for /f "delims=" %%P in ('where python 2^>nul') do if not defined PY set "PY=%%P"
rem  3) common per-user install locations
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"

rem  reject the Microsoft Store stub and anything older than 3.11
if defined PY "%PY%" -c "import sys; sys.exit(0 if sys.version_info>=(3,11) else 1)" >nul 2>&1
if errorlevel 1 set "PY="

if defined PY goto have_py

py -3 -c "import sys" >nul 2>&1
if not errorlevel 1 goto have_pylauncher

echo.
echo   Python 3.11+ not found.
echo   Please install Python and add it to PATH.
echo.
pause
exit /b 1

:have_pylauncher
set "PY=py"
if /i "%MODE%"=="app" goto launch_app_py
py -3 server.py --web
goto finished

:launch_app_py
start "" /min py -3 server.py --app
goto soft_exit

:have_py
if /i "%MODE%"=="app" goto launch_app

"%PY%" server.py --web
goto finished

:launch_app
rem  minimized console (pythonw gets killed by endpoint security on some machines)
start "" /min "%PY%" server.py --app
goto soft_exit

:soft_exit
exit /b 0

:finished
echo.
echo   Server stopped.
pause >nul

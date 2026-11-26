@echo off
setlocal
cd /d "%~dp0"

set "PY_EXE="
if exist "C:\Python314\python.exe" (
    set "PY_EXE=C:\Python314\python.exe"
) else if exist "%~dp0venv\Scripts\python.exe" (
    set "PY_EXE=%~dp0venv\Scripts\python.exe"
) else (
    set "PY_EXE=python"
)

"%PY_EXE%" -u "%~dp0custom_os\launcher\launch_liveview_flow.py" %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
    echo.
    echo [ERROR] Custom OS LiveView launcher exited with code %RC%.
    pause
    exit /b %RC%
)
exit /b 0

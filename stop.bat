@echo off
chcp 65001 > nul
cd /d "%~dp0"

if exist tray.pid (
    set /p PID=<tray.pid
    taskkill /f /pid %PID% 2>nul
    del tray.pid 2>nul
    if exist tray.pid del /f tray.pid 2>nul
    echo cc2go stopped.
) else (
    echo No PID file found. cc2go may not be running, or use task manager to find and kill python process running tray.py.
)
pause

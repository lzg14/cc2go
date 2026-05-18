@echo off
chcp 65001 > nul
cd /d "%~dp0.."

if exist data\cc2go.pid (
    for /f %%i in (data\cc2go.pid) do set PID=%%i
    taskkill /f /pid %PID% 2>nul
    del data\cc2go.pid 2>nul
    echo cc2go stopped.
) else (
    echo No PID file found. cc2go may not be running, or use task manager to find and kill python process.
)
pause

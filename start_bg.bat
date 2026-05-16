@echo off
chcp 65001 > nul
cd /d "%~dp0"

if not exist .env (
    echo Creating default .env...
    copy config.yaml.example .env 2>nul
)

echo Starting cc2go in background...
powershell -Command "Start-Process -WindowStyle Hidden -FilePath 'python' -ArgumentList 'router.py' -WorkingDirectory '%~dp0'"
echo cc2go is running silently in background (PID: unknown).
echo To stop it, run: stop.bat
echo.
pause

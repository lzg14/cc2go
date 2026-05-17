@echo off
chcp 65001 > nul
cd /d "%~dp0"

if not exist .env (
    echo Creating default .env...
    copy .env.example .env 2>nul
)

echo Starting cc2go in system tray...
powershell -Command "Start-Process -WindowStyle Hidden -FilePath 'python' -ArgumentList 'tray.py' -WorkingDirectory '%~dp0'"
echo cc2go is running in system tray.
echo To open the admin page, double-click the tray icon.
echo To stop, run: stop.bat
echo.
pause

@echo off
chcp 65001 > nul
cd /d "%~dp0.."

if not exist .env (
    echo Creating default .env...
    copy .env.example .env 2>nul
)

set "ROUTER_PORT=4001"
for /f "tokens=1,* delims==" %%a in ('findstr /b "ROUTER_PORT" .env 2^>nul') do (
    for /f "tokens=*" %%c in ("%%b") do set "ROUTER_PORT=%%c"
)

wmic process where "commandline like '%%cc2go%%router.py%%'" delete 2>nul >nul
wmic process where "commandline like '%%cc2go%%tray.py%%'" delete 2>nul >nul
timeout /t 2 /nobreak >nul

echo Starting cc2go in system tray (port %ROUTER_PORT%)...
powershell -Command "Start-Process -WindowStyle Hidden -FilePath 'python' -ArgumentList 'src\tray.py' -WorkingDirectory '%cd%'"

timeout /t 3 /nobreak >nul
netstat -ano | findstr ":%ROUTER_PORT%.*LISTENING" >nul 2>&1
if %errorlevel%==0 (
    echo cc2go started successfully on port %ROUTER_PORT%.
) else (
    echo [ERROR] cc2go may have failed to start. Check logs\router.log for details.
)
echo To open the admin page, double-click the tray icon.
echo To stop, run: scripts\stop.bat
echo.
pause
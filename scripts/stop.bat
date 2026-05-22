@echo off
chcp 65001 > nul
cd /d "%~dp0.."

echo Stopping cc2go...

wmic process where "commandline like '%%cc2go%%router.py%%'" delete 2>nul >nul
wmic process where "commandline like '%%cc2go%%tray.py%%'" delete 2>nul >nul

if exist data\cc2go.pid del data\cc2go.pid 2>nul

echo cc2go stopped.
pause
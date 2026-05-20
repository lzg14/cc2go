@echo off
chcp 65001 > nul
cd /d "%~dp0.."

echo Stopping cc2go...

REM 杀所有 cc2go 相关进程（按命令行匹配）
wmic process where "commandline like '%%cc2go%%router.py%%'" delete 2>nul >nul
wmic process where "commandline like '%%cc2go%%tray.py%%'" delete 2>nul >nul

REM 清理 PID 文件
if exist data\cc2go.pid del data\cc2go.pid 2>nul

echo cc2go stopped.
pause
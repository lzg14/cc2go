@echo off
echo Stopping cc2go...
taskkill /f /im python.exe /fi "WINDOWTITLE eq cc2go*" 2>nul
echo Done.
pause

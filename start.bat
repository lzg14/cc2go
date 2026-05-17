@echo off
chcp 65001 > nul
title cc2go

echo.
echo Starting cc2go...
echo.

cd /d "%~dp0"

if not exist .env (
    echo Creating default .env...
    copy .env.example .env 2>nul || echo Please create .env manually
)

echo Starting server...
python router.py

pause

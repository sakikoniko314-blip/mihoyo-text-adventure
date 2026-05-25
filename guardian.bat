@echo off
title Xilian Bot Guardian
cd /d "%~dp0qq_bot"

:loop
echo [%date% %time%] Starting Xilian Bot...
D:\Python\python.exe run.py
echo [%date% %time%] Bot stopped. Waiting for port release...
timeout /t 8 /nobreak >nul
goto loop

@echo off
title Paper trading bot
cd /d "%~dp0"
set PY=
where py >nul 2>nul && set PY=py
if not defined PY (python --version >nul 2>nul && set PY=python)
if not defined PY (
  echo.
  echo Python is not installed. Opening the download page...
  echo Install it, TICK "Add python.exe to PATH" on the first screen, then double-click start.bat again.
  start "" https://www.python.org/downloads/
  pause
  exit /b
)
echo Starting the bot. Leave this window open - close it to stop the bot.
start "" cmd /c "timeout /t 5 >nul & start http://localhost:8050"
%PY% -m predbot.bot --dashboard
pause

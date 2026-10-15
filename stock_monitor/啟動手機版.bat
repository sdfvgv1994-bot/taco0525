@echo off
chcp 65001 > nul
title 自動看盤 手機版
cd /d "%~dp0"
set "PY=python"
if exist "..\..\.venv\Scripts\python.exe" set "PY=..\..\.venv\Scripts\python.exe"
if exist "..\.venv\Scripts\python.exe" set "PY=..\.venv\Scripts\python.exe"
"%PY%" web_app.py %*
pause

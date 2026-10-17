@echo off
chcp 65001 > nul
title 策略回測
cd /d "%~dp0"
set "PY=python"
if exist "..\..\.venv\Scripts\python.exe" set "PY=..\..\.venv\Scripts\python.exe"
if exist "..\.venv\Scripts\python.exe" set "PY=..\.venv\Scripts\python.exe"
:again
"%PY%" backtest.py %*
echo.
echo 圖表存在 stock_backtest\output 資料夾。按任意鍵再測一次，或直接關閉視窗。
pause > nul
if "%~1"=="" goto again

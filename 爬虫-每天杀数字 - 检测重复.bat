@echo off
chcp 65001 >nul
set "PY_CMD="
py -3 --version >nul 2>nul
if not errorlevel 1 set "PY_CMD=py -3"
if not defined PY_CMD (
  python --version >nul 2>nul
  if not errorlevel 1 set "PY_CMD=python"
)
if not defined PY_CMD (
  echo Cannot find Python. Please install Python and add it to PATH.
  pause
  exit /b 1
)
chcp 65001 >nul
title 最新一期爬虫
cd /d "%~dp0"
echo 正在启动...
set /p LATEST_ISSUE=请输入最新期号，例如 158：
if not defined LATEST_ISSUE (
  echo 未输入最新期号，已停止。
  pause
  exit /b 1
)
where py >nul 2>nul
if %errorlevel%==0 (
  %PY_CMD% check_duplicates.py --latest %LATEST_ISSUE% --recent 10 --workers 8 --write-cache --cache recent_10_cache.json
) else (
  %PY_CMD% check_duplicates.py --latest %LATEST_ISSUE% --recent 10 --workers 8 --write-cache --cache recent_10_cache.json
)
echo.
echo 运行结束，查看 重复检测结果.txt 和 recent_10_cache.json
pause

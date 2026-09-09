@echo off
setlocal
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

title 杀数字近10期重复检测
cd /d "%~dp0"
echo 正在按每个站点自己的 region 识别最新期并检测近10期...
%PY_CMD% check_duplicates.py --recent 10 --workers 8 --write-cache --cache recent_10_cache.json
set "EXIT_CODE=%ERRORLEVEL%"
echo.
if "%EXIT_CODE%"=="0" (
  echo 检测完整，查看 重复检测结果.txt 和 recent_10_cache.json
) else (
  echo 本轮未得到完整无重复结论，请查看 重复检测结果.txt 中的异常明细。
)
pause
exit /b %EXIT_CODE%

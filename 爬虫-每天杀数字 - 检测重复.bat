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
title 杀数字正式重复检测
cd /d "%~dp0"
echo 读取每个站点的连续近10期缓存；不发起全站抓取，不修改缓存。
%PY_CMD% check_duplicates.py --from-cache --recent 10 --cache recent_10_cache.json
set "RC=%ERRORLEVEL%"
echo.
echo 运行结束。退出码0=完整无重复，4=检测未完成，5=疑似重复，6=拒收。
echo 请查看 重复检测结果.txt。数据不足不能视为不重复。
pause
exit /b %RC%

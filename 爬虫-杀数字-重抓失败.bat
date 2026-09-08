@echo off
chcp 65001 >nul
cd /d "%~dp0"
set /p ISSUE=请输入要重抓的期数：
py -3 retry_failed.py %ISSUE%
if errorlevel 1 python retry_failed.py %ISSUE%
pause

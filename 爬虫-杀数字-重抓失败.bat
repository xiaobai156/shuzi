@echo off
chcp 65001 >nul
cd /d "%~dp0"
set /p ISSUE=请输入要重抓的期数：
where py >nul 2>nul
if not errorlevel 1 goto usepy
where python >nul 2>nul
if not errorlevel 1 goto usepython
goto done
:usepy
py -3 retry_failed.py %ISSUE%
goto done
:usepython
python retry_failed.py %ISSUE%
:done
pause


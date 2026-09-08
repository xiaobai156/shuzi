@echo off
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul
if not errorlevel 1 goto usepy
where python >nul 2>nul
if not errorlevel 1 goto usepython
goto done
:usepy
py -3 retry_failed.py
goto done
:usepython
python retry_failed.py
:done
pause


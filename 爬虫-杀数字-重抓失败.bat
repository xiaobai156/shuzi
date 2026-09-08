@echo off
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul
if not errorlevel 1 goto usepy
where python >nul 2>nul
if not errorlevel 1 goto usepython
goto done
:usepy
py -3 --version >nul 2>nul
if errorlevel 1 goto usepython
py -3 retry_failed.py
set RET=%ERRORLEVEL%
goto done
:usepython
python --version >nul 2>nul
if errorlevel 1 echo 未找到可用的 Python 3 & set RET=2 & goto done
python retry_failed.py
set RET=%ERRORLEVEL%
:done
exit /b %RET%
pause


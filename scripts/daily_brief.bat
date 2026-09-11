@echo off
REM ==========================================================
REM  Daily Economic Brief - Windows Task Scheduler entry point
REM
REM  ASCII ONLY. Do not put Korean text in this file.
REM  cmd.exe reads .bat as the system codepage (cp949 here), so
REM  UTF-8 Korean bytes corrupt the following command lines.
REM  Korean output belongs in run.py, which handles its own encoding.
REM
REM  Python is called by absolute path: the scheduler session may
REM  resolve "python" to the Microsoft Store alias, which exits 0
REM  without doing anything.
REM ==========================================================
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

set "PYEXE=C:\Users\dongh\AppData\Local\Programs\Python\Python312\python.exe"
cd /d "%~dp0.."

if not exist "%PYEXE%" (
    echo [%date% %time%] python not found: %PYEXE%>>"data\run.log"
    exit /b 9009
)

"%PYEXE%" run.py
set EXITCODE=%ERRORLEVEL%

if not "%EXITCODE%"=="0" (
    echo [%date% %time%] run.py failed with exit %EXITCODE%>>"data\run.log"
)

exit /b %EXITCODE%

@echo off
REM ─────────────────────────────────────────────────────────────
REM  매일 경제 브리핑 — Windows 작업 스케줄러가 실행하는 진입점
REM
REM  python 을 절대경로로 부르는 이유:
REM  스케줄러 세션의 PATH 에는 Microsoft Store 별칭(WindowsApps\python.exe)이
REM  먼저 잡히는 일이 있다. 이 별칭은 비대화형에서 아무 일도 하지 않고
REM  종료 코드 0 으로 끝나서, "성공했는데 아무것도 안 된" 상태가 된다.
REM ─────────────────────────────────────────────────────────────
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

set "PYEXE=C:\Users\dongh\AppData\Local\Programs\Python\Python312\python.exe"
cd /d "%~dp0.."

if not exist "%PYEXE%" (
    echo [%date% %time%] 파이썬 실행 파일을 찾을 수 없습니다: %PYEXE% >> "data\run.log"
    exit /b 9009
)

"%PYEXE%" run.py
set EXITCODE=%ERRORLEVEL%

if %EXITCODE% NEQ 0 (
    echo [%date% %time%] 실행 실패 ^(exit %EXITCODE%^) >> "data\run.log"
)

exit /b %EXITCODE%

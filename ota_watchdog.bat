@echo off
REM ============================================================
REM  ota_watchdog.bat (v2.2 - hard-code pythonw, simplified)
REM
REM  Purpose:
REM    Wrap "pythonw app.py" so Flask is auto-restarted on crash
REM    or after an OTA-triggered self-restart.
REM
REM  Hard requirement (OTA 端 / 正式環境):
REM    - Python (含 pythonw.exe) 已在 PATH 環境變數內
REM    - 不開 console 視窗 → 強制用 pythonw.exe，不用 python.exe
REM
REM  Improvements over v2.1:
REM    - 移除 find python 邏輯（Step 1/2/3 fallback 全部刪掉）
REM    - 直接 hard-code `pythonw app.py`
REM    - 不再做 --version 預先驗證（PATH 內 pythonw 可信任）
REM    - log / echo 改寫固定字串，少 %PYTHON% 變數插值
REM    - 保留 v2.1 的主迴圈（5 次失敗暫停、code 0/非零區分、3 秒重啟）
REM
REM  保留 OTA 功能:
REM    - exit 0 = OTA 自我重啟觸發；迴圈繼續
REM    - exit !=0 = crash；失敗計數累加；>= MAX_FAILS 暫停請人接手
REM
REM  Usage:
REM    ota_watchdog.bat
REM    或 start_forever.bat（背景跑 / 關視窗不殺 watchdog）
REM ============================================================

REM Force UTF-8 codepage
chcp 65001 >nul

REM Always cd to this bat's directory
cd /d %~dp0

REM Setup log dir
if not exist logs mkdir logs
set LOGFILE=logs\watchdog.log

REM ----- Hard requirement: pythonw.exe (v2.4, 2026-07-24) -----
REM Strategy (in order):
REM   1) where pythonw (PATH lookup)
REM   2) where python  -> use its directory + \pythonw.exe
REM      (python.exe is usually on PATH; pythonw is in same dir)
REM   3) Fixed known install locations (no %LocalAppData% / %ProgramFiles%)
REM      because some OTA hosts have those env vars unset or restricted.
REM   4) FATAL if none found.
REM Do NOT downgrade to python.exe (pythonw = no console window).
set PYTHONW_EXE=
REM Step 1: where pythonw
for /f "delims=" %%i in ('where pythonw 2^>nul') do (
    if not defined PYTHONW_EXE set PYTHONW_EXE=%%i
)
if defined PYTHONW_EXE goto :PYW_OK
REM Step 2: where python -> derive pythonw from same dir
for /f "delims=" %%i in ('where python 2^>nul') do (
    if not defined PYTHONW_EXE (
        set "PYTHONW_DIR=%%~dpi"
        if exist "%%~dpi\pythonw.exe" set "PYTHONW_EXE=%%~dpi\pythonw.exe"
    )
)
if defined PYTHONW_EXE goto :PYW_OK
REM Step 3: fixed absolute paths (community / store installer defaults)
REM 3a: per-user store installer (most common on modern Win10/11)
if exist "C:\Users\USER\AppData\Local\Programs\Python\Python313\pythonw.exe" set PYTHONW_EXE=C:\Users\USER\AppData\Local\Programs\Python\Python313\pythonw.exe
if defined PYTHONW_EXE goto :PYW_OK
if exist "C:\Users\USER\AppData\Local\Programs\Python\Python312\pythonw.exe" set PYTHONW_EXE=C:\Users\USER\AppData\Local\Programs\Python\Python312\pythonw.exe
if defined PYTHONW_EXE goto :PYW_OK
if exist "C:\Users\USER\AppData\Local\Programs\Python\Python311\pythonw.exe" set PYTHONW_EXE=C:\Users\USER\AppData\Local\Programs\Python\Python311\pythonw.exe
if defined PYTHONW_EXE goto :PYW_OK
if exist "C:\Users\USER\AppData\Local\Programs\Python\Python310\pythonw.exe" set PYTHONW_EXE=C:\Users\USER\AppData\Local\Programs\Python\Python310\pythonw.exe
if defined PYTHONW_EXE goto :PYW_OK
REM 3b: system-wide installer defaults
if exist "C:\Program Files\Python313\pythonw.exe" set PYTHONW_EXE=C:\Program Files\Python313\pythonw.exe
if defined PYTHONW_EXE goto :PYW_OK
if exist "C:\Program Files\Python312\pythonw.exe" set PYTHONW_EXE=C:\Program Files\Python312\pythonw.exe
if defined PYTHONW_EXE goto :PYW_OK
if exist "C:\Python313\pythonw.exe" set PYTHONW_EXE=C:\Python313\pythonw.exe
if defined PYTHONW_EXE goto :PYW_OK
REM No pythonw found
echo [%date% %time%] [FATAL] pythonw.exe not found anywhere we looked.
echo [%date% %time%] [FATAL] pythonw.exe not found anywhere we looked. >> %LOGFILE%
echo [%date% %time%] [DEBUG] checked: PATH, where python, C:\Users\USER\AppData\Local\Programs\Python\Python3{10-13}, C:\Program Files\Python3{12,13}, C:\Python313
echo [%date% %time%] [DEBUG] checked: PATH, where python, C:\Users\USER\AppData\Local\Programs\Python\Python3{10-13}, C:\Program Files\Python3{12,13}, C:\Python313 >> %LOGFILE%
pause
exit /b 1
:PYW_OK

echo [%date% %time%] [INFO] Using pythonw: %PYTHONW_EXE%
echo [%date% %time%] [INFO] Using pythonw: %PYTHONW_EXE% >> %LOGFILE%

setlocal enabledelayedexpansion
set MAX_FAILS=5
set FAIL_COUNT=0

:LOOP
echo [%date% %time%] [INFO] Watch dog: starting %PYTHONW_EXE% app.py ...
echo [%date% %time%] [INFO] Watch dog: starting %PYTHONW_EXE% app.py ... >> %LOGFILE%

"%PYTHONW_EXE%" app.py
set EXITCODE=%ERRORLEVEL%
echo [%date% %time%] [INFO] pythonw app.py exited, code=%EXITCODE%
echo [%date% %time%] [INFO] pythonw app.py exited, code=%EXITCODE% >> %LOGFILE%

REM code 0  = normal exit (OTA self-restart)
REM code !=0 = abnormal crash
if %EXITCODE% NEQ 0 (
    set /a FAIL_COUNT+=1
    echo [%date% %time%] [WARN] Consecutive failure count: !FAIL_COUNT! / %MAX_FAILS%
    echo [%date% %time%] [WARN] Consecutive failure count: !FAIL_COUNT! / %MAX_FAILS% >> %LOGFILE%
    if !FAIL_COUNT! GEQ %MAX_FAILS% (
        echo [%date% %time%] [FATAL] Stopping watch dog after %MAX_FAILS% consecutive failures. Please check manually.
        echo [%date% %time%] [FATAL] Stopping watch dog after %MAX_FAILS% consecutive failures. Please check manually. >> %LOGFILE%
        pause
        exit /b 1
    )
) else (
    set FAIL_COUNT=0
)

echo [%date% %time%] [INFO] Restarting in 3 seconds ...
echo [%date% %time%] [INFO] Restarting in 3 seconds ... >> %LOGFILE%
timeout /t 3 /nobreak >nul
goto LOOP

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

REM ----- Hard requirement: pythonw.exe on PATH -----
REM 正式環境不再容忍 python.exe（會跳 console 視窗）。
REM pythonw.exe 找不到就直接 fatal，不要降級用 python.exe。
where pythonw >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [%date% %time%] [FATAL] pythonw.exe not found in PATH. OTA host requires Python on PATH (含 pythonw.exe).
    echo [%date% %time%] [FATAL] pythonw.exe not found in PATH. OTA host requires Python on PATH (含 pythonw.exe). >> %LOGFILE%
    pause
    exit /b 1
)

echo [%date% %time%] [INFO] Using pythonw: %ProgramFiles%\Python313\pythonw.exe ^(PATH lookup OK^)
echo [%date% %time%] [INFO] Using pythonw (PATH lookup OK) >> %LOGFILE%

setlocal enabledelayedexpansion
set MAX_FAILS=5
set FAIL_COUNT=0

:LOOP
echo [%date% %time%] [INFO] Watch dog: starting pythonw app.py ...
echo [%date% %time%] [INFO] Watch dog: starting pythonw app.py ... >> %LOGFILE%

pythonw app.py
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

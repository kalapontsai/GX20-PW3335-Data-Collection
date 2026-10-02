@echo off
REM ============================================================
REM  restart_clean.bat -- v1 (2026-10-02)
REM
REM  用途：完全清理 watchdog 重複 / 卡死狀態，再重啟一份乾淨的
REM
REM  觸發情境：
REM    1. 手動跑了多次 start_forever.bat 累積 ota_watchdog.bat + pythonw.exe
REM    2. Flask crash 後 watchdog 沒撐起來（無 background watchdog instance）
REM    3. pythonw 進程數 != 1（重複或缺失）
REM    4. port 5000 listen PID 跟 watchdog 跑的 pythonw 不一致
REM
REM  流程：
REM    [1] taskkill /F /IM pythonw.exe           -- 殺所有 Flask 進程
REM    [2] wmic 找含 ota_watchdog 字串的 cmd.exe 並刪 -- 殺所有 watchdog instance
REM    [3] schtasks /Run \GX20_monitor            -- 觸發排程重啟一份乾淨
REM    [4] 等 8s，tasklist + netstat 驗證         -- 預期 1 pythonw + 1 ota_watchdog + port 5000 listen
REM
REM  使用方式：
REM    - 雙擊執行（會 pause 讓你看到結果）
REM    - SSH 進 Windows 主機跑：`restart_clean.bat`
REM
REM  注意：
REM    - 整個 script 期間 /dashboard 會斷線約 10s
REM    - wmic 在 Windows 11 22H2+ 已 deprecation 但仍可用
REM    - schtasks \GX20_monitor 必須存在（看 setup_autostart.ps1）
REM ============================================================

REM Force UTF-8 codepage
chcp 65001 >nul

REM Always cd to this bat's directory
cd /d %~dp0

echo.
echo === restart_clean start: %date% %time% ===

REM ------------------------------------------------------------
REM [1] 殺所有 pythonw.exe（Flask 進程）
REM ------------------------------------------------------------
echo.
echo [1/4] Killing all pythonw.exe
taskkill /F /IM pythonw.exe 2>&1
if errorlevel 1 (
    echo    (no pythonw process found - already clean)
)

REM ------------------------------------------------------------
REM [2] 殺所有 ota_watchdog.bat 的 cmd.exe（watchdog instance）
REM     用 wmic LIKE 比對 CommandLine
REM ------------------------------------------------------------
echo.
echo [2/4] Killing ota_watchdog.bat cmd.exe
wmic process where "name='cmd.exe' and commandline like '%%ota_watchdog%%'" delete 2>&1
if errorlevel 1 (
    echo    (no ota_watchdog cmd found - already clean)
)

REM 等 3s 確保全部死透
echo.
echo    Wait 3s...
timeout /t 3 /nobreak >nul

REM ------------------------------------------------------------
REM [3] 觸發 scheduled task（start_forever.bat 由 \GX20_monitor 排程啟動）
REM ------------------------------------------------------------
echo.
echo [3/4] schtasks /Run \GX20_monitor
schtasks /Run /TN "\GX20_monitor"
if errorlevel 1 (
    echo    ERROR: schtasks /Run failed. Check if \GX20_monitor exists.
    echo    Try: schtasks /Query /TN "\GX20_monitor"
    goto :verify
)

REM 等 8s 讓 watchdog 啟動 ota_watchdog.bat → ota_watchdog 啟動 pythonw
echo.
echo    Wait 8s for watchdog to spawn pythonw...
timeout /t 8 /nobreak >nul

REM ------------------------------------------------------------
REM [4] 驗證：1 pythonw + 1 ota_watchdog + port 5000 listen
REM ------------------------------------------------------------
:verify
echo.
echo [4/4] Verify state

echo.
echo --- pythonw.exe ---
tasklist /FI "IMAGENAME eq pythonw.exe" /FO TABLE

echo.
echo --- ota_watchdog.bat cmd.exe ---
tasklist /FO LIST /V 2>NUL | findstr /C:"ota_watchdog"
if errorlevel 1 (
    echo    (no ota_watchdog running)
)

echo.
echo --- port 5000 LISTENING ---
netstat -ano 2>NUL | findstr :5000 | findstr LISTENING
if errorlevel 1 (
    echo    WARNING: port 5000 not listening - pythonw may have failed to start
)

echo.
echo === restart_clean finished: %date% %time% ===
echo.

REM 雙擊情境會需要看輸出；SSH 跑可拿掉 pause
if "%~1"=="/nopause" goto :eof
pause
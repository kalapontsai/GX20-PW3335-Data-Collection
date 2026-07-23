@echo off
REM ============================================================
REM  start_forever.bat
REM
REM  Launch ota_watchdog.bat in the background so closing the
REM  parent cmd window does not stop the watch dog.
REM
REM  Usage:
REM    start_forever.bat
REM
REM  To check status:
REM    netstat -ano | findstr :5000
REM
REM  To view log:
REM    type logs\watchdog.log
REM
REM  To stop:
REM    taskkill /F /IM pythonw.exe
REM ============================================================

REM Force UTF-8 codepage (harmless for ASCII content; helps if log is UTF-8)
chcp 65001 >nul

REM Always cd to this bat's directory
cd /d %~dp0

REM /B = same window / background
REM /MIN = minimized
REM We use CALL instead of directly invoking so a syntax error in the
REM parent bat does not break the child bat.
start "" /B /MIN cmd /c ota_watchdog.bat

echo.
echo Watch dog started in background. You may close this window.
echo.
echo To check status : netstat -ano | findstr :5000
echo To view log     : type logs\watchdog.log
echo To stop         : taskkill /F /IM pythonw.exe
echo.
pause

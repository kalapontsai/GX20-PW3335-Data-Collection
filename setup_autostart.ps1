# setup_autostart.ps1
# ============================================================
# 在 Windows 工作排程器建立「GX20 + PW3335 Web Monitor」
# 開機自動啟動任務，呼叫 start_forever.bat
#
# 啟動鏈：
#   Windows 開機
#     ↓
#   工作排程器觸發 GX20-WebMonitor
#     ↓
#   start_forever.bat（PowerShell Start-Process -WindowStyle Hidden）
#     ↓
#   ota_watchdog.bat（持續監看 Flask + 自動重啟）
#     ↓
#   python app.py（Flask 服務）
#
# 使用方式（OTA 主機 PowerShell，需管理員權限）：
#   powershell -ExecutionPolicy Bypass -File .\setup_autostart.ps1
#
# 驗證：
#   Get-ScheduledTask -TaskName "GX20-WebMonitor"
#   # 應該看到 Status: Ready
#
# 移除：
#   Unregister-ScheduledTask -TaskName "GX20-WebMonitor" -Confirm:$false
# ============================================================

$ErrorActionPreference = "Stop"

# ---------- 路徑設定 ----------
$RepoRoot    = "D:\sampo\GX20-PW3335-Data-Collection"
$BatPath     = Join-Path $RepoRoot "start_forever.bat"
$TaskName    = "GX20-WebMonitor"
$Description = "GX20 + PW3335 Web Monitor（開機自動啟動）。呼叫 start_forever.bat 啟動 watchdog + Flask。"

# ---------- 前置檢查 ----------
if (-not (Test-Path $BatPath)) {
    Write-Host "❌ 找不到 $BatPath" -ForegroundColor Red
    Write-Host "   請確認 OTA 端 repo 路徑是否正確" -ForegroundColor Yellow
    exit 1
}

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "⚠ 需要管理員權限才能建立 SYSTEM 帳號的排程器" -ForegroundColor Yellow
    Write-Host "   請用『系統管理員身分執行』PowerShell 再跑一次" -ForegroundColor Yellow
    exit 1
}

# ---------- 若已存在：詢問覆蓋 ----------
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "⚠ 已存在排程器 $TaskName，目前狀態: $($existing.State)" -ForegroundColor Yellow
    $ans = Read-Host "要覆蓋嗎？(y/N)"
    if ($ans -ne "y" -and $ans -ne "Y") {
        Write-Host "已取消" -ForegroundColor Yellow
        exit 0
    }
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "已移除舊任務" -ForegroundColor Gray
}

# ---------- 建立排程器 ----------
Write-Host "建立排程器 $TaskName ..." -ForegroundColor Cyan

$action = New-ScheduledTaskAction `
    -Execute $BatPath `
    -WorkingDirectory $RepoRoot

$trigger = New-ScheduledTaskTrigger -AtStartup

# SYSTEM 帳號 + 不需登入 + 最高權限
$principal = New-ScheduledTaskPrincipal `
    -UserId "SYSTEM" `
    -LogonType ServiceAccount `
    -RunLevel Highest

# 失敗重試：3 次，每次間隔 1 分鐘
$settings = New-ScheduledTaskSettingsSet `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 0)  # 0 = 不限制

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Description $Description | Out-Null

# ---------- 驗證 ----------
$task = Get-ScheduledTask -TaskName $TaskName
Write-Host ""
Write-Host "✅ 排程器建立完成" -ForegroundColor Green
Write-Host "  名稱:        $($task.TaskName)"
Write-Host "  狀態:        $($task.State)"
Write-Host "  觸發:        $($task.Triggers[0].CimClass.CimClassName)"
Write-Host "  執行:        $BatPath"
Write-Host "  工作目錄:    $RepoRoot"
Write-Host "  使用者:      SYSTEM (ServiceAccount)"
Write-Host ""
Write-Host "立即測試啟動（不等開機）：" -ForegroundColor Cyan
Write-Host "  Start-ScheduledTask -TaskName $TaskName" -ForegroundColor Gray
Write-Host ""
Write-Host "驗證 Flask 在 5000 port listen：" -ForegroundColor Cyan
Write-Host "  netstat -ano | findstr :5000" -ForegroundColor Gray
Write-Host ""
Write-Host "移除排程器：" -ForegroundColor Cyan
Write-Host "  Unregister-ScheduledTask -TaskName $TaskName -Confirm:`$false" -ForegroundColor Gray

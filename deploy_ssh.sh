#!/usr/bin/env bash
# ==============================================================================
# deploy_ssh.sh — GX20-PW3335 SSH 遠端維運與發布腳本（WSL 端執行）
#
# 部署端（10.35.31.10 / gx20）僅維護執行目錄 D:\sampo\GX20-dist，不保留原始碼或編譯環境。
#
# 用法：
#   ./deploy_ssh.sh check                       驗證 SSH 通道與遠端執行目錄狀態
#   ./deploy_ssh.sh publish <dist_dir> [--restart]
#                                               將本地已編譯的 Nuitka standalone 目錄發布至 D:\sampo\GX20-dist
#                                               （保留遠端 config/、data/、logs/）
#   ./deploy_ssh.sh backup [local_dir]          備份遠端 data/ 與 config/ 至 WSL（預設 ~/backups/gx20_<ts>）
#   ./deploy_ssh.sh start                       背景啟動 D:\sampo\GX20-dist\gx20_monitor.exe
#   ./deploy_ssh.sh stop                        停止 D:\sampo\GX20-dist\gx20_monitor.exe
#   ./deploy_ssh.sh restart                     重啟 D:\sampo\GX20-dist\gx20_monitor.exe
#   ./deploy_ssh.sh status                      查看執行狀態、Port 5000 與最近 log
#   ./deploy_ssh.sh logs [N]                    查看遠端 logs\app.log 最後 N 行（預設 50）
#
# 環境變數覆寫：
#   GX20_SSH_HOST    預設 gx20
#   GX20_RUNTIME_DIR 預設 D:\sampo\GX20-dist
# ==============================================================================
set -euo pipefail

SSH_HOST="${GX20_SSH_HOST:-gx20}"
RUNTIME_DIR_WIN="${GX20_RUNTIME_DIR:-D:\\sampo\\GX20-dist}"
EXE_NAME="gx20_monitor.exe"
EXE_PATH_WIN="${RUNTIME_DIR_WIN}\\${EXE_NAME}"

SSH_OPTS=(
    -o BatchMode=yes
    -o ConnectTimeout=10
    -o ControlMaster=auto
    -o "ControlPath=/tmp/gx20-ssh-%r@%h-%p"
    -o ControlPersist=300
)

run_ssh_ps() {
    local b64
    b64=$(python3 -c 'import base64, sys; print(base64.b64encode(sys.argv[1].encode("utf-8")).decode("ascii"))' "$1")
    printf '%s\n' "$b64" | ssh "${SSH_OPTS[@]}" "$SSH_HOST" 'powershell -NoProfile -NonInteractive -Command "$b=[Console]::In.ReadToEnd().Trim(); Invoke-Expression ([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($b)))"'
}

cmd_check() {
    echo "==> [check] 驗證 SSH 通道 (${SSH_HOST} -> ${RUNTIME_DIR_WIN})..."
    run_ssh_ps "
        \$ErrorActionPreference = 'Continue'
        Write-Host ('Host: ' + \$env:COMPUTERNAME + ' User: ' + \$env:USERNAME)
        if (-not (Test-Path '${RUNTIME_DIR_WIN}')) {
            Write-Error '找不到遠端執行目錄: ${RUNTIME_DIR_WIN}'; exit 1
        }
        Write-Host 'Runtime dir OK: ${RUNTIME_DIR_WIN}'
        if (Test-Path '${EXE_PATH_WIN}') {
            Write-Host 'Executable OK: ${EXE_PATH_WIN}'
        } else {
            Write-Error '找不到執行檔: ${EXE_PATH_WIN}'; exit 1
        }
        \$task = Get-ScheduledTask -TaskName 'GX20_monitor' -ErrorAction SilentlyContinue
        if (\$task) {
            Write-Host ('ScheduledTask GX20_monitor: ' + \$task.State + ' -> ' + \$task.Actions[0].Execute)
        } else {
            Write-Host 'ScheduledTask GX20_monitor: 未建立'
        }
    "
}

cmd_publish() {
    local local_dist="${1:-}"
    local force_restart="false"
    if [[ -z "$local_dist" || ! -d "$local_dist" ]]; then
        echo "ERROR: 請指定本地已編譯的 Nuitka standalone 目錄，例如：./deploy_ssh.sh publish ./dist" >&2
        exit 2
    fi
    if [[ ! -f "${local_dist}/${EXE_NAME}" ]]; then
        echo "ERROR: 在 ${local_dist} 找不到 ${EXE_NAME}" >&2
        exit 2
    fi
    if [[ "${2:-}" == "--restart" ]]; then
        force_restart="true"
    fi

    echo "==> [publish] 上傳 ${local_dist} 至 ${SSH_HOST}:${RUNTIME_DIR_WIN}（保留 config/、data/、logs/）..."
    local stage_name
    stage_name="gx20_stage_$(date +%Y%m%d_%H%M%S)"

    run_ssh_ps "
        \$ErrorActionPreference = 'Stop'
        \$stage = Join-Path \$env:TEMP '${stage_name}'
        New-Item -ItemType Directory -Force -Path \$stage | Out-Null
    "

    tar -czf - -C "$local_dist" . \
        | ssh "${SSH_OPTS[@]}" "$SSH_HOST" "tar -xzf - -C %TEMP%/${stage_name}"

    run_ssh_ps "
        \$ErrorActionPreference = 'Stop'
        \$runtimeDir = '${RUNTIME_DIR_WIN}'
        \$stage = Join-Path \$env:TEMP '${stage_name}'
        if (-not (Test-Path (Join-Path \$stage '${EXE_NAME}'))) {
            throw '上傳後在暫存區找不到 ${EXE_NAME}'
        }

        New-Item -ItemType Directory -Force -Path \$runtimeDir, \"\$runtimeDir\\config\", \"\$runtimeDir\\data\", \"\$runtimeDir\\logs\" | Out-Null

        \$wasRunning = \$false
        \$running = Get-Process -ErrorAction SilentlyContinue | Where-Object { \$_.Path -eq '${EXE_PATH_WIN}' }
        if (\$running) {
            \$wasRunning = \$true
            Write-Host '停止執行中的 ${EXE_PATH_WIN} 以便更新程式檔...'
            \$running | Stop-Process -Force
            Start-Sleep -Seconds 2
        }

        try {
            # 清除舊版編譯檔案，保留 config / data / logs
            Get-ChildItem \$runtimeDir -Force | Where-Object {
                \$_.Name -notin @('config', 'data', 'logs')
            } | Remove-Item -Recurse -Force

            Get-ChildItem \$stage -Force | Where-Object {
                \$_.Name -notin @('config', 'data', 'logs')
            } | ForEach-Object {
                Copy-Item -Path \$_.FullName -Destination \$runtimeDir -Recurse -Force
            }
            Write-Host '✅ 已發布最新編譯檔至 ${RUNTIME_DIR_WIN}'
        } finally {
            Remove-Item -Recurse -Force \$stage -ErrorAction SilentlyContinue
        }

        if (\$wasRunning -or '${force_restart}' -eq 'true') {
            Start-Process -FilePath '${EXE_PATH_WIN}' -WorkingDirectory \$runtimeDir -WindowStyle Hidden
            Start-Sleep -Seconds 3
            \$started = Get-Process -ErrorAction SilentlyContinue | Where-Object { \$_.Path -eq '${EXE_PATH_WIN}' }
            if (-not \$started) {
                throw '啟動後進程未存活，請檢查 ${RUNTIME_DIR_WIN}\\logs\\app.log'
            }
            Write-Host ('✅ 已啟動 ${EXE_PATH_WIN} (PID: ' + (\$started.Id -join ', ') + ')')
        }
    "
}

cmd_backup() {
    local ts
    ts="$(date +%Y%m%d_%H%M%S)"
    local dest="${1:-$HOME/backups/gx20_${ts}}"
    mkdir -p "$dest"

    echo "==> [backup] 備份 ${SSH_HOST}:${RUNTIME_DIR_WIN} 的 data/ 與 config/ 至 ${dest}..."
    local remote_tmp
    remote_tmp=$(run_ssh_ps "
        \$ErrorActionPreference = 'Stop'
        \$src = '${RUNTIME_DIR_WIN}'
        \$tmp = Join-Path \$env:TEMP ('gx20_bak_' + (Get-Date -Format 'yyyyMMdd_HHmmss'))
        New-Item -ItemType Directory -Force -Path (Join-Path \$tmp 'data\\archive'), (Join-Path \$tmp 'config') | Out-Null

        \$py = 'C:\\Users\\USER\\AppData\\Local\\Programs\\Python\\Python313\\python.exe'
        if (Test-Path \$py) {
            \$pySrc = 'import os, sqlite3, sys; s, d = sys.argv[1], sys.argv[2]; [sqlite3.connect(os.path.join(s, f)).backup(sqlite3.connect(os.path.join(d, f))) for f in os.listdir(s) if f.endswith(\".db\")]'
            \$pyB64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes(\$pySrc))
            & \$py -c \"import base64, sys; exec(base64.b64decode(sys.argv.pop(1)).decode('utf-8'))\" \$pyB64 (Join-Path \$src 'data') (Join-Path \$tmp 'data')
            if (\$LASTEXITCODE -ne 0) { throw 'sqlite3 線上備份失敗' }
        } else {
            Copy-Item (Join-Path \$src 'data\\*.db*') (Join-Path \$tmp 'data') -Force -ErrorAction SilentlyContinue
        }
        if (Test-Path (Join-Path \$src 'data\\archive')) {
            Copy-Item (Join-Path \$src 'data\\archive\\*') (Join-Path \$tmp 'data\\archive') -Recurse -Force -ErrorAction SilentlyContinue
        }
        if (Test-Path (Join-Path \$src 'config')) {
            Copy-Item (Join-Path \$src 'config\\*') (Join-Path \$tmp 'config') -Recurse -Force -ErrorAction SilentlyContinue
        }
        \$tarPath = Join-Path \$tmp 'backup.tar.gz'
        tar -czf \$tarPath -C \$tmp data config
        Write-Output \$tarPath
    " | tr -d '\r' | tail -n 1)

    local remote_tmp_posix="${remote_tmp//\\//}"
    scp "${SSH_OPTS[@]}" "${SSH_HOST}:${remote_tmp_posix}" "${dest}/backup.tar.gz"
    tar -xzf "${dest}/backup.tar.gz" -C "$dest"
    rm -f "${dest}/backup.tar.gz"

    run_ssh_ps "Remove-Item -Recurse -Force (Split-Path '${remote_tmp}' -Parent) -ErrorAction SilentlyContinue"
    echo "✅ 備份完成: ${dest}"
    ls -lh "${dest}/config" "${dest}/data"
}

cmd_stop() {
    echo "==> [stop] 停止 ${EXE_PATH_WIN}..."
    run_ssh_ps "
        \$procs = Get-Process -ErrorAction SilentlyContinue | Where-Object { \$_.Path -eq '${EXE_PATH_WIN}' }
        if (\$procs) {
            \$procs | Stop-Process -Force
            Start-Sleep -Seconds 2
            Write-Host '已停止 ${EXE_PATH_WIN}'
        } else {
            Write-Host '目前沒有執行中的 ${EXE_PATH_WIN}'
        }
    "
}

cmd_start() {
    echo "==> [start] 啟動 ${EXE_PATH_WIN}..."
    run_ssh_ps "
        \$ErrorActionPreference = 'Stop'
        if (-not (Test-Path '${EXE_PATH_WIN}')) {
            throw '找不到 ${EXE_PATH_WIN}'
        }
        \$existing = Get-Process -ErrorAction SilentlyContinue | Where-Object { \$_.Path -eq '${EXE_PATH_WIN}' }
        if (\$existing) {
            Write-Host ('已在執行中 (PID: ' + (\$existing.Id -join ', ') + ')')
            return
        }
        Start-Process -FilePath '${EXE_PATH_WIN}' -WorkingDirectory '${RUNTIME_DIR_WIN}' -WindowStyle Hidden
        Start-Sleep -Seconds 3
        \$started = Get-Process -ErrorAction SilentlyContinue | Where-Object { \$_.Path -eq '${EXE_PATH_WIN}' }
        if (-not \$started) {
            throw '啟動後進程未存活，請檢查 ${RUNTIME_DIR_WIN}\\logs\\app.log'
        }
        Write-Host ('✅ 已啟動 (PID: ' + (\$started.Id -join ', ') + ')')
    "
}

cmd_restart() {
    cmd_stop
    cmd_start
}

cmd_status() {
    echo "==> [status] 檢查 ${SSH_HOST} (${RUNTIME_DIR_WIN})..."
    run_ssh_ps "
        Write-Host '--- 目標進程 (${EXE_PATH_WIN}) ---'
        \$procs = Get-Process -ErrorAction SilentlyContinue | Where-Object { \$_.Path -eq '${EXE_PATH_WIN}' }
        if (\$procs) {
            \$procs | Select-Object Id, ProcessName, StartTime, Path | Format-Table -AutoSize
        } else {
            Write-Host '(未執行)'
        }
        Write-Host '--- Port 5000 ---'
        netstat -ano | findstr :5000
        Write-Host '--- logs\\app.log (最後 10 行) ---'
        \$logFile = '${RUNTIME_DIR_WIN}\\logs\\app.log'
        if (Test-Path \$logFile) {
            Get-Content \$logFile -Tail 10 -Encoding UTF8
        } else {
            Write-Host '(尚無 app.log)'
        }
    "
}

cmd_logs() {
    local lines="${1:-50}"
    run_ssh_ps "
        \$logFile = '${RUNTIME_DIR_WIN}\\logs\\app.log'
        if (Test-Path \$logFile) {
            Get-Content \$logFile -Tail ${lines} -Encoding UTF8
        } else {
            Write-Host '找不到 app.log'
        }
    "
}

usage() {
    sed -n '3,21p' "$0" | sed 's/^# \?//'
    exit 1
}

case "${1:-}" in
    check)   cmd_check ;;
    publish) cmd_publish "${2:-}" "${3:-}" ;;
    backup)  cmd_backup "${2:-}" ;;
    start)   cmd_start ;;
    stop)    cmd_stop ;;
    restart) cmd_restart ;;
    status)  cmd_status ;;
    logs)    cmd_logs "${2:-50}" ;;
    *)       usage ;;
esac

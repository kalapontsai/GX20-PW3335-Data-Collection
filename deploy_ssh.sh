#!/usr/bin/env bash
# ==============================================================================
# deploy_ssh.sh — GX20-PW3335 SSH 部署與 Nuitka 編譯管理腳本（WSL 端執行）
#
# 用法：
#   ./deploy_ssh.sh check              驗證 SSH 通道、編譯與上線目錄環境
#   ./deploy_ssh.sh sync               透過 SSH 同步程式碼與靜態資源到 gx20:D:\sampo\GX20
#   ./deploy_ssh.sh build              在遠端執行 Nuitka --standalone 編譯到 D:\sampo\GX20\dist
#   ./deploy_ssh.sh publish            將 D:\sampo\GX20\dist\* 同步到上線目錄 D:\sampo\GX20-dist（保留 config/data/logs）
#   ./deploy_ssh.sh deploy [--restart] 同步 + 編譯 + 發布到 D:\sampo\GX20-dist（若原在運行中或加 --restart 會自動重啟）
#   ./deploy_ssh.sh start              背景啟動 D:\sampo\GX20-dist\gx20_monitor.exe
#   ./deploy_ssh.sh stop               僅停止 D:\sampo\GX20-dist\gx20_monitor.exe
#   ./deploy_ssh.sh restart            停止後重新啟動 D:\sampo\GX20-dist\gx20_monitor.exe
#   ./deploy_ssh.sh status             查看執行狀態、Port 5000 與最近 log
#   ./deploy_ssh.sh logs [N]           查看遠端 logs\app.log 最後 N 行（預設 50）
#
# 環境變數覆寫：
#   GX20_SSH_HOST    預設 gx20
#   GX20_REMOTE_DIR  預設 D:\sampo\GX20（原始碼與 Nuitka 編譯工作區）
#   GX20_RUNTIME_DIR 預設 D:\sampo\GX20-dist（實際執行與 data/config/logs 目錄）
#   GX20_REMOTE_PY   預設 D:\sampo\GX20\.python\cpython-3.13.18-windows-x86_64-none\python.exe
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SSH_HOST="${GX20_SSH_HOST:-gx20}"
REMOTE_DIR_WIN="${GX20_REMOTE_DIR:-D:\\sampo\\GX20}"
REMOTE_DIR_POSIX="${REMOTE_DIR_WIN//\\//}"
RUNTIME_DIR_WIN="${GX20_RUNTIME_DIR:-D:\\sampo\\GX20-dist}"
REMOTE_PY="${GX20_REMOTE_PY:-${REMOTE_DIR_WIN}\\.python\\cpython-3.13.18-windows-x86_64-none\\python.exe}"
EXE_NAME="gx20_monitor.exe"
BUILD_EXE_WIN="${REMOTE_DIR_WIN}\\dist\\${EXE_NAME}"
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
    echo "==> [check] 驗證 SSH 通道 (${SSH_HOST} -> 編譯區 ${REMOTE_DIR_WIN} / 上線區 ${RUNTIME_DIR_WIN})..."
    run_ssh_ps "
        \$ErrorActionPreference = 'Continue'
        Write-Host ('Host: ' + \$env:COMPUTERNAME + ' User: ' + \$env:USERNAME)
        if (-not (Test-Path '${REMOTE_DIR_WIN}')) {
            Write-Error '找不到編譯工作目錄: ${REMOTE_DIR_WIN}'; exit 1
        }
        Write-Host 'Build dir OK: ${REMOTE_DIR_WIN}'
        if (Test-Path '${RUNTIME_DIR_WIN}') {
            Write-Host 'Runtime dir OK: ${RUNTIME_DIR_WIN}'
        } else {
            Write-Host 'Runtime dir: 尚未建立（publish 時會自動建立 ${RUNTIME_DIR_WIN}）'
        }
        if (-not (Test-Path '${REMOTE_PY}')) {
            Write-Error '找不到 Python: ${REMOTE_PY}'; exit 1
        }
        & '${REMOTE_PY}' --version
        \$venvPy = '${REMOTE_DIR_WIN}\\.venv_build\\Scripts\\python.exe'
        if (Test-Path \$venvPy) {
            \$nuitkaOut = cmd /c \"\"\"\$venvPy\"\" -m nuitka --version 2>&1\"
            if (\$LASTEXITCODE -eq 0) {
                Write-Host ('Nuitka (venv): ' + (\$nuitkaOut | Select-Object -First 1))
            } else {
                Write-Host 'Nuitka: 尚未安裝於 .venv_build（build 時會自動安裝）'
            }
        } else {
            Write-Host 'Build venv: 尚未建立（build 時會自動建立 ${REMOTE_DIR_WIN}\\.venv_build）'
        }
        if (Test-Path '${BUILD_EXE_WIN}') {
            Write-Host 'Build output OK: ${BUILD_EXE_WIN}'
        } else {
            Write-Host 'Build output: 尚未編譯'
        }
        if (Test-Path '${EXE_PATH_WIN}') {
            Write-Host 'Runtime executable OK: ${EXE_PATH_WIN}'
        } else {
            Write-Host 'Runtime executable: 尚未發布'
        }
    "
}

cmd_sync() {
    echo "==> [sync] 同步程式碼到 ${SSH_HOST}:${REMOTE_DIR_WIN}（保留遠端 data/、config/settings.json、logs/）..."
    cd "$SCRIPT_DIR"

    # 確保遠端目錄存在，並清除目標目錄內遺留的 OTA 檔案（不碰 data/ 與 settings.json）
    run_ssh_ps "
        \$ErrorActionPreference = 'Stop'
        New-Item -ItemType Directory -Force -Path '${REMOTE_DIR_WIN}', '${REMOTE_DIR_WIN}\\config', '${REMOTE_DIR_WIN}\\data', '${REMOTE_DIR_WIN}\\logs' | Out-Null
        \$legacy = @(
            'ota.py', 'ota_push.py', 'ota_watchdog.bat',
            'start_forever.bat', 'restart_clean.bat', 'setup_autostart.ps1',
            'config\\ota_token'
        )
        foreach (\$rel in \$legacy) {
            \$p = Join-Path '${REMOTE_DIR_WIN}' \$rel
            if (Test-Path \$p) {
                Remove-Item -Force \$p
                Write-Host ('Removed legacy OTA file: ' + \$rel)
            }
        }
        \$backupDir = Join-Path '${REMOTE_DIR_WIN}' 'config\\ota_backup'
        if (Test-Path \$backupDir) {
            Remove-Item -Recurse -Force \$backupDir
            Write-Host 'Removed legacy dir: config\\ota_backup'
        }
    "

    # 以 tar + gzip 串流傳輸，僅同步執行與編譯所需檔案
    tar -czf - \
        --exclude='__pycache__' \
        --exclude='*.pyc' \
        app.py \
        config.py \
        gx20_reader.py \
        lttb.py \
        paths.py \
        pw3335_reader.py \
        requirements.txt \
        run.py \
        storage.py \
        whitelist.py \
        static \
        templates \
        config/settings.example.json \
        | ssh "${SSH_OPTS[@]}" "$SSH_HOST" "tar -xzf - -C ${REMOTE_DIR_POSIX}"

    echo "==> [sync] 同步完成。"
}

cmd_build() {
    echo "==> [build] 在 ${SSH_HOST} 執行 Nuitka --standalone 編譯..."
    run_ssh_ps "
        \$ErrorActionPreference = 'Continue'
        Set-Location '${REMOTE_DIR_WIN}'

        \$venvPy = '${REMOTE_DIR_WIN}\\.venv_build\\Scripts\\python.exe'
        \$pyvenvCfg = '${REMOTE_DIR_WIN}\\.venv_build\\pyvenv.cfg'
        \$needNewVenv = -not (Test-Path \$venvPy)
        if (-not \$needNewVenv -and (Test-Path \$pyvenvCfg)) {
            \$cfgText = Get-Content \$pyvenvCfg -Raw
            \$expectedHome = Split-Path '${REMOTE_PY}' -Parent
            if (\$cfgText -notlike \"*\$expectedHome*\") {
                Write-Host '偵測到 .venv_build 基底 Python 變更，重建中...'
                Remove-Item -Recurse -Force '${REMOTE_DIR_WIN}\\.venv_build'
                \$needNewVenv = \$true
            }
        }
        if (\$needNewVenv) {
            Write-Host '建立獨立編譯環境 .venv_build...'
            & '${REMOTE_PY}' -m venv .venv_build
            if (\$LASTEXITCODE -ne 0) { Write-Error '建立 .venv_build 失敗'; exit 1 }
        }

        # 確認必要執行期套件與 Nuitka 存在
        & \$venvPy -m pip install --upgrade pip --quiet
        & \$venvPy -m pip install -r requirements.txt --quiet
        if (\$LASTEXITCODE -ne 0) { Write-Error 'pip install -r requirements.txt 失敗'; exit 1 }

        & \$venvPy -m nuitka --version *> \$null
        if (\$LASTEXITCODE -ne 0) {
            Write-Host '安裝 nuitka、ordered-set、zstandard...'
            & \$venvPy -m pip install --upgrade nuitka ordered-set zstandard
            if (\$LASTEXITCODE -ne 0) { Write-Error 'pip install nuitka 失敗'; exit 1 }
        }

        # 清理舊暫存編譯目錄
        if (Test-Path 'build\\run.dist') { Remove-Item -Recurse -Force 'build\\run.dist' }
        if (Test-Path 'build\\run.build') { Remove-Item -Recurse -Force 'build\\run.build' }

        Write-Host '開始 Nuitka 編譯...'
        & \$venvPy -m nuitka \`
            --standalone \`
            --assume-yes-for-downloads \`
            --windows-console-mode=disable \`
            --output-dir=build \`
            --output-filename=${EXE_NAME} \`
            --include-data-dir=static=static \`
            --include-data-dir=templates=templates \`
            --include-module=engineio.async_drivers.threading \`
            --include-module=simple_websocket \`
            --remove-output \`
            run.py

        if (\$LASTEXITCODE -ne 0) { Write-Error 'Nuitka 編譯失敗'; exit 1 }
        if (-not (Test-Path 'build\\run.dist\\${EXE_NAME}')) {
            Write-Error '找不到編譯輸出: build\\run.dist\\${EXE_NAME}'; exit 1
        }

        # 替換編譯區 dist/（先確認 build\run.dist 存在）
        if (Test-Path 'dist_old') { Remove-Item -Recurse -Force 'dist_old' }
        if (Test-Path 'dist') { Rename-Item -Path 'dist' -NewName 'dist_old' }
        Move-Item -Path 'build\\run.dist' -Destination 'dist'
        if (Test-Path 'dist_old') { Remove-Item -Recurse -Force 'dist_old' }

        Write-Host '✅ Nuitka 編譯完成: ${BUILD_EXE_WIN}'
    "
}

cmd_publish() {
    local force_restart="${1:-false}"
    echo "==> [publish] 發布 ${REMOTE_DIR_WIN}\\dist 到 ${RUNTIME_DIR_WIN}（保留 config/、data/、logs/）..."
    run_ssh_ps "
        \$ErrorActionPreference = 'Stop'
        \$buildDist = '${REMOTE_DIR_WIN}\\dist'
        \$runtimeDir = '${RUNTIME_DIR_WIN}'
        if (-not (Test-Path '${BUILD_EXE_WIN}')) {
            throw '找不到編譯輸出: ${BUILD_EXE_WIN}，請先執行 build'
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

        # 清除舊版編譯檔案，保留 config / data / logs
        Get-ChildItem \$runtimeDir -Force | Where-Object {
            \$_.Name -notin @('config', 'data', 'logs')
        } | Remove-Item -Recurse -Force

        Copy-Item -Path \"\$buildDist\\*\" -Destination \$runtimeDir -Recurse -Force
        Write-Host '✅ 已同步最新編譯檔至 ${RUNTIME_DIR_WIN}'

        if (\$wasRunning -or '${force_restart}' -eq 'true') {
            Start-Process -FilePath '${EXE_PATH_WIN}' -WorkingDirectory \$runtimeDir -WindowStyle Hidden
            Start-Sleep -Seconds 3
            \$started = Get-Process -ErrorAction SilentlyContinue | Where-Object { \$_.Path -eq '${EXE_PATH_WIN}' }
            if (-not \$started) {
                throw '啟動後進程未存活，請檢查 ${RUNTIME_DIR_WIN}\\logs\\app.log'
            }
            Write-Host ('✅ 已啟動 ${EXE_PATH_WIN} (PID: ' + (\$started.Id -join ', ') + ')')
        } else {
            Write-Host '（原未執行且未指定 --restart，暫不自動啟動）'
        }
    "
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
            throw '找不到 ${EXE_PATH_WIN}，請先執行 build 與 publish'
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

cmd_deploy() {
    local force_restart="false"
    if [[ "${1:-}" == "--restart" ]]; then
        force_restart="true"
    fi
    cmd_sync
    cmd_build
    cmd_publish "$force_restart"
}

usage() {
    sed -n '3,19p' "$0" | sed 's/^# \?//'
    exit 1
}

case "${1:-}" in
    check)   cmd_check ;;
    sync)    cmd_sync ;;
    build)   cmd_build ;;
    publish) cmd_publish "${2:-false}" ;;
    deploy)  cmd_deploy "${2:-}" ;;
    start)   cmd_start ;;
    stop)    cmd_stop ;;
    restart) cmd_restart ;;
    status)  cmd_status ;;
    logs)    cmd_logs "${2:-50}" ;;
    *)       usage ;;
esac

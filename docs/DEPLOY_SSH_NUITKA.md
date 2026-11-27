# SSH 維運與 Nuitka 打包指南

> 本專案已移除所有 OTA（`ota.py`、`ota_push.py`、`ota_watchdog.bat`、`start_forever.bat`、`restart_clean.bat`、`setup_autostart.ps1` 及 `/api/admin/*` 端點），改由 **WSL SSH + Nuitka `--standalone`** 部署與維運。
>
> 部署端（`10.35.31.10` / `gx20`）只保留乾淨的執行目錄 `D:\sampo\GX20-dist`，不再存放原始碼或編譯工具鏈。

---

## 1. 環境與路徑架構

| 項目 | 位置 / 說明 |
|---|---|
| 開發端（WSL） | 本機 repo，透過 `deploy_ssh.sh` 經 SSH 管理遠端 |
| 遠端主機 | `gx20`（`10.35.31.10`，Windows 11，已設定 SSH 免密登入） |
| 遠端上線執行目錄 | `D:\sampo\GX20-dist`（執行 `gx20_monitor.exe` 並保存 `data\`、`config\`、`logs\`） |

`paths.py` 會自動判斷執行模式：
- **一般 Python 執行**（`python run.py`）：`APP_ROOT` 與 `BUNDLE_DIR` 皆為專案根目錄。
- **Nuitka standalone 執行**（`D:\sampo\GX20-dist\gx20_monitor.exe`）：
  - `static/` 與 `templates/` 從執行檔同層內建資源讀取（`get_bundle_dir()`）。
  - `data/`、`config/`、`logs/` 從 `D:\sampo\GX20-dist\` 讀取（`get_app_root()`；亦可用 `GX20_ROOT` 環境變數指定）。

---

## 2. 常用 SSH 維運指令（WSL 端）

```bash
# 1. 驗證 SSH 通道與 D:\sampo\GX20-dist 執行狀態
./deploy_ssh.sh check

# 2. 備份遠端 data/ 與 config/ 至 WSL（支援 SQLite 線上熱備份）
./deploy_ssh.sh backup

# 3. 發布在 Windows 建置環境編譯好的 Nuitka standalone 目錄至 D:\sampo\GX20-dist
#    （自動保留遠端 config/、data/、logs/）
./deploy_ssh.sh publish ./dist --restart

# 4. 遠端進程與 log 管理（純 PowerShell over SSH）
./deploy_ssh.sh start
./deploy_ssh.sh stop
./deploy_ssh.sh restart
./deploy_ssh.sh status
./deploy_ssh.sh logs 100
```

---

## 3. Windows 端 Nuitka `--standalone` 編譯指令（於建置機執行）

若需重新打包新版 `gx20_monitor.exe`，在乾淨的 Windows Python 3.13 環境安裝相依與 Nuitka 後執行：

```powershell
python -m pip install -r requirements.txt nuitka ordered-set zstandard
python -m nuitka `
  --standalone `
  --assume-yes-for-downloads `
  --windows-console-mode=disable `
  --output-dir=build `
  --output-filename=gx20_monitor.exe `
  --include-data-dir=static=static `
  --include-data-dir=templates=templates `
  --include-module=engineio.async_drivers.threading `
  --include-module=simple_websocket `
  --remove-output `
  run.py
```

編譯產出的 `build\run.dist\` 即可透過 `./deploy_ssh.sh publish <該目錄> --restart` 同步至 `gx20:D:\sampo\GX20-dist`。

---

## 4. 開機自動啟動（Windows 工作排程器）

遠端排程 `\GX20_monitor` 直接指向 `D:\sampo\GX20-dist\gx20_monitor.exe`（工作目錄 `D:\sampo\GX20-dist`）：

```powershell
$action = New-ScheduledTaskAction `
  -Execute "D:\sampo\GX20-dist\gx20_monitor.exe" `
  -WorkingDirectory "D:\sampo\GX20-dist"

$settings = New-ScheduledTaskSettingsSet `
  -RestartCount 3 `
  -RestartInterval (New-TimeSpan -Minutes 1) `
  -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries `
  -StartWhenAvailable `
  -ExecutionTimeLimit (New-TimeSpan -Hours 0)

Set-ScheduledTask -TaskName "GX20_monitor" -Action $action -Settings $settings
```

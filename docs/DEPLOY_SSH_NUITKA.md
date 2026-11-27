# SSH 部署與 Nuitka 打包指南

> 本專案已移除所有 OTA（`ota.py`、`ota_push.py`、`ota_watchdog.bat`、`start_forever.bat`、`restart_clean.bat`、`setup_autostart.ps1` 及 `/api/admin/*` 端點），改由 **WSL SSH + Nuitka `--standalone`** 部署與管理。

---

## 1. 環境與路徑架構

| 項目 | 位置 / 說明 |
|---|---|
| 開發端（WSL） | 本機 repo，透過 `deploy_ssh.sh` 操作遠端 |
| 遠端主機 | `gx20`（Windows 11，已設定 SSH 免密登入） |
| 遠端編譯工作區 | `D:\sampo\GX20`（存放同步原始碼、獨立 Python `.python`、`.venv_build` 與編譯產物 `dist\`） |
| 遠端上線執行目錄 | `D:\sampo\GX20-dist`（實際執行 `gx20_monitor.exe` 與讀寫 `data\`、`config\`、`logs\`） |

`paths.py` 會自動判斷執行模式：
- **一般 Python 執行**（`python run.py`）：`APP_ROOT` 與 `BUNDLE_DIR` 皆為專案根目錄。
- **Nuitka standalone 執行**（`D:\sampo\GX20-dist\gx20_monitor.exe`）：
  - `static/` 與 `templates/` 從執行檔同層內建資源讀取（`get_bundle_dir()`）。
  - `data/`、`config/`、`logs/` 從 `D:\sampo\GX20-dist\` 讀取（`get_app_root()`；若執行檔位於名為 `dist` 的子目錄則自動指向其上一層，亦可用 `GX20_ROOT` 環境變數指定）。

---

## 2. 常用部署指令（WSL 端）

```bash
# 1. 驗證 SSH 通道、編譯區與上線區狀態
./deploy_ssh.sh check

# 2. 僅同步程式碼與前端資源到 D:\sampo\GX20（不影響執行中的服務）
./deploy_ssh.sh sync

# 3. 在遠端透過 Nuitka --standalone 編譯出 D:\sampo\GX20\dist\gx20_monitor.exe
./deploy_ssh.sh build

# 4. 將編譯成品發布至上線目錄 D:\sampo\GX20-dist（保留 config/、data/、logs/）
./deploy_ssh.sh publish

# 5. 一次完成 sync + build + publish（若服務原本在運行或加上 --restart 會自動重啟）
./deploy_ssh.sh deploy
./deploy_ssh.sh deploy --restart

# 6. 遠端進程與 log 管理（純 PowerShell over SSH，操作 D:\sampo\GX20-dist）
./deploy_ssh.sh start
./deploy_ssh.sh stop
./deploy_ssh.sh restart
./deploy_ssh.sh status
./deploy_ssh.sh logs 100
```

---

## 3. 開機自動啟動（Windows 工作排程器）

於遠端以系統管理員 PowerShell 設定或更新排程 `\GX20_monitor` 直接啟動 Nuitka 編譯執行檔：

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

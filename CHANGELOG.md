# GX20 Web Monitor — 版本演進與現況

> 大版本快照 + 重要 bug 修復 + 現況進度（最後更新：2026-07-24 14:xx）
>
> 程式架構見 [ARCHITECTURE.md](ARCHITECTURE.md)；使用者操作見 [README.md](README.md)。

---

## 目錄

1. [版本演進快照](#1-版本演進快照)
2. [現況進度（2026-06-11 session）](#2-現況進度2026-06-11-session)
3. [現況進度（2026-06-12 進階計算上線）](#3-現況進度2026-06-12-進階計算上線)
4. [現況進度（2026-06-15 PW3335 電力計上線）](#4-現況進度2026-06-15-pw3335-電力計上線)
5. [現況進度（2026-06-16 CSV BOM hotfix）](#5-現況進度2026-06-16-csv-bom-hotfix)
6. [現況進度（2026-06-16 設定同步 v8.1/v8.1.1/v8.1.2）](#6-現況進度2026-06-16-設定同步-v8-1-v8-1-1-v8-1-2)
7. [現況進度（2026-06-16 X 軸寬度對齊 v8.2）](#7-現況進度2026-06-16-x-軸寬度對齊-v8-2)
8. [現況進度（2026-06-16 電力 I/W 軸位置對齊 v8.4）](#8-現況進度2026-06-16-電力-iw-軸位置對齊-v8-4)
9. [現況進度（2026-07-01 CSV datetime 格式 v9）](#9-現況進度2026-07-01-csv-datetime-格式-v9)
10. [現況進度（2026-07-22 snapshot viewer v10）](#10-現況進度2026-07-22-snapshot-viewer-v10)
11. [現況進度（2026-07-23 備註同步 + archive meta v10.1）](#11-現況進度2026-07-23-備註同步--archive-meta-v101)
12. [現況進度（2026-07-24 主頁 rate 計算語意修正 v11）](#12-現況進度2026-07-24-主頁-rate-計算語意修正-v11)
13. [現況進度（2026-07-24 snapshot 統計時區 bug v10.1.1）](#13-現況進度2026-07-24-snapshot-統計時區-bug-v1011)
14. [現況進度（2026-07-24 取消 PW3335 『啟用』開關 v10.2）](#14-現況進度2026-07-24-取消-pw3335-啟用-開關-v102)
15. [現況進度（2026-07-27 行動簡式頁面 /mobile v10.3）](#15-現況進度2026-07-27-行動簡式頁面-mobile-v103)
16. [現況進度（2026-07-30 whitelist save/load 鏈修補 v10.3.2）](#16-現況進度2026-07-30-whitelist-saveload-鏈修補-v1032)
17. [現況進度（2026-08-05 計算頁 /calculator v11）](#17-現況進度2026-08-05-計算頁-calculator-v11)
18. [現況進度（2026-09-11 設定重開機還原 bug 修補 v10.4）](#18-現況進度2026-09-11-設定重開機還原-bug-修補-v104)

---

## 1. 版本演進快照

| 版 | 日期 | 重點 |
|---|---|---|
| **v2.0** | — | 資料持久化、LTTB 降取樣、明暗主題、ring buffer 計算 |
| **v3.0** | — | debug logger、圖表精簡、X 軸範圍動態、CSV 整合匯出、設定檔同步 |
| **v4.0** | — | 最新讀值下拉化、CSV 平均整合、設定檔同步 |
| **v4.1** | — | 圖表切換 hotfix + OTA 部署通道 |
| **v5.0** | — | 6 工位獨立 DB + 清除前歸檔 |
| **v6.0** | — | per-station Y 軸範圍 + 動態縮放 + clear_log endpoint |
| **v6.1** | 2026-06-12 | 進階計算：游標模式（拖曳 x-bar 計算區間平均/最大/最小）|
| **v7** | 2026-06-15 | PW3335 電力計整合（6 工位 V/I/W、雙圖表、CSV 補欄）|
| **v8** | 2026-06-16 | CSV 中文欄位 BOM hotfix + 別名長度上限 20 字 |
| **v8.1** | 2026-06-16 | 設定跨瀏覽器/跨電腦不同步 → storage.js debounce auto-save |
| **v8.1.1** | 2026-06-16 | 舊瀏覽器 sessionStorage 殘留 → init 時自動同步 server（**v8.1.2 拿掉**）|
| **v8.1.2** | 2026-06-16 | 遠端瀏覽器鎖死設定權限（127.0.0.1 才可改）— 根治 v8.1.1 跨工位污染 |
| **v8.1.3** | 2026-06-16 | `POST /api/clear` 也鎖遠端 — 不可逆操作一致性 |
| **v8.2** | 2026-06-16 | 溫度與電力 X 軸寬度對齊（afterFit hook） |

### 1.1 v5.0 — 6 工位獨立 DB + 清除前歸檔

**背景**：6 工位非同步上下線，原有單一 `data/gx20.db` 設計會造成：
- 清除某工位只能全刪（其他工位一起陪葬）
- 6 工位輪流上下線，時間軸混雜難以分辨
- 清除無歸檔，按錯救不回

**佈局變更**：

```
data/
├── gx20_<station>.db        # 每工位一份 samples 表
├── gx20_settings.db         # 6 工位共用的 settings 表
└── archive/
    ├── gx20_<station>_<YYYYMMDD_HHMMSS>.db    # 清除前歸檔
    └── gx20_pre_migration_<時間>.db            # 舊佈局 migrate 記錄
```

**新行為**：
- 主畫面 / 設定頁 [清除資料] 改為 [清除此工位]
- 點擊 → 兩段式 confirm：是否歸檔 → 確認清除
- 歸檔自動保留最近 **5 份**（每工位各自），超過自動刪最舊
- 設定與資料分離：清資料不會洗掉 GX20 連線、別名、顏色
- 6 個小 DB 各自 WAL，輪流寫入比 1 個大 DB 友善

**API 變更**：
- `POST /api/clear` 必填 `station`（不再支援全清；如需全清帶 `station=ALL`）
- 新增 `GET /api/archives?station=工位5` 查歸檔清單
- `GET /api/db_stats` 加 `time_range`（每工位首/末筆時間）與 `archive_keep_per_station`

**向後相容**：
- 啟動時偵測舊 `data/gx20.db` → 自動 migrate
  - 1) 整份先歸檔為 `gx20_pre_migration_<時間>.db`
  - 2) samples 按 station 切到 6 個新 DB
  - 3) settings 複製到新 settings DB
  - 4) 刪除舊檔（WAL/SHM/JOURNAL 一起清）

### 1.2 v4.0 — 最新讀值下拉化 + CSV 平均整合 + 設定檔同步

- **主畫面「最新讀值」下拉化**：
  - 三個 select 放同一列：X 軸 / 速率 (1~60 分鐘) / 平均 (1~60 分鐘, 3/6 小時)
  - 表頭「速率 (°C/N 分鐘)」「平均 (°C/N 分鐘)」動態更新
  - select change → 立即 client 端重畫 + 背景 POST `/api/settings`，下個 tick 套用
- **CSV 平均整合**：`/api/export_csv/<station>` 拉 raw rows 後依分鐘 bucket 算術平均
  - 每個 channel 各別平均；全 None → 空字串
  - 區間預設讀 `chart_x_minutes`（與 X 軸一致），可由 `?since_minutes=N` 覆寫
  - 檔名加區間標記：`工位5_60min_20260610_154959.csv`
- **設定檔同步**：`save_settings()` 同步 dump 整包設定到 `config/settings.json`
  - 啟動時若檔案存在 → 優先採用並寫回 SQLite（避免 DB 預設值誤蓋）
  - atomic write：`.tmp` + `os.replace`

### 1.3 v3.0 — Debug logger + 圖表精簡 + 動態 X 軸

- **Debug logger**：log 寫到 `logs/app.log`（RotatingFileHandler，2MB × 5 個備份）
  - 啟動 / 關閉、poller 每輪結果、HTTP 請求、SocketIO 連線 / 斷線都會入 log
  - 等級由 `settings` 表的 `debug_log_enabled` 控制（INFO ↔ DEBUG）
  - 新增 `GET /api/debug`、`POST /api/debug`、`GET /api/debug/log_tail` API
  - 設定頁有「Debug log」開關（3.7 偵錯區）
- **圖表精簡**：原本每接點 3 條線（temp/rate/avg）→ 只保留 20 條溫度線
  - rate / avg 仍由後端推播，前端只用於「最新讀值」表格
  - 圖例 `display: false`（接點顯示/隱藏全交由設定頁管理）
  - 移除主畫面頂部 [保存]、圖表標題、左下角說明框
- **X 軸範圍動態**：`settings.chart_x_minutes`（0 = 全部資料，>0 = 近 N 分鐘）
  - 主畫面新增 X 軸下拉選單（全部 / 15 / 30 / 1時 / 3時 / 6時 / 12時 / 1天）
  - Chart.js time scale 動態錨點，每 tick 滑動避免有效區間縮小
- **CSV 中文檔名 latin-1 修正**：`Content-Disposition` 改 ASCII + `filename*=UTF-8''` 雙路徑

### 1.4 v4.1 — 圖表切換 hotfix + OTA 部署通道

**Bug 修正（`static/js/main.js` v4 hotfix）**：
- 切 X 軸視窗後圖表空白 / X 軸縮成毫秒級：切換時清空 dataset 並重新拉歷史（`patchSettingAndApply` 內 `loadGen += 1` + `await loadHistory`）
- 切主題後渲染錯亂：MutationObserver 用 `requestAnimationFrame` 排隊，銷毀前先 `chart.stop()`
- 切站點後表格空白 / 資料停在舊時間：`loadHistory` 與 `switchStation` 用 `loadGen` 世代號保護
- `pruneOldData` 改為「每條線至少保留 1 點」，避免 Chart.js time scale 在空 dataset 時退化到毫秒
- Chart 加上 `normalized: true` 與 `ticks.source: "auto"`，明確指定資料格式

**OTA 部署通道（`ota.py` / `ota_push.py` / `ota_watchdog.bat`）**：
- `GET  /api/admin/status` 查狀態
- `POST /api/admin/ota` 推單檔（multipart）
- `POST /api/admin/ota_bundle` 一次推多檔（JSON + base64）
- `POST /api/admin/restart` 觸發自我重啟
- Token 認證：環境變數 / 檔案 / 自動產生
- 白名單：限縮 `static/js/`、`static/css/`、`templates/`、核心 `.py`
- 寫入前自動備份到 `config/ota_backup/<timestamp>/`
- `ota_watchdog.bat` 包住 `python app.py`，崩潰或被 OTA 重啟時自動再起

詳細流程見 [docs/DEPLOY_OTA.md](docs/DEPLOY_OTA.md)。

### 1.5 v6.1 — 進階計算：游標模式

> 設計過程詳見 [docs/CURSOR_MODE.md](docs/CURSOR_MODE.md)

**新增功能**：
- 右側「最新讀值」標題旁多一組 toggle button（即時狀態 / 量測狀態）
- 量測狀態下圖表出現 **綠 / 紅兩條可拖曳垂直線**（x-bar）
- 淡黃色 highlight 標示選取區間
- 拖曳即時更新表格：區間內的 **平均 / 最大 / 最小**
- 區間資訊列顯示起訖時間與 duration

**設計決策摘要**：
- 切換按鈕：Toggle（互斥）
- 計算來源：前端 LTTB 資料點（拖曳需 0 延遲）
- 分母：區間內實際筆數（沿用 v6 avg 原則，不補 0）
- 游標線預設位置：1/3 / 2/3
- 切換工位 → 強制回即時狀態
- 切換 X 軸 → 游標重置、模式保留
- 「計算」按鈕 → 取消（拖曳即更新）
- 即時狀態下游標線 → 全部隱藏

---

## 2. 現況進度（2026-06-11 session）

本節記錄 2026-06-11 當日二寶協作發現的 bug、修法、以及進行中的工作。

### 2.1 已修好的 Bug（v4 系列）

實機觀察到三個**切換導致圖表錯亂**的 bug，已在 2026-06-11 全部修好並 OTA 推送。

| # | Bug | 觸發情境 | 修法版本 | 驗證 |
|---|-----|---------|---------|------|
| 1 | X 軸切換後圖表空白 | 把 X 軸從「全部」切到「3 小時」 | v4.1 | ✅ Playwright 自動驗證 |
| 2 | 切主題後線條消失 | 按 ☀/🌙 切到 dark / 切回 light | v4.2 | ✅ Playwright 自動驗證 |
| 3 | 切工位後表格 0~10 秒空白 | 切換工位後右側「最新讀值」空白到下一輪 socket 推播 | v4.3 | ✅ Playwright 自動驗證 |

**v4.1 重點**（X 軸切換）：
- `patchSettingAndApply` 切 X 軸時改用 `rebuildChart()` 取代「清空 dataset + update」
- 因為 Chart.js time scale 的 min/max 是 chart 物件初始化時計算的，後續改 `options.scales.x.min` 在空 dataset 狀態下會讓 scale 退化到毫秒級
- `buildChart` 建好後立即把 min/max 寫進 `chart.options.scales.x`
- `loadHistory` 拉完後再強制設一次 min/max 避免 Chart.js 用舊錨點
- `pruneOldData` 從「至少 1 點」改為「至少 2 點」（起點 + 終點才有線）

**v4.2 重點**（主題切換）：
- 之前切主題觸發 `rebuildChart()`，會清空 dataset 但沒重拉資料，導致線條不見
- 改成只改 chart 顏色（不重建）
- 新增 `applyThemeToChart()`，只更新 `chart.options.scales.*.ticks.color` 與 `grid.color`
- MutationObserver 改呼叫 `applyThemeToChart()` 而非 `rebuildChart()`

**v4.3 重點**（切工位表格立即更新）：
- 之前 `switchStation` 跑完只靠 socket 推播更新表格，10 秒一輪可能讓表格空白
- 後端 `app.py` 的 `/api/latest/<station>` 擴充為回傳完整 `new_sample` payload（含 temps / rate / avg）
- 前端 `switchStation` 跑完後立即呼叫 `/api/latest`，拿最新一筆填表格
- 視覺表現：切工位後 < 1 秒就看到完整 20 列讀值

### 2.2 Watch Dog 穩定性（v4.4 已上線 ✅）

**問題**：v4.1 / v4.2 / v4.3 三次 OTA 重啟後，watch dog 都没撐住重啟 Flask，每次都要請大大手動重啟。

**根因**：舊 `ota.schedule_restart` 用 `subprocess.Popen` spawn 一個新 `python app.py` 自己跑，**撞 watch dog 內同一個 `python app.py` → port 5000 佔用衝突**，且舊 watch dog 沒有日誌，難以事後排查。

**修法（v4.4）**：
- `ota.py.schedule_restart` 改為「只讓主進程退出」，**不自己 spawn 新 Flask**（避免跟 watch dog 撞 port 5000）
- `ota_watchdog.bat` 升級到 v2：
  - 自動偵測 python 絕對路徑（不依賴 PATH）
  - 失敗時自動 fallback 常見安裝位置
  - 所有事件寫到 `logs\watchdog.log` 便於事後診斷
  - `python --version` 預先驗證 executable 可用
  - 連續失敗 5 次才放手（給人接手，不無限循環）
- 新增 `start_forever.bat`：用 `start /B /MIN` 把 watch dog 開在背景，**cmd 視窗關掉不影響 watch dog**
- `ota.py` 白名單加入 `start_forever.bat`
- `/api/admin/status` 加入 `uptime_seconds` 與 `ota_version: 2`

**驗證結果**（2026-06-11 20:13）：

| 驗證項 | 結果 |
| ------ | ---- |
| Flask 連線 | ✅ 5/5 HTTP 200 |
| Watch dog 接手（單次 OTA 重啟） | ✅ 6 秒內 |
| Watch dog 接手（連推 3 次 OTA） | ✅ 3/3 成功 |
| 連推後所有切換（X 軸 / 主題 / 工位） | ✅ 表格 20 列無錯 |

### 2.3 待辦：功能改善（Sprint 1）

v4 系列 bug 修完後，原本提的三大需求進入實作階段。

| 需求 | 內容 | 預估 | 狀態 |
|------|------|------|------|
| 加速理解的指標/統計 | 圖表極值標註、Y 軸參考線、統計摘要卡（最高/最低/平均/最大溫差）、表格新欄位（趨勢箭頭、視窗 Δ / min/max、距上次變化） | 中 | 待開工 |
| 介面觀看便利性 | 雙 Y 軸、十字游標同步 tooltip、快捷縮放 / 框選放大、表格排序 / 凍結 / 快速過濾、點表格行高亮圖表、Sparkline 縮圖 | 中 | 待開工 |
| 更詳細的設定參數 | 通道門檻（高/低溫 + cell 閃爍）、警報 toast、群組、顯示細節（線寬/點大小/平滑度/小數位）、Y 軸自動縮放 | 中 | 待開工 |

詳細子項見 [docs/IMPROVEMENTS.md](docs/IMPROVEMENTS.md)。

### 2.4 環境與工作流摘要

| 角色 | 主機 | 路徑 | 備註 |
|------|------|------|------|
| 開發端 | WSL（<DEV_HOST>） | `<DEV_PROJECT_DIR>` | 二寶改檔的起點 |
| 部署端 | Windows <DEPLOY_HOST> | `<DEPLOY_PATH>` | 跑 python app.py |
| 同步通道 | OTA（HTTP） | `POST /api/admin/*` 帶 `X-OTA-Token` header | 兩端都是 Windows 跑 Python，但沒 OneDrive 同步 |

**二寶工具鏈**（在 WSL 端）：
- 程式碼：直接編輯開發端 `D:\OneDrive\...` 下的檔案
- OTA 推送：`python3 ota_push.py push http://<DEPLOY_HOST>:5000 <local> <target> --restart`
- 多檔推送：`python3 ota_push.py bundle http://<DEPLOY_HOST>:5000 ota_manifest.json`
- 自動驗證：Playwright headless Chromium，跑三個切換情境 + 截圖 + 像素分析

**Token 管理**：
- 部署端 Flask 第一次啟動時自動產生 32 byte token → 寫入 `<DEPLOY_PATH>\config\ota_token`
- 二寶的開發端用對應 token 寫入 `D:\OneDrive\...\config\ota_token`（本機路徑，**不進版控**）
- 指紋（`sha256[:8]`）可在 `/api/admin/status` 看到，用於對照

### 2.5 版本號對照（2026-06-11 20:13 快照）

| 部署端檔案 | 版本 | 對應 commit / 階段 | 狀態 |
|-----------|------|-------------------|------|
| `static/js/main.js` | v4.3 | 切工位立即 fetch /api/latest | ✅ 已上線 |
| `app.py` | v4.3 | `/api/latest/<station>` 回 new_sample 格式 | ✅ 已上線 |
| `ota.py` | **v4.4** | schedule_restart 改不 spawn、加入 uptime / ota_version: 2 | ✅ 已上線 |
| `ota_watchdog.bat` | **v2** | 自動找 python + log + 連敗 5 次放手 | ✅ 已上線 |
| `start_forever.bat` | **v1** | 背景啟動 watch dog（`start /B /MIN`） | ✅ 已上線 |
| `ota_push.py` | **v4.4** | CLI push / bundle / restart / status | ✅ 已上線 |
| `templates/index.html` | `?v=5` | 瀏覽器 cache busting | ✅ 已上線 |
| `static/css/style.css` | `?v=5` | 瀏覽器 cache busting | ✅ 已上線 |

---

## 3. 現況進度（2026-06-12 進階計算上線）

本節記錄 2026-06-12 v6.1 進階計算（游標模式）的設計、實作、迭代。

### 3.1 需求背景

參考其他工業監控軟體（如 A&D AD-1687 / HOBOware）設計「即時 / 游標」切換：
- **即時狀態**（Live）：預設，表格顯示最新讀值
- **量測狀態**（Cursor）：表格顯示游標區間內的統計值（平均 / 最大 / 最小）

提供「拖曳即計算」的即時互動，不需按「計算」按鈕。

### 3.2 設計決策

| 決策項 | 選擇 | 理由 |
|--------|------|------|
| 切換按鈕 | Toggle（互斥） | 兩個狀態互不重疊 |
| 計算來源 | 前端 LTTB 資料點 | 拖曳需 0 延遲 |
| 分母 | 區間內實際筆數 | 沿用 v6 avg 原則（不補 0） |
| 游標線預設位置 | 1/3 / 2/3 | 確保在 X 軸可見範圍內 |
| 切換工位 | 強制回即時狀態 | 不跨工位保留狀態 |
| 切換 X 軸 | 游標重置、模式保留 | 換視窗但「量測中」的語意保留 |
| 「計算」按鈕 | **取消** | 拖曳即更新，不需手動觸發 |
| 即時狀態下游標線 | **全部隱藏** | 避免 UI 雜訊 |

### 3.3 迭代歷史（8 個 commit 4 個小版本）

| 版本 | commit | 修改 |
|------|--------|------|
| v6.1 | 4d2d540 | 初版：toggle、游標拖曳、表格平均/最大/最小、debounce API 查覆蓋率 |
| v6.1.1 | 7967bcd | 修正：切換工位 / X 軸時游標線位置停在舊時間點（清空 tsLeft/tsRight、預設 1/3 / 2/3 取代 25% / 75%） |
| v6.1.2 | da57fa1 | 修正：拖曳時「區間」資訊列沒更新（onMove 漏加 updateCursorInfo()） |
| v6.1.3 | f4eaa28 | 修正：移除「資料覆蓋」整列 UI（語意不清，拖曳時跳動造成誤判） |
| v6.1.4 | 92968ae | 清理：移除 v6.1 殘留的 /api/cursor/coverage endpoint 與 storage.query_count_in_range 函式（-101 +30 行） |

### 3.4 v6.1.3 移除「資料覆蓋」的原因

觸發事件：OTA 端實測發現，拖曳游標線在斷線區間內移動時，「資料覆蓋：xx 筆 / 預期 yy 筆 (zz%)」的數字會跳動。

語意問題：
- **預期筆數** = 區間秒數 / poll 週期（會隨區間長度成比例縮放）
- **實際筆數** 在斷線區間內變動幅度小
- 結果：pct 跳動明顯 → 使用者誤以為資料有問題

無論用方案 A（前端 LTTB 推算，誤差 ±10%）還是方案 B（後端 SQLite 查詢，準但需 debounce）都解決不了「斷線中拖曳 → 預期變動」的語意問題。使用者決策：若不影響計算的正確性，**移除不用顯示**。

### 3.5 設計文件與迭代記錄

完整設計過程（兩個方案的優缺點比較、最終決策、未實作原因）記錄於：
- [docs/CURSOR_MODE.md](docs/CURSOR_MODE.md)：設計文件（375 行）

MEMORY.md 也記下了「使用者決策偏好」，供未來類似需求參考。

### 3.6 版本號對照（2026-06-12 23:47 快照）

| 部署端檔案 | 版本 | 對應 commit | 狀態 |
|-----------|------|-------------|------|
| `app.py` | v6.1.4 | 92968ae | ✅ 已上線（待推送） |
| `storage.py` | v6.1.4 | 92968ae | ✅ 已上線（待推送） |
| `static/js/main.js` | v6.1.3 | f4eaa28 | ✅ 已上線 |
| `static/css/style.css` | v6.1.3 | f4eaa28 | ✅ 已上線 |
| `templates/index.html` | v6.1.3 | f4eaa28 | ✅ 已上線 |
| `templates/index.html` | `?v=6` | 瀏覽器 cache busting | ✅ 已上線 |
| `static/css/style.css` | `?v=6` | 瀏覽器 cache busting | ✅ 已上線 |
| `static/js/main.js` | `?v=6` | 瀏覽器 cache busting | ✅ 已上線 |
| `docs/CURSOR_MODE.md` | v6.1.4 | 92968ae | 📁 本機 + GitHub（OTA 白名單不含 docs/，推不上去） |

---

## 4. 現況進度（2026-06-15 PW3335 電力計上線）

### 4.1 背景

桌機版 `kalapontsai/GX20-PW3335-Data-Collection` 已能用 GW Instek PW3335 讀電壓/電流/功率。
網頁版只接了 GX20 溫度，缺電力資料。本次任務把 PW3335 整合進來。

### 4.2 通訊協定（與桌機版一致）

- TCP 連線到 `<ip>:<port>`（預設 3300）
- 送出指令 `b':MEAS? U,I,P,WH\\n'`
- 回應 `U +110.14E+0;I +0.0000E+0;P +000.00E+0;WP +00.0000E+0`（4 段 `;` 分隔）
- 只取前三段（U / I / P）→ 對應 V / I / W；WP（累積 Wh）丟棄
- 解析失敗或連線錯誤 → 回傳 `(0, 0, 0, False)`，不丟例外

### 4.3 設計決策

| 決策項 | 選擇 | 理由 |
|--------|------|------|
| 連線方式 | 每輪 / 每工位一條 socket | 設定可熱改（IP/port/remote）；故障復原簡單 |
| 預設 IP | `192.168.1.{2..7}` | 沿用桌機版 `GX20_PW3335.py` line 884 對應規則（工位1→.2 ... 工位6→.7） |
| 預設 `remote` | 全 False | 避免第一次啟動就連一堆失敗的 PW3335；使用者到設定頁打勾才啟用 |
| 預設 V/I,W Y軸 | V(0,230) / I,W(0,250) | 依使用者 2026-06-15 決定 |
| 電力 DB 欄位 | `v REAL, i REAL, w REAL` | 跟 `t01~t20` 同檔；nullable 兼容舊 DB |
| DB 升級 | `PRAGMA table_info` + `ALTER TABLE ADD COLUMN` | 舊 DB 自動補欄，無痛升級 |
| CSV 補欄 | `V, I, W` 三欄 | 依使用者需求命名（不用桌機版 U/V/A/P/W/WP） |
| 電力 CSV 精度 | V 2 / I 3 / W 2 | 沿用桌機版（V 跟 W 同精細度、I 較精細） |
| 電力圖表 Y 軸 | 左 I/W 共用、右 V 獨立 | 依使用者需求 |
| 電力圖表高度 | 30% | 溫度 70%（依使用者需求） |
| 電力圖表量測模式 | 隱藏 | 沿用「輔助 UI 在即時模式才顯示」偏好 |
| 電力線顏色 | 預設 V=黃 / I=青 / W=紅 | 避開溫度 20 色；可設定頁改 |
| 量測模式電力表 | 改顯示 平均/最大/最小 | 跟溫度表同行為 |
| 桌面版 `Debug_mode` 模擬值 | **不做** | 使用者明確要求 `remote=False` 寫 0 即可，不要假資料 |

### 4.4 迭代歷史（5 個 commit）

| commit | 修改 |
|--------|------|
| `f8e0d48` | feat(pw3335): 新增 pw3335_reader.py + config.py 預設值擴充 |
| `7002104` | feat(pw3335): storage 加 v/i/w 欄位 + app.py poller 整合（+ /api/pw_connection） |
| `522b860` | feat(csv): 匯出 CSV 補 V/I/W 三欄 |
| `f939bdb` | feat(ui): 主畫面雙圖表 (70/30) + 電力表格 + 設定頁 PW3335 區塊 |
| (本檔) | docs: CHANGELOG / README / example 設定補 v7 |

### 4.5 新 API

| 端點 | 用途 |
|------|------|
| `GET /api/pw_connection` | 6 工位 PW3335 連線狀態 (remote/connected/host/last_error/last_vip) |

### 4.6 DB Schema 變更（向後相容）

`samples` 表新增三欄（v7 之後新建的 DB 自動包含；舊 DB 啟動時自動 ALTER）：

```sql
ALTER TABLE samples ADD COLUMN v REAL;
ALTER TABLE samples ADD COLUMN i REAL;
ALTER TABLE samples ADD COLUMN w REAL;
```

`storage._ensure_power_columns()` 用 `PRAGMA table_info(samples)` 偵測缺欄就 ALTER。

---

## 5. 現況進度（2026-06-16 CSV BOM hotfix）

### 5.1 背景

大在設定頁把溫度別名設成中文（如 `TC1-冷凝器入口`），下載 CSV 用 Excel 開啟後看到 `TC1-?入?` 這種「?」亂碼。且別名輸入框無長度限制，超長中文會撐破表格。

### 5.2 根因

`/api/export_csv` 在 Flask `Response` 送了 `Content-Length` header：

```python
csv_text = "\ufeffdatetime,..."   # 字串內已含 BOM 字元
"Content-Length": str(len(csv_text.encode("utf-8-sig")))   # ⚠️ 會再 prepend BOM
```

`utf-8-sig` 對**任何**字串都會 prepend 一個 BOM。`csv_text` 內部已有 BOM 字串 `\ufeff` → encode 後 body 被多加 3 byte BOM → `Content-Length` 比實際 body 多 3 byte → 瀏覽器讀到 N-3 byte 就關連線 → 結尾的 UTF-8 多 byte 字被切壞 → Excel 解成 `?`。

（順帶：`Content-Type` header 出現 `text/csv; charset=utf-8; charset=utf-8` 重複，是 Flask `Response(..., mimetype="text/csv; charset=utf-8")` 雙重送 header 造成。）

### 5.3 修法

**`app.py` /api/export_csv**：
```python
# 修法：字串內已含 BOM，用 utf-8 算出來才是實際 body 位元組數
body_bytes = csv_text.encode("utf-8")
return Response(
    body_bytes,
    mimetype="text/csv",   # 不再手動帶 charset，避免 header 重複
    headers={
        "Content-Disposition": ...,
        "Content-Length": str(len(body_bytes)),
    },
)
```

**`app.py` _sanitize_csv_cell**（別名長度防線）：
```python
if len(s) > 20:
    s = s[:20]
```

**`static/js/settings.js`**（別名輸入框 maxlength）：
```javascript
aliasInp.maxLength = 20;
```

### 5.4 驗收（OTA 端 `<DEPLOY_HOST>:5000`）

| 項 | 結果 |
|----|------|
| BOM 開頭 | `efbbbf` ✓ |
| actual bytes (743) == Content-Length (743) | ✓（修法前差 3 byte）|
| Content-Type | `text/csv; charset=utf-8`（單一、不再重複）✓ |
| 30 字中文別名 → server 端截到 20 字 | ✓ |
| log tail ERROR 計數 | 0 ✓ |
| 6 工位 `emit new_sample` | 正常 ✓ |

### 5.5 迭代歷史（1 個 commit，2 檔）

| 檔 | 修改 |
|----|------|
| `app.py` | `/api/export_csv` 改用 `utf-8` 算 Content-Length + `mimetype="text/csv"`；`_sanitize_csv_cell` 加 20 字截斷 |
| `static/js/settings.js` | 動態生成的別名 input 加 `maxLength=20` |

### 5.6 教訓

BOM / 編碼相關的 `Content-Length` 永遠用對應 body 的編碼算，不要看字串 encode 怎樣就照抄 `utf-8-sig`。這跟 v7 ring tuple unpack 慘案（commit `2b8de47`）同類——改編碼/序列化/結構的 commit 必跑 repro script 灌真實 shape 跑一次關鍵函式，**只 `py_compile` 抓不到這種語意錯誤**。

### 5.7 設計決策：別名上限 20 字

- 圖表 legend 與「最新讀值」表格的 channel 名稱排成一行，>20 撐破版
- 後端 `_sanitize_csv_cell` 是最後一道防線（避免有人手動 POST 繞過 UI）
- UI `maxLength` 是第一道防線（輸入時就擋，體感比送出後才被截好）

---

## 6. 現況進度（2026-06-16 設定同步 v8.1/v8.1.1/v8.1.2）

### 6.1 背景

2026-06-16 一個工作日內連續迭代三個小版本。起因：

- v7 及之前版本「保存」按鈕才 POST server → 主畫面三 select (X軸/速率/平均) onChange 只寫 sessionStorage 不 POST → 關掉分頁就消失；其他瀏覽器/電腦也看不到
- 嘗試修 → 連踩兩個坑（v8.1.1 跨工位污染、v8.1.1 自我污染）→ 最終採「遠端鎖死」根治

### 6.2 演進時間軸

| 序 | 版本 | 重點 | 狀態 |
|---|------|------|------|
| 1 | v8.1 | 任何 update() 結尾 debounce 300ms 自動 POST server | 保留 |
| 2 | v8.1.1 | init() 結尾掃 sessionStorage 殘留並自動同步 server | **拿掉**（污染問題）|
| 3 | v8.1.2 | 拿掉 v8.1.1 + server 端 IP 鎖 + UI 鎖 | 保留 |

### 6.3 v8.1 — debounce auto-save

**根因**：
- `patchSettingAndApply`（主畫面三 select 用）只做 `GX20State.update(key, value)` → 寫 sessionStorage + 標 dirty，**沒 POST server**
- `patchSettingAndApply` 結尾原本的舊 `fetch POST` 是 PATCH 語意（單欄位），會覆蓋 server 端其他欄位

**修法**：
- `storage.js` 加 `_scheduleSave()`：300ms debounce，呼叫 `GX20State.save()`
- `update()` 結尾加 `_scheduleSave()` → 任何 update 都自動同步 server
- `setTheme()` **不**觸發 save（主題純 UI，不污染 server 端設定）
- save 失敗不擋 UI（console.warn），client sessionStorage 還有值下次重試
- `main.js` 拿掉 `patchSettingAndApply` 結尾的舊 `fetch`（跟 debounce 重複觸發）

**線上驗收（Playwright E2E 連真 OTA）**：
- 改 X 軸 1 次 → 1 POST（chart_x_minutes=180 完整 payload）✓
- 連改 avg 5 次 → 1 POST（debounce 真的 debounce 了）✓
- 設定頁改別名 → 1 POST（含完整中文別名 `TC1-冷凝器入口-AAA`）✓
- 共 3 個 POST，沒有重複

### 6.4 v8.1.1 — 自動 migrate legacy session（⚠️ 已知有 bug，已被 v8.1.2 拿掉）

**目標**：v8.1 之前使用者的修改只寫 sessionStorage、沒 POST server。殘留值卡在「那台瀏覽器」的 sessionStorage 內。
- 那台瀏覽器**重開**時：init() 拉 server baseline（預設）→ 合併 sessionStorage（殘留）→ 畫面顯示殘留
- **別台瀏覽器**（例如本地端）打開同 URL：拉 server baseline → 沒有殘留 → 顯示預設

**修法（v8.1.1）**：
- `storage.js` init() 結尾加 `_migrateLegacySessionIfNeeded()`：
  - 掃 sess 內所有非 `_saved/theme` 的 key
  - 比對 server baseline，列出有 drift 的 key
  - 有 drift → 自動 `_scheduleSave()` 一次（陣地轉移：殘留值 → server 端 source of truth）
  - 沒 drift → 不觸發（節省 request）

**線上驗收（v8.1.1 部署時 Playwright E2E 通過）**：
- 注入殘留 `chart_x_minutes=180, ch_alias 工位4[0]=TC1-冷凝器入口` → reload → 1 POST
- payload 完整保留中文別名 ✓
- server 端 log 收到 `POST /api/settings` 200 ✓
- 清殘留再 reload → 0 POST（不會誤觸）✓

### 6.5 v8.1.1 引入的 bug（跨工位污染）

**症狀**：大大在 <USER_MACHINE_IP> 開瀏覽器連 OTA 主機看到「工位 1 有改變，但那是工位 4 的，而且也非正確複製」

**根因**（v8.1.1 的副作用）：
- `storage.js` merge 邏輯：`this.settings.ch_alias = sess.ch_alias`（整個 dict 替換）
- 假設 sess 內 `ch_alias` 只有工位 4 → settings.ch_alias 也只剩工位 4
- `_scheduleSave()` 觸發整包 POST with `ch_alias: { "工位4": [...] }`
- server `save_settings` 雖然是「逐工位 merge」，但 `for st in v.items():` 只 set 出現在 v 內的工位 → **其他工位不動**
- **等等，那為什麼工位 1 被改了？**

**真正的時序**（debug log 推導）：
- 大大 OTA 主機的瀏覽器（<OTA_HOST_IP>）sessionStorage 內存了 E2E 注入的 6 工位別名殘留
- 11:51:27 v8.1.1 部署後大大開瀏覽器 → `_migrateLegacySessionIfNeeded()` 比對 6 工位都 drift → 整包 POST
- 但 v8.1.1 之前我 E2E 測試 + 直接 curl 模擬本地端時 POST 過 `ch_alias: {工位1: [中文], ..., 工位6: [中文]}`，**這些值在 server 端還在**
- 11:47:33 我 E2E 後還原 `ch_alias: 6 工位都 Ch01`，server 確實改了
- **但** 11:51:27 那次大大瀏覽器觸發的 POST `ch_alias` 內 6 工位都還在（從 init() merge 拿的 baseline）→ server merge 6 工位**全部**被 set
- 11:51:27 那次 POST 的 payload 工位 1~5 帶的是 v8 E2E 注入的中文別名（**不是 Ch01**），**因** v8 期間 E2E 注入時已經把 6 工位都寫中文

**結論**：v8.1.1 的 `_migrateLegacySessionIfNeeded()` 機制本身對，**但**：
- 觸發條件太寬（任何 drift 都觸發）
- merge 邏輯會把 sess 內沒有的工位「弄丟」
- 整包 POST 出去對 server 來說是「請把這 6 個值蓋上去」（雖然是逐工位 merge，但 payload 不齊就糟）

### 6.6 v8.1.2 — 遠端瀏覽器鎖死（根治方案）

**決策（大大）**：
> 遠端瀏覽的視窗，移除可開啟設定，變更設定的權限，只有 OTA 本機的瀏覽器可進行設定。遠端一律讀取同一份設定檔。

**判定**：以 `127.0.0.1` 連線為準，不考慮 NAT 情形。

**修法**：

**`app.py`**：
- `REMOTE_WRITE_ALLOWED_IPS = ("127.0.0.1", "::1")`
- `_is_local_request()` 判定 `request.remote_addr`
- `GET /api/settings` response 加 `is_local` 欄位
- `POST /api/settings` 遠端 → **403 + `{"error":"readonly", "message":"設定變更權限僅限 OTA 本機瀏覽器，遠端只能讀取。"}`**

**`static/js/storage.js`**：
- 拿掉 v8.1.1 的 `_migrateLegacySessionIfNeeded()`（不再 migrate 殘留）
- init() 內 `this.isLocal = srv.is_local !== false`（預設 true 防呆）

**`static/js/main.js`**：
- 新增 `applyRemoteUiLocks()`：遠端時「設定」按鈕 `display: none`、主畫面三 select `disabled = true`
- init() 結尾呼叫

**`templates/index.html`**：
- 「設定」按鈕加 `id="settingsBtn"` 方便 JS 找

### 6.7 v8.1.2 線上驗收

**Case A：WSL 連 OTA（視為遠端）** — Playwright E2E
- `GX20State.isLocal = False` ✓
- 設定按鈕 `display: none` ✓
- 三 select `disabled: [True, True, True]` ✓
- 遠端 POST `/api/settings` → 403 + 明確訊息 ✓
- Server 端設定**沒被改** ✓

**Case B：本機端（127.0.0.1:5000）** — 待大大在 OTA 主機手動點測（WSL 容器無法模擬本機）

### 6.8 教訓（v8.1 / v8.1.1 / v8.1.2 三條）

1. **「同步殘留」策略本質危險**：v8.1.1 migrate 把瀏覽器 local 殘留強寫 server，跨結構覆蓋難控制
2. **「單純權限分層」更安全**：遠端鎖死讀，本機才能寫，server 是 single source of truth
3. **兩個並存的「寫 server」路徑會重複觸發**：v8.1 修法之前 `patchSettingAndApply` 同時有「舊 fetch POST」跟「舊 update 不 POST」兩個 bug 點。決定要 auto-save 就只留一條
4. **PATCH 語意（POST 單一欄位）會覆蓋其他欄位的風險**：寧可全包 POST，不要單欄 PATCH（除非 server 有 partial-update 設計）
5. **跨機器協作時**：把「誰能改」講清楚，比把「怎麼同步」做漂亮更根本
6. **E2E 一定要用 headless browser 跑真實 DOM**：mock 抓不到 `patchSettingAndApply` 內的雙重路徑，也抓不到 E2E 注入資料污染 production
7. **測試時的副作用**：E2E 注入的測試髒資料會被保留到下次 production 部署，要嘛 E2E 跑本機 mock server（不起來因為要 GX20 連線），要嘛收尾用 admin endpoint 還原

### 6.9 仍需處理（v8.1.2 部署後遺留）

- v8 / v8.1.1 E2E 注入留下的工位 1~5 中文別名（被 E2E 殘留覆蓋）— **現在遠端 POST 都被 403 擋，必須大大去 OTA 主機（<DEPLOY_HOST>）開 127.0.0.1:5000 改回 Ch01~Ch20**
- `POST /api/clear`（清除此工位）**未鎖遠端** — 危險操作，**v8.1.3 已鎖** ✓

### 6.10 v8.1.3 — POST /api/clear 也鎖遠端

**決策（大大）**：`POST /api/clear`（清除此工位）也跟設定一樣鎖本機。

**根因**：v8.1.2 只鎖 `POST /api/settings`，但「清除此工位」是**不可逆操作**（即使有 archive 也只是防呆），危險等級比改設定更高。少鎖一個端點，攻擊面就有缺口。

**修法**：

**`app.py` `POST /api/clear`**：handler 開頭加 `if not _is_local_request(): return 403 + readonly 訊息`，跟 `/api/settings` 共用同一個 `_is_local_request()` 函式。

**`static/js/main.js` `applyRemoteUiLocks()`**：多隱藏「清除此工位」按鈕，跟「設定」按鈕一起藏。

**線上驗收（WSL 連 OTA，視為遠端）**：
- isLocal = False ✓
- 設定按鈕 `display: none` ✓
- **清除按鈕 `display: none` ✓**（新）
- 遠端 `POST /api/clear` → 403 + 「清除資料權限僅限 OTA 本機瀏覽器，遠端不能清。」 ✓
- 三 select `disabled: [True, True, True]` ✓

**教訓**：
- **危險等級評估要一致**：所有「寫 server」端點都要用同一把鎖
- **「可逆 vs 不可逆」是重要分類**：改設定可 undo，清資料不行。不可逆操作要更嚴格的存取控制
- **統一抽象**：把 IP 檢查抽成 `_is_local_request()` 函式，未來加任何危險端點只要一行 `if not _is_local_request(): 403`

---

## 7. 現況進度（2026-06-16 X 軸寬度對齊 v8.2）

### 7.1 v8.2 — 溫度與電力 X 軸寬度對齊

**背景**：電力圖有 3 條 Y 軸（yI/yW 左、yV 右）佔用空間比溫度圖的單 Y 軸多，Chart.js 預設各自依 tick 寬度算 chartArea，造成兩圖 X 軸寬度對不上、時間軸刻度錯位，無法直接比對溫度與電力的時間序列。

**決策（大大）**：

1. 兩圖 X 軸寬度一致 → 溫度圖 chartArea 寬度 = 電力圖 chartArea 寬度
2. 電力 Y 軸寬度優先（多軸佔位是必要成本）
3. 溫度圖左側多出空間 → 留白，可接受

**修法**（`static/js/main.js`）：

- 新增 `alignTempChartToPowerYAxis()`：量測電力圖 3 條 Y 軸實際 width，透過溫度圖的 `afterFit` hook 動態設定 `scales.y.width`（左軸 = yI + yW）和加一個**隱藏的右軸 yR**（width = yV），讓 Chart.js 算出的 chartArea 寬度自然與電力圖一致
- **關鍵設計**：不能用 `options.scales.y.width = N` + `chart.update()` 的寫法，因為 Chart.js v4 會 cache 算出來的 `scale._width`，options.width 只在「建構 + 第一次 layout」被讀到。必須用 `afterFit` hook 才能在每次 layout 強制覆寫
- 觸發點（6 處）：
  - `init()` 後 rAF（解決 build 順序問題：溫度先建 / 電力先建都涵蓋）
  - `switchStation()` 後 rAF
  - `loadHistory()` 結尾（電力圖 yW width 需用「實際資料範圍」算 → 拉完歷史才能精準對齊）
  - `setCursorMode("live")` 切回即時狀態後 rAF（電力圖 display 恢復需要 1 幀）
  - `applyThemeToPwChart()` 主題切換後（tick 文字渲染寬度可能變）
  - `ResizeObserver` 監聽 `.chart-area` 主容器 resize

**線上驗收（Playwright E2E）**：

| 場景 | 溫度 chartArea 寬 | 電力 chartArea 寬 | 差 |
|---|---|---|---|
| LIVE 模式（首頁載入 0.8s 後） | 1000.1px | 1000.1px | **0.00px** ✓ |
| CURSOR 模式（電力圖隱藏） | 1091.6px | (隱藏) | 溫度圖 100% 寬 ✓ |
| 切回 LIVE 後 | 1000.1px | 1000.1px | **0.00px** ✓ |

**E2E 注意事項**：

- `display: false` 的 scale 在 Chart.js 4 會跳過 `afterFit`（scale._labelSizes 不算 → width=0 然後 layout 跑掉）。解法：scale `display: true` + `ticks.display: false` + `grid.drawOnChartArea: false` 達到「不畫」效果
- cursor 模式下不要動 `scale.width = undefined`（會讓 Chart.js 4 layout 出 NaN），用 `return` 跳過
- 電力圖必須**先 build**，否則 rAF 對齊時 `pwChart.scales.yI/yW/yV` 還沒 layout → 寬度為 0 對齊失敗。實作為 rAF 雙重保險（不論 build 順序都涵蓋）

**教訓**：

- **Chart.js 4 的 `chartArea` 寬度是「外寬扣 padding」**，跟 scale.width 沒直接關係。要讓兩圖 chartArea 寬度一致，必須**也模擬右軸佔位**（純左軸的圖 chartArea 寬度會比多軸的圖寬）
- **動態 width 必須用 `afterFit` hook**，不能靠 options 設值
- **「實際資料範圍」會改變 Y 軸寬度**（tick 文字位數變）→ loadHistory 完後一定要重對齊

---

## 8. 現況進度（2026-06-17 電力 I/W 軸對齊系列 v8.4 / v8.5 / v8.5.1）

### 8.1 v8.5.1 — powerMax 先 round 到 100 倍數（讓 Chart.js nice() 不再 round 兩軸）

**問題（大大回饋 2026-06-17 11:51）**：v8.5 推上線後大大說「**不是要對齊數值，而是軸刻度的最大值與最小值要在同一高度**」。

**根因**（v8.5 沒解決的部分）：Chart.js 內部會呼叫 d3-scale 的 `nice()`，把 `y.max` 自動 round 到 1/2/5 進位的「漂亮值」。v8.5 設 `yI.max = 1.9349` / `yW.max = 193.49`，Chart.js nice 出來：

- I 軸 step = 0.5 → ticks: 0, 0.5, 1.0, 1.5, **2.0**（不是 1.9！）
- W 軸 step = 50 → ticks: 0, 50, 100, 150, **200**（不是 193.5！）

兩軸頂端 max 標籤佔比 = 2.0/1.9349=1.034 跟 200/193.49=1.034，**理論上相等**——但**兩個 max 標籤都偏離 raw 數字**，圖上看起來像「1.9 跟 193.5」在錯位（其實是 2.0 跟 200，但視覺上兩個都偏離資料峰，感覺沒對齊）。

**決策**：

1. 自己實作 `niceToHundredStep()`，把 powerMax 先 round 到 100 的倍數（1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, ...）
2. `yW.max = nicePowerMax`、`yI.max = nicePowerMax / 100`
3. **兩個 raw max 都已經是「漂亮值」，Chart.js nice() 就不會再 round → 頂端 max 標籤完全等於 raw max → 兩軸佔比 100%/100% 絕對對齊**

**取捨**：

- 犧牲：資料峰值的「絕對位置」更保守（往上一檔）——例如工位5 從 1.9 → 2.0，193.5 → 200
- 換來：兩軸頂端 max 標籤的「刻度數值」剛好等於邊界，使用者看到「I 軸 2.0 / W 軸 200」這種**漂亮對齊數字**

**修法**（`static/js/main.js`）：

- 新增 `niceToHundredStep(value)`：d3-scale 風格的 1-2-5 進位 round
- `alignPowerYAxesToData()`：
  - 算 `powerMax = max(iPeak*100, wPeak) × 1.1`
  - `nicePowerMax = niceToHundredStep(powerMax)`
  - `yW.max = nicePowerMax`
  - `yI.max = nicePowerMax / 100`

**驗證（Node repro + Chart.js nice() 模擬，7 種 shape）**：

| 場景 | powerMax | nicePowerMax | yI.max | yW.max | Chart.js nice 後頂端 | 佔比 | 對齊 |
|------|----------|--------------|--------|--------|----------------------|------|------|
| 工位5 (i=1.73, w=175.9) | 193.49 | 200 | 2.0 | 200 | 2.0 / 200 | 1.0000 / 1.0000 | PASS |
| 低負載 (i=0.5, w=55) | 60.5 | 50 | 0.5 | 50 | 0.5 / 50 | 1.0000 / 1.0000 | PASS |
| 空資料 | 500 | 500 | 5 | 500 | 5 / 500 | 1.0000 / 1.0000 | PASS |
| V=125 浮動 (i=1, w=125) | 137.5 | 100 | 1.0 | 100 | 1.0 / 100 | 1.0000 / 1.0000 | PASS |
| 高負載 (i=2.5, w=240) | 275 | 200 | 2.0 | 200 | 2.0 / 200 | 1.0000 / 1.0000 | PASS |
| 極低 (i=0.3, w=33) | 36.3 | 50 | 0.5 | 50 | 0.5 / 50 | 1.0000 / 1.0000 | PASS |
| 中 (i=1, w=110) | 121 | 100 | 1.0 | 100 | 1.0 / 100 | 1.0000 / 1.0000 | PASS |

**教訓**：

- 「共用 max」還不夠，**還要讓 max 剛好是「nice 數字」**，Chart.js 才不會擅自 round 兩軸導致邊界錯位
- Chart.js 的 nice() 規則是 d3-scale 的 1-2-5 進位（1, 2, 5, 10, 20, 50...），軸設計者必須自己懂這套，**把 raw max 餵進去前先 round 一次**
- 對齊有兩層：(1) **raw 數字對齊**（v8.5 已解） (2) **刻度標籤對齊**（v8.5.1 解這層）——大大說的是第 (2) 層

### 8.2 v8.5 — 電力圖 I/W 軸邊界共用 powerMax（嚴格 100:1 對齊，已被 v8.5.1 取代）

**問題（大大回饋 2026-06-17）**：v8.4 上線後，電力圖左側 I 軸顯示到 1.9 A、W 軸顯示到 193.5 W，**兩條頂端線還是沒對齊**（比值 101.8，不是 100）。

**根因**：v8.4 用「**各自峰 × 1.1**」算 max，當即時 I/W 比例因電壓浮動偏離 100:1（例如 V=110→W/I=110，V=109→W/I=109），兩軸 max 算出來的比例就是「即時電壓」而不是 100，**兩軸頂端邊界永遠差幾個像素**。

**決策**：

1. **共用 powerMax (W 等價)**：`powerMax = max(iPeak*100, wPeak) × 1.1` → 取兩者等效成 W 後的最大值
2. **yW.max = powerMax**、**yI.max = powerMax / 100** → 嚴格 100:1 比例尺
3. 兩軸 max 永遠在同一條水平線，不論電壓怎麼浮動
4. min 仍由設定頁 i_min / w_min 控制（保留彈性）

**取捨說明**：

- 犧牲：當電壓 ≠ 110V 時，實際 I 峰與 W 峰在圖上的「絕對像素位置」會有偏差（例如 V=120 時 I 峰在 yI 上方但 W 峰還在中段）
- 換來：兩軸邊界 100% 對齊，使用者看到「同一條 0 線 / 同一條頂端線」，不再有 1.9/193.5=101.8 那種「軸錯位幾個像素」的小毛病

**修法**（`static/js/main.js`）：

- `alignPowerYAxesToData()`：
  - 改算 `iAsW = iPeak * 100` 跟 `wPeak` 兩者的 `max` × `HEAD_ROOM (1.1)` → `powerMax`
  - `yW.max = powerMax`
  - `yI.max = powerMax / 100`
- 無資料時 fallback：`powerMax = 500` → `yW.max = 500, yI.max = 5`（跟 v8.4 一致）

**驗證（Node repro 跑 5 種 shape）**：

| 場景 | iPeak | wPeak | powerMax | yI.max | yW.max | ratio 驗證 |
|------|-------|-------|----------|--------|--------|-----------|
| 工位5（圖中） | 1.73 | 175.9 | 193.5 | 1.935 | 193.5 | PASS（邊界絕對對齊） |
| 低負載 | 0.5 | 55 | 60.5 | 0.605 | 60.5 | PASS |
| 空資料 | 0 | 0 | 500 | 5 | 500 | PASS |
| V=125 浮動 | 1.0 | 125 | 137.5 | 1.375 | 137.5 | PASS（軸仍對齊） |
| 高負載 | 2.5 | 240 | 275 | 2.75 | 275 | PASS |

每一場景都驗算 `abs(yIMax - yWMax/100) < 1e-9` → 兩軸邊界嚴格重合。

**教訓**：

- v8.4 「各自峰 × 1.1」只在 V 嚴格等於 100V 時才剛好對齊，實務電壓浮動 ±5V 是常態 → 軸邊界一定會偏
- 兩個軸要 100% 對齊，**唯一保證是「共用比例尺」**（這裡 = 共用 powerMax → 嚴格 100:1）
- 「動態各算各的」是「視覺上對齊」≠「像素級對齊」，需求確認時要分清楚

### 8.3 v8.4 — 電力圖 I 軸與 W 軸的 0 與 max 位置對齊

**問題（大大回饋）**：電力圖左側兩個 Y 軸（I 電流、W 功率）預設 max 寫死 5/250，跟實際數據（I~2.7 A、W~200 W）不成比例，導致 I 線的 0 點跟 W 線的 0 點在視覺上不在同一條水平線上（雖都標 0，但佔的「垂直像素位置」不同）。兩個軸的「頂端」也錯位（I max 3.0 vs W max 200 對不上）。

**決策**：

1. I 軸與 W 軸的 max **永遠動態計算** = 當前可見資料的 I 峰 / W 峰 × 1.1（頂端預留 10% 空間）
2. 兩軸 min 沿用 `pw_axis` 設定（預設 0）
3. 兩軸的 max 比例尺 = 各自峰 × 1.1 → 0 與 max 在兩軸的「相對位置」完全相同 → 兩條 0 線與頂端線**重疊**
4. 設定頁的 I.max / W.max 欄位 → **永遠 disabled**（兩軸不可能用不同 max 還對齊）
5. 設定頁拿掉「自動縮放 I」「自動縮放 W」checkbox（這兩軸永遠視為 auto，由系統接管）
6. V 軸維持原樣（auto=true 自動、auto=false 手動 max），因為 V 是電壓、跟 I/W 沒成比例關係

**修法**（`static/js/main.js` + `static/js/settings.js` + `templates/settings.html`）：

- `static/js/main.js` 新增 `alignPowerYAxesToData()`：掃 `pwChart.data.datasets` 的 `pointKey === "i"` / `"w"` 兩條 dataset 的 y 值，取峰 × 1.1 寫進 `chart.options.scales.yI.max` / `yW.max`
- 觸發點（3 處）：
  - `buildPowerChart()` 結尾（build 完立即套用，無資料 fallback 到 5/250）
  - `prunePowerData()` 結尾（每筆 new sample 進來都重算）
  - `loadHistory()` 補完歷史後（用實際拉回的資料峰重算）
- `static/js/settings.js` `fillPwAxisFields` / `bindPwAxisFields` / `applyPwAxisAutoState`：
  - 拿掉 `iAutoEl` / `wAutoEl` 相關 query 與事件綁定
  - `writeBack` 不再寫入 `i_max` / `w_max` / `i_auto` / `w_auto`
  - I.max / W.max input 不再 bind input/change 事件（disabled 狀態不會觸發，但保險起見）
- `templates/settings.html` 2.5 節：
  - I.max / W.max 加上 `disabled style="opacity:0.5"`
  - 拿掉「自動縮放 I」「自動縮放 W」兩行 checkbox
  - 新增「自動對齊 I / W」disabled 說明列（標示 v8.4 行為）

**驗證（Node repro 跑真實 shape）**：

```
yI.max = 2.970 (期望 2.7 × 1.1 = 2.970)
yW.max = 220.000 (期望 200 × 1.1 = 220.000)
I 線 0~2.7 佔 I 軸 90.91%
W 線 0~200 佔 W 軸 90.91%
兩條 0 線與頂端線重疊 → 0 與 max 位置對齊: true

空資料 fallback: yI.max = 5 , yW.max = 250
```

**教訓**：

- 預設 max 寫死值跟實際資料不成比例時，視覺上就會出問題——「0 線對齊」是圖表可讀性的基本要求，不能假設使用者會去設定頁改 max
- 兩軸要對齊 0 跟頂端，**前提是兩軸的「比例尺」相同**（min/max 範圍比 = 資料峰比）。動態算 max 是最直接的解
- 「auto 旗標」語意要跟圖表行為一致——I/W 兩軸要「永遠對齊」就不該讓使用者手動改 max，UI 也要拿掉對應的 input

## 9. 現況進度（2026-07-01 CSV datetime 格式 v9）

### 9.1 v9 — CSV datetime 從 `%m/%d/%y` 改成 `%Y/%m/%d`

**問題（大大回饋 2026-07-01 08:27）**：工廠端用 Excel 開啟匯出的 CSV，datetime 欄位 `06/30/26 23:59:00` 在 Excel 美式 / 歐式 locale 會被當成日期解析：

- 兩位數年份 `26` 被 Windows 系統年份預設帶成 `2026/2027`，下一筆 `07/01/26 00:00:00` 變成 `2007/1/26 12:00:00 AM`（年/月被洗掉）
- 24h `23:59:00` 被轉成 AM/PM 顯示 → 視覺不一致，也讓 grep / awk 之類的工具解析失敗

**決策（2026-07-01 08:31）**：CSV datetime 一勞永逸改為 `YYYY/MM/DD HH:MM:SS`：

- 4 位數年份明確 → Excel 不會亂猜
- 24 小時制 → 沒有 AM/PM 歧義
- 內部 ring buffer / DB 仍維持 ISO 格式 (`datetime.now().isoformat()`)，本變更**僅影響 CSV 匯出字串**，不影響前端 Chart.js / ring buffer 計算 / DB schema

**修法**：

- `app.py:1346` `api_export_csv()` 內:
  ```python
  # 舊
  ts_str = b["_dt"].strftime("%m/%d/%y %H:%M:%S")
  # 新
  ts_str = b["_dt"].strftime("%Y/%m/%d %H:%M:%S")
  ```
- `app.py:1242` docstring 一併更新（標頭格式說明）
- `app.py:585` `ts = datetime.now().replace(microsecond=0).isoformat()`：**不動**（內部用 ISO）

**驗證**：

- `scripts/repro_csv_datetime.py` 跑 6 case（正常 / 壓縮機啟動 / 跨日 / half-up 邊界 / 全 None），全部 datetime 符合 `^\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}$`
- E2E：用真實 CSV (工位4 1440min) 灌進 ring buffer，模擬 endpoint 輸出，20 筆 datetime 全是 `2026/06/30 HH:MM:SS` 格式
- 跨日邊界 `06/30 23:59 → 07/01 00:00` 正確

**下游影響**：無

- 前端 JS 從來不解析 CSV datetime 字串，Chart.js 用 date-fns adapter 吃 ISO ts → 零影響
- DB schema / ring buffer shape / 推播頻率 → 零變更
- 任何「解析 CSV datetime 的腳本」需要更新（目前看 OTA 端 / 本地端都沒有這類腳本）

**教訓**：

- CSV 格式選擇：當下游可能有 Excel 時，**4 位數年份 + 24h** 是最不會誤判的格式
- 「改了 CSV 字串」≠「改了資料結構」，這次只是輸出格式化，所以可以放膽改
- 「BOM + 逗號分隔」對 Excel 已經是最好的「別誤判」策略，但 datetime 欄位本身還是要選不易混淆的格式

---

## 10. 現況進度（2026-07-22 snapshot viewer v10）

### 10.1 v10 — 新增離線瀏覽備份 db 的頁面

**動機（大大需求 2026-07-22 20:22）**：

即時頁 `/` 只保留 ring buffer 的資料（最近約 2 小時）。手動清除工位時雖然會備份到 `data/archive/`，但缺少「讀回來瀏覽」的入口。

**功能**：

1. 新頁 `/snapshot`：列出備份清單，選一個載入後繪成圖表
2. 曲線可放大 / 縮小（wheel zoom，無 plugin 依賴）
3. 特定曲線可顯示 / 隱藏（Chart.js dataset.hidden + checkbox panel）
4. 兩條 X-line（沿用 `docs/CURSOR_MODE.md` 設計）拖曳 → 區間統計
5. **統計走 SQLite 原始資料**（`/api/snapshot/stats`），畫面層才用 LTTB 降取樣

**架構決策**：

| 決策 | 理由 |
|---|---|
| 不切新 branch | 大大決策 2026-07-22 20:42：直接在 `web_UI` 上加檔案 |
| 不改 `index.html` / `settings.html` / 既有 route | 維持即時頁穩定；使用者直接打 `/snapshot` URL |
| 不引進 `chartjs-plugin-zoom` / `chartjs-plugin-annotation` | 自己寫 wheel zoom（~30 行）；兩條 X-line 用 CSS overlay 沿用既有 `.cursor-bar` |
| 統計走後端 `/api/snapshot/stats` | 確保「實際數據計算不失真」（符合大大需求 #6）|
| 路徑 traversal 防護 | `filename` 必須通過 `ARCHIVE_FILENAME_RE` 白名單 + 拒絕 `/`、`\`、`..` |

**新增檔案**：

- `templates/snapshot.html`
- `static/js/snapshot.js`
- `tests/test_snapshot.py`（20 個測試：白名單、攻擊防護、SQLite 統計、API endpoint）
- `docs/specs/snapshot-viewer.md`

**修改檔案**：

- `storage.py` — 新增 5 個函式（`validate_archive_filename` / `archive_meta` / `list_archives_with_meta` / `query_archive_range` / `compute_archive_stats`）。既有函式**零修改**
- `app.py` — 新增 4 個 route（`/snapshot` + `/api/snapshot/{archives,data,stats}`）。既有 route **零修改**
- `static/css/style.css` — 補 `.snapshot-*` 區塊（既有 class 不動）

**驗證**：

- pytest `tests/test_snapshot.py`：20 passed（含路徑 traversal 攻擊 5 個 case + URL-encoded attack）
- 本機 Flask 起 server：`GET /` / `/settings` / `/snapshot` 都 200；既有路由未受影響
- 攻擊測試：`?filename=../../etc/passwd` 回 400；`?filename=..%2F..%2Fetc%2Fpasswd` 回 400
- OTA 端（`<OTA_HOST>:5000`）實機驗收：大大手動確認三條需求通過後才 commit


---

## 11. 現況進度（2026-07-23 備註同步 + archive meta v10.1）

兩個 commit 合併上線，都是補 v10 snapshot viewer 的最後一塊拼圖。

### 11.1 備註欄 per-station 同步（commit `1430f8c`）

**症狀（bug fix）**：
v10 之前的歷史 commit `e0a0a9f`（2026-06-18）已經在 `index.html` 加了 `<input id=noteBox>`，但只有 HTML 沒有 JS 配套。
備註欄從來就沒生效過 — 輸入存不進 server、切工位不會重置、reload 後消失。

**修法**：
- 後端 `config.py` `default_settings()` 加 `notes` key，預設 `{工位1..6: ""}`
- 後端 `app.py` `load_settings()` / `save_settings()`：notes per-station 跟 `ch_alias` 同樣 merge 邏輯
- 前端 `storage.js` `GX20State` 三處加 notes：dirty snapshot + save payload + sessionStorage 回寫
- 前端 `main.js` `noteBox` 雙向綁定：
  - `loadNoteForCurrentStation()` 讀 `GX20State.settings.notes[currentStation]`
  - `oninput` → `GX20State.update('notes', ...)` 走 300ms debounce auto-save
  - `switchStation()` 切換前 `saveNoteForStation()`、切換後 `loadNoteForCurrentStation()`
- 前端 `main.js` `applyRemoteUiLocks()` 遠端隱藏 noteBox（跟 settings 一樣鎖本機）
- 截斷到 20 字防線（HTML maxlength + 前端 slice + 後端 `[:20]`）

**驗收**（`scripts/smoke_note_v10x.py`，6 條 path 全過）：
1. 載入頁面 → noteBox 預設空
2. 寫 ABC → 等 debounce → server 端工位 1 拿到 ABC
3. 切工位 2 → noteBox 清空、工位 1 沒被清
4. 切回工位 1 → 顯示 ABC
5. 工位 2 寫測試 2 → 兩工位獨立
6. reload 頁面 → 兩工位都還原（從 server 拉回）

0 console error。

### 11.2 歸檔 dump alias + note 到 `.meta.json`（commit `1728939`）

**動機**：
v10 snapshot viewer 載入 archive DB 時，圖例只能用預設 `Ch01 ~ Ch20`，無法還原「當時」的使用者別名；備註也只能直接寫在圖上。

**功能**：
- 後端 `storage.archive_station()` 歸檔時**同時**寫 `<archive>.db.meta.json`
  - meta 內容：`{station, archived_at, schema, alias, note}`
  - `alias` 若跟 `default_alias()` 一致就寫 `null`（表示未設），避免永遠寫大資料
  - `note` 若為空字串就寫 `null`
- 後端：`api_snapshot_data` response 加 `meta` 欄位
- 前端 `snapshot.js` `loadArchive()` 套用 `meta` 覆蓋 `FIELD_LABELS` + 頂部備註顯示
  - 新增 `state.activeLabels` + `state.activeNote` + `activeLabel()` helper
- 前端 HTML：加 `<span id=archiveNote>`，CSS：加 `.archive-note` 樣式
- 後端 `_prune_old_archives()`：改用 `*.db` 結尾判定主檔（排除 `.db.meta.json` 誤刪）

**驗收**（`scripts/smoke_archive_meta_v10x.py` + `scripts/smoke_archive_meta_visual.py`）：
- 帶 meta 備份：`dataset.label='sensor-A'`、`archiveNote` text=`高溫警報測試`
- 沒 meta 備份：`dataset.label='T01'`（預設）、`archiveNote` hidden
- `meta.alias=null` + `note=null` 表示「未設」→ 退回預設

**設計決策（避免備份膨脹）**：
alias 全等於 `default_alias()` 時就**不寫實際值**，只標記 `null`。
理由：歸檔頻率受「清除前歸檔」(v5) 觸發，量大時 .meta.json 寫滿 alias 會比 .db 還大。


## 12. 現況進度（2026-07-24 主頁 rate 計算語意修正 v11）

### 12.1 v11 — B1 語意：取 SQLite 視窗內「最舊」與「最新」差 / since_minutes

**使用者回饋**：「主頁的速率計算不正確，F 顯示 0.091/1hr 但實際明顯大於計算結果」。

**根因**：
1. `compute_rate_from_ring` 取 ring buffer 內**首末實際 dt** 當分母（不是視窗分鐘數）→ 若 ring 剛啟動，視窗 1hr 但實際經過只有 58 min → 分母偏小 → 數值偏大
2. ring buffer 上限 720 筆（10s/筆 ≈ 2hr），視窗 > 2hr 就 silently 沒資料
3. 表頭寫 `°C/1hr` 卻回傳 `°C/min` 數字，未做單位換算（這項保留給 v11.x 處理）

**修法**：
1. `compute_rate_from_ring` → `compute_rate_from_db`：來源改成 `storage.query_recent()`，ring 不再用
2. 計算式簡化：`rate = (新值 - 舊值) / since_minutes`，分母固定 = 使用者選的視窗分鐘數
3. `compute_avg_from_db` 同樣改用 SQLite
4. 視窗內不足 2 筆：仍以「視窗內最舊那筆」當起點（不要硬傳 None）→ 剛開機的使用者也能看到速率
5. 兩處呼叫端同步修正（poller emit、`/api/latest`）

**驗證**：`tests/test_rate_v11_repro.py` 灌入使用者提供的 CSV（58 筆 08:48~09:46）跑全部視窗組合：
- 60min F = 0.0917（與畫面顯示一致）
- 5min F = -0.02（最 5 分鐘持平）
- 30min F = +0.03
- 600min F = 0.0092（超長視窗仍出值）
- 空 station => None

**未決**：
- 表頭「°C/1hr」其實是「每分鐘變化 × 視窗分鐘」來的視覺謊言；下一步該改寫表頭顯示為「°C/{視窗}」，列為 v11.x 的次要 TODO
- 由於視窗 1hr 等於「分鐘變化率」，若使用者用「°C/1hr」型話語，表中數字看起來偏小、但「5 min」視窗會看起來偏大；這算是 v11 給使用者的語意校正期

---

## 13. 現況進度（2026-07-24 snapshot 統計時區 bug v10.1.1）

### 13.1 v10.1.1 — `refreshStats` 送 UTC 字串給 SQL 字串比對，count=0

**使用者回饋**：「snapshot 統計失效，工位 1 的 131234 備份檔載入後右側統計表全部 `—` 與 `0`，但圖表本身有畫出曲線且游標可拖」。

**根因**：
- `static/js/snapshot.js` 第 572-573 行原寫：
  ```js
  const t1 = state.cursorTsLeft.toISOString().slice(0, 19);
  const t2 = state.cursorTsRight.toISOString().slice(0, 19);
  ```
- `Date.toISOString()` 永遠回傳 **UTC**，例如台北 11:27:04 → `2026-07-24T03:27:04.000Z`（slice 後 `2026-07-24T03:27:04`）
- DB `samples.ts` 欄位存的是**本地時間（台北）字串**，例如 `2026-07-24T11:27:08`
- `/api/snapshot/stats` 走 `WHERE ts >= ? AND ts <= ?` **字串比較**：`'2026-07-24T11:27:08' >= '2026-07-24T03:27:04'` ✅，但 `'<=' '2026-07-24T05:15:35'` ❌（11 > 05）→ 整個區間落空 → count=0
- 同檔 `fmtTs` 用 `getHours()` 等本地方法 → 游標顯示的時間是台北時間（與 DB 一致），但送 API 的時間是 UTC（與 DB 錯開）→ **畫面顯示跟送出不一致**

**為什麼有些備份「可算」、有些「不可算」**：
- 純粹是巧合。131234 資料集中在台北白天 08:48~13:12，UTC 換算後區間 `00:48~04:48` 字串比對比 DB 內 `08:48~13:12` 完全錯開 → 0 筆
- 084804 資料跨台北 7/23 16:16 ~ 7/24 08:48，UTC 換算後區間 `08:24~20:40` 剛好涵蓋一部分（7/23 16:16~20:40 約 4.5 小時）→ 部分命中
- 兩個檔案都觸發同一個 bug，命中數只是資料分佈的副產品

**修法**：
- 新增 `fmtTsIso(ts)`：跟 `fmtTs` 一樣用本地時間方法，但回傳 ISO 風格字串（`YYYY-MM-DDTHH:MM:SS`）給後端
- `refreshStats` 改呼叫 `fmtTsIso(state.cursorTsLeft/Right)`，不再用 `toISOString()`

**驗證**：
- 131234 備份，截圖游標位置 11:27:04 ~ 13:15:35 → API 回 463 筆命中（修正前 0 筆）
- F/R/FR/Vbox 四欄 avg/max/min 全部正常浮點數（修正前 4 欄全 `—`）

**教訓**：
- `Date.toISOString()` 跟「本地時間字串」永遠不要混用 — 兩個表達「現在幾點」的方式在跨時區時是錯開的
- SQL 字串比對對時區議題沒抵抗力；未來若要徹底免疫，DB 應該存 UTC + 用 `datetime()` 函式比較，但這是大改、目前不在 scope

---

## 14. 現況進度（2026-07-24 取消 PW3335 「啟用」開關 v10.2）

### 14.1 v10.2 — 取消 `pw3335.remote` 開關，6 工位一律 fetch

**使用者回饋**：「OTA 推送後 app.py 重啟時沒有考慮到電力計是否已經在紀錄，將設定內的電力計開關取消，後段啟動時，除了 GX20 的資料擷取，強制同步接收電力計資料」。

**根因**（v10.2 之前的舊邏輯）：
- `config.pw3335.remote[<工位>] = False` 是 v7 設計的「電力計連線開關」，預設全 False
- 應用場景原意：「開發機不需要每工位都裝 PW3335」
- 實際後段：「6 工位都裝了 PW3335」 + 「操作員第一次部署時不會去設定頁打勾」
- 後果：OTA 推送後 app.py 重啟 → 6 工位無聲寫 0 → 重新啟動到下次手動開啟這段時間電力資料完全缺漏，**還沒被前端察覺**（badge 只能看當下抓取狀態，沒歷史）

**修法**（v10.2 變更）：
- `config.default_pw3335()` 拿掉 `remote` 欄位
- `app.py` poller 拿掉 `if not pw_remote.get(station, False)` 分支 → 6 工位一律 `fetch_one_station(host, port)`
- `app.py /api/pw_connection` 拿掉 `remote` 欄位回傳
- 前端 `settings.html` 拿掉 6 工位「啟用」checkbox UI
- 前端 `settings.js renderPw3335()` 拿掉 checkbox 構造 + 監聽
- 前端 `main.js refreshPwConnStatus()` 拿掉「未啟用」badge 路徑，只剩「已連線 / 未連線」兩種
- 「V/I/W = 0」從此不視為錯誤（機器關機是正常）

**不變的保留**：
- 連線失敗 / IP 為空 → 寫 0 + `pw_connected=False` + `last_error`（維持「不連累其他工位 / 不連累溫度」的容錯級行為）
- 歷史 DB 內 `(0, 0, 0)` 資料不動（不重算補抓）
- OTA 白名單不變（`tests/` 不在白名單，repro 腳本只在本機）

**驗證**：
- `tests/test_pw3335_v10_2_repro.py` 5 個 shape 全部通過：
  1. 6 工位都連線成功 → 寫實際值 + `connected=True`
  2. 1 工位 IP 為空 → 寫 0 + `connected=False` + `last_error="未設定 IP"`
  3. 1 工位 fetch 失敗 → 寫 0 + `connected=False` + `last_error="通訊失敗"`
  4. V/I/W=0 視為正常（機器關機）→ `connected=True` 不算錯誤
  5. 1 工位失敗不連累其他工位
- OTA 端推送後 5-10 秒驗證：6 工位 `connected=true`、6 工位 `last_ts` 持續刷新、UPtime 從 0 重啟累加、無 WARNING/ERROR
- `1.5 PW3335 電力計 (v10.2)` 標題已上線；`/api/pw_connection` 沒有 `remote` 欄位（grep count = 0）

**教訓**：
- 「設計時的開關」跟「實際部署的場景」對不上時，**預設值的選擇**很重要：「預設關」對開發安全但對正式環境是死亡預設；「預設開」對正式環境安全但對開發可能誤觸
- 這次選「拿掉開關」是因為「6 工位都有電力計」是 fixed 場景，不會變動 → 預設值策略本身就是設計債
- 如果未來某工位暫時不裝（例如第 6 工位硬體還沒到），用 `hosts[<工位>] = ""` 即可觸發「未設定 IP」路徑，**不需要再開關**

---

## 15. 現況進度（2026-07-27 行動簡式頁面 /mobile v10.3）

**背景**：

使用者要求「攜帶式溫度監測」獨立頁面。原本考慮用 `index.html` 做 RWD（手機/桌機不同版面），但大大指示「桌機瀏覽也同樣顯示簡單版面」，因此**不做響應式切換**，而是開一個獨立 route。

**決策**：

| 項目 | 決定 |
|---|---|
| 路徑 | `/mobile` |
| 內容 | 只 2 樣：工位下拉 + 20 個 channel 讀值表 |
| 字體大小 | 預設就要夠大（手機可閱讀程度），不做調整 UI |
| 入口 | 完全獨立，**不在主頁 / 加連結**，使用者自己 bookmark |
| 資料源 | socket.io `new_sample`（與主頁同源，不輪詢） |
| 設定來源 | `GET /api/settings` 一次拿 `ch_alias` + `ch_visibility` |
| 首頁 / 切工位 fallback | `GET /api/latest/<station>` |

**新增檔案**：

- `templates/mobile.html` — 簡式版面（inline CSS，獨立配色避免污染主 style.css）
- `static/js/mobile.js` — 完全獨立，不引用 `storage.js` / `main.js`

**app.py 變更**：

- 新增 `@app.route("/mobile")` → `render_template("mobile.html", stations=STATIONS, points_per_station=POINTS_PER_STATION)`

**不做的事**：

- 不在 `/`（index.html）加「手機模式」連結（使用者自己 bookmark）
- 不輪詢 `/api/latest`（socket 已 10s 推一次，省頻寬）
- 不寫本地字體大小 / 排序偏好（**沒有任何寫 server 的端點**，符合 v8.1.2 遠端鎖定精神）

**視覺重點**：

- 字體：channel 名稱 1.1rem、讀值 1.6rem（手機模式 1.5rem）
- 行高 / padding 大（tbody 14px）適合手指觸控
- `ch_visibility=false` 的 channel → 整列淡化（資料保留、可見但不搶眼）
- 連線 dot + 時間戳 footer
- header `position: sticky` → 滾動看下方 channel 時工位下拉仍可切換

**驗證 SOP**：

- `py_compile app.py` 通過
- 啟動 Flask，`curl -s http://127.0.0.1:5000/mobile` 確認 200 + HTML 含 `id="stationSelect"` 與 `id="readoutTable"`
- 瀏覽器（手機模式 + 桌機模式各一次）確認版面、字體大小、socket 連線、新資料即時更新

---

## 16. 現況進度（2026-07-29 白名單抽離 + 設定頁編輯支援 v10.3.1）

**背景**：

5 個安全白名單原本 hardcode 在程式碼內：
- `cors_origins` → `app.py` 的 `_ALLOWED_ORIGINS`（含環境變數 `GX20_ALLOWED_ORIGINS` 覆寫）
- `remote_write_ips` → `app.py` 的 `REMOTE_WRITE_ALLOWED_IPS = ("127.0.0.1", "::1")`
- `ota_admin_ips` → `ota.py` 的 `OTA_ALLOWED_IPS`
- `ota_allowed_targets` → `ota.py` 的 `ALLOWED_TARGETS`（路徑前綴 / 具名檔）
- `ota_blocked_exts` → `ota.py` 的 `BLOCKED_EXTS`（副檔名黑名單）

每次 LAN 拓樸變動 / 換 IP / 加新工位都要改 code + commit + push OTA。對非工程師使用者不友善。

**決策**：

| 項目 | 決定 |
|---|---|
| 儲存位置 | 併進 `config/settings.json` 新增的 `whitelist` 區塊（**不獨立檔**） |
| 編輯介面 | `/settings` 頁新增「▼ 安全白名單（進階）」折疊區塊，5 個清單 + 重置預設值 |
| 寫入權限 | 沿用 `POST /api/settings` 的本機鎖（`127.0.0.1` / `::1` only） |
| 熱載入 | `whitelist.py` 模組每次 `get()` 檢查 settings.json mtime，變動就 reload（5 秒內生效） |
| OTA 推送 | `config/settings.json` **不加入 OTA 白名單**（避免雙重故障） |
| 預設值 | 跟 OTA 端原本 hardcode 的內容一致（CORS 含 LAN IP `10.35.31.10:5000`、OTA admin 含 `10.35.32.11`） |

**新增模組**：

- `whitelist.py` — 從 `config/settings.json` 讀 `whitelist` 區塊，提供 `get(key)` / `init()` / `get_status()` / `get_defaults()` API
  - 檔案 mtime 變動 → 自動熱載入
  - JSON 解析失敗 → 整份退回 default（log warning，不 crash）
  - 欄位型別錯誤 → 該欄位退回 default、其他保留

**app.py 變更**：

- 刪除 `_ALLOWED_ORIGINS` hardcode + `GX20_ALLOWED_ORIGINS` 環境變數解析
- `socketio = SocketIO(app, cors_allowed_origins=_wl_get("cors_origins"), ...)`
- `REMOTE_WRITE_ALLOWED_IPS = tuple(_wl_get("remote_write_ips"))`

**ota.py 變更**：

- `ALLOWED_TARGETS = tuple(_wl_get("ota_allowed_targets"))`
- `BLOCKED_EXTS = tuple(_wl_get("ota_blocked_exts"))`
- `OTA_ALLOWED_IPS = tuple(_wl_get("ota_admin_ips"))`

**templates/settings.html 變更**：

- 新增 `<details>` 折疊區塊「▼ 安全白名單（進階）」
- 5 個 `<div class="wl-list">` 容器 + 「+ 新增」按鈕
- 「重置為預設值」按鈕
- 警告文字「改錯可能會把自己鎖在外面」
- 對應 CSS：`.wl-row` / `.btn-add` / `.btn-del` / `.btn-secondary`

**static/js/settings.js 變更**：

- 新增 `WHITELIST_KEYS` / `WHITELIST_DEFAULTS` / `_wlRenderRow()` / `renderWhitelist()`
- 整合進 `GX20State.update("whitelist", {...})`，按保存時隨其他設定一起 POST
- 預設值跟 `whitelist.py` DEFAULTS 一致（兩邊都要同步）

**config/settings.example.json 變更**：

- 新增 `whitelist` 區塊範例（含 `_comment` 註解）

**部署流程變更**：

- `config/settings.example.json` OTA 推送時**會被白名單擋下**（v8.4+ 設計，OTA ALLOWED_TARGETS 本來就不含 `config/`）
- 第一次部署後，OTA 端 `config/settings.json` 沒有 `whitelist` 區塊 → `whitelist.py` 退回 default 載入（**功能正常**，但 UI 編輯改的內容不會被持久化，除非手動合併）
- 部署後**必須手動編輯 OTA 端 `config/settings.json`** 加 whitelist 區塊（或用 git checkout），才能讓 UI 編輯生效

**OTA 推送 SOP 教訓（2026-07-29）**：

| 症狀 | 原因 |
|---|---|
| OTA bundle response `whitelist.py → whitelist.py (6245 bytes) saved: ok`，但磁碟上 `Test-Path .\whitelist.py` 回 False | 新模組檔案第一次推送時，OTA 端 atomic_write 跟 watchdog 重啟可能有 race condition，導致 silent failure |
| 重啟後 watchdog `Start-Process '%~dp0ota_watchdog.bat' -WindowStyle Hidden` 報「找不到檔案」 | 真正原因是 Flask crash（`ModuleNotFoundError: No module named 'whitelist'`），watchdog 找不到活的 Python 進程；PowerShell 包裝層把錯誤訊息誤導成「檔案不存在」 |
| 解法 | 用 `start_forever.bat` 直接拉 Python（繞過 watchdog 邏輯層）；手動從 WSL 複製缺的檔案 |

**新增 SOP**：

- **OTA bundle 推送新檔案時**：除了看 response `saved: ok`，必須再從 OTA 主機 `Test-Path` 確認檔案真的在磁碟
- **首次部署新模組時**：預先在 OTA 主機確認 watchdog 流程、或暫時用 `start_forever.bat` 接手，避免重啟鏈中斷
- **修整計畫（v10.3.1 hotfix）**：watchdog 內錯誤訊息改成「flask process not found」，不要用 PowerShell 預設錯誤訊息誤導

**驗證 SOP**：

- `py_compile app.py ota.py whitelist.py` 通過
- 6 個 repro 測試通過（default 載入、從 settings.json 載入、壞欄退回 default、JSON 解析失敗退回 default、get_status、get_defaults）
- 6 個 E2E 測試通過（合法路徑寫入、具名檔寫入、traversal 攻擊擋下、Windows 磁碟機擋下、副檔名黑名單擋下、白名單前綴外擋下）
- 部署後 OTA 主機 `/api/admin/status` 回 `allowed_targets_count: 15`（移除 `config/settings.json` 後的新值）
- 部署後 OTA 主機 `python -c "import whitelist; print(whitelist.get('cors_origins'))"` 回 `['http://localhost:5000', 'http://127.0.0.1:5000', 'http://10.35.31.10:5000']`

**Commit 鏈**：

- `b9c810b` — feat(whitelist): 抽離 5 個白名單到 settings.json，支援 UI 編輯與熱載入（6 files, 422 +/41 -）

---

## 16. 現況進度（2026-07-30 whitelist save/load 鏈修補 v10.3.2）

**背景**：

v10.3.1（commit `b9c810b`）把 5 個白名單從 hardcode 抽離到 `config/settings.json`，並提供 UI 編輯介面。但**寫入鏈沒對齊**：

- 前端 `settings.js` 在保存時會用 `GX20State.update("whitelist", ...)` 把整包 whitelist 物件送進 `POST /api/settings`
- `app.py:save_settings()` 的 `for k, v in patch.items()` 沒對 `whitelist` 做特別處理 → 走 `else: storage.set_setting(k, str(v))`
- 結果：SQLite 內 `whitelist` 變成 `"{'cors_origins': ...}"`（Python `str(dict)` 不是合法 JSON）
- `load_settings()` 也沒讀 `whitelist` → 回傳的 dict 內沒有 `whitelist` key
- `dump settings.json` 段（save_settings 結尾）也不會包含 `whitelist` 區塊
- 症狀：在 OTA 本機瀏覽器 (`127.0.0.1/settings`) 修改白名單按保存後，`config/settings.json` 內沒有 whitelist 區塊 → 下次重啟 Flask 時 `whitelist.py` 退回 DEFAULTS，UI 編輯全部丟失

**根因**（v10.3.1 漏接的部分）：

1. `save_settings` 的 dispatch 漏了 `whitelist` 這個 key（其他 per-station / per-axis 結構 key 都有專屬處理）
2. `load_settings` 沒從 SQLite 讀回 `whitelist`，也沒 merge DEFAULTS 兜底
3. `dump settings.json` 那段用 `load_settings()` 結果寫檔，所以 #2 沒補 #1 也白搭

**修法**（v10.3.2 變更）：

| 檔 | 變更 |
|---|---|
| `app.py:save_settings` | 新增 `elif k == "whitelist":` 分支，**條件只檢查 key**（不檢查 `isinstance(v, dict)`）；進入後若 `v` 不是 dict → log warning + `continue`（不寫、不覆蓋 SQLite 內原值）。其餘走 per-key merge：5 個子 key 逐個 merge，patch 只送 `cors_origins` 時其他 4 個保留 |
| `app.py:load_settings` | 結尾 `return out` 前加讀 `whitelist`：SQLite → JSON 解碼 → 缺漏子 key 用 `_wl.DEFAULTS` 補；型別錯的子 key 也保留 default（不讓單一欄位壞整份） |
| `whitelist.py` | **不動**（你 14:29 附加條件「執行時檢查 settings.json 是否有值」已是現有設計：`_load_from_disk` + `_reload_if_changed` mtime 熱載入） |
| `config.default_settings` | **不加** `whitelist` key（避免兩邊 default 漂移） |

**B 方案的設計理由**（為什麼非 dict 直接忽略）：

`whitelist` 是關鍵安全設定（CORS / IP 鎖 / OTA 寫入路徑），不能用一般 key 的 `else: str(v)` 容錯哲學。若前端 bug 或惡意 payload 送 `whitelist = "xxx"`（非 dict），**絕對不能**讓它把 SQLite 內原本的 JSON 蓋成 str，然後下次 load 觸發 JSON parse 失敗 fallback 到 DEFAULTS。

正確做法：非 dict 寫入**直接忽略 + log warning**，保留 SQLite 內原值，下一次正確 patch 進來還能 merge 回來。

**單一真相來源**：`whitelist.py:DEFAULTS`（不放在 `config.default_settings()`，避免兩邊 default 漂移）。

**新增測試**：

- `tests/test_whitelist_save_repro.py` — 5 個 shape（basic save+load、per-key merge、非 dict 容錯保留原值、SQLite 已有 whitelist load 時 DEFAULTS 兜底、dump settings.json 含完整結構）全部通過

**驗證 SOP**：

- `py_compile app.py` 通過
- `python tests/test_whitelist_save_repro.py` → 29 pass / 0 fail
- OTA 推送前再跑一次 repro（避免推送後 OTA 端 Flask 重啟鏈中斷時 silent failure）
- 推送後 OTA 端流程：
  - 本機瀏覽器 `127.0.0.1/settings` → 安全白名單區塊 → 改 `cors_origins` 加一筆 → 按保存
  - `curl -s http://127.0.0.1:5000/api/settings | python -m json.tool | grep cors_origins` 確認新值在記憶體
  - OTA 主機開檔 `config/settings.json` 確認 `whitelist` 區塊有寫進去
  - 5 秒內（whitelist.py mtime 熱載入機制）`python -c "import whitelist; whitelist._reload_if_changed(); print(whitelist.get('cors_origins'))"` 確認拿到新值

**Commit 鏈**：

- `<待 commit>` — fix(whitelist): save/load 鏈補上 whitelist patch 處理（save_settings 分支 + load_settings merge + B 方案非 dict 容錯）


---

## 17. 現況進度（2026-08-05 計算頁 /calculator v11）

**新功能**：把桌面版 `plot_gui_雙信.py` 的離線 EF 計算搬到 Web。

**使用者流程**：
1. `/` 按「儲存 CSV」 → 在 Excel 編輯（去空白頻道 / 刪時段）
2. 直接 URL 進入 `/calculator`（不從 topbar 進入）
3. 上傳 CSV → 圖表即時畫出
4. 拖曳兩條 X-line 選計算區間 → 右下結果文字框即時更新
5. 按「儲存結果」 → 瀏覽器原生下載對話框（`.txt`）

**EF 演算法**：完全 1:1 移植 `EnergyCalculator.calculate()` 與 `calculate_statistics()`：
- fridge_type 5 個分支（VF=0 / fan × {eqV<400, eqV≥400}）
- 2018 / 2027 thresholds（type 5 用 1.72/1.54/... vs 1-4 用 1.6/1.45/...）
- grade 規則含 `1*級`（≥T0 × 0.95）
- ON/OFF 週期：cycles、above/below 平均分鐘、百分比（含 `int(avg/60) + 1` 的「Off 加 1」規則）
- `round half-up`（5 永遠進位）— 不用 JS `Math.round`（banker's rounding）

**共用底層抽離**：
- `static/js/chart-utils.js` 抽出 chart init / cursor overlay / roundHalfUp / chartColors
- `main.js` 改成呼叫 ChartUtils.chartColors() + ChartUtils.formatTs()（保守做法，不破壞既有行為）
- `/calculator` 共用整個 chart-utils.js（chart init + cursor overlay 都從這裡建）

**驗證**：
- `tools/verify_calculator.py` 跑 3 個 H61DV CSV（NEW1200 / OLD1200 / VIP800，1442 列/24h）
- Python 原版 vs JS 版 **完全 1:1 一致**（EF 22 個欄位、ON/OFF 統計、電力計算）
- 容忍浮點誤差 1e-9 + ISO 字串時區序列化差異（Python `+08:00` vs JS `Z`）

**節點位置**：
- 不在 `/` topbar 加「計算」按鈕
- 使用者透過 URL `/calculator` 或瀏覽器書籤進入

**檔案清單**：
- `templates/calculator.html` — 計算頁主畫面
- `static/js/calculator.js` — CSV parser / statistics / EF（純前端，755 行）
- `static/js/chart-utils.js` — 共用 chart API（v11 新增，292 行）
- `static/css/style.css` — `v11.x：/calculator 計算頁` 區段（78 行）
- `tools/verify_calculator.py` — Python + JS 驗證工具（752 行）
- `app.py` — 加 `GET /calculator` route
- `templates/index.html` — 載入 chart-utils.js（在 main.js 之前）
- `static/js/main.js` — 共用 ChartUtils.chartColors() / ChartUtils.formatTs()
- `README.md` — §7.7 文件


---

## 18. 現況進度（2026-08-05 計算頁「尋找最佳數據」滑動視窗 v11.1）

**新功能**：「尋找最佳數據」按鈕 — 1440 分鐘（24H）窗口滑動掃描整個資料段，找出 EF 最高的 24H 區段。

**設計決策**：
- 窗口：1440 分鐘（24H，1 分鐘粒度 = 1440 筆）
- 步進：10 分鐘（10 筆）→ 1442 筆資料 = 397 步
- 評分指標：**EF 最高**（同時記錄 watt）
- EF / Watt 在同參數下**負相關**（Watt 越小 EF 越高），驗證確認（NEW1200: 815W → EF 30.8）
- 預估時間 > 3 秒才顯示 spinner + 進度條（3 個 H61DV CSV 都不觸發，純本地幾秒內完成）

**使用者體驗**：
- 按鈕在 topbar 旁「載入範例」右側
- 掃描中按第二次 = 取消
- 完成後 X-line 移到最佳區段邊界 + cursor overlay 變綠色 highlight
- 結果文字框加「【最佳 24H 區段】時間 / EF / 24H 耗電 / 掃描步數」摘要

**演算法細節**：
- 預估：先跑 50 步量時間，線性外推總時間
- 分批執行：`setTimeout(fn, 0)` 200 步一批，避免卡 UI thread
- 完成時 cursor overlay 加 `.best` class（CSS 變綠色）+ X-line 重設位置

**檔案清單**：
- `templates/calculator.html` — topbar 加 findBestBtn
- `static/css/style.css` — `.calc-progress-overlay` + `.cursor-overlay.best` 樣式
- `static/js/calculator.js` — `runBestWindowScan()` + `applyBestResult()` + 動態注入 progress overlay
- `tools/verify_calculator.py` — `py_scan_best_window()` + `JS_BRIDGE_SCAN` + `run_js_scan()` + main 內自動跑
- `README.md` §7.7 補上「尋找最佳數據」段

**驗證結果**（3 個 CSV）：
- NEW1200  : steps=397  bestEF=30.8  bestWatt=815 W  bestStart=2026-08-01T06:46:00
- OLD1200  : steps=397  bestEF=29.6  bestWatt=843 W  bestStart=2026-08-01T05:45:00
- VIP800   : steps=397  bestEF=29.6  bestWatt=843 W  bestStart=2026-08-01T05:45:00
- Py vs JS 掃描結果 3/3 完全一致

---

## 18. 現況進度（2026-09-11 設定重開機還原 bug 修補 v10.4）

**Bug**（大大 09:43 回報）：
  重開機後 /settings 內「Y 軸範圍」「PW 軸」「PW3335 IP」「備註」等恢復成預設值，但「ch_alias / ch_visibility / ch_color」仍保留自訂。

**根因**：
  `apply_json_to_sqlite()`（app.py:530 起）在每次啟動時讀 `config/settings.json` 寫回 SQLite。原本實作：

```python
for k, v in d.items():
    if k in ("ch_visibility", "ch_alias", "ch_color"):
        if isinstance(v, dict):
            storage.set_setting(k, config.to_json(v))   # ← 對這 3 個顯式保護
        else:
            storage.set_setting(k, config.to_json(defaults.get(k, {})))
    else:
        storage.set_setting(k, str(v))   # ← BUG：dict 走 str() → Python repr 單引號
```

當 v 是 dict 時（例如 y_axis = `{"工位1": {"min": 0, "max": 50}}`），`str(v)` 產出 Python repr 用單引號：
```python
>>> str({"工位1": {"min": 0, "max": 50, "auto": False}})
"{'工位1': {'min': 0, 'max': 50, 'auto': False}}"   # ← 不是合法 JSON
```

寫進 SQLite 後，下次 `config.from_json(raw)` 跑 `json.loads(...)` 因單引號失敗 → silently fallback 到 default → load_settings 回傳預設值。

**受影響欄位**（不包含顯式保護的 ch_visibility/alias/color）：
- `y_axis`（dict-of-dict）— 最容易觀察，每工位的 Y 軸上下限丟掉
- `pw_axis`（dict-of-dict，每工位含 v/i/w）
- `pw3335`（nested dict，port + hosts + colors）
- `notes`（dict-of-str）
- `whitelist`（雖然 load_settings 內 merge DEFAULTS 兜底，但 SQLite 內存的是壞字串）

**未受影響**（已有顯式保護）：
- `ch_visibility` / `ch_alias` / `ch_color`（在 if 分支內用 `config.to_json`）

**修法**（v10.4 變更，app.py `apply_json_to_sqlite`）：
  對 `isinstance(v, (dict, list))` 改用 `config.to_json(v)`（產生合法 JSON 雙引號），與 `save_settings()` 內部 `set_setting(..., config.to_json(existing))` 對齊。Scalar（str/int/bool）照舊走 `str(v)`。

```python
for k, v in d.items():
    if k in ("ch_visibility", "ch_alias", "ch_color"):
        # 原顯式保護保留
        if isinstance(v, dict):
            storage.set_setting(k, config.to_json(v))
        else:
            storage.set_setting(k, config.to_json(defaults.get(k, {})))
    elif isinstance(v, (dict, list)):   # ← 新分支：dict/list 走 to_json
        storage.set_setting(k, config.to_json(v))
    else:
        storage.set_setting(k, str(v))
```

**驗證**（`tests/test_apply_json_to_sqlite_v10_4_repro.py`，4 個 shape）：
- shape 1：y_axis 工位1 自訂 `{min:0, max:50, auto:False}` 跨 startup 後保留 ✓
- shape 2：pw_axis 工位3.v 自訂 `{min:0, max:240, auto:False}` 跨 startup 後保留 ✓
- shape 3：notes 工位1「H61DV VIP-800改風道」跨 startup 後保留 ✓
- shape 4：pw3335 自訂 port=3301 / hosts=10.20.30.x / colors.V=綠 跨 startup 後保留 ✓

修前 ✗ 7/8，修後 ✓ 8/8。`test_whitelist_save_repro.py`（既有 29 個 ✓）+ `test_pw3335_v10_2_repro.py`（既有 5 個 ✓）+ `test_snapshot.py`（既有 20 tests OK）皆無 regression。

**已知不會自動回填已被還原成預設值的資料**：
  本次 fix 只保證「重啟後不再丟資料」。**已經被歷次 OTA-restart 還原成預設值的欄位**（y_axis / pw_axis / pw3335 / notes 在 SQLite 內現存的是壞字串），需要使用者重新進 /settings 頁 → 設成想要的值 → 按保存。save_settings() 會用 `config.to_json()` 寫進 SQLite + dump JSON，下次重啟就會正確 round-trip。

**教訓**（進 SOUL/AGENTS）：
  改 save / load chain 必須 repro script 灌真實 shape 跑關鍵 function。**`str(dict)` 不是合法 JSON** 這種小細節，靠 `py_compile` 抓不到（語法對、型別對），靠 Playwright 也看不到（表面 round-trip 沒事，反正「存了、讀了、看起來一樣」）。必須 unit test 用「存 → 重啟 → 再讀」的 round-trip 才抓得到。參考 MEMORY.md 政策 2b8de47 ring tuple unpack 慘案。

**檔案清單**：
- `app.py` — `apply_json_to_sqlite()` 加 `elif isinstance(v, (dict, list))` 分支（+11/-1）
- `tests/test_apply_json_to_sqlite_v10_4_repro.py` — 4-shape repro 測試（新檔）

**部署**：
  透過 OTA 推送 `app.py` 單檔到 `D:\sampo\GX20-PW3335-Data-Collection\app.py`，restart。Token 指紋 `750f9385`。

# GX20 + PW3335 Web Monitor — 溫度 × 電力監視

> YOKOGAWA GX20（溫度）+ GW Instek PW3335（電力）的網頁版監視系統
>
> 6 工位 × 20 接點 = 120 點溫度 + 6 工位 V/I/W 三相電力，每 10 秒取樣一次，**可持續記錄 7 天以上**
>
> **v10.1** 改版重點：備註欄 per-station 同步 + 備份 dump alias/note 到 .meta.json
>
> **v10** 改版重點：離線瀏覽備份 db 的 `/snapshot` 頁（讀 archive DB 畫圖 + 兩條 x-bar 拖曳 + wheel zoom）
>
> **v9** 改版重點：CSV datetime 改 `YYYY/MM/DD HH:MM:SS`（避 Excel 誤判，2026-07-01）
>
> **v8.5.1** 改版重點：電力圖 I/W 兩軸對齊（共用 powerMax，Chart.js nice() 不再 round）
>
> **v8.1.x** 改版重點：設定跨瀏覽器/跨電腦同步（debounce auto-save + 遠端鎖死設定權限 / 清除權限）
>
> **v7** 改版重點：PW3335 電力計整合（6 工位 V/I/W、雙圖表、CSV 補欄）
>
> **v6.1** 改版重點：進階計算 — 游標模式（量測狀態，可拖曳 x-bar 計算區間平均/最大/最小）
>
> **v6.0** 改版重點：per-station Y 軸範圍 + 動態縮放 + clear_log endpoint
>
> **v5.0** 改版重點：6 工位獨立 DB + 清除前歸檔
>
> **v4.0** 改版重點：最新讀值下拉化、CSV 平均整合、設定檔同步
>
> **v3.0** 改版重點：debug logger、圖表精簡、X 軸範圍動態、CSV 整合匯出、設定檔同步
>
> **v2.0** 改版重點：資料持久化、LTTB 降取樣、明暗主題、ring buffer 計算

---

## 目錄

1. [專案概述](#1-專案概述)
2. [與桌面版差異](#2-與桌面版差異)
3. [v3 ~ v8 改版總覽](#3-v3--v8-改版總覽)
4. [系統架構](#4-系統架構)
5. [技術選型](#5-技術選型)
6. [資料模型（SQLite Schema）](#6-資料模型sqlite-schema)
7. [模組設計](#7-模組設計)
8. [設定同步檔 config/settings.json](#8-設定同步檔-configsettingsjson)
9. [資料生命週期與 DB 佈局](#9-資料生命週期與-db-佈局)
10. [效能與降取樣策略](#10-效能與降取樣策略)
11. [前端 UI 與互動](#11-前端-ui-與互動)
12. [路由與 API](#12-路由與-api)
13. [設定頁欄位](#13-設定頁欄位)
14. [主題系統（light / dark）](#14-主題系統light--dark)
15. [執行方式](#15-執行方式)
16. [故障排除](#16-故障排除)
17. [已知限制](#17-已知限制)
18. [OTA 部署通道](#18-ota-部署通道)
19. [現況進度（2026-06-11）](#19-現況進度2026-06-11-session)
19b. [現況進度（2026-06-12 進階計算上線）](#19b-現況進度2026-06-12-進階計算上線)
19c. [現況進度（2026-06-15 PW3335 電力計上線）](#19c-現況進度2026-06-15-pw3335-電力計上線)
20. [功能改善路線圖](docs/IMPROVEMENTS.md)
21. [部署 + OTA 手冊](docs/DEPLOY_OTA.md)

---

## 1. 專案概述

### 功能

- **6 個工位 × 20 個溫度接點**（共 120 點）即時溫度監看
- 每 10 秒取樣一次，**資料以 SQLite 持久保存**（預設保留 7 天）
- 即時趨勢圖：**20 條溫度線**（圖例隱藏，顯示/隱藏統一在設定頁管理）
- 右側「最新讀值」表格：名稱、讀值、**速率 (°C/N 分鐘)**、**平均 (°C/N 分鐘)**
- 主畫面內可直接下拉調整：X 軸範圍、速率/平均計算區間
- 網頁式設定：站點選擇、接點顯示/隱藏、別名、256 色盤選色、Y 軸範圍
- 明亮 / 暗黑主題切換
- CSV 匯出：依目前 X 軸範圍，原始 10 秒/筆自動平均整合為 1 分鐘/筆
- 多瀏覽器分頁透過 SocketIO 自動同步
- Debug logger：寫到 `logs/app.log`，可從設定頁或 API 動態切換等級
- 設定值同步存成 `config/settings.json`，重新啟動自動套用
- 「每分頁 session 暫存」+「全域 SQLite / JSON 持久保存」三層架構

### 不在範圍

- **PW3335 電力計**（題目只要 GX20，桌面版的電力功能不移植）
- 多台 GX20 同時連線（本版只支援一台，Host/Port 已在 settings 留欄位可調）

### 適用場景

- 工廠/實驗室 GX20 溫度即時監看
- 7 天以上趨勢分析（接 GX20 後即可累積資料）
- 多人多裝置同時監看（透過瀏覽器）

---

## 2. 與桌面版差異

| 功能 | 桌面版 `GX20_PW3335.py` | 本網頁版 v3 |
|---|---|---|
| GUI 框架 | Tkinter | HTML / CSS / JS |
| 圖表 | matplotlib（後端算圖） | Chart.js（前端算圖） |
| 圖表曲線 | 溫度 + 速率 + 平均（三層）| **只畫溫度 20 條**；速率/平均改在右側表格 |
| 圖表圖例 | 預設顯示 | **隱藏**（顯示/隱藏統一在設定頁）|
| 資料儲存 | CSV 檔 | SQLite（持久）|
| 資料生命週期 | 永久累積在 CSV | SQLite 預設保留 7 天 |
| 關閉清除 | 不會 | **不會**（改用手動按鈕）|
| 取樣頻率 | 可調 10/60/180/300 秒 | 固定 10 秒 |
| 工位切換 | Notebook 6 個 tab | 下拉式選單（單頁）|
| 接點顏色 | 預設 | 256 色盤自選 |
| PW3335 電力 | 支援 | **不支援**（題目只要 GX20）|
| 多瀏覽器同步 | 無 | 透過 SocketIO 自動同步 |
| 主題 | 兩套（Ocean Deep / Serene Greens）| light / dark（CSS 變數）|
| 計算效能 | query_recent 全表 | ring buffer（720 筆記憶體）|
| 大資料繪圖 | matplotlib 自動處理 | LTTB 自動降取樣到 2000 點 |
| X 軸範圍 | 不可調 | 主畫面下拉：全部 / 15分 ~ 1天 |
| CSV 匯出 | 全部 + 10 秒/筆 | **依 X 軸範圍 + 平均整合為 1 分鐘/筆** |
| 跨 session 設定 | 不適用 | SQLite + JSON 檔 + sessionStorage 三層 |
| Debug 機制 | print | 結構化 logger + 檔案輪詢 + 動態等級 |
| DB 佈局 | 1 個 CSV | **v5：6 工位獨立 DB + 共用 settings DB + 歸檔保留 5 份** |
| 清除手只動 | N/A | **v5：只清當前工位 + 選擇性歸檔** |

---

## 3. v3 ~ v8 改版總覽

### v8 — CSV 中文欄位 BOM hotfix + 別名長度上限

- **`/api/export_csv` BOM 算兩次導致中文尾部被切壞**：用 `utf-8` 算 `Content-Length` + `mimetype="text/csv"` 走 `Response` 建構子
- **別名輸入框加 `maxLength=20`** + 後端 `_sanitize_csv_cell` 加 20 字截斷防線
- 詳細根因 / 修法 / 驗收記錄 → [CHANGELOG §5](CHANGELOG.md#5-現況進度2026-06-16-csv-bom-hotfix)

### v8.1 — 設定跨瀏覽器/跨電腦不同步 → debounce auto-save

- **`storage.js` `update()` 結尾加 300ms debounce 自動 POST server**：所有設定改動自動同步，跨瀏覽器/跨電腦都看得到
- **`main.js` 拿掉 `patchSettingAndApply` 結尾的舊 fetch POST**：避免 PATCH 語意覆蓋其他欄位 + 與 debounce 重複觸發
- 詳細 → [CHANGELOG §6.3](CHANGELOG.md#63-v81--debounce-auto-save)

### v8.1.1 — 自動 migrate legacy session（⚠️ 已被 v8.1.2 拿掉）

- init() 結尾掃 sessionStorage 殘留並自動同步 server
- **修法本身對，但會造成跨工位污染**（sess 內只有工位 N，merge 後整包 POST 出去會清空其他工位）
- v8.1.2 改採「遠端鎖死」策略根治
- 詳細 → [CHANGELOG §6.4–6.5](CHANGELOG.md#6-現況進度2026-06-16-設定同步-v8-1-v8-1-1-v8-1-2)

### v8.1.2 — 遠端瀏覽器鎖死設定權限（127.0.0.1 才可改）

- **決策**：只有 OTA 本機 (`127.0.0.1`) 可變更設定，遠端瀏覽器只能讀
- **判定**：以 `request.remote_addr` 為準，僅接受 `127.0.0.1` / `::1`，其他 → 403 + readonly 訊息
- **前端 UI**：
  - 遠端時「設定」按鈕 `display: none` 隱藏
  - 主畫面三 select (X軸/速率/平均) `disabled = true`
- **server**：`GET /api/settings` response 加 `is_local` 讓前端知道身份
- **根治 v8.1.1 跨工位污染**：遠端根本不能改，sessionStorage 殘留不污染 server
- 詳細 → [CHANGELOG §6.6–6.7](CHANGELOG.md#66-v812--遠端瀏覽器鎖死根治方案)

### v8.1.3 — `POST /api/clear` 也鎖遠端

- **決策**：「清除此工位」是不可逆操作（即使有 archive 也只是防呆），跟設定一樣鎖本機
- **server**：`POST /api/clear` 開頭加 `_is_local_request()` 檢查，遠端 → 403 + 「清除資料權限僅限 OTA 本機瀏覽器，遠端不能清。」
- **前端**：遠端時「清除此工位」按鈕 `display: none` 隱藏
- 詳細 → [CHANGELOG §6.10](CHANGELOG.md#610-v813--post-apiclear-也鎖遠端)

### v8.5.1 — powerMax 先 round 到 100 倍數（Chart.js nice() 不再 round 兩軸）

- **問題**：v8.5 推上線後大大回饋「**不是要對齊數值，而是軸刻度的最大值與最小值要在同一高度**」
- **根因**：Chart.js 內部會呼叫 d3-scale `nice()`，把 `y.max` round 到 1/2/5 進位。v8.5 設 yI.max=1.9349 → Chart.js nice 出 2.0；yW.max=193.49 → nice 出 200。**兩個 max 標籤都偏離 raw** → 圖上看起來仍像 1.9/193.5 在錯位
- **解法**：自己實作 `niceToHundredStep()`，把 powerMax 先 round 到 100 的倍數（200/100=2.0）→ Chart.js nice() 不會再 round → 頂端 max 標籤 = raw max → 兩軸佔比 100%/100% 絕對對齊
- **取捨**：資料峰值的絕對位置更保守（往上跳一檔），換來刻度標籤漂亮對齊（I 軸 2.0 / W 軸 200）
- **設定頁**：不動
- 詳細 → [CHANGELOG §8.1](CHANGELOG.md#81-v851--powermax-先-round-到-100-倍數讓-chartjs-nice-不再-round-兩軸)

### v8.5 — 電力圖 I/W 兩軸 max 共用 powerMax（嚴格 100:1 對齊，已被 v8.5.1 取代）

- **問題**：v8.4 上線後 I 軸 1.9 / W 軸 193.5（比 101.8，不是 100），**頂端還是沒對齊**——v8.4 用「各自峰 × 1.1」，當電壓浮動偏離 110V 時比例就不對
- **解法**：共用 `powerMax = max(iPeak*100, wPeak) × 1.1`，`yW.max = powerMax`、`yI.max = powerMax / 100` → 兩軸邊界 100% 在同一條水平線
- **取捨**：犧牲電壓浮動時 I 峰 / W 峰的絕對像素位置，換來軸邊界絕對對齊
- **設定頁**：維持 v8.4 設定（I.max / W.max disabled）
- 詳細 → [CHANGELOG §8.2](CHANGELOG.md#82-v85--電力圖-iw-軸邊界共用-powerMax嚴格-1001-對齊已被-v851-取代)

### v8.4 — 電力圖 I/W 兩軸 0 與 max 位置對齊（已被 v8.5.1 取代）

- **問題**：I 軸 max 預設 5 A，W 軸 max 預設 250 W，跟實際資料（I~2.7 A、W~200 W）不成比例 → 兩條 0 線視覺上不在同一條水平線、頂端也錯位
- **解法**（已被 v8.5.1 取代）：I 軸與 W 軸的 max **永遠動態算** = 當前可見資料的 I 峰 / W 峰 × 1.1，min 沿用設定（預設 0）→ 兩軸比例尺相同，0 線與頂端線重疊
- **設定頁**：I.max / W.max 欄位 disabled、「自動縮放 I / W」checkbox 拿掉
- **V 軸不動**（電壓跟 I/W 沒成比例）
- 詳細 → [CHANGELOG §8.2](CHANGELOG.md#82-v84--電力圖-i-軸與-w-軸的-0-與-max-位置對齊)

### v5.0 — 6 工位獨立 DB + 清除前歸檔

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

### v4.0 — 最新讀值下拉化 + CSV 平均整合 + 設定檔同步

### v3.0 — Debug logger + 圖表精簡 + 動態 X 軸

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

### v4.0 — 最新讀值下拉化 + CSV 平均整合 + 設定檔同步

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

### 設定頁欄位精簡

- 移除「歷史視窗 (分鐘)」input → 由主畫面 X 軸下拉取代
- 移除「計算時間長度」整段（升降速率 / 平均 input）→ 由主畫面下拉取代
- 保留 [保存] 按鈕（主畫面已無此按鈕）

### v4.1 — 圖表切換 hotfix + OTA 部署通道

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

---

## 4. 系統架構

```
gx20-web-monitor/
├── README.md                       本文件
├── requirements.txt                flask + flask-socketio
├── run.py                          啟動入口（python run.py）
├── gx20_reader.py                  GX20 TCP 通訊（移植自桌面版）
├── storage.py                      SQLite 層（v5：6 工位獨立 DB + settings DB + 歸檔）
├── config.py                       預設值集中管理
├── lttb.py                         LTTB 降取樣（後端版）
├── app.py                          Flask + Flask-SocketIO 主程式
├── data/                           SQLite 檔（v5 佈局）
│   ├── gx20_<station>.db           每工位一份 samples
│   ├── gx20_settings.db            6 工位共用的 settings
│   └── archive/                    清除前歸檔（每工位保留 5 份）
│       └── gx20_<station>_<時間>.db
├── config/                         設定同步檔（v4 新增）
│   ├── settings.json               啟動時若存在 → 自動套用
│   └── settings.example.json       範例
├── logs/                           Debug logger 輸出（v3 新增）
│   └── app.log                     RotatingFileHandler（2MB × 5）
├── templates/
│   ├── index.html                  監看主頁
│   └── settings.html               設定頁
└── static/
    ├── css/
    │   └── style.css               CSS 變數主題系統
    ├── js/
    │   ├── storage.js              跨分頁 sessionStorage 設定層
    │   ├── main.js                 主頁邏輯
    │   ├── settings.js             設定頁邏輯
    │   ├── colorpicker.js          256 色盤
    │   └── lttb.js                 LTTB 降取樣（前端版）
    └── vendor/
        ├── chart.umd.min.js
        ├── chartjs-adapter-date-fns.bundle.min.js
        └── socket.io.min.js
```

---

## 5. 技術選型

| 項目 | 選擇 | 理由 |
|---|---|---|
| 後端框架 | **Flask 3 + Flask-SocketIO** | 輕量；WebSocket 即時推送；Python 生態 |
| 圖表 | **Chart.js** + `chartjs-adapter-date-fns` | 前端算圖、不吃伺服器資源；time scale、auto bounds 內建 |
| 即時通訊 | **Socket.IO** | 廣播給所有 client；多瀏覽器同步 |
| 資料庫 | **SQLite** + WAL 模式 | 零安裝；單檔；本機使用效能足夠 |
| 排程 | `threading.Thread` + `time.sleep(10)` | poller daemon thread |
| 計算降取樣 | **LTTB**（Largest-Triangle-Three-Buckets） | 保留視覺上重要的峰谷，比等距取樣好 |
| 設定持久層 | SQLite + `config/settings.json` + `sessionStorage` | 三層：全域 ↔ 跨重啟 ↔ 分頁 UI |
| Debug log | `logging.handlers.RotatingFileHandler` | 單檔 2MB × 5 個備份，自動輪替 |
| 前端設定暫存 | `sessionStorage`（每分頁獨立）| 同一瀏覽器不同分頁可有不同 UI 狀態 |
| 前端 UI | 原生 HTML / CSS / 少量 vanilla JS | 單頁/雙頁，無需框架 |
| 顏色選擇 | 256 色盤（216 web-safe + 40 灰階） | 預設 |
| 主題 | CSS 變數 + `data-theme` 屬性 | 動態切換不需重整 |

---

## 6. 資料模型（SQLite Schema）

### `samples` 表（持久化取樣資料）

```sql
CREATE TABLE samples (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      TEXT    NOT NULL,              -- ISO 8601 (秒精度)
    station TEXT    NOT NULL,              -- '工位1' ~ '工位6'
    t01 REAL, t02 REAL, ..., t20 REAL      -- 20 個溫度欄位；無效值 NULL
);
CREATE INDEX idx_samples_station_ts ON samples(station, ts);
```

**容量估算**：

| 週期 | 每工位筆數 | 6 工位總計 | 磁碟空間 |
|---|---|---|---|
| 1 小時 | 360 | 2,160 | ~200 KB |
| 1 天 | 8,640 | 51,840 | ~5 MB |
| 7 天 | 60,480 | 362,880 | ~35 MB |

### `settings` 表（key-value 設定）

```sql
CREATE TABLE settings (
    key   TEXT PRIMARY KEY,
    value TEXT                          -- 字串，dict/list 用 JSON
);
```

**v3 設定 key 表**（v2 的 `history_minutes` 已移除）：

| key | 類型 | 預設 | 說明 |
|---|---|---|---|
| `gx20_host` | str | `<GX20_DEFAULT_HOST>` | GX20 IP |
| `gx20_port` | int | `34434` | GX20 port |
| `y_axis_min` | float | `-20` | Y 軸最小值 |
| `y_axis_max` | float | `100` | Y 軸最大值 |
| `rate_window_min` | int | `5` | 升降速率計算區間（主畫面下拉）|
| `avg_window_min` | int | `10` | 平均值計算區間（主畫面下拉）|
| `chart_x_minutes` | int | `0` | X 軸範圍（0=全部，>0=近 N 分鐘）|
| `retention_days` | int | `7` | DB 保留天數（超過自動刪除）|
| `max_points` | int | `2000` | 圖表最大顯示點數（超過 LTTB 降取樣）|
| `theme` | str | `light` | `light` 或 `dark` |
| `debug_log_enabled` | int | `0` | Debug 模式（1=開，0=關）|
| `ch_visibility` | JSON | 全 true | `{"工位1":[true,...], ...}` 6×20 |
| `ch_alias` | JSON | `["Ch01",...]` | `{"工位1":["","",...], ...}` 6×20 |
| `ch_color` | JSON | 20 色預設 | `{"工位1":["#1f77b4",...], ...}` 6×20 |

> 詳細設定流程請見 §8（`config/settings.json` 同步機制）。

---

## 7. 模組設計

### 7.1 `gx20_reader.py`

完全移植自桌面版 `GX20_PW3335.py` 的通訊部分，**協定一字不改**：

- TCP `socket.create_connection(host, port, timeout=3)`
- 指令 `FData,0,0001,1210\r\n`
- 31-char 固定格式解析（data_type / channel / unit / sign / scientific value）
- 999.9 視為無效，回傳 `None`
- 6 工位 × 20 接點的 `CHANNEL_NUMBER` 對應表

**對外主要介面**：

```python
gx = GX20(host="<GX20_DEFAULT_HOST>", port=34434)
data = gx.get_all_temperatures()
# → {"工位1": [t1, t2, ..., t20], ..., "工位6": [...]} 或 None（連線失敗）
```

### 7.2 `storage.py`

```python
storage.init_db(reset=False)         # 啟動時呼叫；reset=False 保留既有資料
storage.insert_sample(ts, station, temps[20])
storage.query_recent(station, since_minutes=60)  →  List[dict]
storage.query_latest(station)         →  dict | None
storage.purge_old_samples(retention_days)  →  int（刪除筆數）
storage.count_samples() / count_samples_by_station()
storage.clear_db()                    # 手動一鍵清除
storage.get_all_settings() / get_setting() / set_setting()
```

**WAL 模式**：`PRAGMA journal_mode=WAL` + `synchronous=NORMAL` → 並行讀取不阻塞 poller 寫入。

### 7.3 `lttb.py`（後端 LTTB 降取樣）

```python
from lttb import lttb_xy, downsample_rows

# 對 (x, y) 序列降取樣
xs, ys = lttb_xy(xs_in, ys_in, threshold=2000)

# 對 list of dict 降取樣（用 ts 為主軸切桶）
rows = downsample_rows(rows, ts_key="ts", point_keys=["t01",...], threshold=2000)
```

演算法概念：把 N 筆切成 `threshold` 桶，每桶挑「與上一選中點 + 下桶平均點構成最大三角形」的那一點。**視覺上能保留峰谷**。

### 7.4 `app.py`（Flask + SocketIO）

詳見 §12 路由表。

關鍵設計：

- **poller thread**：每 10 秒讀 GX20 → 寫 SQLite → 更新 ring buffer → emit `new_sample`
- **ring buffer**：每工位保留最近 720 筆（2hr），rate/avg 直接從記憶體算，不再 query_recent 全表
- **定期 purge**：poller 每 5 分鐘跑一次 `purge_old_samples(retention_days)`
- **LTTB on-the-fly**：`/api/history` 回應前若筆數 > `max_points` 自動降取樣
- **CSV 平均整合**：`/api/export_csv/<station>` 拉 raw rows，依分鐘 bucket 平均輸出
- **Debug logger**：RotatingFileHandler 寫到 `logs/app.log`；HTTP 請求、SocketIO 事件、poller 每輪結果都會入 log
- **設定同步**：`save_settings()` 同步 dump 到 `config/settings.json`；`main()` 啟動時若檔案存在則直接採用

### 7.5 `static/js/storage.js`（跨分頁設定層）

```
GX20State.init()     → 拉 server 設定 → 套 session 覆蓋 → 套主題
GX20State.update(k, v) → 寫 sessionStorage + 標 dirty
GX20State.save()      → POST 到 server + 清 dirty + 寫回 session 頂層 key
GX20State.setTheme(t) → 立即切換主題
```

**關鍵**：每個分頁有自己獨立的 `sessionStorage["gx20.tab_state.v1"]`，
與 SQLite / JSON 持久層分離。**切換分頁不會互相覆蓋未保存的變更**。

### 7.6 `static/js/lttb.js`（前端 LTTB）

`window.lttb(data, threshold)` 對 `{x, y}` 陣列降取樣。當前端 dataset 超過上限時保險用。

---

## 8. 設定同步檔 config/settings.json

### 三層架構

```
┌────────────────────────────────────────────┐
│ sessionStorage  (per tab, dirty until save) │
│     ↑ user changes                          │
│     │ "保存" 按鈕 → POST /api/settings      │
│     ↓                                        │
│ SQLite (gx20.db, 全部設定的事實來源)          │
│     ↑ save_settings()                       │
│     │ 同步 dump                              │
│     ↓                                        │
│ config/settings.json  (重啟時優先採用)        │
└────────────────────────────────────────────┘
```

### 啟動流程

```
main():
    1. storage.init_db(reset=False)
    2. 若 config/settings.json 存在:
         log.info("讀取設定檔 ... 直接套用")
         apply_json_to_sqlite(json)
       elif SQLite 為空:
         寫入預設 + dump 一次 settings.json
    3. 套用 debug 設定
    4. 啟動時 purge 過期資料
    5. 註冊關閉 hooks
    6. 啟動 poller thread
    7. socketio.run(...)
```

### 同步策略

- **寫入側**：`save_settings()` 寫完 SQLite 後，atomic write（`.tmp` + `os.replace`）dump 整包 merge 設定到 `config/settings.json`
- **讀取側**：`main()` 啟動時若檔案存在 → 直接讀取並寫回 SQLite（後續 `load_settings()` 仍以 SQLite 為主，但內容已被 JSON 覆蓋）
- **動底欄位**（`ch_visibility` / `ch_alias` / `ch_color`）：以 dict 完整覆寫

### 範例 `config/settings.json`

```json
{
  "avg_window_min": 10,
  "ch_alias": { "工位1": ["Ch01", "Ch02", ...], ... },
  "ch_color": { "工位1": ["#1f77b4", "#ff7f0e", ...], ... },
  "ch_visibility": { "工位1": [true, true, ...], ... },
  "chart_x_minutes": 0,
  "debug_log_enabled": 0,
  "gx20_host": "<GX20_DEFAULT_HOST>",
  "gx20_port": 34434,
  "max_points": 2000,
  "rate_window_min": 5,
  "retention_days": 7,
  "theme": "light",
  "y_axis_max": 100,
  "y_axis_min": -20
}
```

> `.gitignore` 已排除 `config/settings.json`（避免 commit 使用者實際設定）。
> 範例可參考 `config/settings.example.json`。

---

## 9. 資料生命週期與 DB 佈局

### DB 佈局（v5）

```
data/
├── gx20_<station>.db       # 6 工位各自一份 samples 表
├── gx20_settings.db        # 共用 settings 表
└── archive/
    └── gx20_<station>_<YYYYMMDD_HHMMSS>.db   # 清除前歸檔（每工位保留 5 份）
```

- **每工位獨立 samples DB** → 6 工位可非同步上下線，不互相污染
- **共用 settings DB** → GX20 連線 / 別名 / 顏色與資料分離
- **歸檔保留 5 份** → 超過自動刪最舊（可由 `storage.ARCHIVE_KEEP_PER_STATION` 調整）

### 啟動

```
1. _migrate_legacy_if_needed()        ← v5 自動：若偵測到舊 data/gx20.db，samples 按 station 切到 6 DB + settings 複製
2. config/settings.json 優先套用（v4）
3. 若 SQLite 為空 → 寫入預設值 + dump JSON
4. storage.purge_old_samples(7)       ← 逐工位刪超過 7 天的舊資料
5. 註冊 atexit / SIGINT handler（不再 clear_db）
6. 啟動 poller thread
7. socketio.run(...)
```

### 執行中

- 每 10 秒：poller 讀 GX20 → 寫「該工位」的 SQLite → 更新 ring buffer → emit `new_sample`
- 每 5 分鐘：逐工位 purge 過期資料
- DB 持續累積，**關閉程式也不會被清空**
- 使用者按「保存」→ 同步寫 SQLite + dump JSON

### 關閉

- atexit / SIGINT / SIGTERM 觸發時：只清空 ring buffer 與 GX20 連線
- **不再刪 DB**（v1 行為已拔除）
- 下次啟動可繼續累積

### 手動清除（v5）

主畫面或設定頁頂部 [清除此工位] 按鈕：

1. **第一段 confirm**：是否先歸檔到 `data/archive/`？
2. **第二段 confirm**：確認刪除？顯示此次會做的動作
3. 按下確認後：
   - `archive_station(station)` → 拷貝到 `gx20_<station>_<時間>.db` → 輪替保留 5 份
   - `clear_station_db(station)` → 刪除該工位 DB（含 WAL/SHM/JOURNAL）
   - 清空該工位 ring buffer
4. 其他工位資料完全不動
5. 設定（GX20 連線、別名、顏色）完全不動

> 設定頁可以切工位 tab，所以清除時作用於「當前選定的工位」；
> 主畫面清除時作用於「下拉選單選定的工位」。

---

## 10. 效能與降取樣策略

### 瓶頸與對策

| 瓶頸 | v1 問題 | v3 對策 |
|---|---|---|
| 7 天後 DB 巨大（35 MB）| 沒問題，但每次關閉都被清 | 改為持久 + 7 天自動 purge |
| 7 天後 `/api/history` 慢 | query_recent 全表 60k 筆 → 卡 | LTTB 降取樣到 2000 點（45ms）|
| poller 算 rate/avg 慢 | 每次 query_recent 全表 | ring buffer（720 筆記憶體運算）|
| 前端 chart 塞 60k 點 | 不可能（v1 沒這麼多資料）| 1) 後端先 LTTB 2) 前端再保險 LTTB |
| 瀏覽器記憶體爆 | 沒考慮過 | 20 接點 × 1 曲線 × 2000 點 ≈ 40k 物件 / 幾 MB |
| CSV 輸出太大 | 全部 10 秒/筆（7 天 60k 筆）| 依 X 軸範圍 + 平均整合為 1 分鐘/筆（最多 1k 筆）|
| X 軸範圍硬編碼 | 60 分鐘固定 | 下拉 15分 ~ 1天，0=全部 |
| 故障難排查 | print 在 terminal | 結構化 log 寫檔 + 動態等級 |

### 測試數據（7 天 60480 筆，單工位）

| 操作 | 耗時 |
|---|---|
| INSERT 35,210 筆/秒（batch executemany）| 1.7s / 60k 筆 |
| LTTB 60480 → 2000 | **45ms** |
| CSV 平均整合 60480 → 1440 桶 | **~30ms** |
| `query_recent` 7 天 | 1.07s（poller 不再跑這個）|
| `purge_old_samples` 7 天 | 0.35s |

### 降取樣演算法：LTTB

比「每 N 點取 1 點」更視覺友善，能保留峰谷：

- 第一點與最後一點**永遠保留**
- 中間切成 `threshold` 桶，每桶挑「與上一選中點 + 下桶平均點構成最大三角形」的那一點
- 1000 點 sin 波 + 3 個 spike 降到 100 點：spike **3 個全保留**

### 參數調整

- `max_points = 2000`：圖表總點數上限
  - 螢幕寬度 1080px → 1 點 ≈ 0.5px → 2000 點足夠塞滿且滑順
  - 調高（如 5000）會更精細但可能卡
  - 調低（如 500）會更流暢但丟失細節
- `retention_days = 7`：DB 保留天數
  - 7 天 / 6 工位 ≈ 35 MB
  - 空間夠可調 30，空間緊可調 1~3

---

## 11. 前端 UI 與互動

### 主頁 `index.html`

```
┌──────────────────────────────────────────────────────────────────┐
│ 站點: [工位1 ▼] [設定] [儲存 CSV] [...]  ●已連線  最後更新: 14:32 │
├────────────────────────────────────────┬─────────────────────────┤
│                                          │ 最新讀值                 │
│  Chart.js 圖表區                          │ X 軸:[全部 ▼] 速率:[5] 平均:[10]│
│  - 20 條溫度線（顏色自選）                │  #  名稱   讀值  速率  平均│
│  - 圖例隱藏（顯示/隱藏在設定頁）           │  1  Ch01   25.0 +0.10  24.9│
│  - Y 軸: 溫度 (auto scale, clamp)        │  2  Ch02   25.1 +0.05  25.0│
│  - X 軸: 動態錨點（分鐘滑動）            │  ...                     │
└────────────────────────────────────────┴─────────────────────────┘
```

- **站點下拉選單**：切換時清空 chart、重新拉 history
- **三個 select 同列**：X 軸 / 速率 / 平均，變更後下個 tick 生效
  - 速率：1/2/5/10/15/30/60 分鐘
  - 平均：1/2/5/10/15/30/60 分鐘 / 3 小時 / 6 小時
  - X 軸：全部 / 15分 / 30分 / 1時 / 3時 / 6時 / 12時 / 1天
- **表頭動態單位**：「速率 (°C/N 分鐘)」「平均 (°C/N 分鐘)」跟著設定變
- **右側表格**：4 欄（#、名稱/別名、讀值、速率、平均），依隱藏設定過濾
  - 名稱規則：別名優先，別名為空時顯示頻道號（如 `0005`）
  - 速率含正負號（`+0.123` / `-0.050`）

### v6.1 新增：游標模式（量測狀態）

右側「最新讀值」標題旁多一組 toggle button：
- **即時狀態**（預設）：表格顯示即時溫度、速率、平均（同上表）
- **量測狀態**：表格改為顯示游標區間內的 **平均 / 最大 / 最小**（以實際筆數為分母）

游標線與區間 highlight：
- 量測狀態下圖表出現 **綠 / 紅兩條可拖曳垂直線**（x-bar）
- **淡黃色 highlight** 標示選取區間
- 拖曳即時更新表格（純前端從 LTTB 資料點算）
- 游標線預設放在當前 X 軸範圍的 **1/3 / 2/3** 位置
- 切換工位 → 強制回即時狀態，游標位置重置
- 切換 X 軸範圍 → 游標線重置（模式保留）
- 即時狀態下，游標線 / 區間 highlight / 區間資訊列全部隱藏

區間資訊列：顯示「區間：yy/mm/dd hh:mm:ss ~ yy/mm/dd hh:mm:ss (duration)」

### 設定頁 `settings.html`

```
┌─────────────────────────────────────────┐
│ GX20 監視 — 設定        [回監看] [保存] [立即清除 SQLite] [...] │
├─────────────────────────────────────────┤
│ 1. GX20 連線                             │
│    Host: [<GX20_DEFAULT_HOST>]   Port: [34434]   │
│                                         │
│ 2. Y 軸範圍                              │
│    最小值: [-20]  最大值: [100]           │
│    ※ 歷史視窗/時間範圍請至主畫面右側...  │
│                                         │
│ 3.5 資料保留                             │
│    DB 保留天數: [7]                      │
│    圖表最大顯示點數: [2000]              │
│                                         │
│ 3.7 偵錯                                │
│    ☑ 啟用詳細 log（記錄至 logs/app.log）  │
│                                         │
│ 4. 接點設定                               │
│    [工位1] [工位2] ... [工位6]            │
│    ☑ 全部開啟 / 隱藏                    │
│    ┌──────┬──────┬──────┬──────┐         │
│    │ #1   │ #2   │ #3   │ #4   │         │
│    │ ☑顯示│ ☑顯示│ ☑顯示│ ☑顯示│         │
│    │ 別名:[____] 顏色:[🟦]                 │
│    └──────┴──────┴──────┴──────┘         │
└─────────────────────────────────────────┘
```

### 「保存」按鈕行為

- 設定頁頂部有 [保存] 按鈕（主畫面已無此按鈕）
- 任何欄位改動 → 按鈕變橘色，文字變 `保存 ●`（提示 dirty）
- 按下保存 → 寫 server + 清 dirty + 同步 sessionStorage 頂層 + dump JSON
- **未保存就離開頁面** → 變更只存在本分頁的 sessionStorage，下次進同分頁仍在；進新分頁則消失

---

## 12. 路由與 API

### 頁面

| 路徑 | 方法 | 用途 |
|---|---|---|
| `/` | GET | 監看主頁 |
| `/settings` | GET | 設定頁 |
| `/snapshot` | GET | **v10** 歷史快照檢視頁（讀 archive DB，需 `?db=` 指向歸檔檔名） |
| `/favicon.ico` | GET | 內建 ICO |

### API

| 路徑 | 方法 | 用途 |
|---|---|---|
| `/api/settings` | GET | 讀取全部設定 |
| `/api/settings` | POST | 寫入設定（merge-write + dump JSON）|
| `/api/channels` | GET | 6 工位 × 20 接點的 4 碼頻道號 |
| `/api/history/<station>` | GET | 拉歷史；支援 `?max_points=N` LTTB 降取樣、`?since_minutes=N` |
| `/api/latest/<station>` | GET | 該站最新一筆 |
| `/api/connection` | GET | GX20 連線狀態、host/port |
| `/api/pw_connection` | GET | **v7** 6 工位 PW3335 連線狀態（`remote` / `connected` / `host` / `last_error` / `last_vip`） |
| `/api/db_stats` | GET | DB 統計（每工位筆數、時間範圍、retention、歸檔保留份數）|
| `/api/clear` | POST | **v5**：清除指定工位；body `{"station":"工位5", "archive":true}`。**v8.1.3** 起遠端瀏覽器呼叫會被 403 擋下（本機 `127.0.0.1` / `::1` 才能清） |
| `/api/archives` | GET | 查歸檔清單（`?station=工位5` 過濾）|
| `/api/export_csv/<station>` | GET | 匯出 CSV；依 X 軸範圍 + 平均整合為 1 分鐘/筆 |
| `/api/snapshot/archives` | GET | **v10** 列出 snapshot 可用 archive DB（含每檔 ts 範圍與大小） |
| `/api/snapshot/data` | GET | **v10** 讀 archive DB 歷史點給 snapshot 頁（`?db=&station=&since_minutes=&max_points=`） |
| `/api/snapshot/stats` | GET | **v10** archive DB 每工位筆數 / 時間範圍統計 |
| `/api/debug` | GET | 讀取 debug 狀態 |
| `/api/debug` | POST | 切換 debug 狀態（`{"enabled": true/false}`）|
| `/api/debug/log_tail` | GET | 查 `logs/app.log` 末段（`?lines=N`）|

### Admin（OTA 用，頭帶 `X-OTA-Token`）

| 路徑 | 方法 | 用途 |
|---|---|---|
| `/api/admin/status` | GET | OTA 狀態、token 指紋、uptime、watchdog 版本 |
| `/api/admin/ota` | POST | 推**單檔**（multipart + `target=相對路徑`） |
| `/api/admin/ota_bundle` | POST | 推**多檔**一次到位（JSON + base64） |
| `/api/admin/restart` | POST | 觸發自我重啟（body `{"delay_sec":2}`） |
| `/api/admin/clear_log` | POST | 清空 `logs/app.log` |

### SocketIO 事件

| 事件 | 方向 | payload |
|---|---|---|
| `new_sample` | S → C | `{ts, station, temps:[20], rate:[20], avg:[20]}` |

每 10 秒廣播一次給所有連線的瀏覽器分頁。

---

## 13. 設定頁欄位

| 區塊 | 欄位 | 預設 | 範圍 | 說明 |
|---|---|---|---|---|
| 1. GX20 連線 | Host | `<GX20_DEFAULT_HOST>` | — | GX20 IP |
| | Port | `34434` | — | TCP port |
| 2. Y 軸範圍 | 最小值 (°C) | `-20` | 任意 | Y 軸下限（auto scale 不會低於此）|
| | 最大值 (°C) | `100` | 任意 | Y 軸上限（auto scale 不會高於此）|
| 3.5 資料保留 | DB 保留天數 | `7` | 1~30 | 超過天數自動刪除（啟動時 + 每 5 分鐘 purge）|
| | 圖表最大顯示點數 | `2000` | 200~10000 | dataset 超過此值會 LTTB 降取樣 |
| 3.7 偵錯 | Debug log | `false` | bool | 啟用後 log 寫到 `logs/app.log`（DEBUG 等級）|
| 4. 接點設定 | 顯示 / 隱藏 | 全顯示 | bool | 隱藏的接點不畫線、不入表格（資料仍存）|
| | 別名 | `Ch01`~`Ch20` | str (≤ 20 字) | 右側表格與圖例優先顯示別名。**v8 起最長 20 字**（UI maxLength + 後端 `_sanitize_csv_cell` 防線），避免超長中文撐破版與 CSV 標頭 |
| | 顏色 | 20 色預設 | hex | 圖表曲線顏色 + 表格 swatch |

> **v3 移除的欄位**（已由主畫面下拉取代）：
> - 「2. Y 軸範圍」→「歷史視窗 (分鐘)」
> - 「3. 計算時間長度」整段（升降速率 / 平均 input）
>
> **主畫面下拉的對應設定**（會即時寫回 SQLite + dump JSON）：
> - `chart_x_minutes` (X 軸)
> - `rate_window_min` (速率區間)
> - `avg_window_min` (平均區間)

---

## 14. 主題系統（light / dark）

### 設計

- CSS 變數系統：所有顏色集中在 `:root` 與 `body[data-theme="..."]` 區塊
- `body[data-theme="light"]` / `body[data-theme="dark"]` 兩套配色
- 切換主題**不需重整**，即時生效
- 圖表軸、格線、圖例文字都從 CSS 變數讀取；`MutationObserver` 監聽主題變化自動 rebuild chart

### 顏色

| 變數 | light | dark |
|---|---|---|
| `--bg`（頁面底色）| `#f5f5dc`（米色預設）| `#1d2e17` |
| `--surface`（卡片/表格）| `#ffffff` | `#2c4521` |
| `--surface-2`（輸入框/次要）| `#ecead0` | `#395a2b` |
| `--text` | `#2a2a1a` | `#eaf2e0` |
| `--text-dim` | `#5a5a4a` | `#b8cfce` |
| `--text-mute` | `#8a8a72` | `#8a9a7a` |
| `--primary` | `#2e6b3e` | `#4c6d3b` |
| `--danger` | `#b33a3a` | `#843c39` |
| `--accent` | `#d4a017`（未保存指示）| `#ffcc44` |

### 主題切換行為

- 設定在 `sessionStorage["gx20.tab_state.v1"].theme`（每分頁獨立）
- 按下保存才寫入 server；下次重整會記得
- 預設偵測系統 `prefers-color-scheme`

---

## 15. 執行方式

### 安裝

```bash
cd "<your-project-dir>"
pip install -r requirements.txt
```

> 執行設備可以放在本機磁碟任意位置，**不必放 OneDrive**。
> SQLite WAL 模式對單機存取效能最佳。

### 啟動

```bash
python run.py
```

- 預設綁 `0.0.0.0:5000`
- 開瀏覽器：`http://localhost:5000/`（監看） / `http://localhost:5000/settings`（設定）
- 首次啟動會建立 `config/settings.json` 與 `data/gx20.db`
- 之後重啟若 `config/settings.json` 存在，**自動套用**（不必按保存）

### 關閉

- `Ctrl+C` 終止
- 關閉後 DB **保留**（下次啟動可繼續累積）
- 想清空：到設定頁按「立即清除 SQLite」

### 重啟後行為

- 之前保存的設定（接點、別名、顏色、主題、Host/Port、X 軸、速率/平均…）都還在
- DB 資料保留（最多 7 天，過期自動清）
- poller 重新開始累積新資料
- Debug log 模式沿用上次保存的狀態

### 部署在另一台電腦

1. 整個資料夾複製過去（**不要複製 `config/settings.json`**，讓新機器自己產生）
2. 安裝 Python 3.10+ 與相依
3. 啟動
4. 瀏覽器開 `http://<該機IP>:5000/`

---

## 16. 故障排除

| 症狀 | 可能原因 | 解法 |
|---|---|---|
| 連線狀態一直紅 | GX20 沒開、IP/Port 錯、網路不通 | 設定頁改 Host/Port，按保存 |
| 圖表一直空白 | 還沒累積到一筆資料 | 等 10 秒（首次取樣）|
| 圖表曲線斷斷續續 | 該接點資料常是無效值（999.9）| 檢查 GX20 該通道接線 |
| 切換主題後文字看不到 | 罕見；CSS 變數未生效 | `Ctrl+Shift+R` 強制重整 |
| console 出現 SyntaxError | 瀏覽器 cache 舊 JS | `Ctrl+Shift+R` 或開 DevTools → Network → Disable cache |
| 設定保存後進設定頁又還原 | 極罕見；sessionStorage 被清 | 確認瀏覽器未開「關閉時清除資料」|
| DB 異常大 | `retention_days` 設太大 | 調小，或手動按「清除此工位」（v5）|
| 找不到問題原因 | log 看不到細節 | 設定頁 → 3.7 偵錯 → 開啟 Debug log，查 `logs/app.log` |
| CSV 匯出空白 | 該工位 / 該 X 軸範圍內無資料 | 確認 DB 內有此工位資料 |
| 設定值重啟後不見 | `config/settings.json` 被誤刪 | 從備份還原或到設定頁重新保存 |
| 按「清除此工位」資料不見了怎麼辦 | v5 會先歸檔 | 到 `data/archive/` 找 `gx20_<station>_<時間>.db` 手動複製回去重命名 |
| 清資料後清錯工位 | 歸檔不見得夠 | 從 `data/archive/gx20_pre_migration_<時間>.db` 查舊資料 |

### log 位置

- 預設 INFO 等級 → 終端 + `logs/app.log`
- 開啟 Debug log → 設定頁或 `POST /api/debug {"enabled": true}`
- 查 log 末段：`GET /api/debug/log_tail?lines=100`

---

## 17. 已知限制

- **多進程不安全**：SQLite 不支援多 app 實例同時寫。只能部署 1 份
- **大量歷史查詢**：雖然有 LTTB，但若 6 工位 × 7 天 × 2000 點同時繪製仍需幾秒
- **速率計算**：採「首末兩點差 / 時間差」，資料稀疏時不準
- **平均計算**：採算術平均，異常值會拉偏
- **256 色盤**：web-safe 近似，未做色弱優化
- **GX20 單一連線**：本版只支援 1 台 GX20（多台需改 protocol）
- **即時通訊**：SocketIO broadcast 給所有 client，多瀏覽器開會重複接收（但前端只繪當前站，其他忽略）
- **CSV 平均**：採算術平均；若該分鐘內有突波，會被稀釋掉（建議搭配原始 DB 查詢做比對）

---

## 附錄 A：GX20 通訊協定摘要

| 項目 | 值 |
|---|---|
| 連線 | TCP |
| 預設 IP / Port | `<GX20_DEFAULT_HOST>:34434` |
| 指令 | `FData,0,0001,1210\r\n` |
| 頻道範圍 | 0001 ~ 1210（6 工位 × 20 接點）|
| 回應格式 | 每行 31 char |
| 解析欄位 | `[0]` 狀態 / `[2:6]` 頻道 / `[10:18]` 單位 / `[18]` 正負號 / `[19:31]` 科學符號值 |
| 無效值 | `999.9` 視為無效 |

詳見 `gx20_reader.py`。

## 附錄 B：LTTB 參考

論文：Sveinn Steinarsson, "Downsampling Time Series for Visual Representation" (2013)
https://skemman.is/handle/1946/15343

實作：見 `lttb.py`（後端）與 `static/js/lttb.js`（前端），兩者演算法一致。

---

## 18. OTA 部署通道

> 完整操作手冊：[docs/DEPLOY_OTA.md](docs/DEPLOY_OTA.md)
> 本節為**機制說明**（為什麼這樣設計、各元件怎麼互動、故障怎麼排查）

### 18.1 為什麼需要 OTA

| 角色 | 主機 | 工作目錄 | 同步方式 |
|------|------|----------|----------|
| 開發端 | WSL（<DEV_HOST>） | `<DEV_PROJECT_DIR>\` | 二寶改檔的起點 |
| 部署端 | Windows <DEPLOY_HOST> | `<DEPLOY_PATH>\` | 跑 python app.py |
| 同步通道 | **OTA**（HTTP） | `POST /api/admin/*` 帶 `X-OTA-Token` header | 兩端都是 Windows 跑 Python，但**沒有** OneDrive 同步 |

部署端是工廠機台，沒有開發環境，也不會裝 git、rsync、雲端同步。  
OTA 通道 = **HTTP 上傳檔 + 自我重啟**，讓二寶在 WSL 端改完 → 推檔 → 部署端自動套用。

### 18.2 系統組成（4 隻元件）

```
┌─────────────────────── 開發端 (WSL) ───────────────────────┐
│  1. ota_push.py        CLI 推送工具（單檔 / 批次 / 重啟）   │
└──────────────────────────┬──────────────────────────────────┘
                           │  HTTP POST + X-OTA-Token
                           ▼
┌─────────────────────── 部署端 (<DEPLOY_HOST>) ────────────────┐
│  2. app.py              Flask 主進程（掛載 4 個 admin 端點）│
│       └── ota.py        OTA 模組（白名單、Token、原子寫入）│
│  3. ota_watchdog.bat    Watch dog（v2 自動找 python + log）│
│       └── python app.py  ←── Flask 子進程                  │
│  4. start_forever.bat   背景啟動器（關視窗不殺 watch dog） │
│                                                               │
│  config/                                                       │
│  ├── ota_token             32 byte 隨機 token（不入版控）     │
│  └── ota_backup/<ts>/<f>  寫入前自動備份                      │
└───────────────────────────────────────────────────────────────┘
```

| 元件 | 角色 | 觸發時機 |
|------|------|----------|
| `ota_push.py` | 開發者用的 CLI（單檔 push / 批次 bundle / 純重啟 / 查狀態） | 我改完檔之後 |
| `ota.py` | Flask 內的 OTA 模組；註冊 4 個 admin endpoint | Flask 啟動時掛載 |
| `ota_watchdog.bat` | 包住 `python app.py` 的批次迴圈 | 崩潰或 OTA 重啟時接手 |
| `start_forever.bat` | 用 `start /B /MIN` 把 watch dog 開成背景 | 工廠開機 / 換人接手時 |

### 18.3 4 個 Admin 端點

由 `ota.py` 註冊到 Flask，全部走 `X-OTA-Token` header 認證：

| 方法 | 路徑 | 用途 | 請求格式 |
|------|------|------|----------|
| `GET`  | `/api/admin/status`     | 查 OTA 狀態、token 指紋、uptime、watch dog 版本 | — |
| `POST` | `/api/admin/ota`        | 推**單檔**（multipart） | `multipart/form-data` + `target=相對路徑` |
| `POST` | `/api/admin/ota_bundle` | 推**多檔**一次到位 | JSON + base64 內文 |
| `POST` | `/api/admin/restart`    | 觸發自我重啟 | JSON `{"delay_sec": 2}` |

`ota_push.py` 把這些封裝成子命令：

```bash
python3 ota_push.py status  http://<DEPLOY_HOST>:5000
python3 ota_push.py push    http://<DEPLOY_HOST>:5000 ./static/js/main.js static/js/main.js --restart
python3 ota_push.py bundle  http://<DEPLOY_HOST>:5000 ota_manifest.json
python3 ota_push.py restart http://<DEPLOY_HOST>:5000
```

### 18.4 安全性（為什麼不擔心被打）

| 風險 | 防護 | 實作位置 |
|------|------|----------|
| 未授權推檔 | Token 認證（恆定時間比對） | `ota.check_token()` |
| 路徑穿越 `../` | `os.path.normpath` + 必須落在 `APP_ROOT` 內 | `ota.resolve_target()` |
| 任意檔案覆蓋 | 寫入白名單 `ALLOWED_TARGETS` | `ota.is_allowed_target()` |
| 上傳執行檔 | 黑名單副檔名 `.pyc/.so/.dll/.exe/.sh/.ps1` | `BLOCKED_EXTS`（白名單具名 .bat 例外） |
| 寫到一半壞檔 | Atomic write：`.tmp` + `os.replace` | `ota.atomic_write()` |
| 改壞了救不回 | 寫入前自動備份到 `config/ota_backup/<ts>/` | `ota._backup_existing()` |
| Token 洩漏 | 不進版控（`.gitignore`）、只用指紋對照 | `.gitignore`、`token_fingerprint()` |

**Token 來源優先序**：

1. 環境變數 `GX20_OTA_TOKEN`（最高優先）
2. `config/ota_token` 檔（部署端 Flask 第一次啟動自動產生 32 byte 隨機 token）
3. 自動產生並寫入 `config/ota_token`

`/api/admin/status` 只回 `token_fingerprint`（sha256 前 8 碼），**絕不回傳完整 token**。  
二寶跟大大對照指紋即可確認兩端 token 一致，token 本身不透過 Telegram 明文傳。

### 18.5 一次完整推送的時序

以「修了一個 JS bug」為例：

```
開發端 (WSL)                          部署端 (<DEPLOY_HOST>)
─────────────                         ───────────────────
1. 改 static/js/main.js               
2. 跑 ota_push.py push ...            
   │
   ├─ 讀 token (config/ota_token)     
   ├─ POST /api/admin/ota  ────────►  3. ota.py 收到檔案
   │   (multipart)                       ├─ 檢查 X-OTA-Token ✅
   │                                    ├─ 檢查 target 在白名單 ✅
   │                                    ├─ 備份原檔到 ota_backup/<ts>/
   │                                    ├─ 寫 .tmp → os.replace ✅
   │   ◄── {ok: true, size: 12345}      
   │                                    
   ├─ POST /api/admin/restart ─────►  4. ota.schedule_restart(2)
   │   ({"delay_sec": 2})                ├─ 起一個 daemon thread
   │   ◄── {ok: true}                    ├─ sleep 2 秒
   │                                    └─ os._exit(0)  ← 主進程退出
   │                                    
   │                              ┌──► 5. ota_watchdog.bat 看到 exit code 0
   │                              │     ├─ 視為「正常 OTA 重啟」
   │                              │     ├─ 清掉 FAIL_COUNT
   │                              │     ├─ timeout /t 3
   │                              │     └─ goto LOOP
   │                              │         └─ python app.py   ← 新 Flask 起來
   │                              │             ├─ 載入新版 main.js
   │                              │             └─ 廣播 socket 'version_changed'
   │                              │
   │   ◄── HTTP 200 (新 Flask 接手)    
   │                                    
6. 看到連線回 200 = 推送成功          
7. Playwright 自動驗證圖表/主題/切站
```

關鍵設計點：
- **OTA 自己不 spawn 新 Flask**：只讓主進程退出，由 watch dog 接手。  
  否則 `python app.py` 會跟 `ota_watchdog.bat` 內的 `python app.py` 撞 port 5000，產生兩個 Flask 並存。
- **exit code 區分崩潰 vs 正常重啟**：`os._exit(0)` = 正常 OTA 重啟（清 FAIL_COUNT），  
  其他非零 = 崩潰（累計 FAIL_COUNT，5 次連敗才放手）。
- **重啟期間前端不白屏**：socket 自動重連（Flask-SocketIO client 內建），5~8 秒後新版上線。

### 18.6 寫入路徑白名單

只有這些路徑可以被 OTA 覆蓋（其他路徑直接 400）：

```
前端
  static/js/         static/css/        static/vendor/
  templates/

後端核心
  app.py   config.py   storage.py   gx20_reader.py
  lttb.py  run.py

OTA 自己（不能改 ota.py 繞過自己的白名單檢查）
  ota.py   ota_push.py
  ota_watchdog.bat   start_forever.bat
```

新增可寫入檔 = 改 `ota.py` 的 `ALLOWED_TARGETS` 常數 → **必須透過 OTA 推新版 ota.py 才能加**。  
（避免「OTA 模組自己允許自己寫入任意路徑」的循環漏洞。）

### 18.7 Watch Dog 狀態機

```
            ┌─────────────────────────────────────────────┐
            │ ota_watchdog.bat :LOOP                      │
            └─────────────────────────────────────────────┘
                          │
                          ▼
            ┌─────────────────────────────────────────────┐
            │  python app.py                              │
            │   ├─ 正常服務中（socket 推播、poller 跑）   │
            │   ├─ 收到 /api/admin/restart                │
            │   │    └─ 2 秒後 os._exit(0)                │
            │   ├─ 收到 /api/admin/ota                   │
            │   │    └─ atomic_write → 等下次重啟才生效   │
            │   └─ 崩潰（未捕例外）                       │
            │        └─ 立刻退出，exit code != 0          │
            └─────────────────────────────────────────────┘
                          │
            ┌─────────────┴─────────────┐
            │                           │
       exit 0 (OTA)               exit != 0 (崩潰)
            │                           │
            ▼                           ▼
   FAIL_COUNT ← 0           FAIL_COUNT += 1
   timeout 3s                timeout 3s
   goto LOOP                 若 ≥ 5 → pause 給人接手
                             否則 goto LOOP
```

### 18.8 背景啟動（工廠現場）

部署端不希望有人記得「手動跑 watch dog」 → 用 `start_forever.bat`：

```cmd
cd <DEPLOY_PATH>
start_forever.bat
```

這個 bat 跑一行 `start "" /B /MIN cmd /c ota_watchdog.bat`：
- `/B` = 在同 session 背景跑
- `/MIN` = 縮到工作列
- 父視窗關掉**不影響**子 cmd（這是 `start /B` 跟直接呼叫的差別）

驗證三件事都看到才算活：

```cmd
netstat -ano | findstr :5000      ← Flask 在 listen
tasklist | findstr python.exe     ← python 進程在跑
type logs\watchdog.log            ← watch dog 有紀錄
```

### 18.9 故障排查對照表

| 現象 | 看哪個 log | 可能原因 |
|------|-----------|----------|
| 推檔回 401 | — | Token 錯 / 兩端 token 指紋不一致 → `GET /api/admin/status` 對指紋 |
| 推檔回 400 "target 不在白名單" | — | 想寫的路徑不在 `ALLOWED_TARGETS` → 改 `ota.py` 重推 |
| 推檔回 500 "寫入失敗" | `logs/app.log` | 磁碟滿 / 權限不足 / 路徑含中文路徑問題 |
| 重啟後 Flask 沒起來 | `logs/watchdog.log` | python 路徑找不到 / `app.py` 語法錯誤（已上線版本被改壞） |
| Watch dog 連續 5 次退出 | `logs/watchdog.log` 看到 `[FATAL]` | `app.py` 進 startup 就崩潰；先手動跑 `python app.py` 看錯誤 |
| 前端一直轉圈 | `logs/app.log` 的 socket 連線 | 可能是新版前端 JS 語法錯 → 從 `ota_backup` 還原 |
| 上傳的檔內容跟本地不一樣 | — | `--target` 寫錯路徑，檔案落在白名單允許但位置不對的地方 |
| Watch dog 跑兩份（port 5000 佔用） | `tasklist` 看到兩個 python | 之前 OTA 自己 spawn Flask 的舊 bug → 已修，v4.4 後不會發生 |

### 18.10 跟 Web 版本控制的差別

| 項目 | OTA（本系統） | Git + Pull | Docker 鏡像 |
|------|--------------|-----------|-----------|
| 部署端需要 git | 不需要 | 需要 | 不需要（看鏡像倉庫） |
| 部署端需要 build | 不需要 | 看專案 | 需要（pull image） |
| 部署端需要網路 | 只需能連開發端 | 只需能連 git remote | 需 registry |
| 推送粒度 | 檔案級（單檔 / 批次） | commit 級 | image 級 |
| 失敗回滾 | 自動備份在 `ota_backup/` | `git revert` + 拉 | 重新 pull 上一 tag |
| 適用規模 | 1~3 台現場機 | 5+ 台 | 10+ 台 |
| 本系統選 OTA 的原因 | 工廠機沒裝 git 也不能 build Python 套件 | — | — |

---

## 19. 現況進度（2026-06-11 session）

本節記錄 2026-06-11 當日二寶協作發現的 bug、修法、以及進行中的工作。

### 19.1 已修好的 Bug（v4 系列）

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

### 19.2 Watch Dog 穩定性（v4.4 已上線 ✅）

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

詳細 watch dog 狀態機見 [§18.7](#187-watch-dog-狀態機)，故障排查見 [§18.9](#189-故障排查對照表)。

### 19.3 待辦：功能改善（Sprint 1）

v4 系列 bug 修完後，原本提的三大需求進入實作階段。

| 需求 | 內容 | 預估 | 狀態 |
|------|------|------|------|
| 加速理解的指標/統計 | 圖表極值標註、Y 軸參考線、統計摘要卡（最高/最低/平均/最大溫差）、表格新欄位（趨勢箭頭、視窗 Δ / min/max、距上次變化） | 中 | 待開工 |
| 介面觀看便利性 | 雙 Y 軸、十字游標同步 tooltip、快捷縮放 / 框選放大、表格排序 / 凍結 / 快速過濾、點表格行高亮圖表、Sparkline 縮圖 | 中 | 待開工 |
| 更詳細的設定參數 | 通道門檻（高/低溫 + cell 閃爍）、警報 toast、群組、顯示細節（線寬/點大小/平滑度/小數位）、Y 軸自動縮放 | 中 | 待開工 |

詳細子項見 [docs/IMPROVEMENTS.md](docs/IMPROVEMENTS.md)。

### 19.4 環境與工作流摘要

| 角色 | 主機 | 路徑 | 備註 |
|------|------|------|------|
| 開發端 | WSL (<DEV_HOST>) | `<DEV_PROJECT_DIR>` | 二寶改檔的起點 |
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

### 19.5 版本號對照（2026-06-11 20:13 快照）

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

## 19b. 現況進度（2026-06-12 進階計算上線）

本節記錄 2026-06-12 v6.1 進階計算（游標模式）的設計、實作、迭代。

### 19b.1 需求背景

參考其他工業監控軟體（如 A&D AD-1687 / HOBOware）設計「即時 / 游標」切換：
- **即時狀態**（Live）：預設，表格顯示最新讀值
- **量測狀態**（Cursor）：表格顯示游標區間內的統計值（平均 / 最大 / 最小）

提供「拖曳即計算」的即時互動，不需按「計算」按鈕。

### 19b.2 設計決策（記錄於 [docs/CURSOR_MODE.md](docs/CURSOR_MODE.md)）

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

### 19b.3 迭代歷史（8 個 commit 4 個小版本）

| 版本 | commit | 修改 |
|------|--------|------|
| v6.1 | 4d2d540 | 初版：toggle、游標拖曳、表格平均/最大/最小、debounce API 查覆蓋率 |
| v6.1.1 | 7967bcd | 修正：切換工位 / X 軸時游標線位置停在舊時間點（清空 tsLeft/tsRight、預設 1/3 / 2/3 取代 25% / 75%） |
| v6.1.2 | da57fa1 | 修正：拖曳時「區間」資訊列沒更新（onMove 漏加 updateCursorInfo()） |
| v6.1.3 | f4eaa28 | 修正：移除「資料覆蓋」整列 UI（語意不清，拖曳時跳動造成誤判） |
| v6.1.4 | 92968ae | 清理：移除 v6.1 殘留的 /api/cursor/coverage endpoint 與 storage.query_count_in_range 函式（-101 +30 行） |

### 19b.4 v6.1.3 移除「資料覆蓋」的原因

觸發事件：OTA 端實測發現，拖曳游標線在斷線區間內移動時，「資料覆蓋：xx 筆 / 預期 yy 筆 (zz%)」的數字會跳動。

語意問題：
- **預期筆數** = 區間秒數 / poll 週期（會隨區間長度成比例縮放）
- **實際筆數** 在斷線區間內變動幅度小
- 結果：pct 跳動明顯 → 使用者誤以為資料有問題

無論用方案 A（前端 LTTB 推算，誤差 ±10%）還是方案 B（後端 SQLite 查詢，準但需 debounce）都解決不了「斷線中拖曳 → 預期變動」的語意問題。使用者決策：若不影響計算的正確性，**移除不用顯示**。

### 19b.5 設計文件與迭代記錄

完整設計過程（兩個方案的優缺點比較、最終決策、未實作原因）記錄於：
- [docs/CURSOR_MODE.md](docs/CURSOR_MODE.md)：設計文件（375 行）

MEMORY.md 也記下了「使用者決策偏好」，供未來類似需求參考。

### 19b.6 版本號對照（2026-06-12 23:47 快照）

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

## 19c. 現況進度（2026-06-15 PW3335 電力計上線）

### 19c.1 背景

桌機版已能用 GW Instek PW3335 讀電壓/電流/功率（[`GX20-PW3335-Data-Collection`](../GX20-PW3335-Data-Collection/)），網頁版只接了 GX20 溫度。本次任務把 PW3335 整合進網頁版。

### 19c.2 需求與設計決策

| 決策項 | 選擇 | 理由 |
|--------|------|------|
| IP 預設 | `192.168.1.{2..7}` (工位1→.2 ... 工位6→.7) | 沿用桌機版 `GX20_PW3335.py` line 884 對應規則 |
| `remote` 預設 | 全 False | 避免第一次啟動連一堆失敗的 PW3335 |
| `remote=False` 行為 | 寫 0 值（不是 None） | 依需求 |
| 通訊失敗 | 寫 0，標記 disconnected | 連線恢復後下一輪自動重連 |
| 電力 CSV 欄位 | `V, I, W` | 依需求命名 |
| 電力 CSV 精度 | V 2 / I 3 / W 2 位小數 | 沿用桌機版 |
| 電力 Y 軸 | 左 I/W 共用、右 V 獨立 | 依需求 |
| 電力 Y 軸預設 | V(0,230) / I,W(0,250) | 依需求 |
| 電力圖表高度 | 30%（溫度 70%） | 依需求 |
| 量測模式下電力圖 | 隱藏 | 沿用「輔助 UI 在即時模式才顯示」偏好 |
| 電力線顏色 | V=黃 / I=青 / W=紅，可改 | 避開溫度 20 色；可設定頁改 |
| 電力表（右侧小表） | 即時模式顯示讀值；量測模式顯示 平均/最大/最小 | 跟溫度表同行為 |
| DB 欄位 | `v / i / w` REAL，nullable | 舊 DB 自動 ALTER 相容 |

### 19c.3 迭代歷史（5 個 commit）

| commit | 修改 |
|--------|------|
| `f8e0d48` | feat(pw3335): 新增 pw3335_reader.py + config.py 預設值擴充 |
| `7002104` | feat(pw3335): storage 加 v/i/w 欄位 + app.py poller 整合（+ /api/pw_connection） |
| `522b860` | feat(csv): 匯出 CSV 補 V/I/W 三欄 |
| `f939bdb` | feat(ui): 主畫面雙圖表 (70/30) + 電力表格 + 設定頁 PW3335 區塊 |
| (本檔) | docs: CHANGELOG / README / example 設定補 v7 |

### 19c.4 使用方式

1. **設定頁 → 1.5 PW3335 電力計**：
   - 確認 6 工位 IP（預設 `192.168.1.{2..7}`）
   - 勾選要啟用工位的「啟用」checkbox
   - 需要時改 Port（預設 3300）
   - 需要時改 V/I/W 顏色
2. **設定頁 → 2.5 電力 Y 軸範圍**：
   - 切工位 tab 設定各工位的 V / I,W 上下限
   - 勾「自動縮放」可讓 Chart.js 自行決定範圍
3. **主畫面**：
   - 上 70% 是溫度圖表（v6.1 量測模式可拖曳游標）
   - 下 30% 是電力圖表（V 右軸、I/W 左軸）
   - 右側多一張「PW3335 電力」小表，標題旁有連線狀態 badge（已連線 / 未連線 / 未啟用）
4. **量測模式**（游標開啟）：
   - 電力圖表自動隱藏
   - 電力表 cell 改顯示 區間平均/最大/最小
5. **儲存 CSV**：
   - 多了 `V, I, W` 三欄
   - V 2 位 / I 3 位 / W 2 位小數
   - 該分鐘內 v/i/w 全 None → 該欄輸出空字串（區分「沒拉到」v.s.「全 0」）

### 19c.5 新 API

| 端點 | 用途 |
|------|------|
| `GET /api/pw_connection` | 6 工位 PW3335 連線狀態（remote / connected / host / last_error / last_vip） |

### 19c.6 版本號對照（2026-06-15 快照）

| 部署端檔案 | 版本 | 對應 commit | 狀態 |
|-----------|------|-------------|------|
| `pw3335_reader.py` | v7 | f8e0d48 | ✅ 本機 commit |
| `config.py` | v7 | f8e0d48 | ✅ 本機 commit |
| `storage.py` | v7 | 7002104 | ✅ 本機 commit |
| `app.py` | v7 | 7002104 + 522b860 | ✅ 本機 commit |
| `static/js/main.js` | v7 | f939bdb | ✅ 本機 commit |
| `static/js/settings.js` | v7 | f939bdb | ✅ 本機 commit |
| `static/js/storage.js` | v7 | f939bdb | ✅ 本機 commit |
| `static/css/style.css` | v7 | f939bdb | ✅ 本機 commit |
| `templates/index.html` | v7 | f939bdb | ✅ 本機 commit |
| `templates/settings.html` | v7 | f939bdb | ✅ 本機 commit |


---

## 19d. 現況總覽（2026-07-23 快照）

§19 / §19b / §19c 三節是 **2026-06-15 之前** 的逐節進度。此後到本節更新（2026-07-23）之間共推出 v7 之後 9 個版本，因量大不再逐節搬入 README；本節只列**指標 → CHANGELOG 連結**，細節一律以 [CHANGELOG.md](CHANGELOG.md) 為準。

| 版本 | 時間 | 主題 | 細節 |
|------|------|------|------|
| **v8**    | 2026-06-16 | CSV 中文 BOM hotfix + 別名長度上限 20 字 | CHANGELOG §5 |
| **v8.1**  | 2026-06-16 | 設定跨瀏覽器/跨電腦同步（debounce auto-save） | CHANGELOG §6.3 |
| **v8.1.1**| 2026-06-16 | 舊瀏覽器 sessionStorage → init 自動同步 server | CHANGELOG §6.4 |
| **v8.1.2**| 2026-06-16 | 遠端瀏覽器鎖死設定權限（僅 `127.0.0.1` / `::1` 可改） | CHANGELOG §6.6 |
| **v8.1.3**| 2026-06-16 | `POST /api/clear` 也鎖遠端（不可逆操作一致性） | CHANGELOG §6.10 |
| **v8.2**  | 2026-06-16 | 溫度與電力 X 軸寬度對齊（afterFit hook + yR 佔位軸） | CHANGELOG §7 |
| **v8.4 / v8.5 / v8.5.1** | 2026-06-17 | 電力 I/W 軸邊界自動對齊系列（v8.4 + v8.5 共用 powerMax，v8.5.1 powerMax 先 round 到 100 倍數）→ 已全部 revert 回 v8.2 | CHANGELOG §8 |
| **v9**    | 2026-07-01 | CSV datetime 改 `YYYY/MM/DD HH:MM:SS`（避 Excel 誤判日期） | CHANGELOG §9 |
| **v10**   | 2026-07-22 | 新增 `/snapshot` 歷史快照檢視頁（讀 archive DB + 兩條 x-bar 拖曳 + wheel zoom） | CHANGELOG §10 |
| **v10.1** | 2026-07-23 | 備註欄 per-station 同步 + 備份 dump alias/note 到 `.meta.json`，snapshot 讀取時套用 | [CHANGELOG §11](CHANGELOG.md#11-現況進度2026-07-23-備註同步-archive-meta-v101) |

> 所有 v8 之後的子節連結一律看 [CHANGELOG.md](CHANGELOG.md) 目錄，按編號對應即可；子節 anchor 受 GitHub unicode normalization 影響不一致，本表先用「CHANGELOG §X」標號，不掛細 anchor。

### 19d.1 已知方向修正

2026-06-17 v8.4 / v8.5 / v8.5.1 三版連推皆因**圖表軸對齊 "一鍵自動解" 在本環境走不通**被退回，最終回退到 v8.2 使用者手動設 I.max / W.max。**未來接到圖表軸對齊自動解任務時，先問 "手動設值可不可以接受" 再動手**。完整教訓見 MEMORY 條目與 [CHANGELOG §8](CHANGELOG.md)。

### 19d.2 對應本節驗證

- 路由 / API 列表見 §12（**已補完** `/snapshot` 頁、`/api/pw_connection`、`/api/snapshot/*`、`/api/admin/clear_log`）
- 改版重點見文檔頂部（**已加 v9**，標題已從「GX20 Web Monitor — 溫度監視網頁版」改為「GX20 + PW3335 Web Monitor — 溫度 × 電力監視」）
- 開發端 → 部署端的 OTA 推送流程見 §18
- Watch dog / OTA 重啟 SOP 見 CHANGELOG §5（v8 BOM hotfix）內含 2026-06-11 v4.4 的 watch dog 修正

# Spec: Snapshot 歷史資料瀏覽頁面

> 版本：v1.1（spec，尚未實作）
> 適用版本：v9 之後
> 目標：離線瀏覽 `data/archive/` 內的備份 db 檔，進行區間統計
>
> **變更紀錄**
> - v1.1 (2026-07-22)：依大大決策調整
>   - **不切新 branch**：直接在 `web_UI` 上新增檔案
>   - **不修改既有檔案**：`index.html` / `settings.html` / `app.py` 既有 route / `storage.py` 既有函式 都不動
>   - **commit 時機**：OTA 端（`<OTA_HOST>:5000`）測試通過後，才做 commit 節點；中途不 commit

---

## 一、目標與使用者故事

### 1.1 功能目標

GX20 設備端主畫面（`/`）只保留最近 ring buffer 的即時資料；當操作員手動清空工位（會自動備份到 `data/archive/`）或想回頭看較長一段歷史時，需要一個**離線瀏覽頁面**：

1. **可載入指定備份檔**（single archive）一次看完，不與即時監控互相干擾
2. **曲線可放大 / 縮小**（zoom in / zoom out）
3. **特定曲線可顯示 / 隱藏**（圖例勾選 + checkbox panel）
4. **左右兩條 X-line 指定區間 → 統計**（沿用 `docs/CURSOR_MODE.md` 的設計）
5. **實際數據計算不失真**：統計永遠跑 SQLite 原始資料，畫面層才用 LTTB 降採樣

### 1.2 使用者故事

> **故事 1** — 操作員昨晚 11 點清了工位 4 的資料，今天 9 點想看昨天那段溫度變化。
> 他打開 `/snapshot`，從 dropdown 選「工位 4 / 20260721_225843.db」，頁面畫出整段曲線，拖曳游標線框出 22:00-23:00 的區間，看到平均溫度 / 最大最小值。

> **故事 2** — 工程師懷疑某一輪測試有異常，想只看 T05 跟 T15 兩條線。
> 他把其他 18 條的 checkbox 取消，留下 T05 / T15 兩條 → 圖上只顯示這兩條，但 X 軸統計仍然算到這兩條的資料。

---

## 二、Tech Stack（沿用既有，不新增依賴）

| 項目 | 版本 / 來源 |
|------|------------|
| 後端 | Flask（沿用 `app.py`）|
| 圖表 | Chart.js 4（`static/vendor/chart.umd.min.js`）|
| 日期軸 | chartjs-adapter-date-fns（已內建）|
| 降採樣 | `static/js/lttb.js`（瀏覽器端，已內建）|
| DB 讀取 | Python 標準 `sqlite3`（不引入 SQLAlchemy 等新依賴）|
| CSS | `static/css/style.css`（主題、卡片、按鈕全部沿用）|

**刻意不引入：**
- `chartjs-plugin-zoom`：wheel zoom / pan 自己寫 ~30 行就夠，避免再增 vendor
- `chartjs-plugin-annotation`：兩條 X-line 用 CSS overlay（沿用 index.html 既有 `#cursorOverlay` 概念）就夠，避免 plugin 依賴

---

## 三、URL & API 設計

### 3.1 新增頁面

| URL | 方法 | 說明 |
|-----|------|------|
| `/snapshot` | GET | snapshot 瀏覽頁面（新 HTML template）|
| `/`、`/settings` | — | **不修改**：topbar 不加任何按鈕；使用者直接輸入 URL 進入 `/snapshot` |

### 3.2 新增 API

| Endpoint | 方法 | Query | 回傳 |
|----------|------|-------|------|
| `/api/snapshot/archives` | GET | `?station=工位4`（可選，預設全部） | `{ok, archives: [{station, filename, ts_min, ts_max, count, size, mtime}], count}` |
| `/api/snapshot/data` | GET | `?path=<archive_filename>&station=<station>&lttb=2000` | `{ok, station, filename, ts_min, ts_max, count, series: [{key, label, points: [{x, y}, ...]}]}` |

**重要約束：**
- `path` 必須在 `data/archive/` 目錄內、且檔名匹配 `gx20_<station>_<YYYYMMDD_HHMMSS>.db`（regex 白名單）
- 拒絕任何絕對路徑、`..`、`/data/archive/` 以外的請求（**資安重點**）
- `lttb` 預設 2000（圖上最多 2000 點；統計永遠跑完整 SQLite 結果）
- 7 日上限：若備份檔資料筆數 > 50 萬，預設拒絕（或要求顯式 `lttb=true&max_points=...`）

### 3.3 錯誤格式

沿用 `app.py` 既有風格：

```json
{ "ok": false, "error": "INVALID_PATH", "message": "..." }
```

HTTP status：
- 400 路徑不合法
- 404 找不到該檔
- 500 讀檔失敗

---

## 四、UI 設計（沿用 CURSOR_MODE.md 設計原則）

### 4.1 頁面佈局

```
┌─────────────────────────────────────────────────────────────┐
│ GX20 快照瀏覽 [站點▼] [備份檔▼] [☀/🌙] [返回主畫面]  │  ← topbar
├─────────────────────────────────────────────────────────────┤
│ ┌─曲線面板（70%）─────────────┐ ┌─右側面板（30%）────────┐ │
│ │  [圖表]                      │ │ 📊 統計（兩線之間）   │ │
│ │                              │ │ ┌─────────────────┐   │ │
│ │  ◀── 綠 x-bar    紅 x-bar ▶│ │ │ 區間：18:00-20:00│   │ │
│ │  ─── T01 線 ───────────     │ │ │ 樣本數：120       │   │ │
│ │  ─── T02 線（隱藏）─        │ │ │ 平均：24.3°C      │   │ │
│ │  ─── T03 線 ───────────     │ │ │ 最大：28.1°C      │   │ │
│ │                              │ │ │ 最小：21.5°C      │   │ │
│ │                              │ │ └─────────────────┘   │ │
│ │                              │ │                       │ │
│ │                              │ │ 🔘 曲線顯示控制       │ │
│ │                              │ │ [✓] T01               │ │
│ │                              │ │ [✓] T02               │ │
│ │                              │ │ [✓] T03               │ │
│ │                              │ │ ...                   │ │
│ │                              │ │                       │ │
│ │                              │ │ ⚡ 電力（v/i/w 3 列） │ │
│ │                              │ │ （同樣格式）           │ │
│ └──────────────────────────────┘ └───────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

### 4.2 關鍵互動

| 互動 | 行為 |
|------|------|
| 載入備份檔 | 從 `/api/snapshot/archives` 拉清單 → dropdown 選 → 觸發 `/api/snapshot/data` → 畫圖 |
| Wheel 滾輪 | 在圖上滾：以游標位置為中心 zoom in/out（縮小 0.8x / 放大 1.25x）|
| 拖曳 | 在圖表空白處拖曳 → 平移 X 軸 |
| 拖曳游標線 | 即時更新右側統計（前端從 LTTB 點算，但**發送 /api/snapshot/stats** 拉 SQLite 原始值）|
| 圖例點擊 | 切換單條曲線顯示 |
| Checkbox | 額外提供 checkbox 列表（同功能）|
| 雙擊游標線 | 重置到圖表 25% / 75% 位置 |

### 4.3 統計指標（基本款）

兩條 X-line 之間，每條**顯示中**的曲線各算一組：

| 指標 | 公式 |
|------|------|
| 樣本數 | 區間內該曲線有值的點數（NULL 跳過）|
| 平均 | `sum / count`（分母 = 實際有值筆數）|
| 最大 | `max(vals)` |
| 最小 | `min(vals)` |

> **重要**：平均 / 最大 / 最小走**後端 SQLite 計算**（保留原始精度），不跑 LTTB 後的點。

---

## 五、專案結構（新增）

```
GX20-PW3335-Data-Collection/
├── app.py                              # +1 route: /snapshot, /api/snapshot/archives, /api/snapshot/data, /api/snapshot/stats
├── storage.py                          # （不動）既有 list_archives / query_* 已足夠；補 1 個 query_archive_range()
├── templates/
│   ├── index.html                      # topbar 加一個「歷史備份」按鈕（連到 /snapshot）
│   ├── settings.html                   # （不動）
│   └── snapshot.html                   # 新檔
├── static/
│   ├── css/style.css                   # 補 .snapshot-* 區塊樣式（沿用主題變數）
│   ├── js/
│   │   ├── main.js                     # （不動）
│   │   ├── snapshot.js                 # 新檔
│   │   ├── lttb.js                     # （不動，直接複用 window.lttb）
│   │   └── ...
│   └── vendor/                         # （不動）
└── tests/
    ├── __init__.py
    ├── test_storage_snapshot.py        # 新檔：query_archive_range 行為測試
    └── test_api_snapshot.py            # 新檔：3 個 API endpoint 測試 + 資安測試（路徑 traversal）
```

---

## 六、Code Style（沿用既有）

- Python: PEP 8 + 既有 app.py 的中文 docstring 風格
- JavaScript: ES2020、`"use strict"`、IIFE 模組化（沿用 `lttb.js` 寫法）
- CSS: 用 CSS 變數、不寫死顏色

範例（snapshot.js 模組化）：

```javascript
// snapshot.js
(function (window, document) {
  "use strict";

  const state = {
    station: null,
    archivePath: null,
    rawSeries: [],   // 從 /api/snapshot/data 拿到的 LTTB 後點（畫圖用）
    rawStats: {},    // 從 /api/snapshot/stats 拿到的 SQLite 原始統計（算 avg/max/min）
    cursorTsLeft: null,
    cursorTsRight: null,
  };

  async function loadArchiveList(station) { /* ... */ }
  async function loadArchiveData(path, station) { /* ... */ }
  function renderChart() { /* ... */ }
  function updateStats() { /* ... */ }

  window.snapshotApp = { loadArchiveList, loadArchiveData, renderChart, updateStats };
})(window, document);
```

---

## 七、測試策略

| 測試 | 框架 | 範圍 |
|------|------|------|
| 單元測試（storage）| pytest | `query_archive_range()` 回傳 ts 區間、筆數；邊界（空檔、不存在的檔）|
| API 測試 | pytest + Flask test_client | 3 個 endpoint；資安（路徑 traversal、`?path=../../etc/passwd` 必須 400）|
| 手動驗收（Playwright 或瀏覽器）| — | 三條需求各跑一次 |

`tests/` 目錄目前專案沒有，這次**新增**（不是 `scripts/`）。測試指令：

```bash
cd GX20-PW3335-Data-Collection
python -m pytest tests/ -v
```

不引入 CI（既有的 OTA 推送 SOP 也沒跑 CI）。

---

## 八、邊界（Boundaries）

### Always do
- 跑既有 app 測試 + 新測試後再 commit
- 對外文件 / commit 用佔位符（`<OTA_HOST_IP>` 等），不寫實 IP
- commit 前 `git config --show-origin --get-all commit.gpgsign` 確認為空（沿用 no-sign policy）
- 不刪除既有檔案，只新增
- 不修改 `storage.py` 既有函式簽名（僅新增 `query_archive_range` 一個新函式）
- **git commit 時機：所有程式碼改完、pytest 通過、OTA 端（`<OTA_HOST>:5000`）測試通過後，才做一次 commit**；中途不 commit、不 stage

### Ask first
- 修改 `app.py` 的現有 route（只允許**新增** route，不修改既有）
- 修改 `storage.py` 既有函式
- 改動 `index.html` / `settings.html`（本次不允許；snapshot 頁完全獨立）

### Never do
- 把備份檔上傳到雲端（純本機讀取）
- 用 `subprocess` 跑 sqlite3 CLI（用標準庫 `sqlite3.connect`）
- 直接用 `path` 參數做檔案操作（必須經白名單 regex 驗證）
- 把 LTTB 點當統計來源（畫面失真 ≠ 數據失真）
- **改 `index.html` topbar 加連結**：使用者直接打 `/snapshot` URL，不在即時頁放入口

---

## 九、成功標準

### 必過

- [ ] 在 `/snapshot` 頁面選「工位 4」的備份檔 → 圖表畫出來
- [ ] 圖上有 20 條溫度線（T01-T20）
- [ ] wheel 滾輪可放大 / 縮小
- [ ] 圖例或 checkbox 可切換單條曲線
- [ ] 拖曳兩條游標線 → 右側統計即時更新（**數值是 SQLite 原始值，不是 LTTB 點**）
- [ ] `?path=../../etc/passwd` 回 400
- [ ] pytest 通過

### 加分

- [ ] 暗色 / 亮色主題切換正常
- [ ] 響應式（最小支援 1024x768）

---

## 十、實作順序與 commit 節點

按 `incremental-implementation` 的垂直切片精神，但**所有切片完成、測試通過後才一次 commit**：

### 切片 1（垂直）：後端 API + 單元測試
1. `storage.py` 新增 `query_archive_range(path, station)`（既有函式不動）
2. `app.py` 新增 3 個 route（既有 route 不動）
3. `tests/test_storage_snapshot.py` + `tests/test_api_snapshot.py`

驗收：`pytest tests/ -v` 全部通過；`curl /api/snapshot/archives` 回傳正確 JSON

### 切片 2（垂直）：前端頁面
4. `templates/snapshot.html`
5. `static/js/snapshot.js`
6. `static/css/style.css` 補 `.snapshot-*` 區塊（既有 class 不動）

驗收：本機 `python app.py` 起服務後，瀏覽器 `http://localhost:5000/snapshot` 可用

### 切片 3：OTA 端實機驗證
7. 用 `ota_push.py` 把這次新增檔案推 OTA 端（`<OTA_HOST>:5000`）
8. 在 OTA 端瀏覽器 `http://<OTA_HOST>:5000/snapshot` 跑三條需求驗收
9. 確認 `/`、`/settings` 既有功能未受影響

### Commit 節點
**切片 1+2+3 全綠才做一次 commit**（commit message: `v10: snapshot viewer (歷史備份瀏覽)`），不做中途 commit。

---

## 十一、開放問題（已確認）

| # | 問題 | 大大決策 |
|---|------|---------|
| 1 | 不切新 branch | ✅ 直接在 `web_UI` 上加檔案 |
| 2 | 原即時頁不變更 | ✅ `index.html` / `settings.html` 完全不動 |
| 3 | commit 時機 | ✅ OTA 端測試通過後才做一次 commit 節點 |
| 4 | snapshot 頁是否保留 live 對照組 | 不做（保持純快照語意）|
| 5 | v/i/w 三欄是否同樣做游標統計 | 做 |
| 6 | 雙游標線配色 | 沿用 CURSOR_MODE 綠/紅 |

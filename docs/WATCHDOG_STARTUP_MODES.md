# Watchdog 啟動模式說明

> 對象：OTA 端（工廠機）維護者 + 未來接手工程師 / AGI
> 維護者：二寶（agent）
> 最近更新：2026-07-23（v2.2 watchdog 簡化後補對照文件）

---

## 0. 一句話總結

| 啟動方式 | 一句話 | 適用對象 |
|---|---|---|
| `pythonw app.py`（命令列直接） | **單次啟動**，crash / OTA 重啟都沒人接手 | 開發機（不會被 OTA 推）|
| `start_forever.bat` → `ota_watchdog.bat` | **永久 watchdog 接管**，crash 自動重啟、OTA 重啟自動接手、關 cmd 視窗不影響 | **OTA 端（正式環境，工廠機）** |

---

## 1. 兩種啟動方式的差異對照

| 面向 | `pythonw app.py` | `start_forever.bat` → `ota_watchdog.bat` |
|---|---|---|
| **誰是 Flask 的「父進程」** | `pythonw.exe` 被 cmd 直接拉起來，父 = `cmd.exe` | `ota_watchdog.bat` 跑 `:LOOP`，父 = start_forever 開的 `cmd.exe`（`/B /MIN` 背景）|
| **Flask crash 後** | Flask 死掉就 **死掉了**，沒人重啟 | watchdog 偵測到 `EXITCODE != 0` → 累積 `FAIL_COUNT` → 重啟（最多 **5 次連敗** 才放手）|
| **OTA 推送後觸發 `/api/admin/restart`** | Flask `os._exit(0)` → **沒人接手** → port 5000 釋放但**沒有新 Flask** → 後續 curl `/` 全部 timeout | Flask `os._exit(0)` → watchdog 進下一輪 `:LOOP` → **3 秒後自動 `pythonw app.py` 起來** → `netstat -ano \| findstr :5000` 就看到新 PID |
| **關閉 cmd 視窗** | Flask 一起死（父進程被殺）| 視窗關閉 **不影響** watchdog（start_forever 用 `/B /MIN` 開成無依附背景行程）|
| **log 寫哪** | `logs/app.log`（只有 Flask 自己的 log）| `logs/app.log` + **`logs/watchdog.log`**（多一份 watch dog 心跳紀錄：start / exit code / 重啟時間）|
| **debug 視窗行為** | `pythonw` = 無 console 視窗（Flask 訊息全部進 `logs/app.log`）| watchdog 自身也是 background，**無 console 視窗**。要看就 `type logs\\watchdog.log` |
| **錯誤排查黃金信號** | 沒紀錄時只能從 `app.log` 找線索 | `watchdog.log` 會標每一輪 `exit code=N` 跟時間戳，方便對到「幾點幾分掛掉」|

---

## 2. 流程圖

```
正式環境（正確做法）：
+--------------------+        crash / OTA exit(0)        +-----------+
| ota_watchdog.bat    |  <------------------------------  | pythonw   |
| ( :LOOP )           |   watchdog 接到 exit → 3 秒後     |   app.py  |
| pythonw app.py ─────┼-----→  spawn 新一輪                | (Flask)   |
+--------------------+                                    +-----------+

開發 / 測試（簡化做法）：
+----------+
| pythonw  |   ← 一旦死就死，不重啟
|  app.py  |
+----------+
```

---

## 3. 常見情境

### 情境 A：你在開發機上 F5 重 load 程式碼

→ `pythonw app.py` 一個視窗開著就好。
→ 反正你不是正式環境，這台機器**不會被 OTA 推**，所以 watchdog 不重要。

### 情境 B：OTA 端（工廠機）剛開機或重開機

→ **一定要 `start_forever.bat` 啟動**。

**流程**：

1. 雙擊 `start_forever.bat`（背景跑 `ota_watchdog.bat`）
2. 看到「Watch dog started in background. You may close this window.」→ 可以關閉這個 cmd 視窗
3. 驗證：`netstat -ano | findstr :5000` → 看到 `LISTENING` 的 PID
4. 之後 OTA 推送觸發 `/api/admin/restart` → watchdog 自動接手

---

## 4. 在 watchdog 還沒跑之前**不小心**直接 `pythonw app.py` 啟動會怎樣？

→ Flask 跑起來沒問題，但 **port 5000 被佔住**。
→ 這時才想啟動 watchdog → watchdog 內 `pythonw app.py` 會**撞 port 5000** 直接失敗 → watchdog 計 1 次 `FAIL_COUNT`。
→ 連續 5 次之後 watchdog 會 `exit /b 1` 暫停，請人介入。

**正確順序（v2.2 部署 SOP）**：

```
1. Ctrl+C 停掉所有現有 pythonw app.py
2. 確認 port 5000 沒人佔：netstat -ano | findstr :5000     ← 應該沒結果
3. 雙擊 start_forever.bat
4. 等 3 秒
5. netstat -ano | findstr :5000                            ← 看到新 PID
```

---

## 5. 怎麼看 watchdog 有沒有在跑？

```cmd
netstat -ano | findstr :5000
```

→ 看到 `LISTENING` 那一行最後的 PID。對應 `tasklist | findstr <PID>` 確認是 `pythonw.exe` 而不是別人。

**目前有在跑的跡象**：

| 跡象 | 健康 | 異常 |
|---|---|---|
| `logs\\watchdog.log` 最後一行 `[INFO] Restarting in 3 seconds ...` | ✅ | — |
| watchdog.log 最後一行是 `[WARN] Consecutive failure count: N / 5` | — | ⚠️ Flask 連續掛 N 次 |
| watchdog.log 最後一行是 `[FATAL] Stopping watch dog after 5 consecutive failures` | — | ❌ 已放手，等人接 |
| port 5000 沒人 LISTENING + watchdog.log 沒新行 | — | ❌ watchdog 自己也死了（該走情境 B 重啟一次）|

---

## 6. 相關檔案

- `ota_watchdog.bat`（HEAD = v2.2，2026-07-23）— watchdog 主迴圈
- `start_forever.bat`（HEAD 教學同步 pythonw.exe）— 背景啟動器
- `ota.py` `schedule_restart()` — 自我重啟會 `os._exit(0)`，由 watchdog 接手
- `DEPLOY_OTA.md` — OTA 推送操作手冊（v2.2 watchdog 也要走 ota_push.py bundle 才能上 OTA 端）
- `CHANGELOG §19.2` — watchdog SOP 演進史（v4.4 起的設計）

# -*- coding: utf-8 -*-
"""
app.py
======
Flask + Flask-SocketIO 主程式。

啟動流程:
  1. storage.init_db(reset=False) - 保留既有資料，只補 schema
  2. 寫入預設設定（若 settings 表為空）
  3. 啟動背景 poller thread
  4. 啟動 Flask-SocketIO

資料生命週期（v2）：
  - DB 改為持久：atexit 與 signal 不再 clear_db
  - 啟動時呼叫 purge_old_samples(retention_days) 清掉超過保留天數的舊資料
  - poller 每輪 (約 5 分鐘) 也跑一次 purge，避免無限膨脹
  - 「立即清除 SQLite」按鈕（POST /api/clear）仍可手動一鍵清空

poller 設計：
  - 維護每工位 ring buffer（最近 RING_BUFFER_SIZE 筆，預設 720=2hr）
  - rate / avg 改用 ring buffer 計算，不再 query_recent 全表掃
  - emit `new_sample`：只推 1 筆最新 + 算好的 rate/avg
  - 偶爾 emit `history_full`（首次連線時由前端主動觸發 GET /api/history）

v3 變更:
  - 加上 debug logger：log 寫到 logs/app.log（RotatingFileHandler，2MB×5）
  - debug 模式可從設定頁 / API 動態切換，會即時生效
  - 新增 chart_x_minutes 設定：0=全部資料，>0 只看最近 N 分鐘
  - poller 推播仍含 rate/avg 供右側表格；圖表本身只畫溫度線

路由:
  GET  /                       監看主頁
  GET  /settings               設定頁
  GET  /api/settings           讀取全部設定
  POST /api/settings           寫入設定
  GET  /api/history/<station>  拉取該站歷史，支援 ?max_points=N 自動 LTTB 降取樣
  GET  /api/latest/<station>   該站最新一筆
  GET  /api/connection         連線狀態
  POST /api/clear              手動清除 SQLite
  GET  /api/db_stats           資料庫統計（用於監看）
  GET  /api/debug              讀取 debug 狀態
  POST /api/debug              切換 debug 狀態
  GET  /api/debug/log_tail     查 app.log 末段
  GET  /api/pw_connection      PW3335 6 工位連線狀態 (v7)
"""

import atexit
import csv
import io
import json
import logging
import logging.handlers
import os
import signal
import threading
import time
from collections import deque
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, List, Optional, Deque

from flask import Flask, jsonify, render_template, request, send_from_directory, Response
from flask_socketio import SocketIO

import config
import storage
from gx20_reader import GX20, STATIONS, POINTS_PER_STATION, CHANNEL_NUMBER
from pw3335_reader import fetch_one_station, DEFAULT_PW3335_PORT  # v7
from lttb import downsample_rows

# === Debug logger（v3 新增）===
# 將所有重要事件寫到 logs/app.log：
#   - 啟動 / 關閉
#   - poller 每一輪輪詢結果（成功/失敗、樣本數）
#   - GX20 連線狀態變化
#   - HTTP 請求（method, path, status, ms）
#   - SocketIO 事件（join/leave/new_sample 數量）
#   - storage 動作（insert / purge / clear）
#   - 例外（自動 traceback）
#
# 等級透過 settings 表的 `debug_log_enabled` 動態切換：
#   enabled=True  →  DEBUG（含每輪輪詢細節、HTTP body 等）
#   enabled=False →  INFO（預設，只記重要事件）
# 等級改變後會立即生效（FileHandler 與 Logger 的 level 都會跟著改）

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_PATH = os.path.join(LOG_DIR, "app.log")

# === 設定同步檔（v4 新增）===
# 使用者按「保存」時，除了寫 SQLite 外，順手把整包設定 dump 進
# config/settings.json。下次重啟時若檔案存在，優先採用檔案內容並寫回 SQLite
# （以免 DB 預設值被誤重設）。
SETTINGS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config")
SETTINGS_JSON_PATH = os.path.join(SETTINGS_DIR, "settings.json")

# 共用格式：時間 等級 logger名 訊息
_LOG_FMT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_LOG_DATEFMT = "%Y-%m-%d %H:%M:%S"


def _build_file_handler() -> logging.Handler:
    """輪詢式 file handler，單檔 2MB、保留 5 個備份。"""
    fh = logging.handlers.RotatingFileHandler(
        LOG_PATH,
        maxBytes=2 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    fh.setFormatter(logging.Formatter(_LOG_FMT, datefmt=_LOG_DATEFMT))
    return fh


def _build_stream_handler() -> logging.Handler:
    sh = logging.StreamHandler()
    sh.setFormatter(logging.Formatter(_LOG_FMT, datefmt=_LOG_DATEFMT))
    return sh


def _setup_logging(debug_enabled: bool) -> None:
    """套用 log 設定。會先把舊的 handler 清掉再裝新的，
    避免重複（測試 / reload 場景）。"""
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(_build_file_handler())
    root.addHandler(_build_stream_handler())
    root.setLevel(logging.DEBUG if debug_enabled else logging.INFO)

    # Werkzeug 預設 INFO；debug 模式時降到 DEBUG
    logging.getLogger("werkzeug").setLevel(
        logging.DEBUG if debug_enabled else logging.INFO
    )
    # SocketIO/EngineIO 太吵，壓到 WARNING
    for noisy in ("socketio", "engineio", "geventwebsocket", "gevent"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _is_debug_enabled() -> bool:
    """從 settings 表讀 debug_log_enabled。"""
    try:
        v = storage.get_setting("debug_log_enabled", "0")
        return str(v) in ("1", "true", "True", "yes")
    except Exception:
        return False


def apply_log_level_from_settings() -> None:
    """從 settings 重新讀 debug flag 並重設 log 等級。"""
    _setup_logging(_is_debug_enabled())


# 啟動時先用預設（INFO）；main() 內會再依 settings 切換
_setup_logging(debug_enabled=False)

log = logging.getLogger("app")
log.debug("logger 模組載入完成（尚未決定等級）")

# === Flask / SocketIO 初始化 ===
app = Flask(__name__, static_folder="static", template_folder="templates")
# SECRET_KEY 從環境變數讀取；本機開發可放在 config/.env 或環境裡
app.config["SECRET_KEY"] = os.environ.get("FLASK_SECRET_KEY", "gx20-web-monitor-dev-only")
# CORS 白名單：v10.3+ 從 config/settings.json 的 whitelist 區塊讀取
# （沿用 /api/settings 的本機鎖寫入鏈；改檔 5 秒內熱載入）
# 預設值見 whitelist.py DEFAULTS["cors_origins"]
import whitelist as _wl
_wl.init()
socketio = SocketIO(app, cors_allowed_origins=_wl.get("cors_origins"), async_mode="threading")


# ---------- HTTP 請求 log ----------
# 紀錄每一個 HTTP 請求的方法、路徑、狀態、耗時
# 用 g._t0 傳遞開始時間

@app.before_request
def _http_before():
    request.environ["_gx20_t0"] = time.time()


@app.after_request
def _http_after(resp):
    try:
        t0 = request.environ.get("_gx20_t0")
        if t0 is not None:
            ms = (time.time() - t0) * 1000
        else:
            ms = -1
        # 靜態資源 / favicon 隨手只記 INFO 層級
        if request.path.startswith("/static/") or request.path == "/favicon.ico":
            log.debug("HTTP %s %s → %d (%.1fms)", request.method, request.path, resp.status_code, ms)
        else:
            log.info("HTTP %s %s → %d (%.1fms)", request.method, request.path, resp.status_code, ms)
    except Exception:
        pass
    return resp


# ---------- SocketIO 事件 log ----------

@socketio.on("connect")
def _on_connect():
    log.info("SocketIO connect sid=%s from %s", request.sid, request.remote_addr)


@socketio.on("disconnect")
def _on_disconnect():
    log.info("SocketIO disconnect sid=%s", request.sid)

# === 狀態 ===
state = {
    "gx20":           None,
    "poller_running": False,
    "last_error":     None,
    "last_ts":        None,
    "connected":      False,
    "last_purge_at":  0.0,        # 最後一次 purge 時間戳
    "lock":           threading.Lock(),
    # ring buffer: 每工位 → deque of (ts, temps[20], v, i, w)
    # v7：電力欄位併入同一筆 ring entry，前端推播時可一次拿全
    "ring":           {s: deque(maxlen=config.RING_BUFFER_SIZE) for s in STATIONS},
    # v7：PW3335 6 工位連線狀態 (用於 /api/pw_connection 顯示)
    "pw_connected":   {s: False for s in STATIONS},
    "pw_last_error":  {s: None for s in STATIONS},
    "pw_last_vip":    {s: (None, None, None) for s in STATIONS},  # (V, I, W) 最後成功值
}


# ---------- 設定讀寫輔助 ----------

def load_settings() -> dict:
    """合併 SQLite 設定與預設值（缺項用預設補）。

    v6：Y 軸範圍改為 per-station 結構 y_axis = {station: {min, max, auto}}。
    舊的全域 y_axis_min / y_axis_max 已停用，讀到就忽略（不向下相容）。
    """
    raw = storage.get_all_settings()
    defaults = config.default_settings()
    out = dict(defaults)

    for k in ("gx20_host", "gx20_port",
              "rate_window_min", "avg_window_min",
              "retention_days", "max_points", "chart_x_minutes"):
        if k in raw:
            try:
                out[k] = int(raw[k]) if k in (
                    "gx20_port", "rate_window_min", "avg_window_min",
                    "history_minutes", "retention_days", "max_points",
                    "chart_x_minutes"
                ) else raw[k]
            except (TypeError, ValueError):
                pass

    if "theme" in raw and raw["theme"] in ("light", "dark"):
        out["theme"] = raw["theme"]

    # v6：y_axis 為 per-station dict，每站位是 {min, max, auto}
    y_axis_raw = config.from_json(raw.get("y_axis"), default=defaults["y_axis"])
    if isinstance(y_axis_raw, dict):
        merged = dict(defaults["y_axis"])
        for st in STATIONS:
            entry = y_axis_raw.get(st)
            if not isinstance(entry, dict):
                continue
            try:
                merged[st]["min"]  = float(entry.get("min", defaults["y_axis"][st]["min"]))
                merged[st]["max"]  = float(entry.get("max", defaults["y_axis"][st]["max"]))
                merged[st]["auto"] = bool(entry.get("auto", defaults["y_axis"][st]["auto"]))
            except (TypeError, ValueError):
                # 內容型別錯誤就保留預設
                pass
        out["y_axis"] = merged

    for k in ("ch_visibility", "ch_alias", "ch_color"):
        v = config.from_json(raw.get(k), default=defaults[k])
        if isinstance(v, dict):
            merged = dict(defaults[k])
            for st in STATIONS:
                if st in v:
                    merged[st] = v[st]
            out[k] = merged

    # v10.x：備註欄（per-station 字串，上限 20 字，跟 alias 一樣跨瀏覽器同步）
    notes_raw = config.from_json(raw.get("notes"), default=defaults["notes"])
    if isinstance(notes_raw, dict):
        merged_notes = dict(defaults["notes"])
        for st in STATIONS:
            v = notes_raw.get(st)
            if isinstance(v, str):
                # 截斷到 30 字（前端 maxlength=30 防線）
                merged_notes[st] = v[:30]
            elif v is None:
                merged_notes[st] = ""
        out["notes"] = merged_notes

    # v10.2：pw3335 = {port, hosts, colors}（拿掉 remote）
    pw_raw = config.from_json(raw.get("pw3335"), default=defaults["pw3335"])
    if isinstance(pw_raw, dict):
        merged_pw = dict(defaults["pw3335"])
        # port
        try:
            merged_pw["port"] = int(pw_raw.get("port", defaults["pw3335"]["port"]))
        except (TypeError, ValueError):
            pass
        # hosts
        hosts_in = pw_raw.get("hosts")
        if isinstance(hosts_in, dict):
            for st in STATIONS:
                v = hosts_in.get(st)
                if isinstance(v, str) and v.strip():
                    merged_pw["hosts"][st] = v.strip()
        # colors
        colors_in = pw_raw.get("colors")
        if isinstance(colors_in, dict):
            for key in ("V", "I", "W"):
                v = colors_in.get(key)
                if isinstance(v, str) and v.strip():
                    merged_pw["colors"][key] = v.strip()
        out["pw3335"] = merged_pw

    # v7：pw_axis = {station: {v:{min,max,auto}, i:{min,max,auto}, w:{min,max,auto}}}
    # 向後相容：舊資料的 "iw" 會自動展開成 i + w 兩軸（預設值），不會讓現有使用者設定炸掉。
    pw_axis_raw = config.from_json(raw.get("pw_axis"), default=defaults["pw_axis"])
    if isinstance(pw_axis_raw, dict):
        merged_pw_axis = dict(defaults["pw_axis"])
        for st in STATIONS:
            entry = pw_axis_raw.get(st)
            if not isinstance(entry, dict):
                continue
            cur = {k: dict(v) for k, v in merged_pw_axis[st].items()}  # deep copy of v / i / w

            def _merge_axis(target_key: str, sub: dict):
                if not isinstance(sub, dict):
                    return
                try:
                    cur[target_key] = {
                        "min":  float(sub.get("min",  merged_pw_axis[st][target_key]["min"])),
                        "max":  float(sub.get("max",  merged_pw_axis[st][target_key]["max"])),
                        "auto": bool(sub.get("auto", merged_pw_axis[st][target_key]["auto"])),
                    }
                except (TypeError, ValueError):
                    pass

            # 新結構：v / i / w
            for axis_key in ("v", "i", "w"):
                if axis_key in entry:
                    _merge_axis(axis_key, entry[axis_key])

            # 舊結構："iw" 共用軸 → 展開到 i + w（用 iw 自己的值覆蓋兩軸的 min/max/auto）
            if "iw" in entry:
                iw_sub = entry["iw"]
                _merge_axis("i", iw_sub)
                _merge_axis("w", iw_sub)

            merged_pw_axis[st] = cur
        out["pw_axis"] = merged_pw_axis

    # v10.3.x：whitelist = {cors_origins, ota_admin_ips, remote_write_ips,
    #                       ota_allowed_targets, ota_blocked_exts}
    # 單一真相來源：whitelist.py:DEFAULTS（不再放 config.default_settings()，
    # 避免兩邊 default 漂移）。
    # SQLite 內存的是整包 JSON；缺漏子 key 用 _wl.DEFAULTS 補，
    # 型別錯的子 key 也保留 default（不讓單一欄位壞整份）。
    wl_raw = config.from_json(raw.get("whitelist"), default=_wl.get_defaults())
    if not isinstance(wl_raw, dict):
        wl_raw = _wl.get_defaults()
    wl_merged = dict(_wl.get_defaults())
    for sub_key, sub_default in _wl.DEFAULTS.items():
        v = wl_raw.get(sub_key)
        if isinstance(v, list) and all(isinstance(x, str) for x in v):
            wl_merged[sub_key] = list(v)
        # else: 保留 default（容錯不洗掉）
    out["whitelist"] = wl_merged

    return out


def save_settings(patch: dict) -> None:
    for k, v in patch.items():
        # v6：y_axis 為 per-station dict，每站位是 {min, max, auto}，以工位為單位 merge
        if k == "y_axis" and isinstance(v, dict):
            existing_raw = storage.get_setting("y_axis")
            existing = config.from_json(existing_raw, default=config.default_settings()["y_axis"])
            if not isinstance(existing, dict):
                existing = config.default_settings()["y_axis"]
            for st, val in v.items():
                if st in STATIONS and isinstance(val, dict):
                    # 取現有該工位的值，patch 覆蓋，缺項用預設
                    cur_st = existing.get(st) or config.default_settings()["y_axis"][st]
                    if not isinstance(cur_st, dict):
                        cur_st = config.default_settings()["y_axis"][st]
                    new_st = dict(cur_st)
                    for sk, sv in val.items():
                        new_st[sk] = sv
                    existing[st] = new_st
            storage.set_setting("y_axis", config.to_json(existing))
        elif k == "pw_axis" and isinstance(v, dict):
            # v7：pw_axis per-station，{v:{min,max,auto}, iw:{min,max,auto}}
            existing_raw = storage.get_setting("pw_axis")
            existing = config.from_json(existing_raw, default=config.default_settings()["pw_axis"])
            if not isinstance(existing, dict):
                existing = config.default_settings()["pw_axis"]
            for st, val in v.items():
                if st in STATIONS and isinstance(val, dict):
                    cur_st = existing.get(st) or config.default_settings()["pw_axis"][st]
                    if not isinstance(cur_st, dict):
                        cur_st = config.default_settings()["pw_axis"][st]
                    new_st = dict(cur_st)
                    # 新結構：v / i / w
                    for axis_key in ("v", "i", "w"):
                        sub = val.get(axis_key)
                        if isinstance(sub, dict):
                            new_st[axis_key] = dict(sub)
                    # 向後相容：舊 "iw" 寫入 → 展開到 i + w
                    if "iw" in val and isinstance(val["iw"], dict):
                        new_st["i"] = dict(val["iw"])
                        new_st["w"] = dict(val["iw"])
                    existing[st] = new_st
            storage.set_setting("pw_axis", config.to_json(existing))
        elif k == "pw3335" and isinstance(v, dict):
            # v10.2：pw3335 = {port, hosts, colors}（拿掉 remote）
            existing_raw = storage.get_setting("pw3335")
            existing = config.from_json(existing_raw, default=config.default_settings()["pw3335"])
            if not isinstance(existing, dict):
                existing = config.default_settings()["pw3335"]
            # port
            if "port" in v:
                try:
                    existing["port"] = int(v["port"])
                except (TypeError, ValueError):
                    pass
            # hosts
            hosts_in = v.get("hosts")
            if isinstance(hosts_in, dict):
                existing.setdefault("hosts", {})
                for st, ip in hosts_in.items():
                    if st in STATIONS and isinstance(ip, str) and ip.strip():
                        existing["hosts"][st] = ip.strip()
            # colors
            colors_in = v.get("colors")
            if isinstance(colors_in, dict):
                existing.setdefault("colors", {})
                for key in ("V", "I", "W"):
                    cv = colors_in.get(key)
                    if isinstance(cv, str) and cv.strip():
                        existing["colors"][key] = cv.strip()
            storage.set_setting("pw3335", config.to_json(existing))
        elif k in ("ch_visibility", "ch_alias", "ch_color") and isinstance(v, dict):
            existing_raw = storage.get_setting(k)
            existing = config.from_json(existing_raw, default=config.default_settings()[k])
            if not isinstance(existing, dict):
                existing = config.default_settings()[k]
            for st, val in v.items():
                if st in STATIONS:
                    existing[st] = val
            storage.set_setting(k, config.to_json(existing))
        elif k == "notes" and isinstance(v, dict):
            # v10.x：備註欄（per-station 字串，per-工位覆寫）
            existing_raw = storage.get_setting("notes")
            existing = config.from_json(existing_raw, default=config.default_settings()["notes"])
            if not isinstance(existing, dict):
                existing = config.default_settings()["notes"]
            for st, val in v.items():
                if st in STATIONS and isinstance(val, str):
                    existing[st] = val[:30]  # 與 load_settings 一致的截斷
            storage.set_setting("notes", config.to_json(existing))
        elif k == "whitelist":
            # v10.3.x：whitelist = {cors_origins, ota_admin_ips, remote_write_ips,
            #                       ota_allowed_targets, ota_blocked_exts}
            # 整包存成一個 SQLite row（key="whitelist"，value=JSON），
            # 但 5 個子 key 採 per-key merge：patch 只送 cors_origins 時，
            # 其他 4 個保留（不被洗掉）。
            # 單一真相來源仍是 whitelist.py:DEFAULTS；load_settings 那邊會再 merge。
            #
            # v10.3.x+ B 方案：非 dict 寫入直接忽略（不寫、不覆蓋 SQLite 內原值）。
            # 理由：whitelist 是關鍵安全設定，不能讓前端 bug 或惡意 payload
            # 把整個 whitelist 蓋成 str 後下次 load fallback 到 DEFAULTS。
            if not isinstance(v, dict):
                log.warning("save_settings: whitelist 型別錯誤（%s），忽略 patch（保留現有值）",
                            type(v).__name__)
                continue
            existing_raw = storage.get_setting("whitelist")
            existing = config.from_json(existing_raw, default=_wl.get_defaults())
            if not isinstance(existing, dict):
                existing = _wl.get_defaults()
            for sub_key, sub_val in v.items():
                # 只接受 whitelist.py:DEFAULTS 有的子 key，避免前端塞垃圾欄位
                # 型別檢查：必須是 list[str]，否則保留舊值（容錯不洗掉）
                if sub_key in _wl.DEFAULTS \
                        and isinstance(sub_val, list) \
                        and all(isinstance(x, str) for x in sub_val):
                    existing[sub_key] = list(sub_val)
            storage.set_setting("whitelist", config.to_json(existing))
        else:
            storage.set_setting(k, str(v))

    # v4：同步 dump 一份 JSON 到 config/settings.json
    # 寫檔採 atomic 策略：先寫 .tmp，再 os.replace
    try:
        os.makedirs(SETTINGS_DIR, exist_ok=True)
        merged = load_settings()
        tmp_path = SETTINGS_JSON_PATH + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(merged, f, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(tmp_path, SETTINGS_JSON_PATH)
        log.info("save_settings: 已同步寫入 %s", SETTINGS_JSON_PATH)
    except Exception as e:
        log.warning("save_settings: 寫入 %s 失敗: %s", SETTINGS_JSON_PATH, e)


def load_settings_from_json() -> Optional[dict]:
    """
    讀取 config/settings.json。
    讀到且格式正確 → 回傳 dict；不存在或讀檔失敗 → 回傳 None。
    """
    if not os.path.exists(SETTINGS_JSON_PATH):
        return None
    try:
        with open(SETTINGS_JSON_PATH, "r", encoding="utf-8") as f:
            data = json.loads(f.read())
        if not isinstance(data, dict):
            log.warning("%s 格式不正確（預期 dict）", SETTINGS_JSON_PATH)
            return None
        return data
    except Exception as e:
        log.warning("讀取 %s 失敗: %s", SETTINGS_JSON_PATH, e)
        return None


def apply_json_to_sqlite(d: dict) -> None:
    """
    把 JSON 內的設定寫進 SQLite（寫進後 load_settings() 仍以 SQLite 為主）
    - 以 JSON 內容為準
    - 動底欄位 (ch_visibility/alias/color) 以 JSON 內的 dict 完整覆寫
    - 其他欄位依型別處理：dict/list → config.to_json()（保留原始 JSON 雙引號以利
      下次 from_json 解析回 dict）；scalar → str()。
    v10.4+ 修：原本一律 str(v)，會把 dict 變成 Python repr（單引號），
      例如 "{'工位1': {'min': 0}}"，下次 config.from_json 解析失敗 → fallback
      到 default → 重開機後 y_axis / pw_axis / pw3335 / notes / whitelist
      等「已客製」的設定全部還原成預設值。
    """
    defaults = config.default_settings()
    for k, v in d.items():
        if k in ("ch_visibility", "ch_alias", "ch_color"):
            # 只接受 dict；錯誤值走預設
            if isinstance(v, dict):
                storage.set_setting(k, config.to_json(v))
            else:
                storage.set_setting(k, config.to_json(defaults.get(k, {})))
        elif isinstance(v, (dict, list)):
            # v10.4+：dict/list 型別用 to_json，產出合法 JSON（雙引號）。
            # save_settings() 內部也是用 to_json，兩邊型別處理對齊。
            storage.set_setting(k, config.to_json(v))
        else:
            # scalar（str/int/bool）照舊用 str()
            storage.set_setting(k, str(v))


# ---------- ring buffer 計算（不再 query_recent） ----------

def _ring_window_since(rb: Deque, since_minutes: int):
    """從 ring buffer 取 since_minutes 分鐘內的資料。"""
    if not rb:
        return []
    cutoff = datetime.now() - timedelta(minutes=since_minutes)
    return [item for item in rb if datetime.fromisoformat(item[0]) >= cutoff]


def compute_rate_from_ring(station: str, since_minutes: int, point_index: int) -> Optional[float]:
    rb = state["ring"].get(station)
    if not rb:
        return None
    win = _ring_window_since(rb, since_minutes)
    vals = []
    # v7：ring 是 5-tuple (ts, temps, v, i, w)
    for ts_str, temps, _v, _i, _w in win:
        v = temps[point_index]
        if v is not None:
            vals.append((ts_str, v))
    if len(vals) < 2:
        return None
    t0, v0 = vals[0]
    t1, v1 = vals[-1]
    try:
        dt0 = datetime.fromisoformat(t0)
        dt1 = datetime.fromisoformat(t1)
        minutes = (dt1 - dt0).total_seconds() / 60.0
        if minutes <= 0:
            return None
        return round((v1 - v0) / minutes, 3)
    except Exception:
        return None


def compute_avg_from_ring(station: str, since_minutes: int, point_index: int) -> Optional[float]:
    rb = state["ring"].get(station)
    if not rb:
        return None
    win = _ring_window_since(rb, since_minutes)
    # v7：ring 是 5-tuple (ts, temps, v, i, w)
    vals = [temps[point_index] for _, temps, _v, _i, _w in win if temps[point_index] is not None]
    if not vals:
        return None
    return round(sum(vals) / len(vals), 2)


def compute_rate_from_db(station: str, since_minutes: int, point_index: int) -> Optional[float]:
    """B1 語意（v11）：視窗內「最舊 + 最新」差 / since_minutes。

    來源 = SQLite（query_recent），不走 ring buffer，原因:
    - ring 上限 720 筆(10s/筆 × 720 ≈ 2hr) → 視窗 > 2hr 就 silently 沒有資料。
    - SQLite 有 idx_samples_ts 索引，1hr 視窗約 360 筆，SSD 上 <1ms。

    規則：
    1. 視窗內「最舊一筆（第一個有值的 channel 讀數）」減去「最新一筆（最後一個有值的 channel 讀數）」，差除 since_minutes（使用者選的視窗分鐘數，**不是實際 dt**）。
    2. 視窗內只有 1 筆有值 → 回 0.0（不變就是 0 速率）。
    3. 視窗內 0 筆有值 → 回 None。

    回傳單位：°C / 分鐘。前端表頭「°C/1hr」是不準的歷史口語，正確描述應該是「°C/{視窗分鐘數}」。
    """
    rows = storage.query_recent(station, since_minutes)
    if not rows:
        return None
    col = f"t{point_index+1:02d}"
    # 視窗內最舊那筆（第一個有值的 channel 讀數）
    v_old = None
    for r in rows:
        v = r.get(col)
        if v is not None:
            v_old = v
            break
    if v_old is None:
        return None
    # 最新一筆 = DB 倒數第一個有值（避免最新筆剛好 channel 為 None 時向前回溯）
    v_new = None
    for r in reversed(rows):
        v = r.get(col)
        if v is not None:
            v_new = v
            break
    if v_new is None:
        return None
    # 分母固定 = since_minettes（使用者選的視窗分鐘數）。視窗不足時仍以「最舊那筆」起算，不要硬傳 None。
    if v_old == v_new:
        return 0.0
    return round((v_new - v_old) / since_minutes, 4)


def compute_avg_from_db(station: str, since_minutes: int, point_index: int) -> Optional[float]:
    """視窗內全部取樣做算術平均（語意固定 = 視窗內所有取樣平均）。"""
    rows = storage.query_recent(station, since_minutes)
    if not rows:
        return None
    col = f"t{point_index+1:02d}"
    vals = [r.get(col) for r in rows if r.get(col) is not None]
    if not vals:
        return None
    return round(sum(vals) / len(vals), 2)


# ---------- Debug 輔助：把樣本簡化成可讀字串 ----------

def _fmt_temps(temps: List[Optional[float]]) -> str:
    """20 個溫度用 compact 格式呈現，例如 [25.1, 26.0, ...]"""
    return "[" + ", ".join("—" if v is None else f"{v:.1f}" for v in temps) + "]"


def _fmt_rates(rates: List[Optional[float]]) -> str:
    return "[" + ", ".join("—" if v is None else f"{v:+.2f}" for v in rates) + "]"


# ---------- 背景 poller ----------

def poller() -> None:
    log.info("poller 啟動")
    with state["lock"]:
        state["poller_running"] = True

    s = load_settings()
    state["gx20"] = GX20(host=s["gx20_host"], port=int(s["gx20_port"]))
    log.info("GX20 連線實例建立: %s:%s", s["gx20_host"], s["gx20_port"])

    round_no = 0
    while True:
        round_no += 1
        t_start = time.time()
        try:
            s = load_settings()
            gx = state["gx20"]
            if gx.gsRemoteHost != s["gx20_host"] or gx.gnRemotePort != int(s["gx20_port"]):
                log.info("GX20 連線設定變更 %s:%s → %s:%s",
                         gx.gsRemoteHost, gx.gnRemotePort,
                         s["gx20_host"], s["gx20_port"])
                gx = GX20(host=s["gx20_host"], port=int(s["gx20_port"]))
                with state["lock"]:
                    state["gx20"] = gx

            log.debug("[round #%d] 開始讀取 GX20 (%s:%s)",
                      round_no, gx.gsRemoteHost, gx.gnRemotePort)
            data = gx.get_all_temperatures()
            if data is None:
                with state["lock"]:
                    state["last_error"] = "GX20 讀取失敗"
                    state["connected"]  = False
                log.warning("[round #%d] GX20 讀取失敗（連線中斷？）", round_no)
                time.sleep(config.POLL_INTERVAL_SEC)
                continue

            ts = datetime.now().replace(microsecond=0).isoformat()

            with state["lock"]:
                state["last_ts"]    = ts
                state["last_error"] = None
                state["connected"]  = True

            # 寫 SQLite + 更新 ring buffer
            # 隔離每個工位：一個工位 DB 損壞不連累其他工位
            for station, temps in data.items():
                try:
                    storage.insert_sample(ts, station, temps)
                    state["ring"][station].append((ts, list(temps), None, None, None))
                    log.debug("[round #%d] %s 寫入 SQLite 成功，ring size=%d, temps=%s",
                              round_no, station, len(state["ring"][station]), _fmt_temps(temps))
                except Exception as e:
                    log.warning("[round #%d] %s 寫入 SQLite 失敗（不連累其他工位）: %s",
                                round_no, station, e)

            # v10.2：拉取 PW3335（6 工位一律 fetch）
            # 規則（v10.2 拿掉舊的 `remote` 開關）：
            #   - 全部 6 工位 → fetch_one_station(host, port)
            #   - 連線成功 → 用實際值寫入（V/I/W 可能為 0，機器關機是正常）
            #   - 連線失敗 / IP 未設 → 寫 0 + pw_connected[st]=False + 對應 error message
            # 一輪失敗不連累其他工位（容錯級「同步」）
            pw_settings = s.get("pw3335", {})
            pw_port = int(pw_settings.get("port", DEFAULT_PW3335_PORT))
            pw_hosts = pw_settings.get("hosts", {})
            for station in STATIONS:
                host = pw_hosts.get(station, "")
                if not host:
                    v_val, i_val, w_val, ok = 0.0, 0.0, 0.0, False
                    err_msg = f"未設定 IP（host 空字串）"
                    log.warning("[round #%d] %s PW3335: %s", round_no, station, err_msg)
                    with state["lock"]:
                        state["pw_connected"][station] = False
                        state["pw_last_error"][station] = err_msg
                else:
                    v_val, i_val, w_val, ok = fetch_one_station(host, pw_port)
                    with state["lock"]:
                        if ok:
                            state["pw_connected"][station] = True
                            state["pw_last_error"][station] = None
                            state["pw_last_vip"][station] = (v_val, i_val, w_val)
                        else:
                            state["pw_connected"][station] = False
                            state["pw_last_error"][station] = "通訊失敗（詳見 app.log）"
                    if ok:
                        log.debug("[round #%d] %s PW3335 %s:%d → V=%.2f I=%.4f W=%.2f",
                                  round_no, station, host, pw_port, v_val, i_val, w_val)
                # 把電力值併進 ring buffer 的最後一筆
                try:
                    rb = state["ring"][station]
                    if rb and rb[-1][0] == ts:
                        # 用 list 重建 tuple（deque 不支援直接修改元素）
                        prev = rb.pop()
                        rb.append((prev[0], prev[1], v_val, i_val, w_val))
                    # 補寫 DB：直接更新該 ts 那筆的 v/i/w
                    with state["lock"]:
                        # 使用 _conn_samples 直接 UPDATE
                        db_path = storage.samples_db_path(station)
                        import sqlite3 as _sq
                        _c = _sq.connect(db_path, timeout=5)
                        try:
                            _c.execute(
                                "UPDATE samples SET v=?, i=?, w=? WHERE ts=? AND station=?",
                                (v_val, i_val, w_val, ts, station),
                            )
                            _c.commit()
                        finally:
                            _c.close()
                except Exception as e:
                    log.warning("[round #%d] %s 電力寫入失敗（不連累溫度資料）: %s",
                                round_no, station, e)

            # 計算 rate / avg（用 ring buffer，不再查 DB）
            rate_window = int(s["rate_window_min"])
            avg_window  = int(s["avg_window_min"])

            for station, temps in data.items():
                try:
                    rates = [compute_rate_from_db(station, rate_window, i) for i in range(20)]
                    avgs  = [compute_avg_from_db(station, avg_window, i)  for i in range(20)]
                    # v7：取 ring 最後一筆的 (v, i, w) 一起推
                    rb_last = state["ring"][station][-1] if state["ring"][station] else None
                    pw_payload = {
                        "v": rb_last[2] if rb_last else 0.0,
                        "i": rb_last[3] if rb_last else 0.0,
                        "w": rb_last[4] if rb_last else 0.0,
                    }
                    payload = {
                        "ts":      ts,
                        "station": station,
                        "temps":   temps,
                        "rate":    rates,
                        "avg":     avgs,
                        "pw":      pw_payload,
                    }
                    socketio.emit("new_sample", payload)
                    log.debug("[round #%d] %s emit new_sample rate=%s avg=%s pw=V=%.2f I=%.4f W=%.2f",
                              round_no, station, _fmt_rates(rates), _fmt_rates(avgs),
                              pw_payload["v"], pw_payload["i"], pw_payload["w"])
                except Exception as e:
                    log.warning("[round #%d] %s 計算/推播失敗（不連累其他工位）: %s",
                                round_no, station, e)

            elapsed_ms = (time.time() - t_start) * 1000
            log.info("[round #%d] OK ts=%s stations=%d elapsed=%.1fms",
                     round_no, ts, len(data), elapsed_ms)

            # 定期 purge 過期資料（約每 5 分鐘一次）
            now_ts = time.time()
            if now_ts - state["last_purge_at"] > 300:
                retention = int(s.get("retention_days", config.DEFAULT_RETENTION_DAYS))
                try:
                    deleted = storage.purge_old_samples(retention)
                    log.info("purge 過期資料（保留 %d 天）→ 刪除 %d 筆", retention, deleted)
                except Exception as e:
                    log.warning("purge_old_samples 失敗: %s", e)
                state["last_purge_at"] = now_ts

        except Exception as e:
            log.exception("[round #%d] poller 錯誤: %s", round_no, e)
            with state["lock"]:
                state["last_error"] = str(e)
                state["connected"]  = False

        time.sleep(config.POLL_INTERVAL_SEC)


# ---------- 路由 ----------

@app.route("/favicon.ico")
def favicon():
    return send_from_directory(app.static_folder, "favicon.ico", mimetype="image/x-icon")


@app.route("/")
def index():
    return render_template(
        "index.html",
        stations=STATIONS,
        points_per_station=POINTS_PER_STATION,
    )


@app.route("/snapshot")
def snapshot_page():
    """v10: 離線瀏覽備份 db 的頁面。
    注意：本頁面不與即時頁 / 共用任何前端狀態，URL 直接進入。
    """
    return render_template(
        "snapshot.html",
        stations=STATIONS,
        points_per_station=POINTS_PER_STATION,
    )


@app.route("/settings")
def settings_page():
    return render_template(
        "settings.html",
        stations=STATIONS,
        points_per_station=POINTS_PER_STATION,
    )


@app.route("/mobile")
def mobile_page():
    """v10.3: 攜帶式溫度監測頁（簡式版面）。

    獨立頁面，桌機/手機皆同版面。
    只顯示工位切換 + 20 個 channel 讀值表，無圖表/設定/游標。
    資料源：socket.io new_sample 事件（與主頁一致），不輪詢。
    """
    return render_template(
        "mobile.html",
        stations=STATIONS,
        points_per_station=POINTS_PER_STATION,
    )


# v11.x：/calculator 計算頁
#
# 流程：使用者在 index.html 按「儲存 CSV」 → 編輯（去空白 / 刪時段）
#       → 重新上傳到 /calculator。
# /calculator 純前端解析 CSV（無上傳到 server），EF / 統計 / 週期
# 計算全在瀏覽器執行（從 chart-utils.js 抽共用函式）。
# 結果對齊 Python 原版 plot_gui_雙信.py（EnergyCalculator rev.0.5）。
#
# 對齊頁面：見 templates/calculator.html + static/js/calculator.js
# 不需要 IP 白名單（純前端）、不需要 OTA token。
@app.route("/calculator")
def calculator_page():
    return render_template(
        "calculator.html",
        stations=STATIONS,
        points_per_station=POINTS_PER_STATION,
    )


# v8.1.2：設定變更權限鎖本機
# 決策：只有 OTA 本機 (127.0.0.1) 可變更設定，遠端瀏覽器只能讀。
# 原因：v8.1.1 migrate legacy session 會「意外覆寫 server 設定」（跨工位污染），
#       根治就是讓遠端沒權限改，server 端是 single source of truth。
# 判定：以 client IP 為準，只接受 127.0.0.1 (IPv4) 跟 ::1 (IPv6)。
#       不考慮 NAT 封包型像（大大要求簡化）。
# v10.3+：從 config/settings.json 的 whitelist.remote_write_ips 讀取
REMOTE_WRITE_ALLOWED_IPS = tuple(_wl.get("remote_write_ips"))


def _is_local_request() -> bool:
    """判斷 request 是否來自本機。"""
    # request.remote_addr 是 socket 對端 IP
    return request.remote_addr in REMOTE_WRITE_ALLOWED_IPS


@app.route("/api/settings", methods=["GET"])
def api_get_settings():
    # v8.1.2：GET response 加 is_local 讓前端知道是否本機，
    # 用來決定「設定」按鈕是否隱藏、主畫面三 select 是否 disabled。
    data = load_settings()
    data = dict(data)  # copy
    data["is_local"] = _is_local_request()
    return jsonify(data)


@app.route("/api/settings", methods=["POST"])
def api_set_settings():
    # v8.1.2：只有本機可變更設定
    if not _is_local_request():
        return jsonify({
            "ok": False,
            "error": "readonly",
            "message": "設定變更權限僅限 OTA 本機瀏覽器，遠端只能讀取。"
        }), 403
    patch = request.get_json(silent=True) or {}
    if not isinstance(patch, dict):
        return jsonify({"ok": False, "error": "body 須為 JSON 物件"}), 400
    save_settings(patch)
    return jsonify({"ok": True})


@app.route("/api/history/<station>")
def api_history(station: str):
    """
    拉取該站歷史。
    支援 query string:
      ?max_points=N          → 若原始筆數 > N，自動 LTTB 降取樣到 N
                                （預設為 settings.max_points，預設 2000）
      ?since_minutes=N        → 只拉最近 N 分鐘
                                （預設為 settings.chart_x_minutes，0 = 全部資料）

    v3：原本用的 history_minutes 設定已從 UI 移除，
       改用 chart_x_minutes 為主，0 拉全部。
    """
    if station not in STATIONS:
        return jsonify({"ok": False, "error": "unknown station"}), 404
    s = load_settings()

    # 預設 since_minutes：先看 query string，沒給就以 chart_x_minutes 為準
    # 0 = 拉全部 → 以 10 年 (5_256_000 分) 上限呼叫 query_recent
    try:
        q_since = int(request.args.get("since_minutes", "-1"))
    except (TypeError, ValueError):
        q_since = -1
    if q_since < 0:
        chart_x = int(s.get("chart_x_minutes", 0) or 0)
        q_since = chart_x if chart_x > 0 else 5256000

    rows = storage.query_recent(station, since_minutes=q_since)
    original_count = len(rows)

    # 決定 max_points
    try:
        max_points = int(request.args.get("max_points", s.get("max_points", config.DEFAULT_MAX_POINTS)))
    except (TypeError, ValueError):
        max_points = config.DEFAULT_MAX_POINTS

    downsampled = False
    if max_points > 0 and original_count > max_points:
        rows = downsample_rows(rows, ts_key="ts", point_keys=[f"t{i:02d}" for i in range(1, 21)], threshold=max_points)
        downsampled = True

    return jsonify({
        "ok": True,
        "rows": rows,
        "count": len(rows),
        "original_count": original_count,
        "downsampled": downsampled,
        "max_points": max_points,
    })


# v6.1.3: 移除 /api/cursor/coverage endpoint
# （「資料覆蓋」指標在前端拖曳互動場景下意義不大，已從 UI 移除）


@app.route("/api/latest/<station>")
def api_latest(station: str):
    """取得指定工位最新一筆（完整 new_sample 格式：含 temps / rate / avg）。"""
    if station not in STATIONS:
        return jsonify({"ok": False, "error": "unknown station"}), 404
    r = storage.query_latest(station)
    if r is None:
        return jsonify({"ok": True, "payload": None})
    # 組合成 onNewSample 用的 payload（讓前端可直接 updateReadoutTable）
    s = load_settings()
    rate_window = int(s.get("rate_window_min", config.DEFAULT_RATE_WINDOW_MIN))
    avg_window = int(s.get("avg_window_min", config.DEFAULT_AVG_WINDOW_MIN))
    temps = [r.get(f"t{i+1:02d}") for i in range(20)]
    rates = [compute_rate_from_db(station, rate_window, i) for i in range(20)]
    avgs  = [compute_avg_from_db(station, avg_window,  i) for i in range(20)]
    # v7：電力值來自該 row 的 v/i/w 欄
    pw_payload = {
        "v": r.get("v"),
        "i": r.get("i"),
        "w": r.get("w"),
    }
    payload = {
        "ts":      r["ts"],
        "station": station,
        "temps":   temps,
        "rate":    rates,
        "avg":     avgs,
        "pw":      pw_payload,
    }
    return jsonify({"ok": True, "payload": payload, "row": r})


@app.route("/api/connection")
def api_connection():
    with state["lock"]:
        return jsonify({
            "connected":   state["connected"],
            "last_error":  state["last_error"],
            "last_ts":     state["last_ts"],
            "host":        state["gx20"].gsRemoteHost if state["gx20"] else None,
            "port":        state["gx20"].gnRemotePort if state["gx20"] else None,
        })


@app.route("/api/pw_connection")
def api_pw_connection():
    """
    v10.2：回傳 6 工位 PW3335 連線狀態。
    變更：拿掉 `remote` 欄位（v10.2 取消 PW3335 開關）。
    結構：{station: {connected, host, last_error, last_vip:{v,i,w}}, ...}
    """
    s = load_settings()
    pw = s.get("pw3335", {})
    hosts = pw.get("hosts", {})
    out = {}
    with state["lock"]:
        for st in STATIONS:
            v, i, w = state["pw_last_vip"].get(st, (None, None, None))
            out[st] = {
                "connected":  bool(state["pw_connected"].get(st, False)),
                "host":       hosts.get(st, ""),
                "last_error": state["pw_last_error"].get(st),
                "last_vip":   {"v": v, "i": i, "w": w},
            }
    return jsonify({"ok": True, "stations": out})


@app.route("/api/db_stats")
def api_db_stats():
    """資料庫統計（v5：跨 6 工位 DB）。"""
    s = load_settings()
    by_station = storage.count_samples_by_station()
    time_range = {st: storage.sample_time_range(st) for st in STATIONS}
    total = sum(by_station.values())
    return jsonify({
        "ok": True,
        "by_station": by_station,
        "time_range": time_range,
        "total": total,
        "retention_days": int(s.get("retention_days", config.DEFAULT_RETENTION_DAYS)),
        "archive_keep_per_station": storage.ARCHIVE_KEEP_PER_STATION,
        "db_layout": "v5 (per-station DB + shared settings DB)",
    })


@app.route("/api/clear", methods=["POST"])
def api_clear():
    """
    清除指定工位的 samples（v5）。

    v8.1.3：跟設定一樣鎖本機。
    清除是不可逆操作（即使有 archive 只能防「意外」，不能保證使用者就是要
    刪資料），違規最嚴重。如果讓遠端也能清，那只一個關錯的腳本或按錯的
    連結就把现场工位資料刪了。
    """
    # v8.1.3：跟 /api/settings 一樣鎖本機
    if not _is_local_request():
        return jsonify({
            "ok": False,
            "error": "readonly",
            "message": "清除資料權限僅限 OTA 本機瀏覽器，遠端不能清。"
        }), 403

    """
    接收：
      - body JSON 或 query string 都可以
        - station  = '工位5'         必填
        - archive  = true / false     預設 true
      - 未帶 station → 拒絕
        （原本的「全清」按鈕已移除；如需全清可明確帶 station='ALL'）
    流程：
      1) 若 archive=true → 先 archive_station(station) 拷到 archive/
      2) clear_station_db(station) 刪除該工位 DB
      3) 清空該工位 ring buffer
    """
    payload = request.get_json(silent=True) or {}
    station = (
        payload.get("station")
        or request.args.get("station")
        or request.form.get("station")
    )
    archive_flag = (
        payload.get("archive")
        if "archive" in payload
        else (request.args.get("archive", "true").lower() in ("1", "true", "yes"))
    )

    if station == "ALL":
        # 全清（不再歸檔，避免歸檔檔爆量；如需可擴充為逐一歸檔）
        deleted = 0
        with state["lock"]:
            for s in STATIONS:
                if storage.clear_station_db(s):
                    state["ring"][s].clear()
                    deleted += 1
        log.warning("api_clear: 全清 %d 個工位（未歸檔）", deleted)
        return jsonify({"ok": True, "cleared": deleted, "archived": 0, "mode": "all"})

    if not station or station not in STATIONS:
        return jsonify({"ok": False, "error": "必須帶 station 參數（工位名）"}), 400

    archived_path = None
    if archive_flag:
        archived_path = storage.archive_station(station)
    else:
        log.info("api_clear: 使用者選擇不歸檔，直接刪除 %s", station)

    ok = storage.clear_station_db(station)
    if not ok:
        return jsonify({"ok": False, "error": "刪除 DB 失敗，請看 log"}), 500

    # 清空該工位 ring buffer
    with state["lock"]:
        state["ring"][station].clear()

    log.info("api_clear: 已清除 %s（歸檔=%s，路徑=%s）", station, archive_flag, archived_path)
    return jsonify({
        "ok": True,
        "station": station,
        "archived": bool(archived_path),
        "archive_path": archived_path,
        "mode": "single",
    })


@app.route("/api/archives", methods=["GET"])
def api_archives():
    """
    列出歸檔清單。
    Query:
      ?station=工位5   只列該工位；不帶 → 列全部
    """
    s = request.args.get("station")
    if s and s not in STATIONS:
        return jsonify({"ok": False, "error": "unknown station"}), 404
    archives = storage.list_archives(station=s)
    return jsonify({
        "ok": True,
        "archives": archives,
        "count": len(archives),
        "keep_per_station": storage.ARCHIVE_KEEP_PER_STATION,
    })


# ---------- v10：snapshot viewer ----------

@app.route("/api/snapshot/archives", methods=["GET"])
def api_snapshot_archives():
    """
    列出所有備份 db 的詳細資訊（包含每個檔案的 ts_min / ts_max / count）。
    跟 /api/archives 類似，但每個項目多帶從 DB 內容讀出的時間範圍與筆數。

    Query:
      ?station=工位4   只列該工位；不帶 → 列全部
    """
    s = request.args.get("station")
    if s and s not in STATIONS:
        return jsonify({"ok": False, "error": "unknown station"}), 404
    archives = storage.list_archives_with_meta(station=s)
    return jsonify({
        "ok": True,
        "archives": archives,
        "count": len(archives),
        "keep_per_station": storage.ARCHIVE_KEEP_PER_STATION,
    })


@app.route("/api/snapshot/data", methods=["GET"])
def api_snapshot_data():
    """
    讀取指定備份 db 的內容（畫圖用）。
    Query:
      ?filename=gx20_工位4_20260721_225843.db   （必填，會經白名單 regex 驗證）
      ?max_points=N                              （預設 2000，超過會 LTTB 降取樣）
      ?ts_min=2026-07-21T22:00:00                （可選）
      ?ts_max=2026-07-21T23:00:00                （可選）

    注意：
      - filename 必須通過 storage.validate_archive_filename() 白名單
      - 走 LTTB 是為了「畫面順暢」；統計要走 /api/snapshot/stats 才不失真
      - 讀不下 / 檔案不存在 → 4xx + 錯誤訊息
    """
    fn = request.args.get("filename", "").strip()
    if not fn:
        return jsonify({"ok": False, "error": "MISSING_FILENAME",
                        "message": "filename query param 必填"}), 400
    if storage.validate_archive_filename(fn) is None:
        return jsonify({"ok": False, "error": "INVALID_FILENAME",
                        "message": "filename 不符合白名單格式"}), 400

    # max_points 預設沿用 config.DEFAULT_MAX_POINTS
    try:
        max_points = int(request.args.get("max_points", config.DEFAULT_MAX_POINTS))
    except (TypeError, ValueError):
        max_points = config.DEFAULT_MAX_POINTS

    ts_min = request.args.get("ts_min") or None
    ts_max = request.args.get("ts_max") or None

    try:
        rows = storage.query_archive_range(fn, ts_min=ts_min, ts_max=ts_max)
    except FileNotFoundError:
        return jsonify({"ok": False, "error": "NOT_FOUND",
                        "message": f"找不到備份檔 {fn}"}), 404
    except ValueError as e:
        return jsonify({"ok": False, "error": "INVALID_FILENAME",
                        "message": str(e)}), 400

    original_count = len(rows)
    downsampled = False
    if max_points > 0 and original_count > max_points:
        rows = downsample_rows(
            rows,
            ts_key="ts",
            point_keys=[f"t{i:02d}" for i in range(1, 21)],
            threshold=max_points,
        )
        downsampled = True

    return jsonify({
        "ok": True,
        "filename": fn,
        "rows": rows,
        "count": len(rows),
        "original_count": original_count,
        "downsampled": downsampled,
        "max_points": max_points,
        "ts_min": ts_min,
        "ts_max": ts_max,
        # v10.x：歸檔當時 dump 的該工位顯示設定（alias + note）。
        # snapshot 讀取時可選用這些值覆蓋預設別名 / 顯示備註。
        # 檔案不存在或缺欄位時 meta=null，不影響原本流程。
        "meta": storage.read_archive_meta(fn),
    })


@app.route("/api/snapshot/stats", methods=["GET"])
def api_snapshot_stats():
    """
    對指定備份 db 在 [ts_min, ts_max] 區間內，計算每個欄位的 avg/min/max/count。
    走 SQLite 原始資料，不經 LTTB → 不失真。

    Query:
      ?filename=gx20_工位4_20260721_225843.db   （必填）
      ?ts_min=2026-07-21T22:00:00                （必填）
      ?ts_max=2026-07-21T23:00:00                （必填）
    """
    fn = request.args.get("filename", "").strip()
    ts_min = request.args.get("ts_min", "").strip()
    ts_max = request.args.get("ts_max", "").strip()

    if not fn:
        return jsonify({"ok": False, "error": "MISSING_FILENAME",
                        "message": "filename 必填"}), 400
    if storage.validate_archive_filename(fn) is None:
        return jsonify({"ok": False, "error": "INVALID_FILENAME",
                        "message": "filename 不符合白名單格式"}), 400
    if not ts_min or not ts_max:
        return jsonify({"ok": False, "error": "MISSING_TS",
                        "message": "ts_min 與 ts_max 必填"}), 400

    try:
        stats = storage.compute_archive_stats(fn, ts_min, ts_max)
    except FileNotFoundError:
        return jsonify({"ok": False, "error": "NOT_FOUND",
                        "message": f"找不到備份檔 {fn}"}), 404
    except ValueError as e:
        return jsonify({"ok": False, "error": "INVALID_FILENAME",
                        "message": str(e)}), 400

    return jsonify({
        "ok": True,
        "filename": fn,
        "ts_min": ts_min,
        "ts_max": ts_max,
        "stats": stats,
    })


@app.route("/api/channels")
def api_channels():
    return jsonify({"ok": True, "channels": CHANNEL_NUMBER})


# ---------- Debug 控制 ----------

@app.route("/api/debug", methods=["GET", "POST"])
def api_debug():
    """
    GET  → 回傳目前 debug 狀態
    POST → 切換 debug 狀態
        body: {"enabled": true/false}
        也可省略 body（toggle）
    變更會即時生效，並寫入 settings 表。
    v8.4+：嚴格鎖本機（127.0.0.1 / ::1），遠端不可讀 log 路徑、開關。
    """
    if not _is_local_request():
        log.warning("api_debug 被擋（非本機 IP=%s）", request.remote_addr)
        return jsonify({"ok": False, "error": "readonly"}), 403
    cur = _is_debug_enabled()
    if request.method == "GET":
        return jsonify({
            "ok": True,
            "enabled": cur,
            "log_path": LOG_PATH,
        })

    patch = request.get_json(silent=True) or {}
    if "enabled" in patch:
        new_val = bool(patch["enabled"])
    else:
        new_val = not cur
    storage.set_setting("debug_log_enabled", "1" if new_val else "0")
    apply_log_level_from_settings()
    log.info("debug log 模式切換: %s → %s", cur, new_val)
    return jsonify({
        "ok": True,
        "enabled": new_val,
        "log_path": LOG_PATH,
    })


@app.route("/api/debug/log_tail")
def api_debug_log_tail():
    """
    查詢 app.log 末段（debug 模式診斷用）。
    Query:
      ?lines=N  預設 100
    v8.4+：嚴格鎖本機（127.0.0.1 / ::1），遠端不可讀 log。
    """
    if not _is_local_request():
        log.warning("api_debug/log_tail 被擋（非本機 IP=%s）", request.remote_addr)
        return jsonify({"ok": False, "error": "readonly"}), 403
    try:
        n = max(1, min(2000, int(request.args.get("lines", "100"))))
    except (TypeError, ValueError):
        n = 100
    if not os.path.exists(LOG_PATH):
        return jsonify({"ok": True, "lines": [], "log_path": LOG_PATH})
    try:
        with open(LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        tail = lines[-n:] if len(lines) > n else lines
        return jsonify({
            "ok": True,
            "lines": [l.rstrip("\n") for l in tail],
            "total": len(lines),
            "returned": len(tail),
            "log_path": LOG_PATH,
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ---------- OTA 管理 ----------

import ota as _ota

@app.route("/api/admin/status")
def api_admin_status():
    """
    檢查 OTA 狀態。
    公開可讀（不洩漏 token），只回 fingerprint 供使用者確認 token 已設定。
    v8.4+：IP 白名單限制，只有 OTA 白名單 IP 可呼叫。
    """
    if not _ota.is_allowed_ip(request.remote_addr):
        log.warning("api_admin/status 被擋（非白名單 IP=%s）", request.remote_addr)
        return jsonify({"ok": False, "error": "forbidden"}), 403
    s = _ota.status()
    return jsonify(s)


@app.route("/api/admin/ota", methods=["POST"])
def api_admin_ota():
    """
    上傳單檔 OTA 更新。
    Header: X-OTA-Token: <token>
    Form:
      - file:  檔案
      - target: 相對於 APP_ROOT 的路徑（例: static/js/main.js）
    v8.4+：IP 白名單限制。
    """
    if not _ota.is_allowed_ip(request.remote_addr):
        log.warning("api_admin/ota 被擋（非白名單 IP=%s）", request.remote_addr)
        return jsonify({"ok": False, "error": "forbidden"}), 403
    if not _ota.check_token(request.headers.get("X-OTA-Token")):
        return jsonify({"ok": False, "error": "invalid or missing token"}), 401
    target = (request.form.get("target") or "").strip()
    if not target:
        return jsonify({"ok": False, "error": "missing 'target' field"}), 400
    f = request.files.get("file")
    if not f:
        return jsonify({"ok": False, "error": "missing 'file'"}), 400
    content = f.read()
    result = _ota.save_file(target, content)
    log.info("OTA 上傳 target=%s size=%d → %s", target, len(content), result.get("ok"))
    status_code = 200 if result.get("ok") else 400
    return jsonify(result), status_code


@app.route("/api/admin/ota_bundle", methods=["POST"])
def api_admin_ota_bundle():
    """
    一次推多檔 OTA 更新。
    Header: X-OTA-Token: <token>
    Body (JSON):
      {
        "files": [
          {"target": "static/js/main.js", "content_b64": "..."},
          {"target": "templates/index.html", "content_b64": "..."}
        ],
        "restart": true   // 可選，呼叫後觸發自我重啟
      }
    """
    if not _ota.is_allowed_ip(request.remote_addr):
        log.warning("api_admin/ota_bundle 被擋（非白名單 IP=%s）", request.remote_addr)
        return jsonify({"ok": False, "error": "forbidden"}), 403
    if not _ota.check_token(request.headers.get("X-OTA-Token")):
        return jsonify({"ok": False, "error": "invalid or missing token"}), 401
    body = request.get_json(silent=True) or {}
    files = body.get("files") or []
    if not isinstance(files, list) or not files:
        return jsonify({"ok": False, "error": "missing 'files' list"}), 400
    results = []
    all_ok = True
    import base64 as _b64
    for entry in files:
        target = (entry.get("target") or "").strip()
        b64 = entry.get("content_b64") or ""
        try:
            content = _b64.b64decode(b64)
        except Exception as e:
            results.append({"target": target, "ok": False, "error": f"base64 decode: {e}"})
            all_ok = False
            continue
        r = _ota.save_file(target, content)
        results.append(r)
        if not r.get("ok"):
            all_ok = False
    log.info("OTA bundle 上傳 %d 個檔案，結果: %s", len(files),
             "ALL OK" if all_ok else "PARTIAL/FAIL")
    response = {"ok": all_ok, "results": results, "saved_count": sum(1 for r in results if r.get("ok"))}
    if body.get("restart") and all_ok:
        response["restart"] = _ota.schedule_restart(delay_sec=2)
    return jsonify(response), (200 if all_ok else 400)


@app.route("/api/admin/restart", methods=["POST"])
def api_admin_restart():
    """
    觸發自我重啟。
    Header: X-OTA-Token: <token>
    Body: {"delay": 2}  // 可選，預設 2 秒
    v8.4+：IP 白名單限制。
    """
    if not _ota.is_allowed_ip(request.remote_addr):
        log.warning("api_admin/restart 被擋（非白名單 IP=%s）", request.remote_addr)
        return jsonify({"ok": False, "error": "forbidden"}), 403
    if not _ota.check_token(request.headers.get("X-OTA-Token")):
        return jsonify({"ok": False, "error": "invalid or missing token"}), 401
    body = request.get_json(silent=True) or {}
    delay = int(body.get("delay", 2))
    delay = max(0, min(delay, 10))
    log.warning("管理員觸發自我重啟（%d 秒後）", delay)
    return jsonify(_ota.schedule_restart(delay_sec=delay))


@app.route("/api/admin/clear_log", methods=["POST"])
def api_admin_clear_log():
    """
    清空 logs/app.log 與備份檔（不需重啟）。
    Header: X-OTA-Token: <token>
    Body 可省略；保留期實作為「全清」。
    用途：debug log 開啟前先清，避免前一波資料虛胖。
    v8.4+：IP 白名單限制。
    """
    if not _ota.is_allowed_ip(request.remote_addr):
        log.warning("api_admin/clear_log 被擋（非白名單 IP=%s）", request.remote_addr)
        return jsonify({"ok": False, "error": "forbidden"}), 403
    if not _ota.check_token(request.headers.get("X-OTA-Token")):
        return jsonify({"ok": False, "error": "invalid or missing token"}), 401
    return jsonify(_ota.clear_log_file())


# ---------- CSV 匯出 ----------

def _sanitize_csv_cell(s: str) -> str:
    """避免在 CSV 頭出現裡號 / 跳脫 / 開頭運算式。"""
    if s is None:
        return ""
    s = str(s)
    # v8：別名長度上限 20 字（避免超長中文把 CSV 表格/欄寬撐爆）
    if len(s) > 20:
        s = s[:20]
    # 防止 Excel 公式注入
    if s and s[0] in ("=", "+", "-", "@"):
        s = "'" + s
    # 移除可能造成解析錯誤的字元
    for ch in ['\r', '\n', '\t', '"']:
        s = s.replace(ch, " ")
    return s


@app.route("/api/export_csv/<station>")
def api_export_csv(station: str):
    """
    匯出指定工位記錄為 CSV（v7：溫度 + 電力 V/I/W）。

    區間：
      - query string ?since_minutes=N
          > 0   → 只匯出近 N 分鐘
          = 0   → 拉全部 DB
          未帶 → 以 settings.chart_x_minutes 為準（0=拉全部，>0=近 N 分鐘）
      - chart_x_minutes 是主畫面 X 軸的長度，與圖表看到的一致
    取樣：
      - DB 原始為 10 秒一筆
      - 以「分鐘」為 bucket，同一分鐘內的所有原始樣本以算術平均
        整合成 1 筆（每個 channel 各別平均；全 None → 空字串）
    格式：
      - 編碼: UTF-8-sig (BOM)
      - 標頭: datetime, 別名1, 別名2, ... 別名20, V, I, W
      - 時間格式: %Y/%m/%d %H:%M:%S (24h, 4 位數年份；避免 Excel 誤判 AM/PM / 年份)
      - 不包含 rate / avg
      - 不考慮隱藏狀態 → 20 個接點都出
    電力值（V/I/W）：
      - 精度：V 小數 2 位、I 小數 3 位、W 小數 2 位（沿用 desktop 版）
      - 該分鐘內若有任一筆 v/i/w 為 None → 該分鐘該欄輸出空字串
        （區分「該分鐘確實都是 0」v.s.「該分鐘沒拉到電力」）
      - Decimal 累加 + ROUND_HALF_UP（與溫度同邏輯，避免 IEEE 754 誤差）
    """
    if station not in STATIONS:
        return jsonify({"ok": False, "error": "unknown station"}), 404

    # 決定時間範圍
    try:
        q_since = int(request.args.get("since_minutes", "-1"))
    except (TypeError, ValueError):
        q_since = -1

    s = load_settings()
    if q_since < 0:
        # 未帶 → 以 X 軸長度為主
        chart_x = int(s.get("chart_x_minutes", 0) or 0)
        q_since = chart_x if chart_x > 0 else 0

    if q_since > 0:
        raw_rows = storage.query_recent(station, since_minutes=q_since)
    else:
        # 拉全部：給 10 年上限
        raw_rows = storage.query_recent(station, since_minutes=5256000)

    if not raw_rows:
        return jsonify({"ok": False, "error": "no data"}), 404

    # 取得別名
    aliases = s.get("ch_alias", {}).get(station, [])

    # 造標頭（v7：加 V, I, W）
    headers = ["datetime"]
    for i in range(1, 21):
        alias = (aliases[i - 1] or "").strip() if i - 1 < len(aliases) else ""
        col_name = alias if alias else str(i)
        headers.append(_sanitize_csv_cell(col_name))
    headers.extend(["V", "I", "W"])

    # 整合 10 秒→1 分鐘：以每分鐘 bucket 算術平均
    # bucket_key 取該分鐘的整點 ISO 字串（避免浮點誤差）
    buckets: Dict[str, Dict[str, Any]] = {}   # key=YYYY-MM-DDTHH:MM:00, value={sums, counts, has_any}
    for r in raw_rows:
        try:
            dt = datetime.fromisoformat(r["ts"])
        except Exception:
            continue
        # 對齊到分鐘起點
        bucket_dt = dt.replace(second=0, microsecond=0)
        bkey = bucket_dt.isoformat()
        b = buckets.get(bkey)
        if b is None:
            b = {
                "_dt": bucket_dt,
                "sums":  [Decimal("0")] * 20,
                "cnts":  [0]   * 20,
                "any":   [False] * 20,
                # v7：電力三欄
                "pw_sums": [Decimal("0")] * 3,   # [V, I, W]
                "pw_cnts": [0] * 3,
                "pw_any":  [False] * 3,
            }
            buckets[bkey] = b
        for i in range(1, 21):
            v = r.get(f"t{i:02d}")
            if v is None:
                continue
            try:
                # 用 Decimal 累加避免 IEEE 754 float 在平均時的 0.05→0.04999 誤差
                b["sums"][i - 1] += Decimal(str(v))
                b["cnts"][i - 1] += 1
                b["any"][i - 1]  = True
            except (TypeError, ValueError):
                # 非數字視為 None，不計入平均
                continue
        # v7：電力（V, I, W）
        for k, key in enumerate(("v", "i", "w")):
            pv = r.get(key)
            if pv is None:
                continue
            try:
                b["pw_sums"][k] += Decimal(str(pv))
                b["pw_cnts"][k] += 1
                b["pw_any"][k]  = True
            except (TypeError, ValueError):
                continue

    if not buckets:
        return jsonify({"ok": False, "error": "no data"}), 404

    # 依時間排序後輸出
    sorted_buckets = sorted(buckets.values(), key=lambda b: b["_dt"])

    # 造 CSV (UTF-8-sig BOM)
    buf = io.StringIO()
    buf.write("\ufeff")  # BOM
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(headers)
    for b in sorted_buckets:
        # v9：CSV datetime 改成 YYYY/MM/DD HH:MM:SS (24h)
        # - 舊格式 %m/%d/%y 在 Excel 美式/歐式 locale 會被當日期解析，導致
        #   "26" 被誤讀成 2026/2027 (兩位數年份)、時間被轉成 AM/PM 顯示。
        # - 新格式 4 位數年份明確、24 小時制，Excel 不會亂轉。
        # - 內部 ring buffer / DB 仍維持 ISO 格式 (datetime.now().isoformat())，
        #   本變更僅影響 CSV 匯出字串。
        ts_str = b["_dt"].strftime("%Y/%m/%d %H:%M:%S")
        row = [ts_str]
        for i in range(20):
            if b["any"][i]:
                # 小數第二位四捨五入，輸出小數一位（ROUND_HALF_UP：5 永遠進位）
                # sums 是 Decimal 累加，直接除 cnt 仍是 Decimal，無浮點誤差
                avg = b["sums"][i] / Decimal(b["cnts"][i])
                q = avg.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
                row.append(str(q))
            else:
                row.append("")
        # v7：電力三欄（V 小數 2、I 小數 3、W 小數 2）
        for k, decimals in enumerate((2, 3, 2)):
            if b["pw_any"][k]:
                avg = b["pw_sums"][k] / Decimal(b["pw_cnts"][k])
                q = avg.quantize(Decimal("0.1") if decimals == 1 else Decimal("0.01") if decimals == 2 else Decimal("0.001"),
                                 rounding=ROUND_HALF_UP)
                row.append(str(q))
            else:
                row.append("")
        writer.writerow(row)

    csv_text = buf.getvalue()
    buf.close()

    # 檔名 v3 修正：原本 filename="工位5_xxx.csv" 在 werkzeug 會走 latin-1 編 header
    # → UnicodeEncodeError。改用 ASCII 檔名（拼音化 station）作為 filename=，
    #   UTF-8 版放 filename*= (RFC 5987) 供支援的 client 沿用中文檔名。
    ts_str = datetime.now().strftime('%Y%m%d_%H%M%S')
    ascii_fname = f"{_ascii_station(station)}_{ts_str}.csv"
    utf8_fname  = f"{station}_{ts_str}.csv"
    from urllib.parse import quote
    quoted_utf8 = quote(utf8_fname, safe="")

    # 編 Content-Length：csv_text 內已含 BOM ("\ufeff")，用 utf-8 編出來就是
    # 實際 body 的位元組數 (3 byte BOM + 後續 UTF-8 位元組)。
    # 注意：不能用 utf-8-sig，否則會把 BOM 再算一次，Content-Length 偏多 3 byte，
    # 瀏覽器讀到一半就關連線 → 最後一個中文字被切壞 → Excel 顯示 ? 亂碼。
    body_bytes = csv_text.encode("utf-8")
    return Response(
        body_bytes,
        mimetype="text/csv",
        headers={
            "Content-Disposition": (
                f"attachment; filename=\"{ascii_fname}\"; "
                f"filename*=UTF-8''{quoted_utf8}"
            ),
            "Content-Length": str(len(body_bytes)),
        },
    )


def _ascii_station(station: str) -> str:
    """
    把 "工位5" / "工位A" 這類中文站名轉成 ASCII，給 Content-Disposition 的 filename= 用。
    規則：掳到所有數字 → 拼成 "Station5"；掳不到 → "Station"。
    這可確保 header 走 latin-1 編碼不會爆。
    """
    import re
    m = re.search(r'\d+', station or "")
    if m:
        return f"Station{m.group(0)}"
    return "Station"


# ---------- 啟動 / 關閉 ----------

def _on_shutdown():
    log.info("=" * 60)
    log.info("GX20 Web Monitor 關閉中...")
    log.info("程式關閉，DB 保留（下次啟動可繼續累積）")
    # 不再 clear_db() - 資料持久化
    # 仍關閉 ring buffer 與 GX20 連線
    with state["lock"]:
        for s in STATIONS:
            state["ring"][s].clear()
    if state["gx20"] is not None:
        # GX20 物件無顯式 close（with statement 內已處理）
        pass
    log.info("關閉完成")
    log.info("=" * 60)


def _signal_handler(signum, frame):
    log.info("signal %s received", signum)
    _on_shutdown()
    raise SystemExit(0)


def _register_shutdown_hooks():
    atexit.register(_on_shutdown)
    signal.signal(signal.SIGINT,  _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)


def main():
    # 1) 初始化 DB（保留舊資料）
    storage.init_db(reset=False)

    # 2) v4：若 config/settings.json 存在 → 優先採用並寫入 SQLite
    json_cfg = load_settings_from_json()
    if json_cfg is not None:
        log.info("讀取設定檔 %s，直接套用", SETTINGS_JSON_PATH)
        apply_json_to_sqlite(json_cfg)
    elif not storage.get_all_settings():
        # 3) SQLite 為空 → 寫入預設
        d = config.default_settings()
        for k, v in d.items():
            storage.set_setting(k, config.to_json(v) if isinstance(v, (dict, list)) else str(v))
        # 預設關閉 debug log
        storage.set_setting("debug_log_enabled", "0")
        # 順手 dump 一次，避免下次只能依賴 SQLite
        try:
            merged = load_settings()
            os.makedirs(SETTINGS_DIR, exist_ok=True)
            with open(SETTINGS_JSON_PATH, "w", encoding="utf-8") as f:
                json.dump(merged, f, ensure_ascii=False, indent=2, sort_keys=True)
            log.info("首次啟動，已建立 %s", SETTINGS_JSON_PATH)
        except Exception as e:
            log.warning("首次 dump 設定檔失敗: %s", e)

    # 3) 套用 debug 設定
    apply_log_level_from_settings()
    log.info("=" * 60)
    log.info("GX20 Web Monitor 啟動中...")
    log.info("log file: %s", LOG_PATH)
    log.info("debug 模式: %s", "開" if _is_debug_enabled() else "關")

    # 4) 啟動時先清一次過期資料
    try:
        s = load_settings()
        storage.purge_old_samples(int(s.get("retention_days", config.DEFAULT_RETENTION_DAYS)))
    except Exception as e:
        log.warning("啟動時 purge 失敗: %s", e)

    # 5) 註冊關閉 hooks
    _register_shutdown_hooks()

    # 6) 啟動 poller
    t = threading.Thread(target=poller, name="gx20-poller", daemon=True)
    t.start()

    log.info("啟動 Flask-SocketIO 0.0.0.0:5000")
    log.info("=" * 60)
    socketio.run(app, host="0.0.0.0", port=5000, debug=False, allow_unsafe_werkzeug=True)


if __name__ == "__main__":
    main()

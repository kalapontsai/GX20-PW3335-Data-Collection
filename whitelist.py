# -*- coding: utf-8 -*-
"""
whitelist.py
============
集中管理 GX20 所有白名單（CORS / IP / OTA 寫入路徑 / 副檔名）。

資料來源：config/settings.json 內的 "whitelist" 區塊
（沿用現有 settings 寫入鏈 + 本機鎖，不需新檔案）。

特性：
  - 從 settings.json 讀取 whitelist 區塊
  - 檔案 mtime 變動自動熱載入（5 秒內生效）
  - 改壞降級到 default，並 log warning，不 crash
  - 對外 API：get(key) → list[str]、init()、get_status() → dict
"""

import json
import logging
import os
import threading
import time

log = logging.getLogger("gx20.whitelist")

APP_ROOT = os.path.dirname(os.path.abspath(__file__))
SETTINGS_PATH = os.path.join(APP_ROOT, "config", "settings.json")

# ---------- 預設值（檔案不存在 / 解析失敗 / 欄位缺漏時使用）----------
DEFAULTS = {
    "cors_origins": [
        "http://localhost:5000",
        "http://127.0.0.1:5000",
        "http://10.35.31.10:5000",
    ],
    "remote_write_ips": ["127.0.0.1", "::1"],
    "ota_admin_ips": ["127.0.0.1", "::1", "10.35.32.11"],
    "ota_allowed_targets": [
        # 前端
        "static/js/",
        "static/css/",
        "static/vendor/",
        "templates/",
        # 後端核心
        "app.py",
        "config.py",
        "storage.py",
        "gx20_reader.py",
        "lttb.py",
        "run.py",
        # OTA 自己
        "ota.py",
        # 工具
        "ota_push.py",
        "ota_watchdog.py",
        "ota_watchdog.bat",
        "start_forever.bat",
    ],
    "ota_blocked_exts": [
        ".pyc", ".pyo", ".pyd", ".so", ".dll", ".exe",
        ".bat", ".sh", ".ps1",
    ],
}

# 模組狀態（受 _lock 保護）
_lock = threading.RLock()
_state = {
    "settings_mtime": 0.0,        # settings.json 最後一次載入的 mtime
    "data": dict(DEFAULTS),       # 目前生效的白名單 dict
    "loaded_at": 0.0,             # 最後一次成功載入的時間戳
}


def _extract_whitelist(settings: dict) -> dict:
    """從 settings dict 抽出 whitelist 區塊，缺欄位用 default 補。"""
    out = dict(DEFAULTS)
    wl = settings.get("whitelist")
    if not isinstance(wl, dict):
        return out
    for key in DEFAULTS.keys():
        v = wl.get(key)
        if isinstance(v, list) and all(isinstance(x, str) for x in v):
            out[key] = v
        else:
            # 型別錯誤就保留 default（不讓單一欄位壞整份）
            log.warning("whitelist: 欄位 %s 型別錯誤（%s），用 default",
                        key, type(v).__name__)
    return out


def _load_from_disk() -> dict:
    """讀 settings.json，抽出 whitelist 區塊。失敗回 default。"""
    if not os.path.exists(SETTINGS_PATH):
        log.warning("whitelist: %s 不存在，用 default", SETTINGS_PATH)
        return dict(DEFAULTS)
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            settings = json.load(f)
        if not isinstance(settings, dict):
            log.error("whitelist: settings.json 根節點不是 dict，用 default")
            return dict(DEFAULTS)
        return _extract_whitelist(settings)
    except Exception as e:
        log.error("whitelist: 解析 %s 失敗 (%s)，用 default", SETTINGS_PATH, e)
        return dict(DEFAULTS)


def _reload_if_changed() -> None:
    """檢查 settings.json mtime，變動就 reload。執行緒安全。"""
    with _lock:
        try:
            if not os.path.exists(SETTINGS_PATH):
                # 檔案不存在時，若之前曾載入過，保持舊值（不重置）
                # 避免 log 噪音；只有首次 init 才會用 default
                return
            mtime = os.path.getmtime(SETTINGS_PATH)
            if mtime == _state["settings_mtime"]:
                return  # 沒變
            old = _state["data"]
            new = _load_from_disk()
            _state["data"] = new
            _state["settings_mtime"] = mtime
            _state["loaded_at"] = time.time()
            _log_diff(old, new)
        except Exception as e:
            log.warning("whitelist: 熱載入檢查失敗 (%s)", e)


def _log_diff(old: dict, new: dict) -> None:
    """記錄哪些欄位變動，方便 debug。"""
    for key in DEFAULTS.keys():
        if old.get(key) != new.get(key):
            log.info("whitelist: %s 變動 (%d → %d 筆)",
                     key, len(old.get(key, [])), len(new.get(key, [])))


# ============================================================
# 對外 API
# ============================================================

def init() -> None:
    """app 啟動時呼叫一次。"""
    with _lock:
        _state["data"] = _load_from_disk()
        if os.path.exists(SETTINGS_PATH):
            _state["settings_mtime"] = os.path.getmtime(SETTINGS_PATH)
        _state["loaded_at"] = time.time()
    log.info(
        "whitelist: 啟動載入完成，cors=%d 筆, remote_write=%d 筆, "
        "ota_admin=%d 筆, targets=%d 筆, blocked_exts=%d 筆",
        len(_state["data"]["cors_origins"]),
        len(_state["data"]["remote_write_ips"]),
        len(_state["data"]["ota_admin_ips"]),
        len(_state["data"]["ota_allowed_targets"]),
        len(_state["data"]["ota_blocked_exts"]),
    )


def get(key: str) -> list:
    """
    取白名單欄位（list 複本，避免外部修改污染狀態）。

    每次取用會檢查 settings.json mtime，變動就 reload。
    所以呼叫端不必主動 reload，改檔後 5 秒內自然生效。
    """
    _reload_if_changed()
    with _lock:
        return list(_state["data"].get(key, []))


def get_status() -> dict:
    """給 /api/admin/status 看目前生效的白名單摘要（debug 用）。"""
    _reload_if_changed()
    with _lock:
        return {
            "settings_path": SETTINGS_PATH,
            "settings_exists": os.path.exists(SETTINGS_PATH),
            "settings_mtime": _state["settings_mtime"],
            "loaded_at": _state["loaded_at"],
            "counts": {k: len(v) for k, v in _state["data"].items()},
        }


def get_defaults() -> dict:
    """回傳預設值（給 UI 的「重置為預設」按鈕用）。"""
    return {k: list(v) for k, v in DEFAULTS.items()}

# -*- coding: utf-8 -*-
"""
config.py
=========
集中管理所有預設值、預設顏色、預設站點對應等。
"""

import json
from gx20_reader import STATIONS, POINTS_PER_STATION, DEFAULT_COLORS, CHANNEL_NUMBER

# ---------- GX20 連線 ----------
DEFAULT_GX20_HOST = "192.168.1.1"
DEFAULT_GX20_PORT = 34434
POLL_INTERVAL_SEC = 10

# ---------- PW3335 電力計 (v7 新增) ----------
# 6 工位各一台 PW3335，預設 IP 沿用 desktop 版 GX20_PW3335.py line 884 的對應規則
# （工位1 → 192.168.1.2 ... 工位6 → 192.168.1.7；GX20 本身用 192.168.1.1）
DEFAULT_PW3335_PORT = 3300
DEFAULT_PW3335_HOSTS = {
    "工位1": "192.168.1.2",
    "工位2": "192.168.1.3",
    "工位3": "192.168.1.4",
    "工位4": "192.168.1.5",
    "工位5": "192.168.1.6",
    "工位6": "192.168.1.7",
}

# 電力線顏色（避開溫度線的 20 色，盡量用對比明顯的安全色）
DEFAULT_PW_COLORS = {
    "V": "#f1c40f",   # 電壓：黃 (安全色)
    "I": "#1abc9c",   # 電流：青綠
    "W": "#e74c3c",   # 功率：紅
}

# 電力 Y 軸預設範圍（依使用者 2026-06-15 確認）
DEFAULT_PW_V_MIN  = 0
DEFAULT_PW_V_MAX  = 230
DEFAULT_PW_I_MIN  = 0
DEFAULT_PW_I_MAX  = 5
DEFAULT_PW_W_MIN  = 0
DEFAULT_PW_W_MAX  = 250
DEFAULT_PW_AUTO = False

# ---------- 圖表 ----------
# v3：history_minutes 設定已從 UI 移除，改以 chart_x_minutes 為主
# v6：Y 軸範圍改為 per-station 結構（{工位1: {min, max, auto}, ...}）
#     舊的全域 y_axis_min / y_axis_max 已停用（不相容）
DEFAULT_RATE_WINDOW_MIN = 5             # 升降速率計算區間（分鐘）
DEFAULT_AVG_WINDOW_MIN = 10             # 平均值計算區間（分鐘）
DEFAULT_Y_MIN = -20                     # Y 軸預設最小值
DEFAULT_Y_MAX = 100                     # Y 軸預設最大值
DEFAULT_Y_AUTO = False                  # 預設動態縮放：False=用 min/max 鎖住，True=讓 Chart.js auto-scale
DEFAULT_THEME = "light"                 # 預設主題（light/dark）
DEFAULT_RETENTION_DAYS = 7              # DB 保留天數
DEFAULT_MAX_POINTS = 2000               # /api/history 降取樣門檻
DEFAULT_CHART_X_MINUTES = 0             # 圖表 X 軸視窗（分鐘），0=全部資料；>0 則只顯示最近 N 分鐘
RING_BUFFER_SIZE = 720                  # poller ring buffer 最多保留筆數（10 秒一筆，720=2hr）

# ---------- 預設接點設定 ----------
def default_visibility() -> dict:
    """預設全部顯示。"""
    return {s: [True] * POINTS_PER_STATION for s in STATIONS}


def default_alias() -> dict:
    """預設別名 = 接點編號 1~20。"""
    return {s: [f"Ch{i+1:02d}" for i in range(POINTS_PER_STATION)] for s in STATIONS}


def default_color() -> dict:
    """預設顏色（每工位皆用同一組 20 色）。"""
    return {s: list(DEFAULT_COLORS) for s in STATIONS}


def default_y_axis() -> dict:
    """v6：Y 軸範圍 per-station。結構 = {工位名: {min, max, auto}}。
    預設每個工位都吃全域 DEFAULT_Y_MIN/MAX，不開自動縮放。"""
    return {
        s: {
            "min":  DEFAULT_Y_MIN,
            "max":  DEFAULT_Y_MAX,
            "auto": DEFAULT_Y_AUTO,
        }
        for s in STATIONS
    }


# === v7：PW3335 設定預設值 ===

def default_pw3335() -> dict:
    """PW3335 整體設定。
    v10.2：取消 per-station `remote` 開關。理由：
      1. OTA 推送後 app.py 重啟時只看 `pw_remote`，預設全 False → 該工位永遠寫 0
         → 重新啟動到下次手動開啟這段時間電力資料完全缺漏。
      2. 6 工位都裝了 PW3335，「未啟用」狀態實務上不存在。
      3. 「機器關機」會讓 V/I/W 為 0，不該被當成「未啟用」錯誤。
    結構（v10.2 拿掉 remote）：
      {
        "port":   int,                    # 6 台共用 TCP port
        "hosts":  {station: ip, ...},     # 每工位的 PW3335 IP
        "colors": {"V": "#...", "I": "#...", "W": "#..."},  # 電力線顏色
      }
    規則：app.py 對 6 工位一律 fetch_one_station；連線失敗 → 寫 0 + pw_connected=False。
    """
    return {
        "port":   DEFAULT_PW3335_PORT,
        "hosts":  {s: DEFAULT_PW3335_HOSTS[s] for s in STATIONS},
        "colors": dict(DEFAULT_PW_COLORS),
    }


def default_pw_axis() -> dict:
    """電力 Y 軸 per-station。
    結構：{工位名: {
        "v": {"min", "max", "auto"},  # 電壓軸（畫在右側）
        "i": {"min", "max", "auto"},  # 電流軸（畫在左側）
        "w": {"min", "max", "auto"},  # 功率軸（畫在左側，靠右 offset 推開）
    }}
    預設依使用者 2026-06-16 決定：V(0,230) / I(0,5) / W(0,250)，auto=False。
    """
    return {
        s: {
            "v": {
                "min":  DEFAULT_PW_V_MIN,
                "max":  DEFAULT_PW_V_MAX,
                "auto": DEFAULT_PW_AUTO,
            },
            "i": {
                "min":  DEFAULT_PW_I_MIN,
                "max":  DEFAULT_PW_I_MAX,
                "auto": DEFAULT_PW_AUTO,
            },
            "w": {
                "min":  DEFAULT_PW_W_MIN,
                "max":  DEFAULT_PW_W_MAX,
                "auto": DEFAULT_PW_AUTO,
            },
        }
        for s in STATIONS
    }


# ---------- 預設完整設定 dict（給 settings 頁初始化用） ----------
def default_settings() -> dict:
    return {
        "gx20_host":      DEFAULT_GX20_HOST,
        "gx20_port":      DEFAULT_GX20_PORT,
        # v6：y_axis 為 per-station 結構。舊的 y_axis_min/max 已停用。
        "y_axis":         default_y_axis(),
        "rate_window_min": DEFAULT_RATE_WINDOW_MIN,
        "avg_window_min":  DEFAULT_AVG_WINDOW_MIN,
        "ch_visibility":  default_visibility(),
        "ch_alias":       default_alias(),
        "ch_color":       default_color(),
        "theme":          DEFAULT_THEME,
        "retention_days": DEFAULT_RETENTION_DAYS,
        "max_points":     DEFAULT_MAX_POINTS,
        "chart_x_minutes": DEFAULT_CHART_X_MINUTES,
        # v7：PW3335 電力計
        "pw3335":         default_pw3335(),
        "pw_axis":        default_pw_axis(),
        # v10.x：備註欄（per-station 字串，上限 20 字，跟 alias 一樣跨瀏覽器同步）
        "notes":          {s: "" for s in STATIONS},
    }


def to_json(d) -> str:
    return json.dumps(d, ensure_ascii=False)


def from_json(s, default=None):
    if s is None:
        return default
    try:
        return json.loads(s)
    except (TypeError, ValueError):
        return default


# 對外 re-export，方便 app.py / templates 使用
__all__ = [
    "STATIONS", "POINTS_PER_STATION", "CHANNEL_NUMBER",
    "DEFAULT_GX20_HOST", "DEFAULT_GX20_PORT", "POLL_INTERVAL_SEC",
    "DEFAULT_RATE_WINDOW_MIN", "DEFAULT_AVG_WINDOW_MIN",
    "DEFAULT_Y_MIN", "DEFAULT_Y_MAX", "DEFAULT_Y_AUTO",
    "DEFAULT_THEME", "DEFAULT_RETENTION_DAYS", "DEFAULT_MAX_POINTS", "DEFAULT_CHART_X_MINUTES", "RING_BUFFER_SIZE",
    "DEFAULT_PW3335_PORT", "DEFAULT_PW3335_HOSTS", "DEFAULT_PW_COLORS",
    "DEFAULT_PW_V_MIN", "DEFAULT_PW_V_MAX",
    "DEFAULT_PW_I_MIN", "DEFAULT_PW_I_MAX",
    "DEFAULT_PW_W_MIN", "DEFAULT_PW_W_MAX",
    "DEFAULT_PW_AUTO",
    "default_visibility", "default_alias", "default_color", "default_y_axis",
    "default_pw3335", "default_pw_axis",
    "default_settings",
    "to_json", "from_json",
]

"""
v10.2 PW3335 取消 `remote` 開關 — repro 驗證
================================================

為什麼需要這支（MEMORY 政策 2b8de47 教訓）：
  改 ring / state / schema 必須 repro script 灌真實 shape 跑關鍵 function。
  本次改動：app.py poller 拿掉 `if not pw_remote.get(station, False)` 分支。
  邏輯變更：所有工位一律 fetch_one_station；連線失敗 → 寫 0 + pw_connected=False。

本 repro 對「v10.2 邏輯」的「mock 模擬」，**對照 5 種 shape**：
  1. 6 工位都連線成功 → 寫實際值 + pw_connected=True
  2. 1 工位 IP 為空（host=""）→ 寫 0 + pw_connected=False + last_error="未設定 IP"
  3. 1 工位 fetch 失敗 → 寫 0 + pw_connected=False + last_error="通訊失敗"
  4. 1 工位 V/I/W 全為 0（機器關機）→ 寫 0 + pw_connected=True（不算錯誤）
  5. 1 工位 fetch 失敗，但其他工位照常（驗證「不連累」）

執行：python tests/test_pw3335_v10_2_repro.py
"""

import sys
from pathlib import Path
from collections import deque

# 讓測試腳本可以 import 根目錄的 config module
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config


# ===== 模擬 fetch_one_station（不需要真實網路）=====
def mock_fetch_one_station(host, port):
    """回傳 (V, I, W, ok)。
    host 格式決定回應：
      192.168.1.2  → 成功 V=110.5 I=0.5 W=55.25
      192.168.1.3  → 成功 V=0.0 I=0.0 W=0.0（機器關機）
      192.168.1.4  → 失敗（timeout/refuse）
      192.168.1.5  → 成功 V=220.0 I=2.0 W=440.0
      其他任意 IP  → 成功 V=100.0 I=1.0 W=100.0（預設 OK）
    """
    if host == "192.168.1.4":
        return 0.0, 0.0, 0.0, False  # 失敗
    if host == "192.168.1.3":
        return 0.0, 0.0, 0.0, True   # 關機 → 0 值不回錯誤
    if host == "192.168.1.2":
        return 110.5, 0.5, 55.25, True
    if host == "192.168.1.5":
        return 220.0, 2.0, 440.0, True
    # 預設 OK（讓子位 5/6 不會因為 IP 不在 mock 列表就誤判失敗）
    return 100.0, 1.0, 100.0, True


# ===== 模擬 state（只放 poller 內 PW3335 區塊會用到的 keys）=====
def make_state():
    return {
        "lock": _DummyLock(),
        "ring": {s: deque(maxlen=200) for s in config.STATIONS},
        "pw_connected": {s: False for s in config.STATIONS},
        "pw_last_error": {s: None for s in config.STATIONS},
        "pw_last_vip": {s: (None, None, None) for s in config.STATIONS},
    }


class _DummyLock:
    """poller 內用的 `with state["lock"]` 不需真實 mut，只給 with 介面。"""
    def __enter__(self): return self
    def __exit__(self, *a): return False


# ===== 模擬「v10.2 邏輯」（從 app.py line 681- 抽出）=====
def v10_2_pw_pass(state, pw_settings, ts, log):
    """v10.2：所有工位一律 fetch_one_station。
    回傳 dict[station] = (v, i, w, ok)，方便測試驗證。
    """
    pw_port = int(pw_settings.get("port", 3300))
    pw_hosts = pw_settings.get("hosts", {})
    out = {}
    for station in config.STATIONS:
        host = pw_hosts.get(station, "")
        if not host:
            v_val, i_val, w_val, ok = 0.0, 0.0, 0.0, False
            err_msg = "未設定 IP（host 空字串）"
            log.append(("warn", station, err_msg))
            with state["lock"]:
                state["pw_connected"][station] = False
                state["pw_last_error"][station] = err_msg
        else:
            v_val, i_val, w_val, ok = mock_fetch_one_station(host, pw_port)
            with state["lock"]:
                if ok:
                    state["pw_connected"][station] = True
                    state["pw_last_error"][station] = None
                    state["pw_last_vip"][station] = (v_val, i_val, w_val)
                else:
                    state["pw_connected"][station] = False
                    state["pw_last_error"][station] = "通訊失敗（詳見 app.log）"
        out[station] = (v_val, i_val, w_val, ok)
    return out


# ===== 驗證函式 =====
def require(cond, msg):
    if not cond:
        print(f"  ✗ FAIL: {msg}")
        return False
    print(f"  ✓ {msg}")
    return True


def test_shape_1_all_connected():
    """shape 1：6 工位都連線成功"""
    print("\n[shape 1] 6 工位都連線成功")
    state = make_state()
    log = []
    # 6 工位全部用「成功 IP」 (192.168.1.2/3/5/10/11/12)
    # 分別測試：全部成功（不涵蓋 192.168.1.4 這個 mock 失敗 IP）
    pw = {
        "port": 3300,
        "hosts": {
            "工位1": "192.168.1.2",
            "工位2": "192.168.1.3",  # 關機狀態 (V=0)
            "工位3": "192.168.1.5",  # 成功
            "工位4": "192.168.1.10",
            "工位5": "192.168.1.11",
            "工位6": "192.168.1.12",
        },
    }
    result = v10_2_pw_pass(state, pw, ts="2026-07-24T20:00:00", log=log)
    ok = True
    for s in config.STATIONS:
        ok &= require(state["pw_connected"][s] is True, f"{s} 應為 connected=True")
        ok &= require(state["pw_last_error"][s] is None, f"{s} 應為 last_error=None")
    return ok


def test_shape_2_missing_ip():
    """shape 2：1 工位 IP 為空"""
    print("\n[shape 2] 1 工位 IP 為空")
    state = make_state()
    log = []
    pw = {
        "port": 3300,
        "hosts": {s: f"192.168.1.{i+2}" for i, s in enumerate(config.STATIONS)},
    }
    pw["hosts"]["工位3"] = ""  # 故意空
    result = v10_2_pw_pass(state, pw, ts="2026-07-24T20:00:00", log=log)
    ok = True
    # 工位 3
    ok &= require(state["pw_connected"]["工位3"] is False, "工位3 應為 connected=False")
    ok &= require(state["pw_last_error"]["工位3"] == "未設定 IP（host 空字串）",
                  "工位3 last_error 應為「未設定 IP」")
    ok &= require(result["工位3"] == (0.0, 0.0, 0.0, False), "工位3 應寫 (0,0,0,False)")
    # 其他工位
    for s in config.STATIONS:
        if s == "工位3": continue
        ok &= require(state["pw_connected"][s] is True, f"{s} 應為 connected=True")
    # log 應有 1 條 warn
    ok &= require(len(log) == 1, f"log 應有 1 條 warn，實際 {len(log)}")
    return ok


def test_shape_3_fetch_failed():
    """shape 3：1 工位 fetch 失敗"""
    print("\n[shape 3] 1 工位 fetch 失敗")
    state = make_state()
    log = []
    pw = {
        "port": 3300,
        "hosts": {s: f"192.168.1.{i+2}" for i, s in enumerate(config.STATIONS)},
    }
    pw["hosts"]["工位4"] = "192.168.1.4"  # mock fetch 失敗
    result = v10_2_pw_pass(state, pw, ts="2026-07-24T20:00:00", log=log)
    ok = True
    ok &= require(state["pw_connected"]["工位4"] is False, "工位4 應為 connected=False")
    ok &= require(state["pw_last_error"]["工位4"] == "通訊失敗（詳見 app.log）",
                  "工位4 last_error 應為「通訊失敗」")
    ok &= require(result["工位4"] == (0.0, 0.0, 0.0, False), "工位4 應寫 (0,0,0,False)")
    return ok


def test_shape_4_zero_is_ok():
    """shape 4：機器關機 V/I/W=0 視為正常"""
    print("\n[shape 4] V/I/W=0 視為正常（機器關機）")
    state = make_state()
    log = []
    pw = {
        "port": 3300,
        "hosts": {s: f"192.168.1.{i+2}" for i, s in enumerate(config.STATIONS)},
    }
    pw["hosts"]["工位3"] = "192.168.1.3"  # mock 回 0,0,0, ok=True
    result = v10_2_pw_pass(state, pw, ts="2026-07-24T20:00:00", log=log)
    ok = True
    ok &= require(state["pw_connected"]["工位3"] is True, "工位3 connected=True（0 值不算錯誤）")
    ok &= require(state["pw_last_error"]["工位3"] is None, "工位3 last_error=None")
    ok &= require(state["pw_last_vip"]["工位3"] == (0.0, 0.0, 0.0),
                  "工位3 pw_last_vip 應為 (0,0,0)")
    ok &= require(result["工位3"] == (0.0, 0.0, 0.0, True), "工位3 應寫 (0,0,0,True)")
    return ok


def test_shape_5_failure_isolated():
    """shape 5：1 工位失敗不連累其他工位"""
    print("\n[shape 5] 1 工位失敗不連累其他工位")
    state = make_state()
    log = []
    pw = {
        "port": 3300,
        "hosts": {
            "工位1": "192.168.1.2",
            "工位2": "192.168.1.3",
            "工位3": "192.168.1.5",
            "工位4": "192.168.1.4",  # 唯一失敗 IP
            "工位5": "192.168.1.10",
            "工位6": "192.168.1.11",
        },
    }
    result = v10_2_pw_pass(state, pw, ts="2026-07-24T20:00:00", log=log)
    ok = True
    # 其他 5 工位都應 connected=True
    for s in config.STATIONS:
        if s == "工位4": continue
        ok &= require(state["pw_connected"][s] is True, f"{s} 應為 connected=True")
    # 失敗工位 4 寫 0
    ok &= require(result["工位4"] == (0.0, 0.0, 0.0, False), "工位4 應寫 0")
    return ok


if __name__ == "__main__":
    print("=" * 60)
    print("v10.2 PW3335 取消 remote 開關 — repro 驗證")
    print("=" * 60)
    results = []
    results.append(test_shape_1_all_connected())
    results.append(test_shape_2_missing_ip())
    results.append(test_shape_3_fetch_failed())
    results.append(test_shape_4_zero_is_ok())
    results.append(test_shape_5_failure_isolated())
    print("\n" + "=" * 60)
    if all(results):
        print("✓ 全部 5 個 shape 通過")
        sys.exit(0)
    else:
        print(f"✗ {sum(1 for r in results if not r)} 個 shape 失敗")
        sys.exit(1)

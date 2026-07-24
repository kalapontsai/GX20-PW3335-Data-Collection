"""
v11 rate 演算法 repro。

策略：
  - 直接 import 已經 commit 的 storage.py / app.py / config.py
  - 把 storage.query_recent monkey-patch 成「以現在時間為錨點」的版本，讓時間固定
  - 用大大家的 CSV（58 筆）寫進工位1 DB
  - 跑 compute_rate_from_db("工位1", since_minutes, point_index)
  - 對照預期結果
"""

import csv
import importlib
import sys
import tempfile
import types
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

TMP = Path(tempfile.mkdtemp(prefix="v11_repro_"))

# 重導 DB_DIR 到 tmp
import config
config.DB_DIR = str(TMP)
import storage
storage.DB_DIR = str(TMP)

# init schema
storage.init_db(["工位1"])

# 灌入 CSV（包含 5 alias + 20 channel，後面 15 欄 header 與欄位有出入，
# 直接用 row key 取前 20 個欄位）
# Header: datetime,F,FR,R,Vbox,Suction,6,Ch07,Ch08,Ch09,Ch10,Ch11,Ch12,Ch13,Ch14,Ch15,Ch16,Ch17,Ch18,Ch19,20,V,I,W
# 使用者實際 CSV 路徑（本地端跑 repro 時改這邊）。
# 為避免個人路徑上 GitHub，這裡用佔位符。
# 檔名規則為 <station>_all_<YYYYMMDD_HHMMSS>.csv
# （會從 GX20 下載的回補 CSV, 取一段有溫度變化的時間區間）
CSV_PATH = Path("<USER_CSV_PATH>")
if not CSV_PATH.exists():
    print(f"CSV not found: {CSV_PATH}", file=sys.stderr)
    print(f"請把 tests/test_rate_v11_repro.py 的 CSV_PATH 改成你實際 CSV 路徑", file=sys.stderr)
    sys.exit(2)

with CSV_PATH.open() as f:
    reader = csv.DictReader(f)
    # Channel 欄位順序（前 20 欄）
    chan_keys = ["F","FR","R","Vbox","Suction","6"] + [f"Ch{i:02d}" for i in range(7,20)] + ["20"]
    assert len(chan_keys) == 20
    for r in reader:
        ts_csv = r["datetime"]
        # CSV 是 "2026/07/24 08:48:00" 格式，生產 poller 用 ISO "2026-07-24T08:48:00"
        # repro 用 ISO 寫，模擬生產。
        ts_iso = ts_csv.replace("/", "-").replace(" ", "T")
        parsed = []
        for k in chan_keys:
            v = r.get(k, "")
            parsed.append(float(v) if v not in (None, "") else None)
        storage.insert_sample(ts_iso, "工位1", parsed)
print(f"寫入 {len(chan_keys)}x20 channel 完成")

# 載入 app
import app
importlib.reload(app)  # 確保拿到最新版

# 凍時間：CSV 最新一筆 = 2026-07-24 09:46:00
FROZEN_NOW = datetime(2026, 7, 24, 9, 46, 0)

# query_recent 內用 datetime.now()，用 patch context manager 改
def _patched_query_recent(station, since_minutes=60):
    """模擬 query_recent，但 cutoff 用 FROZEN_NOW 計算。"""
    import sqlite3
    db_path = Path(storage.DB_DIR) / f"gx20_{station}.db"
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    cutoff = (FROZEN_NOW - timedelta(minutes=since_minutes)).isoformat(timespec="seconds")
    rows = con.execute(
        "SELECT * FROM samples WHERE ts >= ? ORDER BY ts ASC", (cutoff,)
    ).fetchall()
    con.close()
    return [storage._row_to_dict(r, station) for r in rows]


print("=" * 60)
print("工位1, channel F (point_index=0)")
print("=" * 60)

with patch.object(storage, "query_recent", side_effect=_patched_query_recent):
    # F 視窗 60 min
    r_60 = app.compute_rate_from_db("工位1", 60, 0)
    v_old, v_new = -23.3, -17.8
    expected_60 = round((v_new - v_old) / 60, 4)
    print(f"  60min : result={r_60}  expected={expected_60}")
    assert abs(r_60 - expected_60) < 1e-6, f"60min fail"

    # F 視窗 5 min：09:41 ~ 09:46
    #   09:41=-17.7, 09:42=-17.7, 09:43=-17.7, 09:44=-17.8, 09:45=-17.8, 09:46=-17.8
    #   最舊那筆 = 09:41=-17.7, 最新 = 09:46=-17.8
    r_5 = app.compute_rate_from_db("工位1", 5, 0)
    expected_5 = round((-17.8 - (-17.7)) / 5, 4)
    print(f"  5min  : result={r_5}  expected={expected_5}")
    assert abs(r_5 - expected_5) < 1e-6, f"5min fail"

    # F 視窗 30 min：09:16 起 → 09:16=-18.7, 09:46=-17.8
    r_30 = app.compute_rate_from_db("工位1", 30, 0)
    expected_30 = round((-17.8 - (-18.7)) / 30, 4)
    print(f"  30min : result={r_30}  expected={expected_30}")
    assert abs(r_30 - expected_30) < 1e-6, f"30min fail"

    # F 視窗 600 min（超長）：08:48=-23.3, 09:46=-17.8 → 5.5/600
    r_600 = app.compute_rate_from_db("工位1", 600, 0)
    expected_600 = round((-17.8 - (-23.3)) / 600, 4)
    print(f"  600min: result={r_600}  expected={expected_600}")
    assert abs(r_600 - expected_600) < 1e-6, f"600min fail"

    # 工位6 沒資料
    r_none = app.compute_rate_from_db("工位6", 60, 0)
    print(f"  empty : result={r_none}  expected=None")
    assert r_none is None, f"empty fail: {r_none}"

print()
print("=" * 60)
print("工位1, channel Suction (point_index=4) — 對照玩家")
print("=" * 60)

with patch.object(storage, "query_recent", side_effect=_patched_query_recent):
    # Suction 視窗 60 min：首 08:48=32.4, 末 09:46=22.3 → -10.1/60 = -0.1683
    r = app.compute_rate_from_db("工位1", 60, 4)
    expected = round((22.3 - 32.4) / 60, 4)
    print(f"  60min : result={r}  expected={expected}")
    assert abs(r - expected) < 1e-6, f"Suction 60min fail"

print()
print("ALL PASS")

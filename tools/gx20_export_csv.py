#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gx20_export_csv.py
==================
GX20 工位紀錄匯出 CSV 工具。

用途：
  將 GX20 的 SQLite 樣本 DB 匯出成 CSV，做為離線報表 / Excel 開檔用。
  規則與 app.py 的 /api/export_csv 完全一致，保證「網頁下載的」跟「CLI 產的」
  是同一份格式。

設計參考（不可背離）：
  - app.py::api_export_csv（line 1520+）：
      * bucket 對齊到分鐘起點 → 算術平均
      * Decimal 累加 + ROUND_HALF_UP（5 永遠進位）
      * 溫度：quantize('0.1') 小數 1 位
      * V/I/W：精度 (2, 3, 2) 對應小數位
      * 全 None 該欄位 → 空字串
  - storage.py::query_recent() / samples schema
  - config.py::default_alias()（拿 settings 失敗時的 fallback 標頭）
  - scripts/repro_csv_datetime.py（半進位 / datetime 格式驗證邏輯）

時間格式（v9 規則）：
  CSV 第 1 欄 datetime 為 %Y/%m/%d %H:%M:%S (24h, 4 位數年份)
  避免 Excel 美式 / 歐式 locale 把 2 位數年份 / AM-PM 亂解。

編碼：
  UTF-8 with BOM ("\\ufeff" 前綴)，Excel 直接開不亂碼。

支援模式（由 CLI 參數互斥）：
  A. --db PATH         讀單一 SQLite 檔（archive 格式或 temp 上傳檔都吃）
  B. --station S       讀 data/gx20_<S>.db（搭配 --data-dir 蓋掉預設路徑）
  C. 都不給            預設 --station=工位4 --data-dir=./data（最常用情境）

選用參數：
  --since-minutes N   只匯出近 N 分鐘（0 或省略 = 拉全部）
  --output / -o PATH  輸出 CSV 路徑（省略 = 自動命名 gx20_<S>_<YYYYMMDD_HHMMSS>.csv）
  --no-bom            關掉 UTF-8 BOM（給程式讀用）
  --no-summary        不要印摘要到 stdout
  --sql-only          只列印 SQL 不寫檔（debug 用）

範例：
  # 最常用
  python3 tools/gx20_export_csv.py --station 工位4

  # 讀臨時 SQLite
  python3 tools/gx20_export_csv.py --db /tmp/gx20_station4.db -o ./out.csv

  # 只取近 60 分鐘
  python3 tools/gx20_export_csv.py --station 工位5 --since-minutes 60

  # 不輸出 BOM（給下游程式接）
  python3 tools/gx20_export_csv.py --station 工位1 --no-bom

退出碼：
  0  成功
  2  CLI 參數錯誤
  3  DB 找不到 / 讀不到
  4  DB 裡沒資料
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import logging
import os
import sqlite3
import sys
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional, Tuple


# === 常數（與 app.py / storage.py 對齊）===

LOG = logging.getLogger("gx20_export_csv")

# 與 storage.py::SAMPLE_PW_COLUMN_NAMES 對齊
PW_COLUMN_NAMES = ("v", "i", "w")
PW_QUANTIZE_DECIMALS = (2, 3, 2)   # V=2, I=3, W=2

# 預設路徑：相對於 repo 根目錄的 data/
DEFAULT_DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),  # repo root
    "data",
)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# 已知工位白名單（從 gx20_reader.STATIONS 拿，避免循環 import）
# 若 gx20_reader 不可用，退而求其次寫死
def _load_stations() -> List[str]:
    try:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from gx20_reader import STATIONS  # type: ignore
        return list(STATIONS)
    except Exception:
        return ["工位1", "工位2", "工位3", "工位4", "工位5", "工位6"]


STATIONS = _load_stations()
POINTS_PER_STATION = 20

# CSV 第 1 欄固定標題
HEADER_DT = "datetime"


# === 路徑 / DB helper ===

def samples_db_path(data_dir: str, station: str) -> str:
    """跟 storage.py::samples_db_path 對齊：data/gx20_<station>.db"""
    return os.path.join(data_dir, f"gx20_{station}.db")


def _sanitize_csv_cell(s: str) -> str:
    """與 app.py::_sanitize_csv_cell 對齊（移除 / r / n，避免 CSV injection）。"""
    if not s:
        return ""
    # 砍掉可能讓 Excel / CSV parser 出事的字元
    return str(s).replace("/", "").replace("\r", "").replace("\n", "")


def _utc_offset_label(dt: datetime) -> str:
    """給 print summary 用：本地時區偏移（避免誤判）。"""
    return dt.astimezone().strftime("%z") if dt.tzinfo else "+0000"


# === 讀 DB ===

def open_samples_db(db_path: str) -> sqlite3.Connection:
    """
    開啟樣本 DB（唯讀模式）。
    - 無 WAL 寫入、避免碰到 live DB 的寫入鎖
    - archive 格式（檔名帶時間戳）也吃，因為 schema 一樣
    """
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"DB 不存在: {db_path}")
    # URI mode=ro：即使沒給 uri=True 也能 fallback 開唯讀
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
    except sqlite3.OperationalError:
        # 部分版本/無 uri 支援時退化
        conn = sqlite3.connect(db_path, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_samples_table(conn: sqlite3.Connection) -> None:
    """確認 DB 裡有 samples 表。沒有就 raise（不要嘗試建表，CLI 工具不做 DDL）。"""
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='samples'"
    )
    if cur.fetchone() is None:
        raise RuntimeError("DB 內沒有 samples 表")


def read_samples_raw(
    conn: sqlite3.Connection,
    station: str,
    since_minutes: int = 0,
) -> List[Dict[str, Any]]:
    """
    讀 samples 全欄，回傳 list of dict（每 dict 含 ts / station / t01..t20 / v/i/w）。
    - since_minutes > 0 → 只拉近 N 分鐘
    - since_minutes == 0 → 全拉
    排序：ts ASC（與 app.py 一致）。
    """
    # 用 storage.py::query_recent 在線時路徑已經驗證過；CLI 模式自己寫 SQL 比較單純
    ensure_samples_table(conn)
    if since_minutes > 0:
        cutoff = (datetime.now() - (
            # fromisoformat + 二分避開，從 datetime 直接算
            __import__("datetime").timedelta(minutes=since_minutes)
        )).isoformat(timespec="seconds")
        rows = conn.execute(
            "SELECT * FROM samples WHERE ts >= ? ORDER BY ts ASC",
            (cutoff,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM samples ORDER BY ts ASC"
        ).fetchall()

    out: List[Dict[str, Any]] = []
    for r in rows:
        d: Dict[str, Any] = {"ts": r["ts"], "station": station}
        for i in range(1, 21):
            try:
                d[f"t{i:02d}"] = r[f"t{i:02d}"]
            except (IndexError, KeyError):
                d[f"t{i:02d}"] = None
        for col in PW_COLUMN_NAMES:
            try:
                d[col] = r[col]
            except (IndexError, KeyError):
                d[col] = None
        out.append(d)
    return out


# === alias 載入（嘗試拿真實 alias，失敗就用預設）===

def load_aliases(data_dir: str, station: str) -> List[str]:
    """
    嘗試從 settings.db 讀 ch_alias，決定 CSV 中 20 個溫度欄的標題。
    拿不到 → 用 ['Ch01', 'Ch02', ...]，與 config.default_alias 一致。
    """
    try:
        settings_path = os.path.join(data_dir, "gx20_settings.db")
        if not os.path.exists(settings_path):
            return [f"Ch{i+1:02d}" for i in range(POINTS_PER_STATION)]
        conn = sqlite3.connect(f"file:{settings_path}?mode=ro", uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT value FROM settings WHERE key='ch_alias'"
            ).fetchone()
            if not row:
                return [f"Ch{i+1:02d}" for i in range(POINTS_PER_STATION)]
            import json
            alias_obj = json.loads(row["value"])
            arr = alias_obj.get(station, [])
            if isinstance(arr, list) and len(arr) >= POINTS_PER_STATION:
                return [str(x or "") for x in arr[:POINTS_PER_STATION]]
        finally:
            conn.close()
    except Exception as e:
        LOG.debug("load_aliases 失敗 (%s)，退回預設", e)
    return [f"Ch{i+1:02d}" for i in range(POINTS_PER_STATION)]


def load_aliases_override(path: str) -> Optional[List[str]]:
    """從 dialog 產的 temp JSON 讀 alias override；格式必須是 20 個字串列表。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception as e:
        raise ValueError(f"無法讀取 alias_override 檔案 {path}: {e}") from e

    if not isinstance(payload, list) or len(payload) < POINTS_PER_STATION:
        raise ValueError(f"alias_override 必須是長度 >= {POINTS_PER_STATION} 的 list")

    arr = [str(x or "") for x in payload[:POINTS_PER_STATION]]
    return arr


# === 分鐘 bucket 聚合（與 app.py:1582+ 對齊）===

def bucket_samples_per_minute(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """
    把 samples 依「分鐘起點」分桶，每桶算術平均。

    規則（與 /api/export_csv 100% 對齊）：
      - bucket_key = dt.replace(second=0, microsecond=0).isoformat()
      - 用 Decimal 累加避免 IEEE 754 float 在平均時的誤差
      - 全 None → 該欄輸出空字串（cnts==0）
      - 該分鐘內若有任一筆 v/i/w 為 None → 整桶 v/i/w 仍可算（其他筆有值就算）

    Returns:
      Dict[bucket_key -> { "_dt": datetime, "sums" / "cnts" / "any" * 20,
                            "pw_sums" / "pw_cnts" / "pw_any" * 3 }]
    """
    buckets: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        # 解析 ISO 字串 → datetime
        try:
            dt = datetime.fromisoformat(r["ts"])
        except Exception:
            continue
        bucket_dt = dt.replace(second=0, microsecond=0)
        bkey = bucket_dt.isoformat()
        b = buckets.get(bkey)
        if b is None:
            b = {
                "_dt":      bucket_dt,
                "sums":     [Decimal("0")] * POINTS_PER_STATION,
                "cnts":     [0]            * POINTS_PER_STATION,
                "any":      [False]        * POINTS_PER_STATION,
                "pw_sums":  [Decimal("0")] * 3,   # [V, I, W]
                "pw_cnts":  [0]            * 3,
                "pw_any":   [False]        * 3,
            }
            buckets[bkey] = b
        # 20 個溫度
        for i in range(1, 21):
            v = r.get(f"t{i:02d}")
            if v is None:
                continue
            try:
                b["sums"][i - 1] += Decimal(str(v))
                b["cnts"][i - 1] += 1
                b["any"][i - 1]  = True
            except (TypeError, ValueError):
                continue
        # 3 個電力
        for k, key in enumerate(PW_COLUMN_NAMES):
            pv = r.get(key)
            if pv is None:
                continue
            try:
                b["pw_sums"][k] += Decimal(str(pv))
                b["pw_cnts"][k] += 1
                b["pw_any"][k]  = True
            except (TypeError, ValueError):
                continue
    return buckets


def build_csv(
    buckets: Dict[str, Dict[str, Any]],
    headers: List[str],
    add_bom: bool = True,
) -> Tuple[bytes, int]:
    """
    把 buckets 序列化為 UTF-8 bytes（含可選 BOM）。
    Returns:
      (csv_bytes, row_count)
    """
    buf = io.StringIO()
    if add_bom:
        buf.write("\ufeff")
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(headers)

    sorted_buckets = sorted(buckets.values(), key=lambda b: b["_dt"])

    for b in sorted_buckets:
        # v9 datetime 格式
        ts_str = b["_dt"].strftime("%Y/%m/%d %H:%M:%S")
        row: List[str] = [ts_str]
        # 20 個溫度
        for i in range(POINTS_PER_STATION):
            if b["any"][i]:
                avg = b["sums"][i] / Decimal(b["cnts"][i])
                q = avg.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
                row.append(str(q))
            else:
                row.append("")
        # V / I / W
        for k, decimals in enumerate(PW_QUANTIZE_DECIMALS):
            if b["pw_any"][k]:
                avg = b["pw_sums"][k] / Decimal(b["pw_cnts"][k])
                q = avg.quantize(
                    Decimal("0.1") if decimals == 1 else
                    Decimal("0.01") if decimals == 2 else
                    Decimal("0.001"),
                    rounding=ROUND_HALF_UP,
                )
                row.append(str(q))
            else:
                row.append("")

        writer.writerow(row)

    text = buf.getvalue()
    return text.encode("utf-8"), len(sorted_buckets)


# === summary 輸出 ===

def summarize(
    rows: List[Dict[str, Any]],
    buckets: Dict[str, Dict[str, Any]],
    csv_row_count: int,
    output_path: Optional[str],
) -> str:
    """列印人類可讀摘要（給 CLI stdout 確認用）。"""
    if not rows:
        return "  (no data)"
    lines: List[str] = []
    ts_first = rows[0]["ts"]
    ts_last  = rows[-1]["ts"]
    # ISO → datetime
    try:
        dt_first = datetime.fromisoformat(ts_first)
        dt_last  = datetime.fromisoformat(ts_last)
        dur = dt_last - dt_first
    except Exception:
        dur = None

    lines.append(f"  raw rows     : {len(rows)}")
    lines.append(f"  minute rows  : {csv_row_count}")
    lines.append(f"  time range   : {ts_first}  →  {ts_last}")
    if dur is not None:
        secs = int(dur.total_seconds())
        h, rem = divmod(secs, 3600)
        m, s = divmod(rem, 60)
        lines.append(f"  duration     : {h:02d}:{m:02d}:{s:02d}  ({secs} s)")
    # 各 channel null 數
    null_lines: List[str] = []
    for i in range(1, 21):
        col = f"t{i:02d}"
        n_null = sum(1 for r in rows if r.get(col) is None)
        if n_null:
            null_lines.append(f"{col}={n_null}")
    if null_lines:
        lines.append(f"  null counts  : {', '.join(null_lines)}")
    if output_path:
        try:
            size = os.path.getsize(output_path)
            lines.append(f"  output       : {output_path}  ({size} bytes)")
        except OSError:
            lines.append(f"  output       : {output_path}  (size unknown)")
    return "\n".join(lines)


# === CLI ===

def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="gx20_export_csv",
        description="匯出 GX20 樣本 DB 為 CSV（每分鐘聚合，與 /api/export_csv 同格式）",
    )
    src = p.add_mutually_exclusive_group()
    src.add_argument(
        "--db",
        metavar="PATH",
        help="讀單一 SQLite 檔（archive 或上傳檔皆可）",
    )
    src.add_argument(
        "--station",
        metavar="S",
        help=f"工位名稱（{', '.join(STATIONS)}），搭配 --data-dir",
    )
    p.add_argument(
        "--data-dir",
        metavar="DIR",
        default=DEFAULT_DATA_DIR,
        help=f"samples DB 目錄（預設: {DEFAULT_DATA_DIR}）",
    )
    p.add_argument(
        "--since-minutes",
        type=int,
        default=0,
        metavar="N",
        help="只匯出近 N 分鐘（0 或省略 = 全部；與 /api/export_csv 同語意）",
    )
    p.add_argument(
        "-o", "--output",
        metavar="PATH",
        help="輸出 CSV 路徑（省略 = 自動命名）",
    )
    p.add_argument(
        "--aliases-file",
        metavar="PATH",
        help="可選：JSON alias override 檔（20 個欄位標題），取代 settings.db 的 ch_alias",
    )
    p.add_argument(
        "--no-bom",
        action="store_true",
        help="不要 UTF-8 BOM（給下游程式讀時用）",
    )
    p.add_argument(
        "--no-summary",
        action="store_true",
        help="不印摘要到 stdout",
    )
    p.add_argument(
        "--sql-only",
        action="store_true",
        help="只列印 SQL / 樣本數，不寫檔（debug 用）",
    )
    p.add_argument(
        "-v", "--verbose",
        action="count",
        default=0,
        help="詳細 log（可重複：-v / -vv）",
    )
    return p


def _resolve_db_path(args: argparse.Namespace) -> Tuple[str, str]:
    """
    解 CLI 衝突，回傳 (db_path, station)。
    預設走 --station=工位4 --data-dir=./data。
    """
    if args.db:
        # 從檔名盡量猜 station；拿不到就標 "unknown"
        base = os.path.basename(args.db)
        station = "unknown"
        for s in STATIONS:
            # 檔名形如 gx20_<station>_<ts>.db 或 gx20_<station>.db
            if base.startswith(f"gx20_{s}_") or base == f"gx20_{s}.db":
                station = s
                break
        return args.db, station
    # --station 模式
    station = args.station or "工位4"
    if station not in STATIONS:
        raise argparse.ArgumentTypeError(
            f"未知工位: {station!r}（應為 {STATIONS} 之一）"
        )
    db_path = samples_db_path(args.data_dir, station)
    if not os.path.exists(db_path):
        raise FileNotFoundError(
            f"找不到 {station} 的 DB: {db_path}\n"
            f"提示：--db PATH 模式可讀取任何 SQLite 檔"
        )
    return db_path, station


def _default_output_path(station: str, since_minutes: int) -> str:
    """自動命名：沿用工具同目錄輸出，避免落到 repo 根目錄。"""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = f"_last{since_minutes}min" if since_minutes > 0 else ""
    filename = f"gx20_{station}{suffix}_{ts}.csv"
    return os.path.join(SCRIPT_DIR, filename)


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_argparser()
    args = parser.parse_args(argv)

    # log 等級
    level = logging.WARNING
    if args.verbose >= 2:
        level = logging.DEBUG
    elif args.verbose == 1:
        level = logging.INFO
    logging.basicConfig(level=level, format="%(levelname)s %(name)s: %(message)s")

    # 解 CLI 衝突
    try:
        db_path, station = _resolve_db_path(args)
    except (argparse.ArgumentTypeError, FileNotFoundError) as e:
        print(f"[gx20_export_csv] {e}", file=sys.stderr)
        return 2

    # 開 DB / 讀資料
    try:
        conn = open_samples_db(db_path)
    except FileNotFoundError as e:
        print(f"[gx20_export_csv] {e}", file=sys.stderr)
        return 3
    except Exception as e:
        print(f"[gx20_export_csv] 開 DB 失敗: {e}", file=sys.stderr)
        return 3
    try:
        try:
            rows = read_samples_raw(conn, station, since_minutes=args.since_minutes)
        except RuntimeError as e:
            # 沒有 samples 表
            print(f"[gx20_export_csv] {e}", file=sys.stderr)
            return 3
    finally:
        conn.close()

    if not rows:
        print(
            f"[gx20_export_csv] {station} {db_path} 內無資料"
            + (f"（過濾近 {args.since_minutes} 分鐘）" if args.since_minutes else ""),
            file=sys.stderr,
        )
        return 4

    # 分鐘聚合
    buckets = bucket_samples_per_minute(rows)

    # alias 標頭
    aliases: List[str]
    if args.aliases_file:
        try:
            aliases = load_aliases_override(args.aliases_file)
        except ValueError as e:
            print(f"[gx20_export_csv] {e}", file=sys.stderr)
            return 2
    else:
        aliases = load_aliases(args.data_dir, station)
    headers: List[str] = [HEADER_DT]
    for i in range(1, 21):
        alias = (aliases[i - 1] or "").strip()
        col_name = alias if alias else str(i)
        headers.append(_sanitize_csv_cell(col_name))
    headers.extend(["V", "I", "W"])

    # --sql-only 模式：只列狀態不寫
    if args.sql_only:
        print(f"DB path     : {db_path}")
        print(f"station     : {station}")
        print(f"raw rows    : {len(rows)}")
        print(f"buckets     : {len(buckets)}")
        print(f"headers ({len(headers)}): {headers[:6]}... {headers[-3:]}")
        return 0

    # 序列化
    csv_bytes, row_count = build_csv(buckets, headers, add_bom=not args.no_bom)

    # 寫檔
    out_path = args.output or _default_output_path(station, args.since_minutes)
    out_dir = os.path.dirname(os.path.abspath(out_path))
    if out_dir and not os.path.isdir(out_dir):
        os.makedirs(out_dir, exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(csv_bytes)

    if not args.no_summary:
        print("[gx20_export_csv] done")
        print(summarize(rows, buckets, row_count, out_path))

    return 0


if __name__ == "__main__":
    sys.exit(main())

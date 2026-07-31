#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gx20_export_csv_dialog.py
=========================
GX20 樣本 DB 匯出 CSV 對話框工具（v10.x 對話框工具）。

用途：
  互動式選單列出可匯出的 DB 檔，自動載入 <db>.meta.json 的別名，
  將結果匯出為 CSV。完全包裝既有 CLI 工具 gx20_export_csv.py 的邏輯
  ——保證 CLI 與 dialog 產出 100% 一致。

候選 DB 來源：
  - data/archive/gx20_<station>_<YYYYMMDD_HHMMSS>.db   (archive / 快照)
  - data/gx20_<station>.db                            (live / 即時)
  - 可額外加 --input-dir DIR 多掃一個目錄
  - 互動中也可「指定路徑」指向任意 SQLite

別名優先順序（與使用者 2026-07-31 確認）：
  1. <db_path>.meta.json 的 alias (list of 20) → 用它，空字串 fallback ChXX
  2. <db_path>.meta.json 的 alias 為 null → 預設 Ch01~Ch20
  3. 沒有 meta.json → 從 data/gx20_settings.db 讀 ch_alias[station]
  4. 都拿不到 → 預設 Ch01~Ch20

對話框 UX（每行格式）：
  [ 1] data/archive/gx20_工位4_20260723_140756.db   24KB  2026-07-23 14:07  工位4  955筆  11:00~14:34  alias=自訂
  [ 2] data/archive/gx20_工位4_20260723_140758.db    24KB  2026-07-23 14:07  工位4    0筆  (空)         alias=未設
  [ 3] data/gx20_工位4.db                          156KB  live              工位4  954筆  11:00~14:34  (live,別名讀settings.db)
  [ 4] 重新掃描
  [ 5] 指定路徑 (輸入完整路徑)
  [ 0] 離開

UX 細節：
  - 編號輸入：`5` → 指定路徑（會再問一次完整路徑）；`0` → 離開；`4` → 重新掃描
  - 列表用 basename 顯示以保留可讀性，路徑以相對於 cwd 或絕對路徑呈現
  - 結束後問「再選一個嗎？(y/N)」可繼續

實作細節：
  - 別名注入：把決定好的 alias list 寫到 temp JSON，呼叫 CLI 的 --aliases-file
  - temp 檔放在系統 temp，subprocess 結束後自動刪
  - 由 dialog 端算「時間區間 / 筆數」等 meta 給對話框顯示（讀 DB 即可）

退出碼：
  0  成功 / 使用者選擇離開
  1  一般錯誤
  2  CLI 參數錯誤
  3  DB 開啟錯誤
  4  DB 內無資料
  130  Ctrl+C
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# === 路徑常數（與既有 CLI 對齊）===

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)
DEFAULT_DATA_DIR = os.path.join(REPO_ROOT, "data")
DEFAULT_ARCHIVE_DIR = os.path.join(DEFAULT_DATA_DIR, "archive")
CLI_SCRIPT = os.path.join(SCRIPT_DIR, "gx20_export_csv.py")

LOG = logging.getLogger("gx20_export_csv_dialog")


# === 候選 DB 收集 ===

def list_candidate_dbs(
    data_dir: str,
    additional_dirs: Optional[List[str]] = None,
) -> List[Tuple[str, str, bool]]:
    """
    列出候選 DB。
    Returns:
      List of (db_path, kind, has_meta)：
        kind = "archive" | "live"
        has_meta = bool（旁邊是否有 <name>.meta.json）
    排序：archive 在前（依檔名遞增，包含時間戳自然新到舊）；live 在後。
    """
    candidates: List[Tuple[str, str, bool]] = []

    # archive
    if os.path.isdir(DEFAULT_ARCHIVE_DIR):
        for f in sorted(os.listdir(DEFAULT_ARCHIVE_DIR)):
            if f.endswith(".db"):
                p = os.path.join(DEFAULT_ARCHIVE_DIR, f)
                has_meta = os.path.exists(p + ".meta.json")
                candidates.append((p, "archive", has_meta))

    # live
    if os.path.isdir(data_dir):
        for f in sorted(os.listdir(data_dir)):
            if f.startswith("gx20_") and f.endswith(".db") and not f.endswith("-wal") and not f.endswith("-shm"):
                # 只列 live，不重複列 archive（archive 路徑已分開）
                # 篩掉非工位檔（gx20_settings.db / gx20_*.db-wal 等）
                p = os.path.join(data_dir, f)
                has_meta = os.path.exists(p + ".meta.json")
                candidates.append((p, "live", has_meta))

    # 額外掃描目錄
    for d in additional_dirs or []:
        if os.path.isdir(d):
            for f in sorted(os.listdir(d)):
                if f.endswith(".db") and not f.endswith("-wal") and not f.endswith("-shm"):
                    p = os.path.join(d, f)
                    if not any(p == c[0] for c in candidates):  # 避免重複
                        has_meta = os.path.exists(p + ".meta.json")
                        candidates.append((p, "archive", has_meta))

    return candidates


# === DB meta 預覽 ===

def probe_db_meta(db_path: str, sample_limit: int = 5) -> Dict[str, Any]:
    """
    對單一 DB 預覽：
      - 檔案大小（bytes）
      - mtime（ISO）
      - station（從檔名猜）
      - 是否含 samples 表
      - 筆數 / 時間區間
    不完整的 meta 回傳：{"error": "..."}（給對話框忽略列）。
    """
    out: Dict[str, Any] = {
        "path": db_path,
        "exists": os.path.exists(db_path),
        "size": 0,
        "mtime": "",
        "station": _guess_station_from_filename(db_path),
        "count": 0,
        "ts_min": None,
        "ts_max": None,
    }
    if not out["exists"]:
        out["error"] = "not_found"
        return out
    try:
        out["size"] = os.path.getsize(db_path)
        out["mtime"] = datetime.fromtimestamp(os.path.getmtime(db_path)).isoformat(timespec="seconds")
    except OSError:
        pass

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
    except sqlite3.OperationalError:
        # 沒 uri 支援就退
        conn = sqlite3.connect(db_path, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        # 檢查表是否存在
        r = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='samples'"
        ).fetchone()
        if not r:
            out["count"] = 0
            out["ts_min"] = None
            out["ts_max"] = None
        else:
            r = conn.execute(
                "SELECT COUNT(*) AS n, MIN(ts) AS mn, MAX(ts) AS mx FROM samples"
            ).fetchone()
            out["count"] = r["n"] or 0
            out["ts_min"] = r["mn"]
            out["ts_max"] = r["mx"]
    except sqlite3.Error as e:
        out["error"] = f"db_read_error: {e}"
    finally:
        conn.close()
    return out


def _guess_station_from_filename(db_path: str) -> str:
    """從檔名 gx20_<station>.db 或 gx20_<station>_<ts>.db 抽出 station。"""
    base = os.path.basename(db_path)
    if not base.startswith("gx20_"):
        return "?"
    stem = base[len("gx20_"):]
    if stem.endswith(".db"):
        stem = stem[:-3]
    parts = stem.split("_")
    return parts[0] if parts else "?"


def _fmt_size(n: int) -> str:
    """bytes → '24KB' / '156KB' / '1.2MB' 顯示。"""
    if n < 1024:
        return f"{n}B"
    if n < 1024 * 1024:
        return f"{n // 1024}KB"
    return f"{n / 1024 / 1024:.1f}MB"


def _fmt_ts_range(ts_min: Optional[str], ts_max: Optional[str]) -> str:
    """ISO 8601 → 'HH:MM~HH:MM' / '(空)'"""
    if not ts_min or not ts_max:
        return "(空)"
    try:
        m = datetime.fromisoformat(ts_min).strftime("%H:%M")
        x = datetime.fromisoformat(ts_max).strftime("%H:%M")
        return f"{m}~{x}"
    except Exception:
        return ts_min[:16] + "~" + ts_max[:16]


def _fmt_mtime_short(iso: str) -> str:
    """ISO → 'YYYY-MM-DD HH:MM'。"""
    try:
        return datetime.fromisoformat(iso).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return iso[:16] if iso else "?"


# === 顯示候選清單 ===

def render_candidate_list(
    candidates: List[Tuple[str, str, bool]],
    data_dir: str,
    additional_dirs: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """
    對每個 candidate 跑 probe，組合成對話框資料。
    Returns: List of dict, 每 dict 包含 path, kind, has_meta, station, size, mtime, count, ts_min, ts_max
    """
    rows: List[Dict[str, Any]] = []
    for db_path, kind, has_meta in candidates:
        meta = probe_db_meta(db_path)
        meta["kind"] = kind
        meta["has_meta"] = has_meta
        rows.append(meta)
    return rows


def format_row(idx: int, row: Dict[str, Any]) -> str:
    """
    對話框單行顯示。格式（寬度動態調整）：
      [ N] <relative_or_absolute_path> <size> <mtime>  <station>  <count>筆  <ts_range>  alias=<自訂/未設/live>
    """
    # 路徑：相對於 cwd 顯示，太長就用絕對
    try:
        rel = os.path.relpath(row["path"], os.getcwd())
        p = rel if len(rel) <= 60 else row["path"]
    except ValueError:
        p = row["path"]
    sz = _fmt_size(row.get("size", 0))
    mt = _fmt_mtime_short(row.get("mtime", ""))
    st = row.get("station") or "?"
    cnt = row.get("count", 0)
    cnt_str = f"{cnt}筆" if cnt else "(空)"
    rng = _fmt_ts_range(row.get("ts_min"), row.get("ts_max"))

    if row.get("kind") == "live":
        alias_label = "(live,別名讀settings.db)"
    elif row.get("has_meta"):
        # meta 內容預覽：自訂 / 未設
        meta_info = read_meta_file(row["path"] + ".meta.json")
        if meta_info is None:
            alias_label = "(meta 讀不到)"
        elif meta_info.get("alias") is None:
            alias_label = "alias=未設"
        elif isinstance(meta_info.get("alias"), list):
            alias_label = f"alias=自訂({sum(1 for x in meta_info['alias'] if x) }/20)"
        else:
            alias_label = "alias=?"
    else:
        alias_label = "(無meta)"

    return f"[{idx:>2}] {p}  {sz:>5}  {mt}  {st:>3}  {cnt_str:>5}  {rng:>11}  {alias_label}"


def read_meta_file(meta_path: str) -> Optional[Dict[str, Any]]:
    """讀 <db>.meta.json。損壞回 None。"""
    if not os.path.exists(meta_path):
        return None
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception as e:
        LOG.debug("read_meta_file %s 失敗: %s", meta_path, e)
    return None


# === Alias 解析（dialog 端決策）===

def resolve_alias(
    db_path: str,
    station: str,
    data_dir: str,
) -> Tuple[List[str], str]:
    """
    決定 20 個溫度欄的別名 + 來源標籤。

    Returns:
      (alias_list, source_label)
      source_label ∈ {"meta", "settings.db", "default"}

    規則（按優先順序）：
      1. <db_path>.meta.json 的 alias = list → 用它（空字串保留，呼叫端 fallback）
      2. <db_path>.meta.json 的 alias = null → 預設
      3. 沒 meta → 從 data/gx20_settings.db 的 ch_alias[station] 讀
      4. 都沒 → Ch01~Ch20
    """
    # 1) 先看 meta
    meta_path = db_path + ".meta.json"
    meta_info = read_meta_file(meta_path)
    if meta_info is not None:
        # meta 存在但 alias = null → 預設
        if meta_info.get("alias") is None:
            return _default_alias_list(), "default(meta=null)"
        # meta 存在且 alias = list → 用它
        if isinstance(meta_info.get("alias"), list):
            arr = meta_info["alias"]
            if len(arr) >= 20:
                # 完整 20 個就用，空字串也照給（呼叫端 fallback 為 ChXX）
                return [str(x or "") for x in arr[:20]], "meta"
            # 長度不足 → 退回 settings.db 路徑
            LOG.debug("meta alias 長度 %d < 20，退回 settings.db", len(arr))

    # 2) 從 settings.db 找
    alias = _load_alias_from_settings_db(data_dir, station)
    if alias:
        return alias, "settings.db"

    # 3) 預設
    return _default_alias_list(), "default"


def _default_alias_list() -> List[str]:
    return [f"Ch{i+1:02d}" for i in range(20)]


def _load_alias_from_settings_db(data_dir: str, station: str) -> Optional[List[str]]:
    settings_path = os.path.join(data_dir, "gx20_settings.db")
    if not os.path.exists(settings_path):
        return None
    try:
        conn = sqlite3.connect(f"file:{settings_path}?mode=ro", uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT value FROM settings WHERE key='ch_alias'"
            ).fetchone()
            if not row:
                return None
            obj = json.loads(row["value"])
            arr = obj.get(station, [])
            if isinstance(arr, list) and len(arr) >= 20:
                return [str(x or "") for x in arr[:20]]
        finally:
            conn.close()
    except Exception as e:
        LOG.debug("讀 settings.db ch_alias 失敗 (%s)", e)
    return None


# === CLI subprocess 呼叫 ===

def run_cli_export(
    db_path: str,
    station: str,
    data_dir: str,
    alias_list: List[str],
    output_path: Optional[str] = None,
    since_minutes: int = 0,
) -> Tuple[int, str]:
    """
    呼叫既有 gx20_export_csv.py 產 CSV。
    Returns:
      (returncode, summary_text_or_empty)
    """
    # 1) 寫 alias 到 temp JSON
    tmp_fd, aliases_tmp = tempfile.mkstemp(prefix="gx20_aliases_", suffix=".json")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(alias_list, f, ensure_ascii=False)

        # 2) 組 CLI 命令列
        cmd: List[str] = [
            sys.executable,
            CLI_SCRIPT,
            "--db", db_path,
            "--data-dir", data_dir,
            "--aliases-file", aliases_tmp,
        ]
        if since_minutes > 0:
            cmd += ["--since-minutes", str(since_minutes)]
        if output_path:
            cmd += ["-o", output_path]

        LOG.debug("run_cli_export: %s", " ".join(cmd))
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
        )
        return proc.returncode, (proc.stdout or "") + (proc.stderr if proc.returncode != 0 else "")

    finally:
        # 3) 清 temp
        try:
            os.unlink(aliases_tmp)
        except OSError:
            pass


# === 對話框主流程 ===

SENTINEL_RESCAN = 4
SENTINEL_CUSTOM_PATH = 5
SENTINEL_QUIT = 0


def prompt_choice(prompt_text: str, max_choice: int) -> Optional[int]:
    """
    顯示 prompt，讀一行輸入，回傳：
      - 0..max_choice 內的編號
      - None → 離開 / Ctrl+C
    任何輸入錯誤（不是整數或不在範圍）→ 重新問。
    """
    while True:
        try:
            s = input(prompt_text).strip()
        except EOFError:
            return None
        except KeyboardInterrupt:
            print()
            return None
        if not s:
            continue
        try:
            n = int(s)
        except ValueError:
            print(f"  → 請輸入 0~{max_choice} 的數字")
            continue
        if 0 <= n <= max_choice:
            return n
        print(f"  → 範圍外（0~{max_choice}）")


def prompt_path() -> Optional[str]:
    """互動讀完整檔案路徑。空行 / Ctrl+C → None。"""
    try:
        s = input("請輸入完整 DB 路徑（或按 Enter 取消）: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return None
    if not s:
        return None
    if s.startswith("~"):
        s = os.path.expanduser(s)
    return os.path.abspath(s)


def confirm_again() -> bool:
    try:
        s = input("再選一個？(y/N): ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return s in ("y", "yes")


def run_dialog(
    data_dir: str,
    additional_dirs: Optional[List[str]] = None,
) -> int:
    """對話框主迴圈。回傳最終退出碼。"""
    print("=" * 78)
    print(" GX20 樣本 DB 匯出 CSV 對話框 (v10.x)")
    print(" = 一條 gx20_export_csv.py 的 wrapper（保證輸出格式一致）")
    print("=" * 78)

    while True:
        # === 1. 收集候選 ===
        candidates = list_candidate_dbs(data_dir, additional_dirs)
        rows = render_candidate_list(candidates, data_dir, additional_dirs)

        # === 2. 顯示選單 ===
        print()
        print(f"掃描 {data_dir} [+archive +{len(additional_dirs or [])} dirs] 共 {len(rows)} 個 DB")
        print("-" * 78)
        for i, row in enumerate(rows, start=1):
            print(format_row(i, row))
        # 額外指令
        print()
        print(f"[{SENTINEL_RESCAN:>2}] 重新掃描")
        print(f"[{SENTINEL_CUSTOM_PATH:>2}] 指定路徑 (輸入完整路徑)")
        print(f"[{SENTINEL_QUIT:>2}] 離開")
        print("-" * 78)

        # === 3. 輸入選擇 ===
        max_choice = max(len(rows), SENTINEL_CUSTOM_PATH)
        choice = prompt_choice(f"\n請選擇（0~{max_choice}）: ", max_choice)
        if choice is None:
            print("使用者中斷 / EOF → 離開")
            return 130 if not candidates else 0
        if choice == SENTINEL_QUIT:
            print("Bye.")
            return 0
        if choice == SENTINEL_RESCAN:
            continue
        if choice == SENTINEL_CUSTOM_PATH:
            custom = prompt_path()
            if custom is None:
                continue
            if not os.path.exists(custom):
                print(f"  找不到檔案: {custom}")
                continue
            rows = [probe_db_meta(custom)]
            rows[0]["kind"] = "custom"
            rows[0]["has_meta"] = os.path.exists(custom + ".meta.json")
            # 後面走「單檔」流程
            return _run_single_export(rows[0], data_dir)
        # === 4. 選了某個候選 DB：匯出 ===
        if 1 <= choice <= len(rows):
            row = rows[choice - 1]
            rc = _run_single_export(row, data_dir)
            if rc != 0:
                return rc
            # 問要不要再選
            if not confirm_again():
                return 0
            # 否則回到 while 開頭重新掃描
            continue
        # 其他（理論上不會到這裡）
        print(f"  → 未處理的選擇 {choice}")


def _run_single_export(row: Dict[str, Any], data_dir: str) -> int:
    """
    對單一 DB 跑匯出流程：解析 alias → 寫 temp → 呼叫 CLI → 顯示結果。
    """
    db_path = row["path"]
    station = row.get("station") or "?"
    if row.get("count", 0) == 0 and not row.get("kind") == "custom":
        print(f"  警告：{db_path} 內無資料，仍可繼續（會寫出只有標頭的 CSV）")

    # 別名決策
    alias_list, source = resolve_alias(db_path, station, data_dir)
    print()
    print(f"  alias 來源   : {source}")
    if source == "meta":
        custom_count = sum(1 for x in alias_list if x)
        print(f"  自訂別名     : {custom_count}/20 個非空")
    print(f"  預覽 (前 5)  : {alias_list[:5]}")
    print()

    # 呼叫 CLI
    rc, output = run_cli_export(
        db_path=db_path,
        station=station,
        data_dir=data_dir,
        alias_list=alias_list,
    )

    # 把 CLI 輸出原封不動回傳給使用者（包含 summary）
    if output:
        print(output, end="")
    if rc != 0:
        print(f"  CLI 退出碼 {rc} → 視為錯誤返回")
        return rc
    return 0


# === CLI ===

def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="gx20_export_csv_dialog",
        description="GX20 樣本 DB 匯出 CSV 對話框工具（呼叫 gx20_export_csv.py）",
    )
    p.add_argument(
        "--data-dir",
        metavar="DIR",
        default=DEFAULT_DATA_DIR,
        help=f"samples DB 目錄（預設: {DEFAULT_DATA_DIR}）",
    )
    p.add_argument(
        "--input-dir",
        metavar="DIR",
        action="append",
        default=[],
        help="額外掃描的目錄（可多次指定）",
    )
    p.add_argument(
        "-v", "--verbose",
        action="count",
        default=0,
        help="詳細 log（可重複：-v / -vv）",
    )
    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_argparser()
    args = parser.parse_args(argv)

    level = logging.WARNING
    if args.verbose >= 2:
        level = logging.DEBUG
    elif args.verbose == 1:
        level = logging.INFO
    logging.basicConfig(level=level, format="%(levelname)s %(name)s: %(message)s")

    # 環境檢查
    if not os.path.isfile(CLI_SCRIPT):
        print(f"[gx20_export_csv_dialog] 找不到 CLI: {CLI_SCRIPT}", file=sys.stderr)
        return 2

    # 沒 TTY 就別浪費時間（連 fallback 都沒了）
    if not sys.stdin.isatty():
        print("[gx20_export_csv_dialog] 非 TTY 環境，請直接用 gx20_export_csv.py", file=sys.stderr)
        return 2

    try:
        return run_dialog(args.data_dir, args.input_dir or None)
    except KeyboardInterrupt:
        print()
        print("使用者中斷")
        return 130


if __name__ == "__main__":
    sys.exit(main())

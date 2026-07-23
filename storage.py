# -*- coding: utf-8 -*-
"""
storage.py
==========
SQLite 儲存層（v5 多工位獨立 DB 版）。

佈局：
  data/
  ├── gx20_<station>.db    # 各工位一份 samples 表
  ├── gx20_settings.db     # 6 工位共用的 settings 表
  └── archive/
      └── gx20_<station>_<YYYYMMDD_HHMMSS>.db   # 清除前歸檔

設計理由：
  - 6 工位非同步上下線 → 各自獨立 DB 互不污染
  - 清特定工位時先歸檔 → 救得回來
  - 設定與資料分離 → 清資料不會洗掉 GX20 連線、別名、顏色

向後相容：
  - 啟動時若偵測到舊的 data/gx20.db，自動 migrate：
      1) samples 按 station 切到 6 個新 DB
      2) settings 全部複製到 gx20_settings.db
      3) 舊檔刪除（先歸檔到 archive/gx20_pre_migration_<時間>.db）
"""

import glob
import os
import re
import shutil
import sqlite3
import logging
import json
from contextlib import contextmanager
from typing import Dict, List, Optional, Any, Iterator, Tuple
from datetime import datetime, timedelta

import config  # v10.x：讀 defaults / from_json 給 archive meta 用

log = logging.getLogger("storage")

# 延遲載入：避免 storage.py 被 import 時 gx20_reader 還沒初始化
_STATIONS: Optional[List[str]] = None

DB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
ARCHIVE_DIR = os.path.join(DB_DIR, "archive")

# 每工位歸檔保留份數（超過自動刪最舊）
ARCHIVE_KEEP_PER_STATION = 5

# 舊版單一 DB 檔名（用於 migrate 偵測）
LEGACY_DB_NAME = "gx20.db"
LEGACY_SETTINGS_DB_NAME = "gx20_settings.db"  # migrate 完後 settings 用這個檔名

# === schema ===
SAMPLE_T_COLS = ", ".join(f"t{i:02d} REAL" for i in range(1, 21))
# v7：每工位樣本表新增 v/i/w 三欄（PW3335 電力）
#  對於舊 DB（v6.1 之前），init_db() 內會用 _migrate_add_power_columns() 補欄
SAMPLE_PW_COLS = "v REAL, i REAL, w REAL"
SCHEMA_SAMPLES = (
    "CREATE TABLE IF NOT EXISTS samples ("
    " id INTEGER PRIMARY KEY AUTOINCREMENT,"
    " ts TEXT NOT NULL,"
    " station TEXT NOT NULL,"
    f" {SAMPLE_T_COLS},"
    f" {SAMPLE_PW_COLS}"
    ")"
)

# ALTER TABLE 用的欄位定義（給舊 DB 補欄用）
# 與 SAMPLE_PW_COLS 同名、同型別，這裡列出來方便比對
SAMPLE_PW_COLUMN_NAMES = ("v", "i", "w")
SCHEMA_SETTINGS = (
    "CREATE TABLE IF NOT EXISTS settings ("
    " key TEXT PRIMARY KEY,"
    " value TEXT"
    ")"
)


def _stations() -> List[str]:
    """惰性載入 STATIONS 列表。"""
    global _STATIONS
    if _STATIONS is None:
        from gx20_reader import STATIONS  # 避免循環 import
        _STATIONS = list(STATIONS)
    return _STATIONS


# === 路徑 helper ===

def samples_db_path(station: str) -> str:
    return os.path.join(DB_DIR, f"gx20_{station}.db")


def settings_db_path() -> str:
    return os.path.join(DB_DIR, LEGACY_SETTINGS_DB_NAME)


def _ensure_dirs() -> None:
    os.makedirs(DB_DIR, exist_ok=True)
    os.makedirs(ARCHIVE_DIR, exist_ok=True)


# === connection helper ===

@contextmanager
def _conn_samples(station: str) -> Iterator[sqlite3.Connection]:
    """特定工位的 samples DB 連線。"""
    path = samples_db_path(station)
    _ensure_dirs()
    c = sqlite3.connect(path, timeout=5, isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    try:
        yield c
    finally:
        c.close()


@contextmanager
def _conn_settings() -> Iterator[sqlite3.Connection]:
    """共用 settings DB 連線。"""
    _ensure_dirs()
    c = sqlite3.connect(settings_db_path(), timeout=5, isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    try:
        yield c
    finally:
        c.close()


# === init / migrate ===

def init_db(reset: bool = False) -> None:
    """
    啟動時呼叫。
    1) 若有舊 data/gx20.db → migrate
    2) 為每工位建立 samples DB（補 schema）
    3) 為 settings DB 補 schema
    """
    _ensure_dirs()
    _migrate_legacy_if_needed()

    if reset:
        log.warning("init_db: reset=True，刪除所有 data/gx20_*.db 與 settings db")
        for s in _stations():
            p = samples_db_path(s)
            if os.path.exists(p):
                os.remove(p)
        if os.path.exists(settings_db_path()):
            os.remove(settings_db_path())

    # 為每工位建 schema（CREATE IF NOT EXISTS，不刪資料）
    for s in _stations():
        with _conn_samples(s) as c:
            c.execute(SCHEMA_SAMPLES)
            c.execute("CREATE INDEX IF NOT EXISTS idx_samples_ts ON samples(ts)")
            # v7：補上 v/i/w 三欄（舊 DB 向後相容）
            _ensure_power_columns(c, s)
    # settings
    with _conn_settings() as c:
        c.execute(SCHEMA_SETTINGS)
    log.info("init_db: 6 工位 samples DB + settings DB 就緒 (dir=%s)", DB_DIR)


def _migrate_legacy_if_needed() -> None:
    """若偵測到舊 data/gx20.db，把 samples 按 station 切到新 DB，settings 移到新 settings DB。"""
    legacy = os.path.join(DB_DIR, LEGACY_DB_NAME)
    if not os.path.exists(legacy):
        return

    log.info("偵測到舊 DB %s，開始 migrate 到 6 獨立 DB 佈局", legacy)
    ts_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive_path = os.path.join(ARCHIVE_DIR, f"gx20_pre_migration_{ts_str}.db")

    # 1) 先把舊檔整份歸檔（含 settings + samples）
    try:
        shutil.copy2(legacy, archive_path)
        log.info("migrate: 舊 DB 已歸檔到 %s", archive_path)
    except Exception as e:
        log.warning("migrate: 歸檔舊 DB 失敗: %s（繼續 migrate）", e)

    # 2) 連舊 DB 拉資料
    try:
        c = sqlite3.connect(legacy, timeout=5)
        c.row_factory = sqlite3.Row
        # 2a) samples 按 station 切到新 DB
        rows = c.execute("SELECT * FROM samples ORDER BY id ASC").fetchall()
        grouped: Dict[str, List[sqlite3.Row]] = {}
        for r in rows:
            grouped.setdefault(r["station"], []).append(r)
        for station, srows in grouped.items():
            # 若 station 不在 _stations() 內（理論不會），仍建檔
            with _conn_samples(station) as nc:
                nc.execute(SCHEMA_SAMPLES)
                nc.execute("CREATE INDEX IF NOT EXISTS idx_samples_ts ON samples(ts)")
                cols = "ts, station, " + ", ".join(f"t{i:02d}" for i in range(1, 21))
                placeholders = "?, ?, " + ", ".join("?" for _ in range(20))
                sql = f"INSERT INTO samples ({cols}) VALUES ({placeholders})"
                for r in srows:
                    vals = [r["ts"], r["station"]] + [r[f"t{i:02d}"] for i in range(1, 21)]
                    nc.execute(sql, vals)
            log.info("migrate: %s 寫入 %d 筆", station, len(srows))

        # 2b) settings 移到新 settings DB
        try:
            srows = c.execute("SELECT key, value FROM settings").fetchall()
            with _conn_settings() as nc:
                nc.execute(SCHEMA_SETTINGS)
                for r in srows:
                    nc.execute(
                        "INSERT INTO settings(key, value) VALUES(?, ?) "
                        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                        (r["key"], r["value"]),
                    )
            log.info("migrate: settings 寫入 %d 筆", len(srows))
        except sqlite3.OperationalError:
            # 舊 DB 沒有 settings 表（v1 之前），略過
            log.info("migrate: 舊 DB 無 settings 表，略過")

        c.close()
    except Exception as e:
        log.exception("migrate 過程失敗: %s", e)
        return

    # 3) 刪除舊檔（含 WAL/SHM）
    for ext in ("", "-wal", "-shm", "-journal"):
        p = legacy + ext
        if os.path.exists(p):
            try:
                os.remove(p)
            except OSError as e:
                log.warning("migrate: 刪除 %s 失敗: %s", p, e)
    log.info("migrate: 完成")


# === samples CRUD ===

def insert_sample(
    ts: str,
    station: str,
    temps: List[Optional[float]],
    v: Optional[float] = None,
    i: Optional[float] = None,
    w: Optional[float] = None,
) -> None:
    """寫入一筆取樣（temps 必須長度 20；None 視為 NULL）。

    v7：新增 v / i / w 三個 optional 參數，給 PW3335 用。
    - 寫 0.0 時也存成 0（不是 NULL），方便後端 CSV 輸出有實值
    - 寫 None 時存 NULL
    - 舊呼叫端不傳 v/i/w → 預設 None → 存 NULL
    """
    assert len(temps) == 20, f"temps 長度必須為 20，收到 {len(temps)}"
    assert station in _stations(), f"未知工位: {station}"
    # 註：必須在 _conn_samples 之前補 schema。若 DB 檔剛被刪除（clear_station_db 後），
    # 不建表寫入會跳 "no such table: samples" → 整個 round 死掉。
    _ensure_samples_table(station)
    cols = (
        "ts, station, "
        + ", ".join(f"t{i:02d}" for i in range(1, 21))
        + ", v, i, w"
    )
    placeholders = "?, ?, " + ", ".join("?" for _ in range(20)) + ", ?, ?, ?"
    sql = f"INSERT INTO samples ({cols}) VALUES ({placeholders})"
    vals: List[Any] = [ts, station] + [t if t is not None else None for t in temps] + [v, i, w]
    with _conn_samples(station) as c:
        c.execute(sql, vals)


def query_recent(station: str, since_minutes: int = 60) -> List[Dict[str, Any]]:
    """拉取指定工位最近 N 分鐘的 samples。"""
    assert station in _stations(), f"未知工位: {station}"
    _ensure_samples_table(station)
    cutoff = (datetime.now() - timedelta(minutes=since_minutes)).isoformat(timespec="seconds")
    with _conn_samples(station) as c:
        rows = c.execute(
            "SELECT * FROM samples WHERE ts >= ? ORDER BY ts ASC",
            (cutoff,),
        ).fetchall()
    return [_row_to_dict(r, station) for r in rows]


def query_latest(station: str) -> Optional[Dict[str, Any]]:
    """取得指定工位最新一筆。"""
    assert station in _stations(), f"未知工位: {station}"
    _ensure_samples_table(station)
    with _conn_samples(station) as c:
        r = c.execute(
            "SELECT * FROM samples ORDER BY ts DESC LIMIT 1"
        ).fetchone()
    return _row_to_dict(r, station) if r else None


# v6.1.3: 移除 query_count_in_range() 函式
# （「資料覆蓋」指標已從 UI 移除，對應的 SQLite COUNT 查詢不再需要）


def _row_to_dict(r: sqlite3.Row, station: str) -> Dict[str, Any]:
    d = {"ts": r["ts"], "station": station}
    for i in range(1, 21):
        d[f"t{i:02d}"] = r[f"t{i:02d}"]
    # v7：PW3335 欄位（舊 DB 補欄前會跳 KeyError；用 try 包起來向後相容）
    for col in SAMPLE_PW_COLUMN_NAMES:
        try:
            d[col] = r[col]
        except (IndexError, KeyError):
            d[col] = None
    return d


# === 清除 / 歸檔 ===

def _archive_path(station: str, ts_str: str) -> str:
    return os.path.join(ARCHIVE_DIR, f"gx20_{station}_{ts_str}.db")


def _archive_meta_path(archive_db_path: str) -> str:
    """備份 DB 對應的 meta JSON 檔路徑。"""
    return archive_db_path + ".meta.json"


def _build_archive_meta(station: str) -> Optional[dict]:
    """備份當下拍下該工位的 alias + note 等顯示設定。

    snapshot 頁讀取備份時、若有 meta 就用裡面的值覆蓋預設別名。
    讀不到任何設定也照樣寫一個空 meta（保證檔案存在、表示「歸檔當下沒有額外設定」）。
    """
    try:
        defaults = config.default_settings()
    except Exception as e:
        log.warning("_build_archive_meta: 取 defaults 失敗 (%s)，只寫基本 meta", e)
        defaults = {}

    meta = {
        "station":     station,
        "archived_at": datetime.now().isoformat(timespec="seconds"),
        "schema":      1,
        "alias":       None,        # 改 None 表示「沒設過」
        "note":        None,
    }
    try:
        alias_raw = get_setting("ch_alias")
        alias_obj = config.from_json(alias_raw, default=defaults.get("ch_alias"))
        if isinstance(alias_obj, dict):
            arr = alias_obj.get(station)
            if isinstance(arr, list):
                truncated = [str(x or "")[:20] for x in arr]
                # v10.x：若 alias 與預設完全一致，視為「未設定」→ meta.alias=null
                #         （讓 snapshot 退回顯示預設別名）
                default_alias = defaults.get("ch_alias", {}).get(station) if isinstance(defaults.get("ch_alias"), dict) else None
                if default_alias and truncated == default_alias:
                    meta["alias"] = None
                else:
                    meta["alias"] = truncated
    except Exception as e:
        log.warning("_build_archive_meta: alias 讀取失敗 (%s)，跳過", e)

    try:
        notes_raw = get_setting("notes")
        notes_obj = config.from_json(notes_raw, default=defaults.get("notes"))
        if isinstance(notes_obj, dict):
            v = notes_obj.get(station)
            if isinstance(v, str):
                truncated = v[:20]
                # v10.x：note 為空字串視為「未設定」→ meta.note=null
                if truncated.strip() == "":
                    meta["note"] = None
                else:
                    meta["note"] = truncated
    except Exception as e:
        log.warning("_build_archive_meta: note 讀取失敗 (%s)，跳過", e)

    return meta


def read_archive_meta(filename: str) -> Optional[dict]:
    """讀取備份 DB 對應的 meta JSON。檔案不存在或格式不正確回傳 None。"""
    # filename 只接受 basename（防路徑傳入）
    safe = os.path.basename(filename)
    db_path = os.path.join(ARCHIVE_DIR, safe)
    meta_path = _archive_meta_path(db_path)
    if not os.path.exists(meta_path):
        return None
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            data = json.loads(f.read())
        if not isinstance(data, dict):
            return None
        return data
    except Exception as e:
        log.warning("read_archive_meta: %s 讀取失敗 (%s)", meta_path, e)
        return None


def archive_station(station: str) -> Optional[str]:
    """
    把指定工位的 samples DB 歸檔到 archive/，並回傳歸檔檔路徑。
    若該工位 DB 不存在或無資料，回傳 None。
    同時 dump 該工位當下的 alias + note 到 .meta.json。
    """
    assert station in _stations(), f"未知工位: {station}"
    src = samples_db_path(station)
    if not os.path.exists(src):
        return None
    # 用檔案大小判斷：空 DB 也照歸檔（一致性）
    ts_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = _archive_path(station, ts_str)
    try:
        # 先關掉可能的連線（SQLite WAL 切乾淨）
        with _conn_samples(station) as c:
            c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        shutil.copy2(src, dst)
        log.info("archive_station: %s 已歸檔到 %s", station, dst)
    except Exception as e:
        log.error("archive_station: %s 歸檔失敗: %s", station, e)
        return None

    # v10.x：dump 顯示設定到 .meta.json（snapshot 讀取時覆蓋預設別名）
    try:
        meta = _build_archive_meta(station)
        if meta is not None:
            meta_path = _archive_meta_path(dst)
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)
            log.info("archive_station: %s meta 寫入 %s", station, meta_path)
    except Exception as e:
        log.warning("archive_station: %s meta 寫入失敗: %s", station, e)

    # 輪替：超過 ARCHIVE_KEEP_PER_STATION 份，刪最舊
    _prune_old_archives(station)
    return dst


def _prune_old_archives(station: str) -> int:
    """保留最近 ARCHIVE_KEEP_PER_STATION 份，刪除其餘。回傳刪除數。"""
    pattern = os.path.join(ARCHIVE_DIR, f"gx20_{station}_*.db*")
    files = sorted(glob.glob(pattern))
    # 同檔可能被列出多次（含 -wal, -shm, -journal）
    # 主檔定義：.db 結尾
    main_files = [
        f for f in files
        if f.endswith(".db")  # 只算純 .db 主檔，不算 .db-wal / .db.meta.json 等
    ]
    if len(main_files) <= ARCHIVE_KEEP_PER_STATION:
        return 0
    to_delete = main_files[:-ARCHIVE_KEEP_PER_STATION]
    deleted = 0
    for f in to_delete:
        try:
            os.remove(f)
            # 連 WAL/SHM/JOURNAL 也一起刪
            for ext in ("-wal", "-shm", "-journal"):
                p = f + ext
                if os.path.exists(p):
                    os.remove(p)
            # v10.x：連 .meta.json 也一起刪（保持一致性）
            meta_p = _archive_meta_path(f)
            if os.path.exists(meta_p):
                os.remove(meta_p)
            deleted += 1
        except OSError as e:
            log.warning("刪除歸檔 %s 失敗: %s", f, e)
    if deleted:
        log.info("歸檔輪替: 刪除 %s 的 %d 份舊歸檔", station, deleted)
    return deleted


def list_archives(station: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    列出歸檔。
      station=None → 列全部工位的歸檔
      station='工位5' → 只列該工位
    回傳：[{station, filename, path, size, mtime}, ...]（新到舊排序）
    """
    if not os.path.isdir(ARCHIVE_DIR):
        return []
    out: List[Dict[str, Any]] = []
    for s in ([station] if station else _stations()):
        pattern = os.path.join(ARCHIVE_DIR, f"gx20_{s}_*.db")
        for f in glob.glob(pattern):
            try:
                st = os.stat(f)
            except OSError:
                continue
            out.append({
                "station":   s,
                "filename":  os.path.basename(f),
                "path":      f,
                "size":      st.st_size,
                "mtime":     datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
            })
    out.sort(key=lambda d: d["mtime"], reverse=True)
    return out


def clear_station_db(station: str) -> bool:
    """
    刪除指定工位的 samples DB。
    注意：歸檔由呼叫端（archive_station）決定，不在此函式處理。
    """
    assert station in _stations(), f"未知工位: {station}"
    path = samples_db_path(station)
    if not os.path.exists(path):
        return True
    try:
        # 先 WAL checkpoint
        with _conn_samples(station) as c:
            c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        # 刪主檔 + WAL/SHM
        for ext in ("", "-wal", "-shm", "-journal"):
            p = path + ext
            if os.path.exists(p):
                os.remove(p)
        log.info("clear_station_db: 已刪除 %s 的 DB", station)
        return True
    except OSError as e:
        log.error("clear_station_db: 刪除 %s DB 失敗: %s", station, e)
        return False


def clear_all_samples() -> int:
    """
    刪除所有工位的 samples DB（不動 settings）。
    用於「確定要清空所有量測資料」場景。
    回傳成功刪除的工位數。
    """
    n = 0
    for s in _stations():
        if clear_station_db(s):
            n += 1
    return n


# === purge（保留天數） ===

def purge_old_samples(retention_days: int) -> int:
    """逐工位刪除超過保留天數的資料，回傳總刪除筆數。"""
    if retention_days <= 0:
        return 0
    cutoff = (datetime.now() - timedelta(days=retention_days)).isoformat(timespec="seconds")
    total = 0
    for s in _stations():
        _ensure_samples_table(s)
        with _conn_samples(s) as c:
            cur = c.execute("DELETE FROM samples WHERE ts < ?", (cutoff,))
            deleted = cur.rowcount or 0
            total += deleted
            if deleted:
                log.info("purge_old_samples[%s]: 刪除 %d 筆（保留 %d 天）", s, deleted, retention_days)
    if total:
        log.info("purge_old_samples: 總計刪除 %d 筆", total)
    return total


# === settings ===

def get_setting(key: str, default: Optional[str] = None) -> Optional[str]:
    with _conn_settings() as c:
        r = c.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return r["value"] if r else default


def set_setting(key: str, value: str) -> None:
    with _conn_settings() as c:
        c.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def get_all_settings() -> Dict[str, str]:
    with _conn_settings() as c:
        rows = c.execute("SELECT key, value FROM settings").fetchall()
    return {r["key"]: r["value"] for r in rows}


# === 統計 ===

def _ensure_samples_table(station: str) -> None:
    """確保該工位 DB 有 samples 表（DB 不存在時也順手建檔）。"""
    if station not in _stations():
        return
    with _conn_samples(station) as c:
        c.execute(SCHEMA_SAMPLES)
        c.execute("CREATE INDEX IF NOT EXISTS idx_samples_ts ON samples(ts)")
        # v7：補上 v/i/w 三欄（舊 DB 向後相容）
        _ensure_power_columns(c, station)


def _ensure_power_columns(c: sqlite3.Connection, station: str) -> None:
    """v7：確保 samples 表有 v / i / w 三欄。
    對 v7 之後新建的 DB：SCHEMA_SAMPLES 內已含這三欄，no-op。
    對舊 DB（v6.1 之前）：逐一 ALTER TABLE ADD COLUMN 補上。
    """
    try:
        rows = c.execute("PRAGMA table_info(samples)").fetchall()
    except sqlite3.OperationalError:
        # 表根本還沒建（理論上 SCHEMA_SAMPLES 會建，但保險起見）
        return
    existing = {row[1] for row in rows}  # row[1] = column name
    for col in SAMPLE_PW_COLUMN_NAMES:
        if col not in existing:
            try:
                c.execute(f"ALTER TABLE samples ADD COLUMN {col} REAL")
                log.info("storage: 為 %s samples 表補上 %s 欄", station, col)
            except sqlite3.OperationalError as e:
                # 萬一 race condition（兩個 process 同時 ALTER）只 warn
                log.debug("storage: %s ADD COLUMN %s 失敗（可能已被其他 process 加好）: %s",
                          station, col, e)


def count_samples() -> int:
    total = 0
    for s in _stations():
        _ensure_samples_table(s)
        with _conn_samples(s) as c:
            r = c.execute("SELECT COUNT(*) AS n FROM samples").fetchone()
            total += r["n"]
    return total


def count_samples_by_station() -> Dict[str, int]:
    out: Dict[str, int] = {}
    for s in _stations():
        _ensure_samples_table(s)
        with _conn_samples(s) as c:
            r = c.execute("SELECT COUNT(*) AS n FROM samples").fetchone()
            out[s] = r["n"]
    return out


def sample_time_range(station: str) -> Optional[Dict[str, str]]:
    """該工位最早/最晚一筆的 ts。"""
    if station not in _stations():
        return None
    _ensure_samples_table(station)
    with _conn_samples(station) as c:
        r = c.execute("SELECT MIN(ts) AS mn, MAX(ts) AS mx FROM samples").fetchone()
    if not r or r["mn"] is None:
        return None
    return {"first": r["mn"], "last": r["mx"]}


# === v10：snapshot viewer（離線瀏覽備份 db）===

# 嚴格的 archive 檔名白名單
# 檔名格式：gx20_<station>_YYYYMMDD_HHMMSS.db
# station 允許中英文數字（避免 path traversal），長度 1~32
ARCHIVE_FILENAME_RE = re.compile(r"^gx20_([\w\u4e00-\u9fff]{1,32})_(\d{8})_(\d{6})\.db$")


def validate_archive_filename(filename: str) -> Optional[Tuple[str, str]]:
    """
    驗證使用者傳入的 archive 檔名是否符合白名單格式。
    回傳 (station, ts_suffix) 給呼叫端組合路徑用；
    不合法回傳 None。
    注意：只驗證檔名，不檢查實際檔案存在與否。
    """
    if not filename or not isinstance(filename, str):
        return None
    # 防 path traversal：拒絕 / \ .. 等
    if "/" in filename or "\\" in filename or ".." in filename:
        return None
    m = ARCHIVE_FILENAME_RE.match(filename)
    if not m:
        return None
    return (m.group(1), m.group(2) + "_" + m.group(3))


def archive_meta(filename: str) -> Optional[Dict[str, Any]]:
    """
    從歸檔檔名 + DB 內容算出 metadata：
      station, filename, ts_min, ts_max, count, size, mtime
    若檔案不存在或檔名不合法，回傳 None。
    """
    parsed = validate_archive_filename(filename)
    if not parsed:
        return None
    station, _ts_suffix = parsed
    path = os.path.join(ARCHIVE_DIR, filename)
    if not os.path.exists(path):
        return None
    try:
        st = os.stat(path)
    except OSError:
        return None
    # 開啟 archive DB（唯讀）
    try:
        c = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        c.row_factory = sqlite3.Row
        # 確保 samples 表存在
        tables = {r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        if "samples" not in tables:
            c.close()
            return None
        r = c.execute("SELECT COUNT(*) AS n, MIN(ts) AS mn, MAX(ts) AS mx FROM samples").fetchone()
        c.close()
    except sqlite3.Error:
        return None
    if not r or r["mn"] is None:
        # 歸檔檔存在但裡面沒資料（清空後歸檔的情況）
        return {
            "station":  station,
            "filename": filename,
            "ts_min":   None,
            "ts_max":   None,
            "count":    0,
            "size":     st.st_size,
            "mtime":    datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
        }
    return {
        "station":  station,
        "filename": filename,
        "ts_min":   r["mn"],
        "ts_max":   r["mx"],
        "count":    r["n"],
        "size":     st.st_size,
        "mtime":    datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
    }


def list_archives_with_meta(station: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    跟 list_archives() 一樣列歸檔清單，但每個檔案多帶 ts_min / ts_max / count。
    對 archive 數量少（≤ 5 per station）的場景，讀檔成本可接受。
    """
    items = list_archives(station=station)
    out: List[Dict[str, Any]] = []
    for it in items:
        meta = archive_meta(it["filename"])
        if meta is None:
            # 檔案剛被刪掉或檔名毀損：跳過
            continue
        # 合併 it 的基本欄位（路徑、檔名）與 meta（DB 內容）
        out.append({
            "station":  meta["station"],
            "filename": meta["filename"],
            "path":     it["path"],
            "size":     meta["size"],
            "mtime":    meta["mtime"],
            "ts_min":   meta["ts_min"],
            "ts_max":   meta["ts_max"],
            "count":    meta["count"],
        })
    return out


def query_archive_range(filename: str, ts_min: Optional[str] = None, ts_max: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    從歸檔 db 讀取指定時間區間的 samples（無區間 → 全讀）。
    回傳格式跟 query_recent() 一致（list of dict, 每 dict 含 ts + station + t01..t20 + v/i/w）。

    注意：
      - 只讀 archive DB（不影響 live DB）
      - 用 mode=ro 開啟，避免意外寫入
      - 區間邊界用 ISO 字串比對（SQLite TEXT 比較 OK，因為 ISO 8601 字典序 == 時間序）
    """
    parsed = validate_archive_filename(filename)
    if not parsed:
        raise ValueError(f"invalid archive filename: {filename!r}")
    station, _ts_suffix = parsed
    path = os.path.join(ARCHIVE_DIR, filename)
    if not os.path.exists(path):
        raise FileNotFoundError(f"archive not found: {filename}")
    # 建 WHERE 條件
    clauses = []
    params: List[Any] = []
    if ts_min:
        clauses.append("ts >= ?")
        params.append(ts_min)
    if ts_max:
        clauses.append("ts <= ?")
        params.append(ts_max)
    where_sql = (" WHERE " + " AND ".join(clauses)) if clauses else ""

    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    c.row_factory = sqlite3.Row
    try:
        rows = c.execute(
            f"SELECT * FROM samples{where_sql} ORDER BY ts ASC",
            tuple(params),
        ).fetchall()
    finally:
        c.close()

    # 用 _row_to_dict 統一格式（station 用檔名解出來的，不是 row 內的 station 欄）
    return [_row_to_dict(r, station) for r in rows]


def compute_archive_stats(filename: str, ts_min: str, ts_max: str) -> Dict[str, Dict[str, Any]]:
    """
    對歸檔 db 在 [ts_min, ts_max] 區間內，跑 SQLite 原始計算：
      對每個欄位（t01..t20, v, i, w）回傳 {count, avg, min, max}。

    注意：
      - 用 SUM/COUNT/MIN/MAX 等聚合，**完全跑原始資料**，不經 LTTB
      - NULL 值自動跳過（AVG 的分母 = 非 NULL 筆數）
      - count = 0 的欄位回傳 None（避免除以 0）
    """
    parsed = validate_archive_filename(filename)
    if not parsed:
        raise ValueError(f"invalid archive filename: {filename!r}")
    path = os.path.join(ARCHIVE_DIR, filename)
    if not os.path.exists(path):
        raise FileNotFoundError(f"archive not found: {filename}")

    # 動態組 SQL：對每個欄位算一次
    fields: List[str] = [f"t{i:02d}" for i in range(1, 21)] + list(SAMPLE_PW_COLUMN_NAMES)
    select_parts: List[str] = []
    for f in fields:
        select_parts.append(
            f"SUM(CASE WHEN {f} IS NULL THEN 0 ELSE 1 END) AS cnt_{f},"
            f"AVG({f}) AS avg_{f},"
            f"MIN({f}) AS min_{f},"
            f"MAX({f}) AS max_{f}"
        )
    sql = (
        "SELECT " + ", ".join(select_parts) +
        " FROM samples WHERE ts >= ? AND ts <= ?"
    )

    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    c.row_factory = sqlite3.Row
    try:
        r = c.execute(sql, (ts_min, ts_max)).fetchone()
    finally:
        c.close()

    out: Dict[str, Dict[str, Any]] = {}
    if r is None:
        # 區間內完全沒有資料 → 全欄位 None
        for f in fields:
            out[f] = {"count": 0, "avg": None, "min": None, "max": None}
        return out
    for f in fields:
        cnt = r[f"cnt_{f}"] or 0
        avg = r[f"avg_{f}"]
        mn  = r[f"min_{f}"]
        mx  = r[f"max_{f}"]
        out[f] = {
            "count": int(cnt),
            # SQLite AVG 回 float；Python 端不轉 Decimal，統計用途精準度足夠
            "avg": float(avg) if avg is not None else None,
            "min": float(mn) if mn is not None else None,
            "max": float(mx) if mx is not None else None,
        }
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    init_db()
    insert_sample("2026-06-09T13:50:00", "工位1", [25.0 + i * 0.1 for i in range(20)])
    print("最新:", query_latest("工位1"))
    print("近 60 分鐘筆數:", len(query_recent("工位1", 60)))
    print("總筆數:", count_samples())
    print("by_station:", count_samples_by_station())
    print("歸檔清單:", list_archives())
    clear_station_db("工位1")
    print("清除後存在?", os.path.exists(samples_db_path("工位1")))

# -*- coding: utf-8 -*-
"""
tests/test_snapshot.py
======================
v10: snapshot viewer 的單元 + API 測試。

測試範圍：
  1) storage.validate_archive_filename() 白名單（路徑 traversal 防護）
  2) storage.query_archive_range() 讀備份 db
  3) storage.compute_archive_stats() 算 SQLite 原始統計
  4) /api/snapshot/archives
  5) /api/snapshot/data（含路徑 traversal 攻擊防護）
  6) /api/snapshot/stats

執行：python -m pytest tests/test_snapshot.py -v
"""

import os
import re
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

# 確保可以 import 上層模組
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import storage  # noqa: E402


# ---------- storage 白名單（路徑 traversal）測試 ----------

class TestValidateArchiveFilename(unittest.TestCase):
    """storage.validate_archive_filename() 的白名單行為。"""

    def test_valid_filenames(self):
        """合法的 archive 檔名應該通過。"""
        cases = [
            "gx20_工位4_20260721_225843.db",
            "gx20_a_20260101_000000.db",
            "gx20_Station1_20261231_235959.db",
            "gx20_工位_20260101_000000.db",  # 中文 1~32 字範圍
        ]
        for fn in cases:
            with self.subTest(fn=fn):
                result = storage.validate_archive_filename(fn)
                self.assertIsNotNone(result, f"{fn} 應該合法")
                station, ts_suffix = result
                self.assertEqual(len(ts_suffix), 15)  # YYYYMMDD_HHMMSS

    def test_invalid_filenames(self):
        """不合法的 archive 檔名應該被拒絕。"""
        cases = [
            "",                                  # 空字串
            "../etc/passwd",                     # path traversal
            "..\\windows\\system32",              # Windows path traversal
            "gx20_工位4_20260721_225843",        # 缺少 .db
            "gx20_工位4_20260721_225843.db.exe", # 錯誤副檔名
            "gx20_工位4_2026-07-21_22-58-43.db", # 時間格式錯誤
            "gx20_工位4_20260721.db",            # 缺少時間
            "gx20_/../etc/passwd.db",            # 偽裝合法但有 /
            "/etc/passwd",                       # 絕對路徑
            None,                                # None
            123,                                 # 非字串
        ]
        for fn in cases:
            with self.subTest(fn=fn):
                result = storage.validate_archive_filename(fn)
                self.assertIsNone(result, f"{fn!r} 應該被拒絕")

    def test_path_traversal_attacks(self):
        """明確模擬攻擊者嘗試繞過白名單。"""
        attacks = [
            "../../../etc/passwd",
            "gx20_工位4_20260721_225843.db/../../etc/passwd",
            "gx20_工位4_20260721_225843.db\x00.txt",  # null byte 注入
            "gx20_工位4_20260721_225843.db%00.txt",  # url-encoded null
            "gx20_a_20260101_000000.db\x00/../etc",
        ]
        for attack in attacks:
            with self.subTest(attack=attack):
                self.assertIsNone(
                    storage.validate_archive_filename(attack),
                    f"攻擊應被擋下: {attack!r}"
                )


# ---------- storage query / stats 測試（用 tempfile 做 fixture） ----------

class TestArchiveQueryAndStats(unittest.TestCase):
    """
    用 tempfile 建一個假的 archive DB，跑 query_archive_range 與 compute_archive_stats。
    為了測試，我們暫時 monkey-patch storage.ARCHIVE_DIR。
    """

    @classmethod
    def setUpClass(cls):
        # 建立暫存 archive 目錄
        cls.tmpdir = tempfile.mkdtemp(prefix="gx20_test_archive_")
        cls.archive_dir_backup = storage.ARCHIVE_DIR
        storage.ARCHIVE_DIR = cls.tmpdir

        # 建立一個假 archive DB（與 v7 live DB schema 一致：t01..t20 + v/i/w）
        cls.filename = "gx20_工位4_20260101_120000.db"
        path = os.path.join(cls.tmpdir, cls.filename)
        c = sqlite3.connect(path)
        t_cols = ", ".join(f"t{i:02d} REAL" for i in range(1, 21))
        c.execute(f"""
            CREATE TABLE samples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                station TEXT NOT NULL,
                {t_cols},
                v REAL, i REAL, w REAL
            )
        """)
        # 塞入 3 筆樣本，t01 為 25.0/30.0/35.0；v 在第三筆為 NULL
        # 其他溫度欄位填 dummy 值讓 _row_to_dict 不會炸
        base = [
            "2026-01-01T12:00:00", "工位4",
            25.0, 26.0, 27.0, 28.0, 29.0, 30.0, 31.0, 32.0, 33.0, 34.0,
            35.0, 36.0, 37.0, 38.0, 39.0, 40.0, 41.0, 42.0, 43.0, 44.0,
            220.0, 1.0, 220.0,
        ]
        mid = [
            "2026-01-01T12:00:10", "工位4",
            30.0, 31.0, 32.0, 33.0, 34.0, 35.0, 36.0, 37.0, 38.0, 39.0,
            40.0, 41.0, 42.0, 43.0, 44.0, 45.0, 46.0, 47.0, 48.0, 49.0,
            221.0, 1.5, 332.0,
        ]
        third = [
            "2026-01-01T12:00:20", "工位4",
            35.0, 36.0, 37.0, 38.0, 39.0, 40.0, 41.0, 42.0, 43.0, 44.0,
            45.0, 46.0, 47.0, 48.0, 49.0, 50.0, 51.0, 52.0, 53.0, 54.0,
            None, 2.0, 440.0,
        ]
        col_names = "ts, station, " + ", ".join(f"t{i:02d}" for i in range(1, 21)) + ", v, i, w"
        placeholders = ", ".join("?" for _ in range(25))
        for row in (base, mid, third):
            c.execute(f"INSERT INTO samples ({col_names}) VALUES ({placeholders})", row)
        c.commit()
        c.close()
        cls.path = path

    @classmethod
    def tearDownClass(cls):
        storage.ARCHIVE_DIR = cls.archive_dir_backup
        import shutil
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def test_query_archive_range_full(self):
        """不給區間 → 全讀。"""
        rows = storage.query_archive_range(self.filename)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["ts"], "2026-01-01T12:00:00")
        self.assertEqual(rows[0]["t01"], 25.0)
        self.assertEqual(rows[0]["station"], "工位4")
        # v 為 NULL 的第三筆 → 應為 None
        self.assertIsNone(rows[2]["v"])

    def test_query_archive_range_with_window(self):
        """指定 ts_min / ts_max → 只回該區間。"""
        rows = storage.query_archive_range(
            self.filename,
            ts_min="2026-01-01T12:00:05",
            ts_max="2026-01-01T12:00:15",
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["ts"], "2026-01-01T12:00:10")

    def test_query_archive_range_invalid_filename(self):
        """不合法的檔名 → ValueError。"""
        with self.assertRaises(ValueError):
            storage.query_archive_range("../etc/passwd")

    def test_query_archive_range_not_found(self):
        """檔案不存在 → FileNotFoundError。"""
        with self.assertRaises(FileNotFoundError):
            storage.query_archive_range("gx20_工位4_20990101_000000.db")

    def test_compute_archive_stats_all_three_points(self):
        """整段區間統計：t01 應為 avg=30, min=25, max=35, count=3。"""
        stats = storage.compute_archive_stats(
            self.filename, "2026-01-01T12:00:00", "2026-01-01T12:00:20"
        )
        self.assertEqual(stats["t01"]["count"], 3)
        self.assertAlmostEqual(stats["t01"]["avg"], 30.0, places=5)
        self.assertEqual(stats["t01"]["min"], 25.0)
        self.assertEqual(stats["t01"]["max"], 35.0)

    def test_compute_archive_stats_v_skips_null(self):
        """v 在第三筆是 NULL → count=2, avg=220.5, min=220, max=221。"""
        stats = storage.compute_archive_stats(
            self.filename, "2026-01-01T12:00:00", "2026-01-01T12:00:20"
        )
        self.assertEqual(stats["v"]["count"], 2)
        self.assertAlmostEqual(stats["v"]["avg"], 220.5, places=5)
        self.assertEqual(stats["v"]["min"], 220.0)
        self.assertEqual(stats["v"]["max"], 221.0)

    def test_compute_archive_stats_empty_range(self):
        """區間無資料 → count=0, avg/min/max 全 None。"""
        stats = storage.compute_archive_stats(
            self.filename, "2099-01-01T00:00:00", "2099-01-01T01:00:00"
        )
        for key in stats:
            self.assertEqual(stats[key]["count"], 0)
            self.assertIsNone(stats[key]["avg"])
            self.assertIsNone(stats[key]["min"])
            self.assertIsNone(stats[key]["max"])

    def test_compute_archive_stats_all_23_fields(self):
        """應回傳 t01..t20 + v/i/w 共 23 個欄位。"""
        stats = storage.compute_archive_stats(
            self.filename, "2026-01-01T12:00:00", "2026-01-01T12:00:20"
        )
        expected = [f"t{i:02d}" for i in range(1, 21)] + ["v", "i", "w"]
        self.assertEqual(set(stats.keys()), set(expected))


# ---------- API 測試（Flask test_client） ----------

class TestSnapshotAPI(unittest.TestCase):
    """
    用 Flask test_client 測 3 個 API endpoint。
    不啟動 socketio、不跑 background poller。
    """

    @classmethod
    def setUpClass(cls):
        # import app（會載入 storage，但不啟動 poller）
        import app as flask_app
        cls.app = flask_app.app
        cls.client = cls.app.test_client()

    def test_snapshot_page_renders(self):
        """/snapshot 頁面回 200 + HTML。
        前提：templates/snapshot.html 存在（在切片 2 建立）。
        """
        import os
        tmpl_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "templates", "snapshot.html"
        )
        if not os.path.exists(tmpl_path):
            self.skipTest("templates/snapshot.html 未建立（切片 2 未完成）")
        r = self.client.get("/snapshot")
        self.assertEqual(r.status_code, 200)
        # snapshot.html 內有 snapshot 關鍵字
        body = r.get_data(as_text=True).lower()
        self.assertIn("snapshot", body)

    def test_api_archives_returns_envelope(self):
        """/api/snapshot/archives 至少回 ok 欄位。"""
        r = self.client.get("/api/snapshot/archives")
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertTrue(body["ok"])
        self.assertIn("archives", body)
        self.assertIn("count", body)

    def test_api_data_missing_filename(self):
        """沒給 filename → 400 MISSING_FILENAME。"""
        r = self.client.get("/api/snapshot/data")
        self.assertEqual(r.status_code, 400)
        body = r.get_json()
        self.assertFalse(body["ok"])
        self.assertEqual(body["error"], "MISSING_FILENAME")

    def test_api_data_path_traversal_blocked(self):
        """path traversal 攻擊 → 400 INVALID_FILENAME。"""
        r = self.client.get("/api/snapshot/data?filename=../../etc/passwd")
        self.assertEqual(r.status_code, 400)
        body = r.get_json()
        self.assertFalse(body["ok"])
        self.assertEqual(body["error"], "INVALID_FILENAME")

    def test_api_data_url_encoded_traversal_blocked(self):
        """URL-encoded path traversal 也應該被擋。"""
        # %2F = /
        r = self.client.get("/api/snapshot/data?filename=..%2F..%2Fetc%2Fpasswd")
        self.assertEqual(r.status_code, 400)
        body = r.get_json()
        self.assertFalse(body["ok"])
        self.assertEqual(body["error"], "INVALID_FILENAME")

    def test_api_data_nonexistent_valid_format(self):
        """檔名格式合法但檔案不存在 → 404。"""
        r = self.client.get("/api/snapshot/data?filename=gx20_工位4_20990101_000000.db")
        self.assertEqual(r.status_code, 404)
        body = r.get_json()
        self.assertFalse(body["ok"])
        self.assertEqual(body["error"], "NOT_FOUND")

    def test_api_stats_missing_ts(self):
        """缺 ts_min / ts_max → 400 MISSING_TS。"""
        r = self.client.get(
            "/api/snapshot/stats?filename=gx20_工位4_20990101_000000.db"
        )
        self.assertEqual(r.status_code, 400)
        body = r.get_json()
        self.assertEqual(body["error"], "MISSING_TS")

    def test_api_stats_invalid_filename(self):
        """不合法的 filename → 400。"""
        r = self.client.get(
            "/api/snapshot/stats?filename=../etc/passwd&ts_min=2026-01-01T00:00:00&ts_max=2026-01-01T01:00:00"
        )
        self.assertEqual(r.status_code, 400)
        body = r.get_json()
        self.assertEqual(body["error"], "INVALID_FILENAME")

    def test_existing_routes_not_broken(self):
        """既有路由不應被這次改動影響。"""
        # / 應該還是回 200
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        # /settings 也是
        r = self.client.get("/settings")
        self.assertEqual(r.status_code, 200)


if __name__ == "__main__":
    unittest.main()

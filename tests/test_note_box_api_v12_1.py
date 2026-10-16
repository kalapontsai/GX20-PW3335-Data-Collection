# -*- coding: utf-8 -*-
"""
tests/test_note_box_api_v12_1.py
================================
v12.1：per-station 備註 API 端到端測試。

測試範圍：
  1) GET /api/note_box/<station> 讀單一工位備註
  2) PUT /api/note_box/<station> 寫單一工位備註
  3) 255 字截斷、空字串清空
  4) 未知工位 → 404
  5) Body 型別錯誤 → 400
  6) notes[station] 形狀向後相容
  7) 寫入後 /api/settings 內 notes 同步更新
  8) 寫入後 SocketIO `note_box_updated` 事件廣播

執行：
  1) 啟動 server：python3 run.py
  2) 跑測試：python3 -m pytest tests/test_note_box_api_v12_1.py -v
     或直接 python3 tests/test_note_box_api_v12_1.py
"""

import json
import os
import sys
import time
import threading
import unittest
import urllib.parse
import urllib.request
import urllib.error

import socketio  # python-socketio[client]


BASE = os.environ.get("GX20_BASE", "http://127.0.0.1:5000")
NOTE_LIMIT = 255
STATIONS = [f"工位{i}" for i in range(1, 7)]


def _ep(station: str) -> str:
    """產生 path，並對中文工位名做 percent-encode。"""
    return "/api/note_box/" + urllib.parse.quote(station)


def _req(method: str, path: str, body=None):
    """HTTP 請求輔助；回傳 (status_code, parsed_json)。"""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    r = urllib.request.Request(f"{BASE}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _reset_notes():
    """測試前置：清空 6 工位備註。"""
    for st in STATIONS:
        _req("PUT", _ep(st), {"note": ""})


class TestNoteBoxApiV12_1(unittest.TestCase):
    """v12.1 新增的 per-station 備註 API。"""

    @classmethod
    def setUpClass(cls):
        # 先確認 server 有在聽（避免測試環境沒起 server 一直誤報）
        try:
            with urllib.request.urlopen(f"{BASE}/api/settings", timeout=3) as r:
                if r.status != 200:
                    raise RuntimeError(f"server not healthy: {r.status}")
        except Exception as e:
            raise unittest.SkipTest(f"server not reachable at {BASE}: {e}")
        _reset_notes()

    def test_01_get_empty_note(self):
        code, body = _req("GET", _ep("工位5"))
        self.assertEqual(code, 200)
        self.assertTrue(body.get("ok"))
        self.assertEqual(body.get("station"), "工位5")
        self.assertEqual(body.get("note"), "")

    def test_02_put_note(self):
        text = "爐溫偏高,12:30 開門"
        code, body = _req("PUT", _ep("工位5"), {"note": text})
        self.assertEqual(code, 200)
        self.assertEqual(body.get("note"), text)
        # GET 確認持久化
        code, body = _req("GET", _ep("工位5"))
        self.assertEqual(code, 200)
        self.assertEqual(body.get("note"), text)

    def test_03_truncate_to_255(self):
        code, body = _req("PUT", _ep("工位5"), {"note": "A" * 300})
        self.assertEqual(code, 200)
        self.assertEqual(len(body.get("note", "")), NOTE_LIMIT)

    def test_04_empty_string_clears(self):
        _req("PUT", _ep("工位5"), {"note": "something"})
        code, body = _req("PUT", _ep("工位5"), {"note": ""})
        self.assertEqual(code, 200)
        self.assertEqual(body.get("note"), "")
        code, body = _req("GET", _ep("工位5"))
        self.assertEqual(body.get("note"), "")

    def test_05_unknown_station_get_404(self):
        code, body = _req("GET", _ep("工位9"))
        self.assertEqual(code, 404)
        self.assertIn("unknown station", body.get("error", ""))

    def test_06_unknown_station_put_404(self):
        code, body = _req("PUT", _ep("工位9"), {"note": "x"})
        self.assertEqual(code, 404)
        self.assertIn("unknown station", body.get("error", ""))

    def test_07_bad_body_list_returns_400(self):
        """body 為 list `[]` 不應被誤當成 dict（既有的 `or {}` 陷阱）。"""
        r = urllib.request.Request(
            f"{BASE}{_ep('工位5')}", data=b"[]",
            headers={"Content-Type": "application/json"}, method="PUT",
        )
        try:
            with urllib.request.urlopen(r, timeout=5) as resp:
                code, body = resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            code, body = e.code, json.loads(e.read())
        self.assertEqual(code, 400)

    def test_08_non_string_note_returns_400(self):
        code, body = _req("PUT", _ep("工位5"), {"note": 12345})
        self.assertEqual(code, 400)

    def test_09_notes_dict_shape_backward_compat(self):
        """PUT 也接受 `notes[station]` 形狀（與整包 PATCH 一致）。"""
        code, body = _req("PUT", _ep("工位3"), {"notes": {"工位3": "v10.x 相容路徑"}})
        self.assertEqual(code, 200)
        self.assertEqual(body.get("note"), "v10.x 相容路徑")
        code, body = _req("GET", _ep("工位3"))
        self.assertEqual(body.get("note"), "v10.x 相容路徑")

    def test_10_put_does_not_affect_other_stations(self):
        _req("PUT", _ep("工位1"), {"note": "工位1 備註"})
        code, body = _req("GET", _ep("工位2"))
        self.assertEqual(body.get("note"), "")

    def test_11_settings_notes_synced(self):
        _req("PUT", _ep("工位1"), {"note": "sync 測試"})
        code, body = _req("GET", "/api/settings")
        self.assertEqual(body.get("notes", {}).get("工位1"), "sync 測試")

    def test_12_socket_note_box_updated_emitted(self):
        """PUT 後應 emit `note_box_updated` socket 事件給所有 client。"""
        events = []
        ev_lock = threading.Lock()

        sio = socketio.Client(logger=False, engineio_logger=False)

        @sio.on("note_box_updated")
        def on_update(payload):
            with ev_lock:
                events.append(payload)

        sio.connect(BASE, wait_timeout=5)
        try:
            # 觸發 PUT
            code, _ = _req("PUT", _ep("工位4"), {"note": "socket 同步測試"})
            self.assertEqual(code, 200)

            # 最多等 2s 等事件
            deadline = time.time() + 2.0
            while time.time() < deadline:
                with ev_lock:
                    if events:
                        break
                time.sleep(0.1)

            with ev_lock:
                self.assertTrue(events, "沒收到 note_box_updated 事件")
                ev = events[-1]
            self.assertEqual(ev.get("station"), "工位4")
            self.assertEqual(ev.get("note"), "socket 同步測試")
        finally:
            sio.disconnect()


if __name__ == "__main__":
    unittest.main(verbosity=2)

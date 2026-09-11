"""
v10.4 apply_json_to_sqlite dict-corruption fix — repro 驗證
==========================================================

為什麼需要這支（MEMORY 政策 2b8de47 教訓）：
  改 save/load chain 必須 repro script 灌真實 shape 跑關鍵 function。

本次 bug：
  apply_json_to_sqlite() 對所有非 (ch_visibility/alias/color) 的設定都用
  `storage.set_setting(k, str(v))`。當 v 是 dict 時，Python 的 str() 用單引號
  (e.g. "{'工位1': {'min': 0}}")，這不是合法 JSON。

  結果：
    - SQLite 內存的是 Python repr（單引號）
    - 下次 load_settings() 內 config.from_json(raw) 跑 json.loads 失敗
    - fallback 到 default → 重開機後 y_axis / pw_axis / pw3335 / notes 等
      「已經自訂」的設定全部還原成預設值。

本次 fix：
  對 dict / list 型別用 config.to_json(v) 取代 str(v)，跟 save_settings()
  保持一致；保留 ch_visibility/alias/color 的顯式白名單保護。

本 repro 對「apply_json_to_sqlite → load_settings」的「真實 round-trip」，
**對照 4 種 shape**：
  1. y_axis（dict-of-dict）跨 startup → 不還原預設
  2. pw_axis（dict-of-dict {v, i, w}） → 不還原預設
  3. notes（dict-of-str） → 不還原預設（雖然目前沒壞，但保險證明）
  4. pw3335（nested dict {port, hosts, colors}） → 不還原預設

執行：python tests/test_apply_json_to_sqlite_v10_4_repro.py

預期：fix 之前 → 全 fail（證明 bug 存在，y_axis/pw_axis/pw3335/notes 被還原）
      fix 之後 → 全 pass（證明修好）
"""

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# ============================================================
# Sandbox：把 storage.DB_DIR 跟 app.SETTINGS_DIR 重導到 temp dir
# ============================================================

class _Sandbox:
    def __init__(self):
        self.tmpdir = tempfile.mkdtemp(prefix="apply_json_repro_")
        self.data_dir = os.path.join(self.tmpdir, "data")
        self.config_dir = os.path.join(self.tmpdir, "config")
        os.makedirs(self.data_dir, exist_ok=True)
        os.makedirs(self.config_dir, exist_ok=True)

    def __enter__(self):
        import storage
        import app
        self._storage_db_dir = storage.DB_DIR
        self._app_settings_dir = app.SETTINGS_DIR
        self._app_settings_json_path = app.SETTINGS_JSON_PATH
        storage.DB_DIR = self.data_dir
        app.SETTINGS_DIR = self.config_dir
        app.SETTINGS_JSON_PATH = os.path.join(self.config_dir, "settings.json")
        return self

    def __exit__(self, *exc):
        import storage
        import app
        storage.DB_DIR = self._storage_db_dir
        app.SETTINGS_DIR = self._app_settings_dir
        app.SETTINGS_JSON_PATH = self._app_settings_json_path
        shutil.rmtree(self.tmpdir, ignore_errors=True)


_passed = 0
_failed = 0


def require(cond, msg):
    global _passed, _failed
    if not cond:
        print(f"  ✗ FAIL: {msg}")
        _failed += 1
        return False
    print(f"  ✓ {msg}")
    _passed += 1
    return True


def reset_sandbox(sandbox):
    import storage
    for f in os.listdir(sandbox.data_dir):
        if f.startswith("gx20") and f.endswith(".db"):
            os.remove(os.path.join(sandbox.data_dir, f))
    settings_json = os.path.join(sandbox.config_dir, "settings.json")
    if os.path.exists(settings_json):
        os.remove(settings_json)
    storage.init_db(reset=True)


def seed_dump_file(sandbox, dumped_dict):
    """模擬「上次正常關機」留下 config/settings.json 的場景。"""
    json_path = os.path.join(sandbox.config_dir, "settings.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(dumped_dict, f, ensure_ascii=False, indent=2, sort_keys=True)


# ============================================================
# 4 個 shape 驗證
# ============================================================

def test_shape_1_y_axis():
    """shape 1：y_axis (dict-of-dict) 跨 startup round-trip 不還原預設"""
    print("\n[shape 1] y_axis dict-of-dict round-trip")
    with _Sandbox() as sb:
        import storage
        import app
        reset_sandbox(sb)

        # 模擬「使用者把工位1 的 y_range 改成 0~50 auto=False」
        st1_custom = {"min": 0.0, "max": 50.0, "auto": False}
        dumped = {
            "y_axis": {"工位1": st1_custom, "工位2": {"min": 0, "max": 100, "auto": True}},
            "theme": "dark",
        }
        seed_dump_file(sb, dumped)

        # 模擬「重啟：apply_json_to_sqlite + load_settings」
        json_cfg = app.load_settings_from_json()
        require(json_cfg is not None, "load_settings_from_json() 應有值")
        app.apply_json_to_sqlite(json_cfg)

        loaded = app.load_settings()
        got = loaded.get("y_axis", {}).get("工位1")
        require(got == st1_custom,
                f"y_axis 工位1 應保留 {st1_custom}，實際 {got}")
        return True


def test_shape_2_pw_axis():
    """shape 2：pw_axis (dict-of-dict with v/i/w sub-dict) round-trip"""
    print("\n[shape 2] pw_axis dict-of-dict round-trip")
    with _Sandbox() as sb:
        import storage
        import app
        reset_sandbox(sb)

        # 模擬「使用者把工位3 的 V 改成 0~240，I 改 0~3」
        dumped = {
            "pw_axis": {
                "工位3": {
                    "v": {"min": 0.0, "max": 240.0, "auto": False},
                    "i": {"min": 0.0, "max": 3.0, "auto": False},
                    "w": {"min": 0.0, "max": 250.0, "auto": False},
                }
            }
        }
        seed_dump_file(sb, dumped)

        json_cfg = app.load_settings_from_json()
        app.apply_json_to_sqlite(json_cfg)

        loaded = app.load_settings()
        got_v = loaded.get("pw_axis", {}).get("工位3", {}).get("v")
        require(got_v == {"min": 0.0, "max": 240.0, "auto": False},
                f"pw_axis 工位3.v 應保留客製，實際 {got_v}")
        return True


def test_shape_3_notes():
    """shape 3：notes (dict-of-str) round-trip — 雖然目前沒壞但要保險證明"""
    print("\n[shape 3] notes dict-of-str round-trip")
    with _Sandbox() as sb:
        import storage
        import app
        reset_sandbox(sb)

        dumped = {
            "notes": {
                "工位1": "H61DV VIP-800改風道",
                "工位2": "F42D-EV蓋變更-原狀",
                "工位3": "09091654CC",
                "工位4": "",
                "工位5": "",
                "工位6": "",
            }
        }
        seed_dump_file(sb, dumped)

        json_cfg = app.load_settings_from_json()
        app.apply_json_to_sqlite(json_cfg)

        loaded = app.load_settings()
        got = loaded.get("notes", {})
        require(got.get("工位1") == "H61DV VIP-800改風道",
                f"notes 工位1 應保留客製，實際 {got.get('工位1')!r}")
        require(got.get("工位3") == "09091654CC",
                f"notes 工位3 應保留客製，實際 {got.get('工位3')!r}")
        return True


def test_shape_4_pw3335():
    """shape 4：pw3335 (nested dict {port, hosts, colors}) round-trip"""
    print("\n[shape 4] pw3335 nested dict round-trip")
    with _Sandbox() as sb:
        import storage
        import app
        reset_sandbox(sb)

        dumped = {
            "pw3335": {
                "port": 3301,   # 客製：原本 3300 改成 3301
                "hosts": {
                    "工位1": "10.20.30.41",   # 客製
                    "工位2": "10.20.30.42",
                    "工位3": "10.20.30.43",
                    "工位4": "10.20.30.44",
                    "工位5": "10.20.30.45",
                    "工位6": "10.20.30.46",
                },
                "colors": {
                    "V": "#00ff00",   # 客製：原本黃色改成綠
                    "I": "#1abc9c",
                    "W": "#e74c3c",
                },
            }
        }
        seed_dump_file(sb, dumped)

        json_cfg = app.load_settings_from_json()
        app.apply_json_to_sqlite(json_cfg)

        loaded = app.load_settings()
        got_pw = loaded.get("pw3335", {})
        require(got_pw.get("port") == 3301,
                f"pw3335.port 應為 3301，實際 {got_pw.get('port')}")
        require(got_pw.get("hosts", {}).get("工位1") == "10.20.30.41",
                f"pw3335.hosts 工位1 應為客製 IP，實際 {got_pw.get('hosts', {}).get('工位1')}")
        require(got_pw.get("colors", {}).get("V") == "#00ff00",
                f"pw3335.colors.V 應為客製色，實際 {got_pw.get('colors', {}).get('V')}")
        return True


# ============================================================
# 入口
# ============================================================

if __name__ == "__main__":
    print("=" * 60)
    print("v10.4 apply_json_to_sqlite dict-corruption fix — repro 驗證")
    print("=" * 60)
    results = []
    results.append(test_shape_1_y_axis())
    results.append(test_shape_2_pw_axis())
    results.append(test_shape_3_notes())
    results.append(test_shape_4_pw3335())
    print("\n" + "=" * 60)
    total = len(results)
    passed_count = sum(1 for r in results if r)
    print(f"✓ pass: {_passed}    ✗ fail: {_failed}")
    if _failed == 0:
        print(f"✓ 全部 {total} 個 shape 通過")
        sys.exit(0)
    else:
        print(f"✗ {passed_count}/{total} 個 shape 通過，{total - passed_count} 個失敗")
        print("\n如果看到 shape 失敗 → bug 存在：apply_json_to_sqlite 對 dict 用 str(v)")
        print("解法：對 isinstance(v, (dict, list)) 改用 config.to_json(v)")
        sys.exit(1)

"""
v10.3.x whitelist save 鏈修補 — repro 驗證
============================================

為什麼需要這支（MEMORY 政策 2b8de47 教訓）：
  改 ring / state / schema 必須 repro script 灌真實 shape 跑關鍵 function。
  本次改動：app.py save_settings 加 whitelist 分支、load_settings 加 whitelist 回傳。
  邏輯變更：前端送出的 whitelist patch 真的寫進 SQLite + dump 進 settings.json。

本 repro 對「save_settings / load_settings」的「mock 模擬」，**對照 5 種 shape**：
  1. patch = {whitelist: {cors_origins: [...]}} → 寫 SQLite + dump json 後 load 拿回
  2. patch 只送 cors_origins → 其他 4 個子 key 不被洗掉（per-key merge 驗證）
  3. patch 內 whitelist 不是 dict → 走 else 變 str(v)，不 crash
  4. SQLite 已有 whitelist 寫過 → load 時 5 個子 key 都回填（DEFAULTS 兜底）
  5. dump settings.json 那段也要包含 whitelist 區塊（檔案內容驗證）

執行：python tests/test_whitelist_save_repro.py

預期：fix 之前 → 全 fail（證明 bug 存在）
      fix 之後 → 全 pass（證明修好）
"""

import sys
import os
import json
import shutil
import tempfile
from pathlib import Path

# 讓測試腳本可以 import 根目錄的 module
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# ============================================================
# Sandbox：用 temp dir 隔離 settings DB 跟 settings.json
# 避免污染 repo 內的 data/ 跟 config/
# ============================================================

class _Sandbox:
    """重導 storage.DB_DIR 跟 app.SETTINGS_DIR / SETTINGS_JSON_PATH 到 temp dir。"""

    def __init__(self):
        self.tmpdir = tempfile.mkdtemp(prefix="whitelist_repro_")
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
        # 重導（改 module-level 常數，storage/app 內部用 os.path.join 接常數）
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


# ============================================================
# 驗證函式
# ============================================================

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
    """清掉 sandbox 內的 SQLite + settings.json，重新 init_db。"""
    import storage
    # 刪所有 gx20_*.db + settings DB
    for f in os.listdir(sandbox.data_dir):
        if f.startswith("gx20") and f.endswith(".db"):
            os.remove(os.path.join(sandbox.data_dir, f))
    # 刪 settings.json
    settings_json = os.path.join(sandbox.config_dir, "settings.json")
    if os.path.exists(settings_json):
        os.remove(settings_json)
    storage.init_db(reset=True)


# ============================================================
# 5 個 shape 驗證
# ============================================================

def test_shape_1_basic_save_and_load():
    """shape 1：patch = {whitelist: {cors_origins: [...]}} → 寫後能 load 拿回"""
    print("\n[shape 1] basic save + load whitelist")
    with _Sandbox() as sb:
        import storage
        import app
        reset_sandbox(sb)

        cors = ["http://test1.example:5000", "http://test2.example:5000"]
        app.save_settings({"whitelist": {"cors_origins": cors}})

        # 1) SQLite 內有 key="whitelist" 且內容是 JSON
        raw = storage.get_setting("whitelist")
        require(isinstance(raw, str), f"SQLite 內 whitelist 應為 str，實際 {type(raw).__name__}")
        try:
            parsed = json.loads(raw)
        except Exception as e:
            require(False, f"SQLite 內 whitelist 應為合法 JSON，但 parse 失敗: {e}")
            return False
        require(isinstance(parsed, dict), "SQLite 內 whitelist JSON 應為 dict")
        require(parsed.get("cors_origins") == cors,
                f"SQLite 內 cors_origins 應為 {cors}，實際 {parsed.get('cors_origins')}")

        # 2) dump 出來的 settings.json 也要包含 whitelist 區塊
        json_path = os.path.join(sb.config_dir, "settings.json")
        require(os.path.exists(json_path), f"{json_path} 應存在（dump json 段應執行）")
        with open(json_path, "r", encoding="utf-8") as f:
            dumped = json.load(f)
        require("whitelist" in dumped, "settings.json 內應包含 whitelist 區塊")
        wl_dumped = dumped.get("whitelist", {})
        require(isinstance(wl_dumped, dict), "settings.json 內 whitelist 應為 dict")
        require(wl_dumped.get("cors_origins") == cors,
                f"settings.json 內 cors_origins 應為 {cors}，實際 {wl_dumped.get('cors_origins')}")

        # 3) load_settings() 回傳也要有 whitelist，且 cors_origins 對
        loaded = app.load_settings()
        require("whitelist" in loaded, "load_settings() 回傳應包含 whitelist")
        wl_loaded = loaded.get("whitelist", {})
        require(wl_loaded.get("cors_origins") == cors,
                f"load_settings().whitelist.cors_origins 應為 {cors}，實際 {wl_loaded.get('cors_origins')}")
        return True


def test_shape_2_per_key_merge():
    """shape 2：patch 只送 cors_origins → 其他 4 個子 key 不被洗掉"""
    print("\n[shape 2] per-key merge（只送一個子 key 不洗其他）")
    with _Sandbox() as sb:
        import storage
        import app
        reset_sandbox(sb)

        # 先寫一包完整的
        full = {
            "cors_origins": ["http://a.example:5000"],
            "ota_admin_ips": ["10.0.0.1", "10.0.0.2"],
            "remote_write_ips": ["127.0.0.1", "::1"],
            "ota_allowed_targets": ["static/js/", "app.py"],
            "ota_blocked_exts": [".pyc", ".exe"],
        }
        app.save_settings({"whitelist": full})

        # 再只送 cors_origins（模擬使用者單獨改一欄）
        new_cors = ["http://b.example:5000", "http://c.example:5000"]
        app.save_settings({"whitelist": {"cors_origins": new_cors}})

        loaded = app.load_settings()
        wl = loaded.get("whitelist", {})
        require(wl.get("cors_origins") == new_cors,
                f"cors_origins 應被更新為 {new_cors}，實際 {wl.get('cors_origins')}")
        require(wl.get("ota_admin_ips") == ["10.0.0.1", "10.0.0.2"],
                f"ota_admin_ips 應保留 [10.0.0.1, 10.0.0.2]，實際 {wl.get('ota_admin_ips')}")
        require(wl.get("remote_write_ips") == ["127.0.0.1", "::1"],
                f"remote_write_ips 應保留，實際 {wl.get('remote_write_ips')}")
        require(wl.get("ota_allowed_targets") == ["static/js/", "app.py"],
                f"ota_allowed_targets 應保留，實際 {wl.get('ota_allowed_targets')}")
        require(wl.get("ota_blocked_exts") == [".pyc", ".exe"],
                f"ota_blocked_exts 應保留，實際 {wl.get('ota_blocked_exts')}")
        return True


def test_shape_3_non_dict_whitelist():
    """shape 3：patch 內 whitelist 不是 dict → 不 crash，維持現狀"""
    print("\n[shape 3] 非 dict whitelist 不 crash，維持現狀")
    with _Sandbox() as sb:
        import storage
        import app
        reset_sandbox(sb)

        # 先建立正常 whitelist
        app.save_settings({"whitelist": {"cors_origins": ["http://orig.example:5000"]}})

        # 故意送非 dict（模擬前端壞掉或攻擊）
        try:
            app.save_settings({"whitelist": "this is not a dict"})
            no_crash = True
        except Exception as e:
            no_crash = False
            print(f"  ✗ FAIL: save_settings 應容錯，卻丟出: {e}")

        require(no_crash, "save_settings 收到非 dict 應容錯不 crash")

        # 之前的值應該還在
        loaded = app.load_settings()
        wl = loaded.get("whitelist", {})
        require(wl.get("cors_origins") == ["http://orig.example:5000"],
                f"非 dict 寫入後，原 cors_origins 應保留，實際 {wl.get('cors_origins')}")
        return True


def test_shape_4_load_with_partial_db():
    """shape 4：SQLite 已有 whitelist 但缺子 key → load 時 DEFAULTS 兜底"""
    print("\n[shape 4] load 時缺子 key 用 DEFAULTS 兜底")
    with _Sandbox() as sb:
        import storage
        import app
        reset_sandbox(sb)

        # 模擬 SQLite 內存的是「只有 cors_origins」的舊資料
        partial = {"cors_origins": ["http://legacy.example:5000"]}
        storage.set_setting("whitelist", json.dumps(partial))

        loaded = app.load_settings()
        wl = loaded.get("whitelist", {})
        require(isinstance(wl, dict), "whitelist 應為 dict")
        require(wl.get("cors_origins") == ["http://legacy.example:5000"],
                f"cors_origins 應為 legacy 值，實際 {wl.get('cors_origins')}")
        # 其他 4 個子 key 應該用 DEFAULTS 兜底
        require("ota_admin_ips" in wl, "ota_admin_ips 應存在（DEFAULTS 兜底）")
        require("remote_write_ips" in wl, "remote_write_ips 應存在（DEFAULTS 兜底）")
        require("ota_allowed_targets" in wl, "ota_allowed_targets 應存在（DEFAULTS 兜底）")
        require("ota_blocked_exts" in wl, "ota_blocked_exts 應存在（DEFAULTS 兜底）")
        return True


def test_shape_5_dump_json_includes_whitelist():
    """shape 5：dump 出來的 settings.json 結構正確"""
    print("\n[shape 5] dump settings.json 含完整 whitelist 結構")
    with _Sandbox() as sb:
        import storage
        import app
        reset_sandbox(sb)

        wl_in = {
            "cors_origins": ["http://x.example:5000"],
            "ota_admin_ips": ["10.99.99.99"],
            "remote_write_ips": ["127.0.0.1"],
            "ota_allowed_targets": ["static/js/", "templates/"],
            "ota_blocked_exts": [".pyc"],
        }
        app.save_settings({"whitelist": wl_in})

        json_path = os.path.join(sb.config_dir, "settings.json")
        require(os.path.exists(json_path), "settings.json 應存在")
        with open(json_path, "r", encoding="utf-8") as f:
            dumped = json.load(f)

        require("whitelist" in dumped, "settings.json 含 whitelist 區塊")
        wl = dumped["whitelist"]
        for k, v in wl_in.items():
            require(wl.get(k) == v, f"settings.json whitelist.{k} 應為 {v}，實際 {wl.get(k)}")
        return True


# ============================================================
# 入口
# ============================================================

if __name__ == "__main__":
    print("=" * 60)
    print("v10.3.x whitelist save 鏈修補 — repro 驗證")
    print("=" * 60)
    results = []
    results.append(test_shape_1_basic_save_and_load())
    results.append(test_shape_2_per_key_merge())
    results.append(test_shape_3_non_dict_whitelist())
    results.append(test_shape_4_load_with_partial_db())
    results.append(test_shape_5_dump_json_includes_whitelist())
    print("\n" + "=" * 60)
    total = len(results)
    passed_count = sum(1 for r in results if r)
    print(f"✓ pass: {_passed}    ✗ fail: {_failed}")
    if _failed == 0:
        print(f"✓ 全部 {total} 個 shape 通過")
        sys.exit(0)
    else:
        print(f"✗ {passed_count}/{total} 個 shape 通過，{total - passed_count} 個失敗")
        sys.exit(1)

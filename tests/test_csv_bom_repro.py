"""
v12.x CSV BOM 漏失 regression test
===================================

Bug 背景：
  工位3_all_20261006_105917.csv 第一列出現 V\xe4\xb8?V\xe4\xb8?8，
  bytes 內 E4 B8 3F 表示原始 UTF-8 被破壞：
    E4 B8 8A（上）→ E4 B8 3F（8A 被換成 ? = 0x3F）
  0x3F 是 Excel 解碼失敗的標準替代 byte：CP950 / Big5 視角下，8A 不是
  合法 lead byte（合法範圍 A1–F9），所以整個 fallback 字串直接被置換成 ?。

Root cause（兩處）：
  1. static/js/main.js::exportCurrentStationAsCsv 的 File System Access 路徑
     直接把 raw JS string 傳給 writable.write(csvText)。W3C File System
     Access API 的 write() 規格只收 BufferSource | Blob | WriteParams，
     raw string 的字節編碼是實作依賴的，BOM (\ufeff) 可能漏失 → 113426.csv
     沒 BOM、clean bytes 就是這個路徑下來的。
     Fix：把 Blob 構造拉到兩條路徑共用，writable.write(blob)。

  2. app.py::api_export_csv 的 mimetype="text/csv" 沒 charset 宣告。
     部分 client 在 Content-Type 無 charset 時 fallback 到 latin-1 解碼
     BOM 開頭的 body，r.text() 拿到的字串可能就漏 BOM。
     Fix：mimetype="text/csv; charset=utf-8" 副防線。

本測試確認 /api/export_csv/<station> 的 response contract：
  1. body 開頭是 EF BB BF（3 byte UTF-8 BOM）
  2. Content-Type 含 charset=utf-8
  3. Content-Length header 與實際 body 長度一致
  4. 全文無 E4 B8 3F（疑似 UTF-8 byte 被 ? 取代）損壞 pattern
  5. 中文 alias 標頭（如設了 V上 / V下）的 bytes 是完整 3-byte UTF-8

執行：python tests/test_csv_bom_repro.py

預期：
  fix 前 → Contract 2 fail（Content-Type 無 charset）+ Contract 1 雖然 server
          side 已經寫 BOM 但無第二道防線
  fix 後 → 5 條 contract 全 pass
"""

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class _Sandbox:
    """重導 storage / settings 寫入到 temp dir，避免污染 repo data/ 與 config/。

    注意：whitelist.py 的 SETTINGS_PATH 是 module-level 常數（指向 repo 的
    config/settings.json），import app 時會讀該檔。讀不影響、寫不影響 ——
    本測試不走 save_settings()，所以不會污染。但若日後要加 save 的測試，
    要再把 whitelist.SETTINGS_PATH 也重導。
    """
    def __init__(self):
        self.tmpdir = tempfile.mkdtemp(prefix="csv_bom_repro_")
        self.data_dir = os.path.join(self.tmpdir, "data")
        self.config_dir = os.path.join(self.tmpdir, "config")
        os.makedirs(self.data_dir, exist_ok=True)
        os.makedirs(self.config_dir, exist_ok=True)

    def __enter__(self):
        import storage
        self._orig_storage_db_dir = storage.DB_DIR
        storage.DB_DIR = self.data_dir
        # config.py 預設沒有 DB_DIR 屬性（只在 storage.py 定義），
        # 但部分 test helper 會動態加上去，所以這裡只覆寫、不 save/restore。
        try:
            import config
            self._orig_config_db_dir = getattr(config, "DB_DIR", None)
            config.DB_DIR = self.data_dir
        except ImportError:
            pass
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        import storage
        storage.DB_DIR = self._orig_storage_db_dir
        try:
            import config
            if self._orig_config_db_dir is not None:
                config.DB_DIR = self._orig_config_db_dir
            elif hasattr(config, "DB_DIR"):
                delattr(config, "DB_DIR")
        except ImportError:
            pass
        shutil.rmtree(self.tmpdir, ignore_errors=True)


def _seed_settings_with_aliases(station: str, aliases: list):
    """把 ch_alias[station] 寫進 SQLite，模擬「使用者改過別名」的情境。

    aliases 長度必須 >= 20（POINTS_PER_STATION）。"""
    import storage
    if len(aliases) < 20:
        raise ValueError(f"aliases 長度必須 >= 20，收到 {len(aliases)}")
    raw = storage.get_setting("ch_alias")
    if raw:
        cur = json.loads(raw)
    else:
        cur = {}
    cur[station] = aliases[:20]
    storage.set_setting("ch_alias", json.dumps(cur, ensure_ascii=False))


def test_bom_in_export_csv_response():
    """驗證 /api/export_csv/<station> 的 BOM 與 Content-Type contract。"""
    print("\n=== test: /api/export_csv BOM contract ===")
    with _Sandbox():
        # 在 reload app 之前，先把 settings DB 灌好（避免 reload 後 init 預設 settings）
        # 注意：init_db(reset=False) 不收 stations 參數，_stations() 會惰性載入。
        # 之前寫成 init_db(STATIONS) 等同 reset=True（list truthy）→ DB 被清光。
        import storage
        storage.init_db()

        # 設 alias：index 5、6 對應到 user 實際的 V上 / V下
        # （user CSV 第一列中那兩個被破壞的中文欄位）
        aliases = [f"Ch{i+1:02d}" for i in range(20)]
        aliases[5] = "V上"
        aliases[6] = "V下"
        _seed_settings_with_aliases("工位3", aliases)

        # 灌一筆 sample（讓 CSV 有資料可輸出，避免 empty data error）
        storage.insert_sample(
            "2026-10-06T11:00:00", "工位3",
            [25.0 + i * 0.1 for i in range(20)],
            v=110.0, i=0.5, w=55.0,
        )

        # 載 app（reload 確保讀到 storage 的新設定）
        import importlib
        import app as app_mod
        importlib.reload(app_mod)

        # 透過 Flask test client 打 endpoint
        client = app_mod.app.test_client()
        resp = client.get("/api/export_csv/工位3")
        if resp.status_code != 200:
            raise AssertionError(f"expected 200, got {resp.status_code}: {resp.data[:200]}")

        body = resp.data
        ct = resp.headers.get("Content-Type", "")
        cl_header = resp.headers.get("Content-Length", "")

        print(f"  status              : {resp.status_code}")
        print(f"  Content-Type        : {ct!r}")
        print(f"  Content-Length hdr  : {cl_header!r}")
        print(f"  actual body length  : {len(body)}")
        print(f"  first 6 bytes (hex) : {body[:6].hex(' ')}")

        failures = []

        # --- Contract 1: BOM present ---
        if body[:3] != b"\xef\xbb\xbf":
            failures.append(
                f"Contract 1 FAIL：BOM 漏失！預期 EF BB BF，實際 {body[:6].hex(' ')}"
            )
        else:
            print("  ✓ Contract 1: BOM EF BB BF 存在")

        # --- Contract 2: charset declared ---
        if "charset=utf-8" not in ct.lower():
            failures.append(
                f"Contract 2 FAIL：Content-Type 缺 charset=utf-8: {ct!r}"
            )
        else:
            print("  ✓ Contract 2: Content-Type 含 charset=utf-8")

        # --- Contract 3: Content-Length 與實際一致 ---
        if cl_header:
            try:
                if int(cl_header) != len(body):
                    failures.append(
                        f"Contract 3 FAIL：Content-Length 不一致: "
                        f"header={cl_header}, actual={len(body)}"
                    )
                else:
                    print("  ✓ Contract 3: Content-Length 與 body 長度一致")
            except ValueError:
                failures.append(
                    f"Contract 3 FAIL：Content-Length 不是整數: {cl_header!r}"
                )

        # --- Contract 4: body 無 E4 B8 3F 損壞 pattern ---
        if b"\xe4\xb8\x3f" in body:
            # 找第一個出現的位置跟前後 context
            pos = body.index(b"\xe4\xb8\x3f")
            ctx = body[max(0, pos-20):pos+20]
            failures.append(
                f"Contract 4 FAIL：body 出現 E4 B8 3F pattern（疑似 UTF-8 byte 被 ? 取代）\n"
                f"  pos={pos}, context (hex): {ctx.hex(' ')}\n"
                f"  context (utf-8): {ctx.decode('utf-8', errors='replace')!r}"
            )
        else:
            print("  ✓ Contract 4: body 無 E4 B8 3F 損壞 pattern")

        # --- Contract 5: 中文 alias 標頭 bytes 完整 ---
        text = body.decode("utf-8")
        if text.startswith("\ufeff"):
            text = text[1:]
        first_line = text.split("\n")[0]
        print(f"  first line          : {first_line[:120]!r}")

        for ch_name, ch_glyph, expected_bytes in [
            ("上", "上", b"\xe4\xb8\x8a"),
            ("下", "下", b"\xe4\xb8\x8b"),
        ]:
            if ch_glyph not in first_line:
                # alias 沒用到這個 glyph 就跳過（不要 false fail）
                print(f"  · Contract 5.{ch_name}: alias 沒用到「{ch_glyph}」，跳過")
                continue
            idx = first_line.index(ch_glyph)
            actual = first_line[idx].encode("utf-8")
            if actual != expected_bytes:
                failures.append(
                    f"Contract 5.{ch_name} FAIL：「{ch_glyph}」bytes 損壞: "
                    f"{actual.hex(' ')} (預期 {expected_bytes.hex(' ')})"
                )
            else:
                print(f"  ✓ Contract 5.{ch_name}: 「{ch_glyph}」bytes 是 {actual.hex(' ')}")

        if failures:
            print("\n  FAIL:")
            for f in failures:
                print(f"    - {f}")
            raise AssertionError("\n".join(failures))

    print("  PASS")


def main():
    test_bom_in_export_csv_response()
    print("\nAll BOM tests passed.")


if __name__ == "__main__":
    main()
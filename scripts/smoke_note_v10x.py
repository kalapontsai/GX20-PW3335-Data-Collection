#!/usr/bin/env python3
"""
v10.x 備註欄驗收（4 條路徑）：
1. 載入頁面 → noteBox 出現，預設空
2. 打到工位 1 備註「ABC」 → 等待 400ms（debounce 300ms）+ 確認 server 端儲存
3. 切到工位 2 → noteBox 清空（工位 2 備註是空）
4. 切回工位 1 → noteBox 顯示「ABC」（保留切換前的值）
5. reload 頁面 → 工位 1 仍是「ABC」（從 server 端拉回來）
"""
import json
import time
import urllib.request
from playwright.sync_api import sync_playwright

BASE = "http://localhost:5000"


def get_settings():
    with urllib.request.urlopen(f"{BASE}/api/settings", timeout=5) as r:
        return json.loads(r.read())


def main():
    print("=== 0. 前置：清空所有工位備註 ===")
    s = get_settings()
    s["notes"] = {st: "" for st in s["notes"]}
    req = urllib.request.Request(
        f"{BASE}/api/settings",
        data=json.dumps(s).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as r:
        body = json.loads(r.read())
        assert body.get("ok"), f"reset failed: {body}"
    print("  ✅ reset OK")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1400, "height": 900})
        page = ctx.new_page()

        console_msgs = []
        page.on("console", lambda msg: console_msgs.append((msg.type, msg.text)))
        page.on("pageerror", lambda exc: console_msgs.append(("PAGE_ERROR", str(exc))))

        print("=== 1. 載入頁面，noteBox 預設空 ===")
        page.goto(f"{BASE}/")
        page.wait_for_selector("#noteBox", timeout=5000)
        page.wait_for_function("GX20State && GX20State.settings && GX20State.settings.notes", timeout=5000)
        note_val = page.evaluate("document.getElementById('noteBox').value")
        print(f"  noteBox.value = {note_val!r}")
        assert note_val == "", f"預期空，拿到 {note_val!r}"
        print("  ✅ 預設空 OK")

        print("=== 2. 打到工位 1 備註「ABC」+ 等 debounce 寫回 server ===")
        page.fill("#noteBox", "ABC")
        # 確認 GX20State.settings.notes["工位1"] === "ABC"
        v = page.evaluate('GX20State.settings.notes["工位1"]')
        print(f"  GX20State.settings.notes['工位1'] = {v!r}")
        assert v == "ABC", f"預期 ABC，拿到 {v!r}"

        # 等 debounce + POST 完成（300ms debounce + fetch）
        time.sleep(1.0)
        s = get_settings()
        print(f"  server notes['工位1'] = {s['notes'].get('工位1', 'MISSING')!r}")
        assert s["notes"].get("工位1") == "ABC", f"預期 ABC，server 拿到 {s['notes']!r}"
        print("  ✅ 寫入 server OK")

        print("=== 3. 切到工位 2，noteBox 清空 ===")
        page.select_option("#stationSelect", "工位2")
        time.sleep(0.5)
        note_val = page.evaluate("document.getElementById('noteBox').value")
        print(f"  切工位 2 後 noteBox.value = {note_val!r}")
        assert note_val == "", f"預期空（工位 2 沒備註），拿到 {note_val!r}"
        # 確認工位 1 還在 server
        s = get_settings()
        assert s["notes"].get("工位1") == "ABC", "切工位後工位 1 備註被清空了"
        print("  ✅ 工位 1 沒被清空 OK")

        print("=== 4. 切回工位 1，noteBox 顯示 ABC ===")
        page.select_option("#stationSelect", "工位1")
        time.sleep(0.5)
        note_val = page.evaluate("document.getElementById('noteBox').value")
        print(f"  切回工位 1 後 noteBox.value = {note_val!r}")
        assert note_val == "ABC", f"預期 ABC，拿到 {note_val!r}"
        print("  ✅ 切回工位 1 還原 OK")

        print("=== 5. 給工位 2 也寫「測試2」，再切換確認獨立 ===")
        page.select_option("#stationSelect", "工位2")
        time.sleep(0.5)
        page.fill("#noteBox", "測試2")
        time.sleep(1.0)
        page.select_option("#stationSelect", "工位1")
        time.sleep(0.5)
        v1 = page.evaluate("document.getElementById('noteBox').value")
        page.select_option("#stationSelect", "工位2")
        time.sleep(0.5)
        v2 = page.evaluate("document.getElementById('noteBox').value")
        print(f"  工位1: {v1!r} | 工位2: {v2!r}")
        assert v1 == "ABC", f"工位1 預期 ABC，拿到 {v1!r}"
        assert v2 == "測試2", f"工位2 預期 '測試2'，拿到 {v2!r}"
        print("  ✅ 兩工位獨立 OK")

        print("=== 6. reload 頁面，兩工位都保留 ===")
        page.reload()
        page.wait_for_selector("#noteBox", timeout=5000)
        page.wait_for_function("GX20State && GX20State.settings && GX20State.settings.notes", timeout=5000)
        # reload 後 currentStation 可能是 工位 2（最後一次切換的位置，從 sessionStorage 還原）
        # 切到工位 1 讀
        page.select_option("#stationSelect", "工位1")
        time.sleep(0.5)
        v1 = page.evaluate("document.getElementById('noteBox').value")
        print(f"  reload 後 工位1 noteBox.value = {v1!r}")
        assert v1 == "ABC", f"reload 後工位 1 預期 ABC，拿到 {v1!r}"
        # 切到工位 2
        page.select_option("#stationSelect", "工位2")
        time.sleep(0.5)
        v2 = page.evaluate("document.getElementById('noteBox').value")
        print(f"  reload 後 工位2 noteBox.value = {v2!r}")
        assert v2 == "測試2", f"reload 後工位 2 預期 '測試2'，拿到 {v2!r}"
        print("  ✅ reload 跨 session 還原 OK")

        print("=== 7. 截圖最終狀態 ===")
        page.screenshot(path="screenshots/note_v10x.png", full_page=False)

        print("=== 8. console 錯誤檢查 ===")
        errors = [m for m in console_msgs if m[0] in ("error", "PAGE_ERROR")]
        if errors:
            print("  ⚠️ console 有錯誤:")
            for t, txt in errors:
                print(f"    [{t}] {txt[:200]}")
        else:
            print("  ✅ 0 errors")

        print()
        print("=== 所有驗收通過 ===")
        browser.close()


if __name__ == "__main__":
    main()

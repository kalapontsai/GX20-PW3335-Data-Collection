#!/usr/bin/env python3
"""
v10.x archive meta 視覺驗收：開 snapshot 頁載入備份，截圖人工目視確認。
"""
import time
import urllib.parse
from playwright.sync_api import sync_playwright

BASE = "http://localhost:5000"


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1400, "height": 900})
        page = ctx.new_page()

        # 列出備份
        import urllib.request, json
        archives_body = json.loads(
            urllib.request.urlopen(f"{BASE}/api/snapshot/archives?station=%E5%B7%A5%E4%BD%8D4", timeout=5).read()
        )
        archives = archives_body["archives"]
        print(f"備份清單:")
        for a in archives:
            print(f"  - {a['filename']}  ({a.get('count')} 筆)")

        # 用 API 確認哪個有 meta
        with_meta = None
        without_meta = None
        for a in archives:
            fn = a["filename"]
            body = json.loads(
                urllib.request.urlopen(
                    f"{BASE}/api/snapshot/data?filename={urllib.parse.quote(fn)}&max_points=5",
                    timeout=5,
                ).read()
            )
            meta = body.get("meta")
            if meta and meta.get("alias"):
                with_meta = fn
            elif meta and meta["alias"] is None:
                if not without_meta:
                    without_meta = fn

        print(f"\n有 alias 的備份: {with_meta}")
        print(f"沒 alias 的備份: {without_meta}")

        # 截圖 1：載入帶 meta 的備份
        page.goto(f"{BASE}/snapshot")
        page.wait_for_selector("#stationSelect", timeout=5000)
        page.select_option("#stationSelect", "工位4")
        page.wait_for_function("document.querySelectorAll('#archiveSelect option').length > 1", timeout=5000)
        # 選 with_meta
        page.select_option("#archiveSelect", with_meta)
        page.click("#loadBtn")
        # 等 chart 建好（不再用 archiveNote hidden，純粹等 chart 存在）
        page.wait_for_function("Chart.getChart && Chart.getChart('chart')", timeout=8000)
        time.sleep(1)
        # 截圖
        page.screenshot(path="screenshots/snapshot_meta_with_alias.png", full_page=False)
        print(f"\n  ✅ 截圖: screenshots/snapshot_meta_with_alias.png")

        # 截圖 2：載入沒 meta 的備份
        page.select_option("#archiveSelect", without_meta)
        page.click("#loadBtn")
        page.wait_for_function("Chart.getChart && Chart.getChart('chart')", timeout=8000)
        time.sleep(1)
        page.screenshot(path="screenshots/snapshot_meta_without_alias.png", full_page=False)
        print(f"  ✅ 截圖: screenshots/snapshot_meta_without_alias.png")

        # 看 DOM 狀態
        for fn, label in [(with_meta, "帶 meta"), (without_meta, "沒 meta")]:
            page.select_option("#archiveSelect", fn)
            page.click("#loadBtn")
            page.wait_for_function("Chart.getChart && Chart.getChart('chart')", timeout=8000)
            time.sleep(0.5)
            state = page.evaluate("""() => {
                const el = document.getElementById('archiveNote');
                const ds = Chart.getChart('chart')?.data?.datasets?.[0]?.label || '(no chart)';
                return {
                    noteHidden: el.hidden,
                    noteText: el.textContent,
                    firstDatasetLabel: ds,
                };
            }""")
            print(f"\n[{label}] 備份 {fn}:")
            print(f"  archiveNote: hidden={state['noteHidden']}, text={state['noteText']!r}")
            print(f"  第一條 dataset.label: {state['firstDatasetLabel']!r}")

        browser.close()
        print("\n=== 視覺驗收完成 ===")


if __name__ == "__main__":
    main()

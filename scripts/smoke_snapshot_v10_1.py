#!/usr/bin/env python3
"""
切片 5 驗收 — 跑 http://localhost:5000/snapshot
檢查:
  1. 載入備份檔後圖表分上下子圖（溫度 + 電力）
  2. 滾輪縮放有效
  3. 拖曳 pan 有效
  4. checkbox 切換曲線有效
  5. console 沒有 error
"""
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE = "http://localhost:5000"
SHOT = Path(__file__).parent.parent / "screenshots"
SHOT.mkdir(exist_ok=True)


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1400, "height": 900})
        page = ctx.new_page()

        errs = []
        page.on("pageerror", lambda exc: errs.append(f"PAGE_ERROR: {exc}"))
        def on_console(msg):
            if msg.type == "error":
                errs.append(f"CONSOLE_error: {msg.text}")
        page.on("console", on_console)

        # 1. 載入 /snapshot
        page.goto(f"{BASE}/snapshot")
        page.wait_for_load_state("networkidle")
        page.wait_for_function("window.Chart !== undefined", timeout=10000)
        page.wait_for_selector("#chart", state="attached")
        page.wait_for_selector("#pwChart", state="attached")
        page.screenshot(path=str(SHOT / "v10_1_01_initial.png"))

        # 2. 選站點 + 載入備份
        page.select_option("#stationSelect", value="工位4")
        # 等 fetch /api/snapshot/archives 回來；至少要有 1 個 value 非空的 option
        page.wait_for_function(
            "() => Array.from(document.querySelector('#archiveSelect').options).some(o => o.value && o.value.length > 0)",
            timeout=15000,
        )
        page.wait_for_timeout(300)  # 等 DOM update
        first_real = page.evaluate("""
            () => Array.from(document.querySelector('#archiveSelect').options).find(o => o.value && o.value.length > 0)?.value
        """)
        page.select_option("#archiveSelect", value=first_real)
        page.click("#loadBtn")
        page.wait_for_function("() => Chart.getChart('chart') !== undefined", timeout=10000)
        page.wait_for_timeout(500)
        page.screenshot(path=str(SHOT / "v10_1_02_loaded.png"))

        # 3. 一次拿所有初始資訊（避免 Playwright 序列化 instance getter 失敗）
        init = page.evaluate("""
            () => {
                const t = Chart.getChart('chart');
                const p = Chart.getChart('pwChart');
                return {
                    temp_ds_count: t.data.datasets.length,
                    pw_ds_count: p ? p.data.datasets.length : 0,
                    temp_x_min: t.scales.x.min,
                    temp_x_max: t.scales.x.max,
                    pw_x_min: p ? p.scales.x.min : null,
                    pw_x_max: p ? p.scales.x.max : null,
                };
            }
        """)
        print(f"溫度 chart 內 datasets: {init['temp_ds_count']}")
        print(f"電力 chart 內 datasets: {init['pw_ds_count']}")
        assert init['temp_ds_count'] == 20, f"溫度圖應有 20 條線，實際 {init['temp_ds_count']}"
        assert init['pw_ds_count'] == 3, f"電力圖應有 3 條線，實際 {init['pw_ds_count']}"
        print(f"溫度 X 軸: {init['temp_x_min']} ~ {init['temp_x_max']}")
        print(f"電力 X 軸: {init['pw_x_min']} ~ {init['pw_x_max']}")
        assert init['temp_x_min'] == init['pw_x_min'], "X 軸 min 不一致"
        assert init['temp_x_max'] == init['pw_x_max'], "X 軸 max 不一致"

        # 4. 滾輪縮放（向 chart canvas 中心 wheel 一次）
        chart_box = page.locator("#chart").bounding_box()
        cx = chart_box["x"] + chart_box["width"] / 2
        cy = chart_box["y"] + chart_box["height"] / 2
        page.mouse.move(cx, cy)
        page.mouse.wheel(0, 500)  # 向下滾 = 放大（var factor = e.deltaY > 0 ? 0.8 : 1.25）
        page.wait_for_timeout(300)
        zoomed = page.evaluate("""
            () => {
                const t = Chart.getChart('chart');
                const p = Chart.getChart('pwChart');
                return {
                    temp_x_min: t.scales.x.min,
                    temp_x_max: t.scales.x.max,
                    pw_x_min: p.scales.x.min,
                    pw_x_max: p.scales.x.max,
                };
            }
        """)
        range_before = init['temp_x_max'] - init['temp_x_min']
        range_after = zoomed['temp_x_max'] - zoomed['temp_x_min']
        print(f"zoom 前範圍: {range_before}")
        print(f"zoom 後範圍: {range_after}")
        assert range_after < range_before, f"滾輪縮放失敗（範圍沒變小）：{range_before} → {range_after}"
        assert abs(zoomed['temp_x_min'] - zoomed['pw_x_min']) < 1, "zoom 後兩圖 X 軸 min 不同步"
        assert abs(zoomed['temp_x_max'] - zoomed['pw_x_max']) < 1, "zoom 後兩圖 X 軸 max 不同步"
        page.screenshot(path=str(SHOT / "v10_1_03_zoomed.png"))

        # 5. drag pan（在 chart-temp pane 中間 → 往左拖 50px，避免燑到左邊界）
        pane_box = page.locator(".chart-temp").bounding_box()
        px = pane_box["x"] + pane_box["width"] / 2  # pane 中間
        py = pane_box["y"] + 10  # pane 上邊 padding
        page.mouse.move(px, py)
        page.mouse.down()
        page.mouse.move(px - 50, py, steps=10)
        page.mouse.up()
        page.wait_for_timeout(300)
        panned = page.evaluate("""
            () => {
                const t = Chart.getChart('chart');
                const p = Chart.getChart('pwChart');
                return {
                    temp_x_min: t.scales.x.min,
                    pw_x_min: p.scales.x.min,
                };
            }
        """)
        print(f"pan 後 temp X.min: {panned['temp_x_min']}, pw X.min: {panned['pw_x_min']}")
        assert abs(panned['temp_x_min'] - zoomed['temp_x_min']) > 1, "拖曳 pan 後 X 軸沒移動"
        assert abs(panned['temp_x_min'] - panned['pw_x_min']) < 1, "pan 後兩圖不同步"
        page.screenshot(path=str(SHOT / "v10_1_04_panned.png"))

        # 6. checkbox 切換（溫度 T01）
        page.locator(".snapshot-cb input[data-field=t01]").uncheck()
        page.wait_for_timeout(200)
        cb_t = page.evaluate("() => Chart.getChart('chart').data.datasets[0].hidden")
        print(f"取消 T01 checkbox 後 dataset[0].hidden = {cb_t}")
        assert cb_t is True, "checkbox 取消後 dataset 沒隱藏"
        page.locator(".snapshot-cb input[data-field=t01]").check()
        page.wait_for_timeout(200)
        cb_t2 = page.evaluate("() => Chart.getChart('chart').data.datasets[0].hidden")
        assert cb_t2 is False, "checkbox 勾選後 dataset 沒顯示"
        page.screenshot(path=str(SHOT / "v10_1_05_checkbox.png"))
        print("checkbox 切換 OK")

        # 7. 電力 checkbox (v)
        page.locator(".snapshot-cb input[data-field=v]").uncheck()
        page.wait_for_timeout(200)
        cb_v = page.evaluate("() => Chart.getChart('pwChart').data.datasets[0].hidden")
        print(f"取消 V checkbox 後 pwChart.datasets[0].hidden = {cb_v}")
        assert cb_v is True, "電力 checkbox 取消後 dataset 沒隱藏"

        page.screenshot(path=str(SHOT / "v10_1_06_final.png"))

        # 8. console / page error check
        critical = [e for e in errs if "PAGE_ERROR" in e or "CONSOLE_error" in e]
        if critical:
            print("--- Console / Page errors ---")
            for e in critical:
                print(e)
            browser.close()
            sys.exit(1)

        print("=== 所有驗收通過 ===")
        browser.close()


if __name__ == "__main__":
    main()
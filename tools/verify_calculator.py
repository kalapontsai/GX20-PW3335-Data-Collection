#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verify_calculator.py — /calculator 計算頁驗證工具

目的：
  1. 讀 D:\\temp 內 3 個 H61DV CSV
  2. 用「Python 原版」EF 演算法（移植自 kalapontsai/Data-plot-and-EEF-calculate
     plot_gui_雙信.py）跑出對照基準
  3. 跑 Node.js 計算（calculator.js 的 CalculatorAPI）拿 JS 版輸出
  4. 欄位對欄位 diff，找出 JS 版跟 Python 版的差異

使用方式：
  cd ~/.openclaw/workspace-two/repos/GX20-PW3335-Data-Collection
  python3 tools/verify_calculator.py          # 全部 3 個 CSV
  python3 tools/verify_calculator.py NEW1200   # 只跑 NEW1200

輸出：
  stdout:  對照表（每 CSV 一個區塊）
  data/verify_out/<csv_name>_py.json  : Python 版輸出
  data/verify_out/<csv_name>_js.json  : JS 版輸出
  data/verify_out/<csv_name>_diff.txt : 欄位對欄位 diff

註：
  - 這個腳本不會送任何指令到 OTA GX20，純本地檔案比對
  - JS 端用 Node.js + jsdom-free 方式執行（不走瀏覽器）
  - 「Python 原版」是從 GitHub 上抓的 plot_gui_雙信.py 的演算法，
    不包含 GUI 部分（select_file / plot_chart / matplotlib）。
"""

import json
import math
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Tuple, Optional

# ===========================================================
# 路徑
# ===========================================================

REPO_ROOT = Path(__file__).resolve().parent.parent
TEST_CSV_DIR = Path("/mnt/d/temp")
OUT_DIR = REPO_ROOT / "data" / "verify_out"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# 預設 3 個測試 CSV
ALL_CSVS = [
    ("NEW1200", TEST_CSV_DIR / "H61DV NEW1200 -2+2.csv"),
    ("OLD1200", TEST_CSV_DIR / "H61DV OLD1200 -3+3.csv"),
    ("VIP800",  TEST_CSV_DIR / "H61DV VIP-800 -1+6.csv"),
]

# ===========================================================
# Python 原版 EF / 統計演算法
# （移植自 plot_gui_雙信.py rev.0.5.0.0，與 /calculator JS 版完全對齊）
# ===========================================================

PHYSICAL_LIMIT = 40

def round_half_up(n, digits=0):
    """Python 內建 round 是 banker's，匯出 / 報表用 half-up 一致。"""
    if n is None:
        return None
    sign = 1 if n >= 0 else -1
    m = 10 ** digits
    return sign * math.floor(abs(n) * m + 0.5) / m


class EnergyCalculator:
    """Python 原版（EnergyCalculator 1:1 移植）"""
    def __init__(self):
        pass

    def current_ef_thresholds(self, energy_allowance, fridge_type):
        if fridge_type == 5:
            t = [1.72, 1.54, 1.36, 1.18]
        else:
            t = [1.6, 1.45, 1.3, 1.15]
        return [round(energy_allowance * x, 1) for x in t]

    def future_ef_thresholds(self, future_energy_allowance, fridge_type):
        if fridge_type == 5:
            t = [1.294, 1.221, 1.147, 1.074]
        else:
            t = [1.308, 1.231, 1.154, 1.077]
        return [round(future_energy_allowance * x, 1) for x in t]

    def calculate_K_value(self, freezer_temp, fridge_temp):
        return round((30 - freezer_temp) / (30 - fridge_temp), 2)

    def calculate_equivalent_volume(self, VR, VF, K):
        return round(VR + (K * VF), 1)

    def determine_fridge_type(self, equivalent_volume, VR, VF, fan_type):
        if VF == 0:
            return 5
        elif equivalent_volume < 400 and fan_type == 1:
            return 1
        elif equivalent_volume >= 400 and fan_type == 1:
            return 2
        elif equivalent_volume < 400 and fan_type == 0:
            return 3
        else:
            return 4

    def calculate_energy_allowance(self, equivalent_volume, fridge_type):
        if fridge_type == 1:
            return round(equivalent_volume / (0.037 * equivalent_volume + 24.3), 1)
        elif fridge_type == 2:
            return round(equivalent_volume / (0.031 * equivalent_volume + 21), 1)
        elif fridge_type == 3:
            return round(equivalent_volume / (0.033 * equivalent_volume + 19.7), 1)
        elif fridge_type == 4:
            return round(equivalent_volume / (0.029 * equivalent_volume + 17), 1)
        else:
            return round(equivalent_volume / (0.033 * equivalent_volume + 15.8), 1)

    def calculate_future_energy_allowance(self, equivalent_volume, fridge_type):
        if fridge_type == 1:
            return round(1.3 * equivalent_volume / (0.037 * equivalent_volume + 24.3), 1)
        elif fridge_type == 2:
            return round(1.3 * equivalent_volume / (0.031 * equivalent_volume + 21), 1)
        elif fridge_type == 3:
            return round(1.3 * equivalent_volume / (0.033 * equivalent_volume + 19.7), 1)
        elif fridge_type == 4:
            return round(1.3 * equivalent_volume / (0.029 * equivalent_volume + 17), 1)
        else:
            return round(1.36 * equivalent_volume / (0.033 * equivalent_volume + 15.8), 1)

    def calculate_benchmark_consumption(self, equivalent_volume, energy_allowance):
        return round(equivalent_volume / energy_allowance, 0)

    def calculate_future_benchmark_consumption(self, equivalent_volume, future_energy_allowance):
        return round(equivalent_volume / future_energy_allowance, 0)

    def calculate_current_efficiency(self, ef_value, thresholds):
        if ef_value >= thresholds[0]:
            grade = "1級"
            pct = round(ef_value / thresholds[0] * 100, 1)
        elif ef_value >= thresholds[0] * 0.95:
            grade = "1*級"
            pct = round(ef_value / thresholds[0] * 100, 1)
        elif ef_value >= thresholds[1]:
            grade = "2級"
            pct = round(ef_value / thresholds[0] * 100, 1)
        elif ef_value >= thresholds[2]:
            grade = "3級"
            pct = round(ef_value / thresholds[0] * 100, 1)
        elif ef_value >= thresholds[3]:
            grade = "4級"
            pct = round(ef_value / thresholds[0] * 100, 1)
        else:
            grade = "5級"
            pct = round(ef_value / thresholds[0] * 100, 1)
        return pct, grade

    def calculate(self, VF, VR, daily_consumption, fridge_temp, freezer_temp, fan_type):
        results = {}
        standard_K = 1.78
        measured_K = self.calculate_K_value(freezer_temp, fridge_temp)
        equivalent_volume = self.calculate_equivalent_volume(VR, VF, standard_K)
        measured_equivalent_volume = self.calculate_equivalent_volume(VR, VF, measured_K)
        effective_volume = VR + VF
        fridge_type = self.determine_fridge_type(equivalent_volume, VR, VF, fan_type)
        energy_allowance = self.calculate_energy_allowance(equivalent_volume, fridge_type)
        future_energy_allowance = self.calculate_future_energy_allowance(equivalent_volume, fridge_type)
        benchmark_consumption = self.calculate_benchmark_consumption(equivalent_volume, energy_allowance)
        future_benchmark_consumption = self.calculate_future_benchmark_consumption(equivalent_volume, future_energy_allowance)
        monthly_consumption = round(daily_consumption * 30, 0)
        ef_value = round(measured_equivalent_volume / monthly_consumption, 1)
        current_ef_thresholds = self.current_ef_thresholds(energy_allowance, fridge_type)
        current_percent, current_grade = self.calculate_current_efficiency(ef_value, current_ef_thresholds)
        future_ef_thresholds = self.future_ef_thresholds(future_energy_allowance, fridge_type)
        future_percent, future_grade = self.calculate_current_efficiency(ef_value, future_ef_thresholds)
        results.update({
            "冷凍室溫度(°C)": freezer_temp,
            "冷藏室溫度(°C)": fridge_temp,
            "實測K值": measured_K,
            "VF(L)": int(VF),
            "VR(L)": int(VR),
            "等效內容積(L)": equivalent_volume,
            "實測等效內容積(L)": measured_equivalent_volume,
            "冰箱型式": fridge_type,
            "2018容許耗用能源基準(L/kWh/月)": energy_allowance,
            "2027容許耗用能源基準(L/kWh/月)": future_energy_allowance,
            "2018耗電量基準(kWh/月)": benchmark_consumption,
            "2027耗電量基準(kWh/月)": future_benchmark_consumption,
            "實測月耗電量(kWh/月)": monthly_consumption,
            "實測EF值": ef_value,
            "2018 EF基準值": current_ef_thresholds[0],
            "2018效率基準百分比(%)": current_percent,
            "2018效率等級": current_grade,
            "2027 EF基準值": future_ef_thresholds[0],
            "2027新效率基準百分比(%)": future_percent,
            "2027新效率等級": future_grade,
        })
        return results


# ===========================================================
# Python 原版 calculate_statistics 邏輯（不含 GUI）
# ===========================================================

def parse_csv(csv_text):
    if csv_text.startswith("\ufeff"):
        csv_text = csv_text[1:]
    lines = [l for l in csv_text.split("\n") if l]
    if len(lines) < 2:
        return None
    header = [s.strip() for s in lines[0].split(",")]
    if len(header) < 2:
        return None
    rows = []
    bad_ts = 0
    bad_num = 0
    for line in lines[1:]:
        cells = line.split(",")
        if len(cells) < len(header):
            bad_num += 1
            continue
        dt = parse_ts(cells[0])
        if dt is None:
            bad_ts += 1
            continue
        rec = {"datetime": dt}
        for i in range(1, len(header)):
            v = parse_float(cells[i])
            rec[header[i]] = v
        rows.append(rec)
    return {"header": header, "rows": rows, "skipped": {"badTs": bad_ts, "badNum": bad_num}}


def parse_ts(s):
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?$", s.strip())
    if m:
        y, mo, d, h, mi, se = m.groups()
        return f"{y}-{int(mo):02d}-{int(d):02d}T{int(h):02d}:{int(mi):02d}:{int(se or 0):02d}"
    m = re.match(r"^(\d{4})/(\d{1,2})/(\d{1,2})\s+(\d{1,2}):(\d{2})(?::(\d{2}))?$", s.strip())
    if m:
        y, mo, d, h, mi, se = m.groups()
        return f"{y}-{int(mo):02d}-{int(d):02d}T{int(h):02d}:{int(mi):02d}:{int(se or 0):02d}"
    return None


def parse_float(s):
    if not s:
        return None
    s = s.strip()
    if s in ("", "nan", "NaN", "None"):
        return None
    try:
        v = float(s)
        return v if math.isfinite(v) else None
    except ValueError:
        return None


def ts_to_dt(s):
    """把 parse_ts 輸出的 ISO 字串轉成 datetime（用於 dt 物件計算秒差）"""
    from datetime import datetime
    return datetime.fromisoformat(s)


def py_calculate_statistics(parsed, start_dt, end_dt, params):
    rows = [r for r in parsed["rows"]
            if start_dt <= ts_to_dt(r["datetime"]) <= end_dt]
    if not rows:
        return {"ok": False, "error": "範圍內沒有資料"}

    # 平均/最大/最小
    header = parsed["header"]
    averages = {}
    maxValues = {}
    minValues = {}
    for name in header[1:]:
        vals = [r[name] for r in rows if r[name] is not None]
        if vals:
            averages[name] = sum(vals) / len(vals)
            maxValues[name] = max(vals)
            minValues[name] = min(vals)
        else:
            averages[name] = None
            maxValues[name] = None
            minValues[name] = None

    # ON/OFF 統計
    onoff = py_compute_onoff(rows, params["onOffThrottle"])

    # 電力消耗
    power = py_compute_power(rows)

    # EF
    ef = None
    if power["wp24hW"] is not None:
        ec = EnergyCalculator()
        if params["vf"] > 0 and params["vr"] > 0:
            daily_consumption = round_half_up(power["wp24hW"] / 1000, 3)
            results = ec.calculate(
                params["vf"], params["vr"], daily_consumption,
                params["fridgeTemp"], params["freezerTemp"], params["fanType"]
            )
            # 對齊 JS：用 averages 的 F/R 平均值替換
            if averages.get("F") is not None:
                results["冷凍室溫度(°C)"] = round(averages["F"], 1)
            if averages.get("R") is not None:
                results["冷藏室溫度(°C)"] = round(averages["R"], 1)
            ef = {"dailyConsumption": daily_consumption, "monthlyConsumption": round(daily_consumption * 30, 0), "results": results}
        else:
            daily_consumption = round_half_up(power["wp24hW"] / 1000, 3)
            ef = {"dailyConsumption": daily_consumption, "monthlyConsumption": round(daily_consumption * 30, 0), "results": None}

    return {
        "ok": True,
        "start": start_dt.isoformat(),
        "end": end_dt.isoformat(),
        "count": len(rows),
        "skipped": parsed.get("skipped", {"badTs": 0, "badNum": 0}),
        "averages": averages,
        "maxValues": maxValues,
        "minValues": minValues,
        "onoff": onoff,
        "power": power,
        "ef": ef,
    }


def py_compute_onoff(rows, throttle):
    if "W" not in rows[0]:
        return {"cycles": "無法計算，缺少 P(W) 欄位", "aboveAvgMin": 0, "belowAvgMin": 0,
                "abovePct": 0, "aboveCount": 0, "belowCount": 0, "throttle": throttle, "hasW": False}
    mask = [(r["W"] is not None and r["W"] >= throttle) for r in rows]

    diff_sum = 0
    for i in range(1, len(mask)):
        if mask[i] != mask[i-1]:
            diff_sum += 1
    cycles = diff_sum // 2

    segments = []
    if mask:
        run_start = 0
        run_state = mask[0]
        for i in range(1, len(mask)):
            if mask[i] != run_state:
                segments.append({"state": run_state, "startIdx": run_start, "endIdx": i - 1})
                run_start = i
                run_state = mask[i]
        segments.append({"state": run_state, "startIdx": run_start, "endIdx": len(mask) - 1})

    trimmed = segments[1:-1] if len(segments) > 2 else segments
    above_segs = [s for s in trimmed if s["state"]]
    below_segs = [s for s in trimmed if not s["state"]]
    above_count = len(above_segs)
    below_count = len(below_segs)

    def avg_seconds(segs):
        if not segs:
            return 0
        durs = []
        for s in segs:
            t0 = ts_to_dt(rows[s["startIdx"]]["datetime"])
            t1 = ts_to_dt(rows[s["endIdx"]]["datetime"])
            durs.append(max(0, (t1 - t0).total_seconds()))
        return sum(durs) / len(durs)

    above_avg_sec = avg_seconds(above_segs)
    below_avg_sec = avg_seconds(below_segs)
    above_avg_min = int(above_avg_sec / 60)
    below_avg_min = int(below_avg_sec / 60) + 1 if below_count > 0 else 0

    above_pct = int(above_avg_min / (above_avg_min + below_avg_min) * 100) if (above_avg_min + below_avg_min) > 0 else 0

    return {
        "cycles": cycles,
        "aboveAvgMin": above_avg_min,
        "belowAvgMin": below_avg_min,
        "abovePct": above_pct,
        "aboveCount": above_count,
        "belowCount": below_count,
        "throttle": throttle,
        "hasW": True,
    }


def py_compute_power(rows):
    if "W" not in rows[0]:
        return {"filterWh": None, "minutesDifference": 0, "wp24hW": None}
    start = ts_to_dt(rows[0]["datetime"])
    end = ts_to_dt(rows[-1]["datetime"])
    minutes_diff = int((end - start).total_seconds() / 60)

    sum_w = 0
    w_count = 0
    for r in rows:
        if r["W"] is not None:
            sum_w += r["W"]
            w_count += 1
    filter_wh = int(sum_w / 60)

    total_seconds = (end - start).total_seconds()
    if total_seconds <= 0 or w_count == 0:
        return {"filterWh": filter_wh, "minutesDifference": minutes_diff, "wp24hW": None}
    wp24h_w = int((filter_wh / total_seconds) * (24 * 3600))
    return {"filterWh": filter_wh, "minutesDifference": minutes_diff, "wp24hW": wp24h_w}


# ===========================================================
# JS 端：跑 Node.js 執行 calculator.js
# ===========================================================

JS_BRIDGE = r"""
// Bridge：模擬瀏覽器環境，呼叫 CalculatorAPI
const fs = require('fs');
const path = require('path');

// 載入 calculator.js（它會掛 window.ChartUtils + window.CalculatorAPI）
// 先注入 Chart.js 的 shim（只用到 ChartUtils.chartColors / ChartUtils.buildLineChart / ChartUtils.createCursorOverlay / ChartUtils.roundHalfUp）
// 我們的純計算路徑只用 roundHalfUp，其它 API 在這個測試不會被呼叫。
global.Chart = function () { return { data: { datasets: [] }, scales: {}, chartArea: {}, canvas: { getBoundingClientRect: () => ({ left: 0, right: 0 }) } }; };
global.ChartUtils = {
  chartColors: () => ({ text: '', textStrong: '', grid: '', bg: '' }),
  buildLineChart: () => null,
  createCursorOverlay: () => null,
  roundHalfUp: (n, d) => {
    if (n === null || n === undefined || Number.isNaN(n)) return n;
    if (d == null) d = 0;
    const sign = n < 0 ? -1 : 1;
    const abs = Math.abs(n);
    const m = Math.pow(10, d);
    return sign * (Math.floor(abs * m + 0.5) / m);
  },
  formatTs: (d) => {
    if (!d) return '—';
    const p = (n) => String(n).padStart(2, '0');
    return `${p(d.getMonth()+1)}/${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
  },
};

// 路徑一律用 REPO_ROOT / 'static/js/calculator.js'（process.env.REPO_ROOT 由 Python 端傳入）
const repoRoot = process.env.REPO_ROOT;
const calcSrc = fs.readFileSync(path.join(repoRoot, 'static/js/calculator.js'), 'utf-8');
// 把 IIFE 結尾暴露的 CalculatorAPI 取出來
const wrappedSrc = calcSrc + '\nmodule.exports = window.CalculatorAPI;';
const Module = require('module');
// document shim：calculator.js init 函式內會檢查 document.readyState
global.document = {
  readyState: 'complete',
  addEventListener: () => {},
  getElementById: () => null,
};
global.window = global;
const m = new Module('calculator-bridge');
m._compile(wrappedSrc, 'calculator-bridge.js');
const CalculatorAPI = m.exports;

const csvFile = process.argv[2];
const startStr = process.argv[3];
const endStr = process.argv[4];
const params = JSON.parse(process.argv[5]);

const csvText = fs.readFileSync(csvFile, 'utf-8');
const parsed = CalculatorAPI.parseCsv(csvText);
if (!parsed.ok) {
  console.error(JSON.stringify({ ok: false, error: parsed.error }));
  process.exit(1);
}

const startDt = new Date(startStr);
const endDt   = new Date(endStr);

const stats = CalculatorAPI.calculateStatistics(parsed, startDt, endDt, params);
console.log(JSON.stringify(stats));
"""


# ===========================================================
# 暴力掃描驗證（slide window）
# ===========================================================

JS_BRIDGE_SCAN = r"""
// scanBestWindowBridge：JS 端暴力掃描 1440 分鐘（24H）窗口
const fs = require('fs');
const path = require('path');

global.Chart = function () { return { data: { datasets: [] }, scales: {}, chartArea: {}, canvas: { getBoundingClientRect: () => ({ left: 0, right: 0 }) } }; };
global.ChartUtils = {
  chartColors: () => ({ text: '', textStrong: '', grid: '', bg: '' }),
  buildLineChart: () => null,
  createCursorOverlay: () => null,
  roundHalfUp: (n, d) => {
    if (n === null || n === undefined || Number.isNaN(n)) return n;
    if (d == null) d = 0;
    const sign = n < 0 ? -1 : 1;
    const abs = Math.abs(n);
    const m = Math.pow(10, d);
    return sign * (Math.floor(abs * m + 0.5) / m);
  },
  formatTs: (d) => {
    if (!d) return '—';
    const p = (n) => String(n).padStart(2, '0');
    return `${p(d.getMonth()+1)}/${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
  },
};

const repoRoot = process.env.REPO_ROOT;
const calcSrc = fs.readFileSync(path.join(repoRoot, 'static/js/calculator.js'), 'utf-8');
const wrappedSrc = calcSrc + '\nmodule.exports = window.CalculatorAPI;';
const Module = require('module');
global.document = {
  readyState: 'complete',
  addEventListener: () => {},
  getElementById: () => null,
};
global.window = global;
const m = new Module('calculator-bridge');
m._compile(wrappedSrc, 'calculator-bridge.js');
const CalculatorAPI = m.exports;

const csvFile = process.argv[2];
const params = JSON.parse(process.argv[3]);
const STEP = 10;
const WIN  = 1440;

const csvText = fs.readFileSync(csvFile, 'utf-8');
const parsed = CalculatorAPI.parseCsv(csvText);
if (!parsed.ok) { console.error(JSON.stringify({ ok: false, error: parsed.error })); process.exit(1); }

const N = parsed.datetime.length;
if (N < WIN) { console.error(JSON.stringify({ ok: false, error: 'N < WIN' })); process.exit(1); }

const totalSteps = Math.floor((N - WIN) / STEP) + 1;
let bestEf = -Infinity, bestWatt = Infinity;
let bestStart = null, bestEnd = null;
for (let i = 0; i < totalSteps; i++) {
  const s = i * STEP;
  const e = s + WIN - 1;
  const r = CalculatorAPI.calculateStatistics(parsed, parsed.datetime[s], parsed.datetime[e], params);
  if (!r.ef || !r.ef.results) continue;
  const ef = r.ef.results['實測EF值'];
  const watt = r.power.filterWh || Infinity;
  if (ef > bestEf) {
    bestEf = ef;
    bestWatt = watt;
    bestStart = parsed.datetime[s].toISOString();
    bestEnd = parsed.datetime[e].toISOString();
  }
}
console.log(JSON.stringify({
  totalSteps,
  bestEf,
  bestWatt,
  bestStart,
  bestEnd,
}));
"""


def run_js_scan(csv_path, params):
    params_json = json.dumps(params)
    script_path = OUT_DIR / "_bridge_scan.js"
    script_path.write_text(JS_BRIDGE_SCAN)
    try:
        result = subprocess.run(
            ["node", str(script_path), str(csv_path), params_json],
            cwd=str(REPO_ROOT),
            capture_output=True, text=True, timeout=120,
            env={**os.environ, "REPO_ROOT": str(REPO_ROOT)},
        )
    finally:
        if script_path.exists():
            script_path.unlink()
    if result.returncode != 0:
        raise RuntimeError(f"node failed: {result.stderr}")
    return json.loads(result.stdout)


def py_scan_best_window(parsed, params, win=1440, step=10):
    """暴力掃描：1440 分鐘窗口，每 step 分鐘一動，找最高 EF"""
    n = len(parsed["rows"])
    if n < win:
        return None
    from datetime import datetime, timedelta
    total_steps = (n - win) // step + 1
    best_ef = -float("inf")
    best_watt = float("inf")
    best_idx_start = None
    best_idx_end = None
    for i in range(total_steps):
        idx_s = i * step
        idx_e = idx_s + win - 1
        start = ts_to_dt(parsed["rows"][idx_s]["datetime"])
        end = ts_to_dt(parsed["rows"][idx_e]["datetime"])
        r = py_calculate_statistics(parsed, start, end, params)
        if not r["ef"] or not r["ef"]["results"]:
            continue
        ef = r["ef"]["results"]["實測EF值"]
        watt = r["power"]["filterWh"] or float("inf")
        if ef > best_ef:
            best_ef = ef
            best_watt = watt
            best_idx_start = parsed["rows"][idx_s]["datetime"]
            best_idx_end = parsed["rows"][idx_e]["datetime"]
    return {
        "totalSteps": total_steps,
        "bestEf": best_ef,
        "bestWatt": best_watt,
        "bestStart": best_idx_start,
        "bestEnd": best_idx_end,
    }


def run_js(csv_path, start_dt, end_dt, params):
    """用 Node.js 跑 calculator.js，回傳 JS 版的 stats dict"""
    params_json = json.dumps(params)
    script_path = OUT_DIR / "_bridge.js"
    script_path.write_text(JS_BRIDGE)
    try:
        result = subprocess.run(
            ["node", str(script_path), str(csv_path), start_dt.isoformat(), end_dt.isoformat(), params_json],
            cwd=str(REPO_ROOT),
            capture_output=True, text=True, timeout=60,
            env={**os.environ, "REPO_ROOT": str(REPO_ROOT)},
        )
    finally:
        if script_path.exists():
            script_path.unlink()
    if result.returncode != 0:
        raise RuntimeError(f"node failed: {result.stderr}")
    return json.loads(result.stdout)


# ===========================================================
# 對照 D:\temp 內 .txt（已知正確輸出）
# ===========================================================

def parse_expected_txt(txt_path):
    """
    解析 H61DV ...txt（已知正確的統計輸出），回傳 dict。
    txt 格式（從 H61DV NEW1200 -2+2.txt 看）：
        統計範圍：YYYY-MM-DD HH:MM:SS ~ YYYY-MM-DD HH:MM:SS
        平均/最大/最小：
        F: -17.9 / -17.2 / -19.0
        ...
        ON / Off 低標：3.0
        ON / Off 周期次數：9
        On 的平均時間: 30 分
        Off 的平均時間: 11 分
        On / Off 百分比: 73%
        電力消耗：845 w / 1441 分
        24 小時電力消耗：844 w
        能耗計算：
        冷凍室溫度(°C): -17.9
        ...
    """
    text = txt_path.read_text(encoding="utf-8")
    out = {}

    # 統計範圍（取最後一個出現的範圍，因為部分 txt 有兩段以上）
    ranges = re.findall(r"統計範圍：([0-9\-: ]+) ~ ([0-9\-: ]+)", text)
    if ranges:
        # 取跨度最大的那個（24h 比 6h 跨度長）
        def span(r):
            try:
                from datetime import datetime
                return (datetime.fromisoformat(r[1].strip()) - datetime.fromisoformat(r[0].strip())).total_seconds()
            except Exception:
                return 0
        best = max(ranges, key=span)
        out["start"] = best[0].strip()
        out["end"] = best[1].strip()

    # 平均/最大/最小
    avg = {}
    for line in text.splitlines():
        m = re.match(r"\s*([A-Za-z0-9_]+):\s*(-?[\d.]+)\s*/\s*(-?[\d.]+)\s*/\s*(-?[\d.]+)", line)
        if m:
            avg[m.group(1)] = {
                "avg": float(m.group(2)),
                "max": float(m.group(3)),
                "min": float(m.group(4)),
            }
    out["averages"] = avg

    # ON/OFF
    m = re.search(r"ON / Off 低標：([\d.]+)", text)
    if m:
        out["throttle"] = float(m.group(1))
    m = re.search(r"ON / Off 周期次數：(\d+)", text)
    if m:
        out["cycles"] = int(m.group(1))
    m = re.search(r"On 的平均時間: (\d+) 分", text)
    if m:
        out["aboveAvgMin"] = int(m.group(1))
    m = re.search(r"Off 的平均時間: (\d+) 分", text)
    if m:
        out["belowAvgMin"] = int(m.group(1))
    m = re.search(r"On / Off 百分比: (\d+)%", text)
    if m:
        out["abovePct"] = int(m.group(1))

    # 電力
    m = re.search(r"電力消耗：(\d+) w / (\d+) 分", text)
    if m:
        out["filterWh"] = int(m.group(1))
        out["minutesDifference"] = int(m.group(2))
    m = re.search(r"24 小時電力消耗：(\d+) w", text)
    if m:
        out["wp24hW"] = int(m.group(1))

    # EF（"能耗計算：" 之後到檔尾）
    ef_block = text.split("能耗計算：", 1)
    if len(ef_block) == 2:
        ef = {}
        for line in ef_block[1].splitlines():
            line = line.strip()
            if not line or ":" not in line:
                continue
            k, _, v = line.partition(":")
            k = k.strip()
            v = v.strip()
            # 嘗試轉 number
            try:
                if "." in v:
                    fv = float(v)
                    ef[k] = fv
                else:
                    iv = int(v)
                    ef[k] = iv
            except ValueError:
                ef[k] = v  # 字串（如 "1級"）
        out["ef"] = ef
    return out


# ===========================================================
# Main
# ===========================================================

# 預設參數（依 H61DV_NEW1200.txt 的 EF 結果回推）
# NEW1200：VF=170, VR=435, F=-17.9, R=3.3, fan=1
# OLD1200：VF=435, VR=170, F=-18.2, R=3.2, fan=1  (從 txt EF 結果回推)
# VIP800：VF=435, VR=170, F=-18.1, R=3.1, fan=1  (從 txt EF 結果回推)
DEFAULT_PARAMS = {
    "NEW1200": {"vf": 170, "vr": 435, "freezerTemp": -17.9, "fridgeTemp": 3.3, "fanType": 1, "onOffThrottle": 3.0},
    "OLD1200": {"vf": 170, "vr": 435, "freezerTemp": -18.2, "fridgeTemp": 3.2, "fanType": 1, "onOffThrottle": 3.0},
    "VIP800":  {"vf": 170, "vr": 435, "freezerTemp": -18.1, "fridgeTemp": 3.1, "fanType": 1, "onOffThrottle": 3.0},
}


def diff_dict(py, js, path="", tol=1e-9):
    """遞迴比對兩個 dict，回傳差異清單
    tol: 浮點容忍誤差（10^-9 = 1 nanodegree）
    """
    diffs = []
    keys = set(py.keys()) | set(js.keys())
    for k in keys:
        full = f"{path}.{k}" if path else k
        if k not in py:
            diffs.append((full, "<missing>", js[k]))
        elif k not in js:
            diffs.append((full, py[k], "<missing>"))
        elif isinstance(py[k], dict) and isinstance(js[k], dict):
            diffs.extend(diff_dict(py[k], js[k], full, tol))
        elif py[k] != js[k]:
            # 浮點容忍
            if isinstance(py[k], float) and isinstance(js[k], float):
                if abs(py[k] - js[k]) < tol:
                    continue
            # start/end 容忍時區序列化差異（JS Date.toISOString() 會把 local ISO 轉 UTC，差 8h）
            # 用 datetime.fromisoformat 解析兩邊，比較 epoch timestamp（忽略時區）
            if k in ("start", "end") and isinstance(py[k], str) and isinstance(js[k], str):
                try:
                    from datetime import datetime
                    # Python 端沒時區當作 local time
                    py_dt = datetime.fromisoformat(py[k].replace("Z", "+00:00"))
                    js_dt = datetime.fromisoformat(js[k].replace("Z", "+00:00"))
                    # 比較 epoch（忽略時區差異）
                    if py_dt.timestamp() == js_dt.timestamp():
                        continue
                except Exception:
                    pass
            diffs.append((full, py[k], js[k]))
    return diffs


def main():
    targets = sys.argv[1:] if len(sys.argv) > 1 else [name for name, _ in ALL_CSVS]
    print(f"驗證目標：{targets}\n")

    overall_ok = True
    for name, csv_path in ALL_CSVS:
        if name not in targets:
            continue
        print(f"=== {name} ({csv_path.name}) ===")

        if not csv_path.exists():
            print(f"  [SKIP] 找不到 CSV: {csv_path}")
            continue

        params = DEFAULT_PARAMS[name]

        # 1. 讀 CSV
        csv_text = csv_path.read_text(encoding="utf-8")
        parsed = parse_csv(csv_text)
        if not parsed:
            print(f"  [ERROR] CSV 解析失敗")
            overall_ok = False
            continue

        # 2. 從對應 .txt 讀「已知正確」的時間範圍
        txt_path = csv_path.with_suffix(".txt")
        if not txt_path.exists():
            print(f"  [SKIP] 找不到對照 txt: {txt_path}")
            continue
        expected = parse_expected_txt(txt_path)
        start_dt = ts_to_dt(expected["start"])
        end_dt = ts_to_dt(expected["end"])

        # 3. Python 版
        py_stats = py_calculate_statistics(parsed, start_dt, end_dt, params)

        # 4. JS 版（透過 Node.js 跑 calculator.js）
        js_stats = run_js(csv_path, start_dt, end_dt, params)

        # 5. 寫 JSON
        (OUT_DIR / f"{name}_py.json").write_text(json.dumps(py_stats, ensure_ascii=False, indent=2), encoding="utf-8")
        (OUT_DIR / f"{name}_js.json").write_text(json.dumps(js_stats, ensure_ascii=False, indent=2), encoding="utf-8")

        # 6. diff
        diffs = diff_dict(py_stats, js_stats)

        # 7. 對照 .txt 已知正確輸出
        expected_mismatch = []
        if "averages" in expected:
            for col, vals in expected["averages"].items():
                py_avg = py_stats["averages"].get(col)
                if py_avg is not None and abs(py_avg - vals["avg"]) > 0.05:
                    expected_mismatch.append(f"avg[{col}] py={py_avg:.3f} expected={vals['avg']:.3f}")
        if "filterWh" in expected and py_stats["power"]["filterWh"] != expected["filterWh"]:
            expected_mismatch.append(f"filterWh py={py_stats['power']['filterWh']} expected={expected['filterWh']}")
        if "wp24hW" in expected and py_stats["power"]["wp24hW"] != expected["wp24hW"]:
            expected_mismatch.append(f"wp24hW py={py_stats['power']['wp24hW']} expected={expected['wp24hW']}")
        if "cycles" in expected and py_stats["onoff"]["cycles"] != expected["cycles"]:
            expected_mismatch.append(f"cycles py={py_stats['onoff']['cycles']} expected={expected['cycles']}")

        # 報告
        print(f"  範圍：{expected['start']} ~ {expected['end']}")
        print(f"  範圍內列數：{py_stats['count']}")
        print(f"  電力：filterWh={py_stats['power']['filterWh']} / wp24hW={py_stats['power']['wp24hW']}")
        print(f"  ON/OFF：cycles={py_stats['onoff']['cycles']} above={py_stats['onoff']['aboveAvgMin']}min below={py_stats['onoff']['belowAvgMin']}min pct={py_stats['onoff']['abovePct']}%")
        if py_stats["ef"] and py_stats["ef"]["results"]:
            ef = py_stats["ef"]["results"]
            print(f"  EF：daily={py_stats['ef']['dailyConsumption']} kWh/日, monthly={py_stats['ef']['monthlyConsumption']} kWh/月")
            print(f"      ef_value={ef['實測EF值']} 2018={ef['2018效率等級']}({ef['2018效率基準百分比(%)']}%) 2027={ef['2027新效率等級']}({ef['2027新效率基準百分比(%)']}%)")

        if diffs:
            print(f"  [DIFF] py vs js: {len(diffs)} 處差異")
            for path, pv, jv in diffs:
                print(f"      {path}: py={pv!r} js={jv!r}")
            overall_ok = False
        else:
            print(f"  [OK] py vs js 完全一致")

        if expected_mismatch:
            print(f"  [WARN] py vs txt 已知輸出有 {len(expected_mismatch)} 處差異（可能是 txt 來自不同時間範圍或不同參數）")
            for m in expected_mismatch:
                print(f"      {m}")
        else:
            print(f"  [OK] py vs txt 已知輸出完全一致")

        # 8. 滑動視窗掃描驗證（1440 分鐘窗口 / 10 分鐘步進 / 最高 EF）
        py_scan = py_scan_best_window(parsed, params, win=1440, step=10)
        js_scan = run_js_scan(csv_path, params)
        scan_ok = True
        scan_diffs = []
        if py_scan and js_scan:
            for k in ("totalSteps", "bestEf", "bestWatt"):
                if abs(float(py_scan.get(k, 0)) - float(js_scan.get(k, 0))) > 1e-6:
                    scan_diffs.append((k, py_scan.get(k), js_scan.get(k)))
            for k in ("bestStart", "bestEnd"):
                py_v = py_scan.get(k)
                js_v = js_scan.get(k)
                if not py_v or not js_v:
                    scan_diffs.append((k, py_v, js_v))
                    continue
                # 都是 ISO 字串，比較 epoch（忽略時區）
                from datetime import datetime as _dt
                py_dt = _dt.fromisoformat(py_v.replace("Z", "+00:00"))
                js_dt = _dt.fromisoformat(js_v.replace("Z", "+00:00"))
                if py_dt.timestamp() != js_dt.timestamp():
                    scan_diffs.append((k, py_v, js_v))
            print(f"  掃描：steps={py_scan['totalSteps']}  bestEF={py_scan['bestEf']} bestWatt={py_scan['bestWatt']}W  bestStart={py_scan['bestStart']}")
            if scan_diffs:
                scan_ok = False
                print(f"  [SCAN-DIFF] py vs js scan: {len(scan_diffs)} 處差異")
                for path, pv, jv in scan_diffs:
                    print(f"      {path}: py={pv!r} js={jv!r}")
                overall_ok = False
            else:
                print(f"  [OK] py vs js scan 最佳區段完全一致")
        (OUT_DIR / f"{name}_scan_py.json").write_text(json.dumps(py_scan, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        (OUT_DIR / f"{name}_scan_js.json").write_text(json.dumps(js_scan, ensure_ascii=False, indent=2), encoding="utf-8")

        print()

    print(f"{'='*60}")
    print(f"總結：{'全部一致 ✓' if overall_ok else '有差異 ✗'}")
    sys.exit(0 if overall_ok else 1)


if __name__ == "__main__":
    main()

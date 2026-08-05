// calculator.js — 計算頁前端（v1）
//
// 流程：
//   1. 使用者按「儲存 CSV」→ 編輯 → 上傳到 /calculator
//   2. parseCsv() 解析成 { datetime, cols: { name: [values] }, ts: [Date] }
//   3. renderChart() 畫 Chart.js 折線圖
//   4. 使用者拖曳兩條 X-line 選範圍（start / end）→ 即時計算並更新結果文字框
//   5. 按「儲存結果」→ 用 <a download> 讓瀏覽器跳儲存對話框
//
// EF 演算法 1:1 移植自 kalapontsai/Data-plot-and-EEF-calculate 的
//   plot_gui_雙信.py EnergyCalculator.calculate()（rev.0.5.0.0）。
// round() 用 chart-utils.js 的 roundHalfUp（half-up，與 Python 一致）。
//
// 與 Python 對齊的關鍵細節：
//   - daily_consumption = round(wp_24h_difference / 1000, 3)  // kWh/日
//   - monthly_consumption = round(daily_consumption * 30, 0)   // kWh/月
//   - K_value = round((30 - freezer_temp) / (30 - fridge_temp), 2)
//   - ef_value = round(measured_equivalent_volume / monthly_consumption, 1)
//   - 2018 thresholds：1.6/1.45/1.3/1.15（type 1-4）或 1.72/1.54/1.36/1.18（type 5）
//   - 2027 thresholds：1.308/1.231/1.154/1.077（type 1-4）或 1.294/1.221/1.147/1.074（type 5）
//   - grade 規則：≥T1 → 1級；≥T1*0.95 → 1*級；≥T2 → 2級；≥T3 → 3級；≥T4 → 4級；<T4 → 5級
//
// ON/OFF 週期統計（照 Python calculate_statistics）：
//   - power_on = (W >= on_off_throttle)
//   - cycles = abs(power_on.diff()).sum() // 2
//   - segments = group by 連續 power_on 狀態，計算持續時間（秒）
//   - above_avg_time = int(mean(above_durations) / 60)
//   - below_avg_time = int(mean(below_durations) / 60) + 1
//   - above_percentage = int(above_avg / (above + below) * 100)

(function () {
  'use strict';

  // ===========================================================
  // CSV 解析
  // ===========================================================

  /**
   * 解析 CSV 字串。
   * 規則（對齊 index.html「儲存 CSV」格式）：
   *   - 第 1 列為標頭
   *   - 第 1 欄為 datetime，格式 `YYYY/M/D H:M` 或 `YYYY-MM-DD HH:MM:SS`
   *   - 中間欄為溫度欄（任意數量）
   *   - 末 3 欄固定為 V, I, W（可能缺席）
   *
   * @param {string} csvText  UTF-8 BOM 開頭會自動去掉
   * @returns {{ok: boolean, error?: string, datetime?: Date[], cols?: Object<string, Array<number|null>>, header?: string[]}}
   */
  function parseCsv(csvText) {
    if (!csvText) return { ok: false, error: 'CSV 是空的' };
    // 去 BOM
    if (csvText.charCodeAt(0) === 0xFEFF) csvText = csvText.slice(1);

    const lines = csvText.split(/\r?\n/).filter((l) => l.length > 0);
    if (lines.length < 2) return { ok: false, error: 'CSV 至少要有標頭 + 1 筆資料' };

    const header = lines[0].split(',').map((s) => s.trim());
    if (header.length < 2) return { ok: false, error: '標頭至少需要 2 欄' };

    const datetime = [];
    const cols = {};
    for (let i = 1; i < header.length; i++) cols[header[i]] = [];

    const skipped = { badTs: 0, badNum: 0 };

    for (let li = 1; li < lines.length; li++) {
      const line = lines[li];
      // 簡單 split：對齊 Python pandas read_csv 預設行為（不處理引號）
      // 因為 GX20 匯出的 CSV 都是 `datetime,num,num,...` 純數字格式
      const cells = line.split(',');
      if (cells.length < header.length) {
        skipped.badNum++;
        continue;
      }
      const ts = parseTimestamp(cells[0]);
      if (!ts) {
        skipped.badTs++;
        continue;
      }
      datetime.push(ts);
      for (let ci = 1; ci < header.length; ci++) {
        const name = header[ci];
        const v = parseFloatSafe(cells[ci]);
        cols[name].push(v);
      }
    }

    if (datetime.length === 0) {
      return { ok: false, error: '沒有任何有效的 datetime 列（請檢查第一欄格式）' };
    }

    return { ok: true, header, datetime, cols, skipped };
  }

  /**
   * 嘗試解析 `YYYY/M/D H:M` 或 `YYYY-MM-DD HH:MM:SS` 格式。
   * 對齊 GX20 匯出格式（`2026/7/31 16:46`）跟 Python 原版的 datetime 解析。
   * 回傳 Date 或 null。
   */
  function parseTimestamp(s) {
    if (!s) return null;
    s = s.trim();
    // 嘗試 ISO 格式
    let m = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?$/.exec(s);
    if (m) {
      const [, y, mo, d, h, mi, se] = m;
      return new Date(+y, +mo - 1, +d, +h, +mi, +(se || 0), 0);
    }
    // 嘗試 `YYYY/M/D H:M` 格式（GX20 匯出預設）
    m = /^(\d{4})\/(\d{1,2})\/(\d{1,2})\s+(\d{1,2}):(\d{2})(?::(\d{2}))?$/.exec(s);
    if (m) {
      const [, y, mo, d, h, mi, se] = m;
      return new Date(+y, +mo - 1, +d, +h, +mi, +(se || 0), 0);
    }
    return null;
  }

  function parseFloatSafe(s) {
    if (s == null) return null;
    s = String(s).trim();
    if (s === '' || s === 'nan' || s === 'NaN' || s === 'None') return null;
    const v = parseFloat(s);
    return Number.isFinite(v) ? v : null;
  }

  // ===========================================================
  // 統計計算（對齊 Python plot_gui_雙信.py calculate_statistics）
  // ===========================================================

  /**
   * 計算範圍內的統計。
   * @param {Object} parsed   parseCsv() 結果
   * @param {Date} start
   * @param {Date} end
   * @returns {Object}  stats + onoff + power + ef
   */
  function calculateStatistics(parsed, start, end, params) {
    // 1. 篩選範圍
    const idx = [];
    for (let i = 0; i < parsed.datetime.length; i++) {
      const t = parsed.datetime[i].getTime();
      if (t >= start.getTime() && t <= end.getTime()) idx.push(i);
    }
    if (idx.length === 0) {
      return { ok: false, error: '範圍內沒有資料' };
    }

    // 2. 平均 / 最大 / 最小（每欄）
    const cols = parsed.cols;
    const colNames = Object.keys(cols);
    const averages = {};
    const maxValues = {};
    const minValues = {};
    for (const name of colNames) {
      const vals = [];
      for (const i of idx) {
        const v = cols[name][i];
        if (v != null) vals.push(v);
      }
      if (vals.length > 0) {
        averages[name] = vals.reduce((a, b) => a + b, 0) / vals.length;
        maxValues[name] = Math.max(...vals);
        minValues[name] = Math.min(...vals);
      } else {
        averages[name] = null;
        maxValues[name] = null;
        minValues[name] = null;
      }
    }

    // 3. ON / OFF 週期統計
    const onoff = computeOnOff(parsed, idx, params.onOffThrottle);

    // 4. 電力消耗
    const power = computePower(parsed, idx);

    // 5. EF 計算（只在 W 有值時算）
    const ef = power.wp24hW != null && power.filterWh != null
      ? computeEf(parsed, idx, averages, params)
      : null;

    return {
      ok: true,
      start, end,
      count: idx.length,
      skipped: parsed.skipped,
      averages,
      maxValues,
      minValues,
      onoff,
      power,
      ef,
    };
  }

  /**
   * ON / OFF 週期統計（對齊 Python calculate_statistics 邏輯）
   * @returns {Object} { cycles, aboveAvgMin, belowAvgMin, abovePct, aboveCount, belowCount, throttle }
   */
  function computeOnOff(parsed, idx, throttle) {
    const wCol = parsed.cols['W'];
    if (!wCol) {
      return {
        cycles: '無法計算，缺少 P(W) 欄位',
        aboveAvgMin: 0,
        belowAvgMin: 0,
        abovePct: 0,
        aboveCount: 0,
        belowCount: 0,
        throttle,
        hasW: false,
      };
    }
    // power_on mask
    const mask = idx.map((i) => {
      const w = wCol[i];
      return w != null && w >= throttle;
    });

    // cycles = int(sum(|mask.diff()|)) // 2
    let diffSum = 0;
    for (let i = 1; i < mask.length; i++) {
      if (mask[i] !== mask[i - 1]) diffSum++;
    }
    const cycles = Math.floor(diffSum / 2);

    // segments: 連續同狀態的群組
    const segments = [];
    if (mask.length > 0) {
      let runStart = 0;
      let runState = mask[0];
      for (let i = 1; i < mask.length; i++) {
        if (mask[i] !== runState) {
          segments.push({ state: runState, startIdx: runStart, endIdx: i - 1 });
          runStart = i;
          runState = mask[i];
        }
      }
      segments.push({ state: runState, startIdx: runStart, endIdx: mask.length - 1 });
    }

    // 取首尾以外（對齊 Python `if len(segments) > 2: segments = segments.iloc[1:-1]`）
    const trimmed = segments.length > 2 ? segments.slice(1, -1) : segments;

    const aboveSegs = trimmed.filter((s) => s.state);
    const belowSegs = trimmed.filter((s) => !s.state);
    const aboveCount = aboveSegs.length;
    const belowCount = belowSegs.length;

    function avgSeconds(segs) {
      if (segs.length === 0) return 0;
      const durs = segs.map((s) => {
        const tStart = parsed.datetime[idx[s.startIdx]].getTime();
        const tEnd = parsed.datetime[idx[s.endIdx]].getTime();
        return Math.max(0, (tEnd - tStart) / 1000);
      });
      return durs.reduce((a, b) => a + b, 0) / durs.length;
    }

    const aboveAvgSec = avgSeconds(aboveSegs);
    const belowAvgSec = avgSeconds(belowSegs);
    const aboveAvgMin = Math.floor(aboveAvgSec / 60);
    const belowAvgMin = belowCount > 0 ? Math.floor(belowAvgSec / 60) + 1 : 0;

    const abovePct = (aboveAvgMin + belowAvgMin) > 0
      ? Math.floor((aboveAvgMin / (aboveAvgMin + belowAvgMin)) * 100)
      : 0;

    return {
      cycles,
      aboveAvgMin,
      belowAvgMin,
      abovePct,
      aboveCount,
      belowCount,
      throttle,
      hasW: true,
    };
  }

  /**
   * 電力消耗統計
   * @returns {Object} { filterWh, minutesDifference, wp24hW }
   *   filterWh        = int(sum(W) / 60)         // 範圍內 Wh
   *   minutesDifference = int((end - start).total_seconds() / 60)
   *   wp24hW          = int((filterWh / total_seconds) * 24 * 3600)   // 換算 24h 耗電（W）
   */
  function computePower(parsed, idx) {
    const wCol = parsed.cols['W'];
    const vCol = parsed.cols['V'];
    const iCol = parsed.cols['I'];

    // 範圍首尾 datetime
    const start = parsed.datetime[idx[0]];
    const end = parsed.datetime[idx[idx.length - 1]];
    const minutesDifference = Math.floor((end.getTime() - start.getTime()) / 1000 / 60);

    if (!wCol) {
      return { filterWh: null, minutesDifference, wp24hW: null };
    }

    // filter_WH = int(sum(W) / 60)
    let sumW = 0;
    let wCount = 0;
    for (const i of idx) {
      const w = wCol[i];
      if (w != null) {
        sumW += w;
        wCount++;
      }
    }
    const filterWh = Math.floor(sumW / 60);

    const totalSeconds = (end.getTime() - start.getTime()) / 1000;
    if (totalSeconds <= 0 || wCount === 0) {
      return { filterWh, minutesDifference, wp24hW: null };
    }
    const wp24hW = Math.floor((filterWh / totalSeconds) * (24 * 3600));

    return { filterWh, minutesDifference, wp24hW };
  }

  /**
   * EF 計算（對齊 Python EnergyCalculator）
   * @param {Object} parsed
   * @param {Array<number>} idx
   * @param {Object} averages  calculateStatistics 的 averages（含 F / R 欄平均）
   * @param {Object} params    { vf, vr, fridgeTemp, freezerTemp, fanType }
   */
  function computeEf(parsed, idx, averages, params) {
    const { vf, vr, fridgeTemp, freezerTemp, fanType } = params;
    const power = computePower(parsed, idx);
    if (power.wp24hW == null) return null;

    const dailyConsumption = ChartUtils.roundHalfUp(power.wp24hW / 1000, 3); // kWh/日
    const monthlyConsumption = Math.round(dailyConsumption * 30);            // kWh/月

    if (!(vf > 0) || !(vr > 0)) return { dailyConsumption, monthlyConsumption, results: null };

    const standardK = 1.78;
    const measuredK = ChartUtils.roundHalfUp((30 - freezerTemp) / (30 - fridgeTemp), 2);
    const equivalentVolume = ChartUtils.roundHalfUp(vr + standardK * vf, 1);
    const measuredEquivalentVolume = ChartUtils.roundHalfUp(vr + measuredK * vf, 1);

    const effectiveVolume = vr + vf;
    const fridgeType = determineFridgeType(equivalentVolume, effectiveVolume, vf, fanType);

    const energyAllowance = calculateEnergyAllowance(equivalentVolume, fridgeType);
    const futureEnergyAllowance = calculateFutureEnergyAllowance(equivalentVolume, fridgeType);
    const benchmarkConsumption = Math.round(equivalentVolume / energyAllowance);
    const futureBenchmarkConsumption = Math.round(equivalentVolume / futureEnergyAllowance);
    const efValue = ChartUtils.roundHalfUp(measuredEquivalentVolume / monthlyConsumption, 1);

    const currentThresholds = currentEfThresholds(energyAllowance, fridgeType);
    const currentPercent = Math.round(efValue / currentThresholds[0] * 100 * 10) / 10; // 1 位小數
    const currentGrade = calculateEfficiencyGrade(efValue, currentThresholds);

    const futureThresholds = futureEfThresholds(futureEnergyAllowance, fridgeType);
    const futurePercent = Math.round(efValue / futureThresholds[0] * 100 * 10) / 10;
    const futureGrade = calculateEfficiencyGrade(efValue, futureThresholds);

    // 取得對齊計算欄（用 averages）
    const frozenC = averages['F'];
    const fridgeC = averages['R'];

    const results = {
      '冷凍室溫度(°C)': frozenC != null ? Math.round(frozenC * 10) / 10 : freezerTemp,
      '冷藏室溫度(°C)': fridgeC != null ? Math.round(fridgeC * 10) / 10 : fridgeTemp,
      '實測K值': measuredK,
      'VF(L)': Math.round(vf),
      'VR(L)': Math.round(vr),
      '等效內容積(L)': equivalentVolume,
      '實測等效內容積(L)': measuredEquivalentVolume,
      '冰箱型式': fridgeType,
      '2018容許耗用能源基準(L/kWh/月)': energyAllowance,
      '2027容許耗用能源基準(L/kWh/月)': futureEnergyAllowance,
      '2018耗電量基準(kWh/月)': benchmarkConsumption,
      '2027耗電量基準(kWh/月)': futureBenchmarkConsumption,
      '實測月耗電量(kWh/月)': monthlyConsumption,
      '實測EF值': efValue,
      '2018 EF基準值': currentThresholds[0],
      '2018效率基準百分比(%)': currentPercent,
      '2018效率等級': currentGrade,
      '2027 EF基準值': futureThresholds[0],
      '2027新效率基準百分比(%)': futurePercent,
      '2027新效率等級': futureGrade,
    };

    return { dailyConsumption, monthlyConsumption, results };
  }

  function determineFridgeType(equivalentVolume, effectiveVolume, vf, fanType) {
    if (vf === 0) return 5;
    if (equivalentVolume < 400 && fanType === 1) return 1;
    if (equivalentVolume >= 400 && fanType === 1) return 2;
    if (equivalentVolume < 400 && fanType === 0) return 3;
    return 4;
  }

  function calculateEnergyAllowance(equivalentVolume, fridgeType) {
    let v;
    switch (fridgeType) {
      case 1: v = equivalentVolume / (0.037 * equivalentVolume + 24.3); break;
      case 2: v = equivalentVolume / (0.031 * equivalentVolume + 21);   break;
      case 3: v = equivalentVolume / (0.033 * equivalentVolume + 19.7); break;
      case 4: v = equivalentVolume / (0.029 * equivalentVolume + 17);   break;
      default: v = equivalentVolume / (0.033 * equivalentVolume + 15.8); break;
    }
    return Math.round(v * 10) / 10;
  }

  function calculateFutureEnergyAllowance(equivalentVolume, fridgeType) {
    let v;
    switch (fridgeType) {
      case 1: v = 1.3 * equivalentVolume / (0.037 * equivalentVolume + 24.3); break;
      case 2: v = 1.3 * equivalentVolume / (0.031 * equivalentVolume + 21);   break;
      case 3: v = 1.3 * equivalentVolume / (0.033 * equivalentVolume + 19.7); break;
      case 4: v = 1.3 * equivalentVolume / (0.029 * equivalentVolume + 17);   break;
      default: v = 1.36 * equivalentVolume / (0.033 * equivalentVolume + 15.8); break;
    }
    return Math.round(v * 10) / 10;
  }

  function currentEfThresholds(energyAllowance, fridgeType) {
    let t;
    if (fridgeType === 5) {
      t = [1.72, 1.54, 1.36, 1.18];
    } else {
      t = [1.6, 1.45, 1.3, 1.15];
    }
    return t.map((x) => Math.round(energyAllowance * x * 10) / 10);
  }

  function futureEfThresholds(futureEnergyAllowance, fridgeType) {
    let t;
    if (fridgeType === 5) {
      t = [1.294, 1.221, 1.147, 1.074];
    } else {
      t = [1.308, 1.231, 1.154, 1.077];
    }
    return t.map((x) => Math.round(futureEnergyAllowance * x * 10) / 10);
  }

  function calculateEfficiencyGrade(efValue, thresholds) {
    if (efValue >= thresholds[0]) {
      return '1級';
    } else if (efValue >= thresholds[0] * 0.95) {
      return '1*級';
    } else if (efValue >= thresholds[1]) {
      return '2級';
    } else if (efValue >= thresholds[2]) {
      return '3級';
    } else if (efValue >= thresholds[3]) {
      return '4級';
    }
    return '5級';
  }

  // ===========================================================
  // 結果格式化（對齊 Python f-string 輸出格式）
  // ===========================================================

  function formatResult(r) {
    if (!r.ok) return `[錯誤] ${r.error}`;
    const lines = [];
    lines.push(`統計範圍：${formatTsFull(r.start)} ~ ${formatTsFull(r.end)}`);
    if (r.skipped && (r.skipped.badTs > 0 || r.skipped.badNum > 0)) {
      lines.push(`(略過：${r.skipped.badTs} 列時間格式錯誤，${r.skipped.badNum} 列欄位不足)`);
    }
    lines.push(`平均/最大/最小：`);
    for (const name of Object.keys(r.averages)) {
      const a = r.averages[name];
      const mx = r.maxValues[name];
      const mn = r.minValues[name];
      lines.push(`${name}: ${fmt1(a)} / ${fmt1(mx)} / ${fmt1(mn)}`);
    }
    lines.push('');
    lines.push(`ON / Off 低標：${r.onoff.throttle}`);
    lines.push(`ON / Off 周期次數：${r.onoff.cycles}`);
    if (r.onoff.aboveCount > 0) {
      lines.push(`On 的平均時間: ${r.onoff.aboveAvgMin} 分`);
    } else {
      lines.push(`P(W) >= ${r.onoff.throttle} 的平均時間: 無資料`);
    }
    if (r.onoff.belowCount > 0) {
      lines.push(`Off 的平均時間: ${r.onoff.belowAvgMin} 分`);
    } else {
      lines.push(`P(W) < ${r.onoff.throttle} 的平均時間: 無資料`);
    }
    lines.push(`On / Off 百分比: ${r.onoff.abovePct}%`);
    lines.push('');
    if (r.power.filterWh != null) {
      lines.push(`電力消耗：${r.power.filterWh} w / ${r.power.minutesDifference} 分`);
      lines.push(`24 小時電力消耗：${r.power.wp24hW} w`);
    } else {
      lines.push(`電力消耗：無法計算（範圍內無 W 資料）`);
      lines.push(`24 小時電力消耗：無法計算`);
    }
    lines.push('');
    lines.push(`能耗計算：`);
    if (r.ef && r.ef.results) {
      for (const k of Object.keys(r.ef.results)) {
        lines.push(`${k}: ${r.ef.results[k]}`);
      }
    } else {
      lines.push('無法計算能耗，請檢查數據');
    }
    return lines.join('\n');
  }

  function fmt1(v) {
    if (v == null || Number.isNaN(v)) return '—';
    return (Math.round(v * 10) / 10).toFixed(1);
  }

  function formatTsFull(d) {
    if (!d) return '—';
    const p = (n) => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
  }

  // ===========================================================
  // UI 控制
  // ===========================================================

  let chart = null;
  let cursor = null;
  let parsed = null;       // parseCsv 結果
  let lastResult = '';     // 結果文字（用於「儲存結果」）

  const COLORS = [
    '#e6194b', '#3cb44b', '#4363d8', '#f58231', '#911eb4',
    '#42d4f4', '#f032e6', '#9a6324', '#800000', '#808000',
    '#000075', '#a9a9a9', '#fabed4', '#ffd8b1', '#fffac8',
    '#aaffc3', '#808080', '#ffd700', '#ff69b4', '#1e90ff',
  ];

  async function init() {
    const themeBtn = document.getElementById('themeBtn');
    themeBtn.addEventListener('click', () => {
      const next = document.body.dataset.theme === 'dark' ? 'light' : 'dark';
      document.body.dataset.theme = next;
      if (chart) {
        // 重新套主題色
        const c = ChartUtils.chartColors();
        chart.options.scales.x.ticks.color = c.text;
        chart.options.scales.x.grid.color = c.grid;
        chart.options.scales.y.ticks.color = c.text;
        chart.options.scales.y.grid.color = c.grid;
        chart.options.scales.y.title.color = c.text;
        chart.update('none');
      }
    });

    document.getElementById('csvFileInput').addEventListener('change', onFileSelected);
    document.getElementById('loadSampleBtn').addEventListener('click', loadSample);
    document.getElementById('saveResultBtn').addEventListener('click', saveResult);
    document.getElementById('findBestBtn').addEventListener('click', onFindBestClicked);

    // 動態注入進度遮罩（只在掃描時顯示，預設 0.1 秒前都不出現）
    injectProgressOverlay();

    // 參數變更時即時重算
    ['paramVf', 'paramVr', 'paramFreezerTemp', 'paramFridgeTemp', 'paramFanType', 'paramOnOffTh'].forEach((id) => {
      const el = document.getElementById(id);
      const evt = (el.type === 'checkbox') ? 'change' : 'input';
      el.addEventListener(evt, recompute);
    });

    // 初始化空 chart
    const canvas = document.getElementById('calcChart');
    chart = ChartUtils.buildLineChart(canvas, {
      datasets: [],         // 等 CSV 載入後再填
      yTitle: '讀值',
    });

    // 建立 cursor overlay
    cursor = ChartUtils.createCursorOverlay({
      chart,
      overlay: document.getElementById('calcCursorOverlay'),
      onChange: () => recompute(),
    });

    document.getElementById('connText').textContent = '請載入 CSV';
    document.getElementById('connDot').classList.remove('on');
    document.getElementById('connDot').classList.add('off');
  }

  function onFileSelected(e) {
    const file = e.target.files && e.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      const text = String(reader.result || '');
      onCsvTextLoaded(text, file.name);
    };
    reader.onerror = () => {
      setStatus('檔案讀取失敗', 'err');
    };
    reader.readAsText(file, 'utf-8');
  }

  function onCsvTextLoaded(text, filename) {
    const result = parseCsv(text);
    if (!result.ok) {
      setStatus(`解析失敗：${result.error}`, 'err');
      document.getElementById('resultText').textContent = `[錯誤] ${result.error}`;
      return;
    }
    parsed = result;
    document.getElementById('fileInfo').textContent =
      `${filename}  (${result.datetime.length} 列, ${result.header.length - 1} 欄)`;
    setStatus(`已載入 ${result.datetime.length} 列`, 'ok');

    // 重建 chart datasets（依欄位數自動配色）
    rebuildChart();

    // 預設 cursor 放在 1/4 / 3/4 位置
    cursor.setPositions(
      result.datetime[0],
      result.datetime[result.datetime.length - 1]
    );
    cursor.show();
    recompute();
  }

  function rebuildChart() {
    // 銷毀舊 chart
    if (chart) {
      try { chart.stop(); } catch (_) {}
      chart.destroy();
      chart = null;
    }
    const canvas = document.getElementById('calcChart');
    const datasets = [];
    // datetime 為第 0 欄；後續為溫度欄；最後 V, I, W（如果有）
    const tempCols = parsed.header.slice(1).filter((n) => !['V', 'I', 'W'].includes(n));
    tempCols.forEach((name, i) => {
      datasets.push({
        label: name,
        color: COLORS[i % COLORS.length],
        pointIndex: i,
      });
    });
    chart = ChartUtils.buildLineChart(canvas, {
      datasets,
      yTitle: '讀值',
    });
    // 把每個 dataset 的 data 填進去
    tempCols.forEach((name, i) => {
      const vals = parsed.cols[name];
      const data = [];
      for (let j = 0; j < parsed.datetime.length; j++) {
        const v = vals[j];
        if (v != null) data.push({ x: parsed.datetime[j].getTime(), y: v });
      }
      chart.data.datasets[i].data = data;
    });
    chart.update('none');

    // 重建 cursor（因為 chart 重建了，舊的綁定失效）
    cursor = ChartUtils.createCursorOverlay({
      chart,
      overlay: document.getElementById('calcCursorOverlay'),
      onChange: () => recompute(),
    });
    cursor.show();
  }

  function getParams() {
    return {
      vf: parseFloat(document.getElementById('paramVf').value) || 0,
      vr: parseFloat(document.getElementById('paramVr').value) || 0,
      fridgeTemp: parseFloat(document.getElementById('paramFridgeTemp').value) || 0,
      freezerTemp: parseFloat(document.getElementById('paramFreezerTemp').value) || 0,
      fanType: document.getElementById('paramFanType').checked ? 1 : 0,
      onOffThrottle: parseFloat(document.getElementById('paramOnOffTh').value) || 0,
    };
  }

  function recompute() {
    if (!parsed || !cursor || !cursor.tsLeft || !cursor.tsRight) return;
    const params = getParams();
    const r = calculateStatistics(parsed, cursor.tsLeft, cursor.tsRight, params);
    lastResult = formatResult(r);
    document.getElementById('resultText').textContent = lastResult;
  }

  // ===========================================================
  // 「尋找最佳數據」：以 1440 分鐘（24H）窗口滑動掃描找最高 EF
  // ===========================================================

  // 預估單步計算成本（每次掃描前先跑 1 步量時間，用線性預估）
  const SCAN_SAMPLE_MS = 50;          // 預估取樣步數
  const SCAN_THRESHOLD_MS = 3000;     // 預估超過 3 秒才顯示動畫

  // 掃描狀態（單一執行緒）
  const scanState = {
    running: false,
    cancel: false,
    startTs: 0,
  };

  function onFindBestClicked() {
    if (scanState.running) {
      // 第二次按 = 取消
      scanState.cancel = true;
      setStatus('掃描取消中…', 'warn');
      return;
    }
    if (!parsed || !parsed.datetime || parsed.datetime.length === 0) {
      alert('請先載入 CSV');
      return;
    }
    const params = getParams();
    if (!(params.vf > 0) || !(params.vr > 0)) {
      alert('請設定 VF / VR（必須 > 0）');
      return;
    }
    runBestWindowScan(parsed, params);
  }

  function injectProgressOverlay() {
    if (document.getElementById('calcProgressOverlay')) return;
    const div = document.createElement('div');
    div.className = 'calc-progress-overlay';
    div.id = 'calcProgressOverlay';
    div.innerHTML = `
      <div class="progress-box">
        <div class="spinner"></div>
        <div class="progress-title">操作中 — 掃描中</div>
        <div class="progress-bar"><div class="progress-fill" id="calcProgressFill"></div></div>
        <div class="progress-percent" id="calcProgressPercent">0%</div>
      </div>`;
    document.body.appendChild(div);
  }

  function showProgress(percent, title) {
    const overlay = document.getElementById('calcProgressOverlay');
    if (!overlay) return;
    overlay.classList.add('active');
    const fill = document.getElementById('calcProgressFill');
    const pct = document.getElementById('calcProgressPercent');
    const tt = overlay.querySelector('.progress-title');
    if (fill) fill.style.width = Math.min(100, Math.max(0, percent)).toFixed(1) + '%';
    if (pct)  pct.textContent = Math.round(percent) + '%';
    if (tt && title) tt.textContent = title;
  }
  function hideProgress() {
    const overlay = document.getElementById('calcProgressOverlay');
    if (overlay) overlay.classList.remove('active');
  }

  /**
   * 1440 分鐘（24H）窗口滑動掃描找最高 EF。
   * 步進 = 10 分鐘（= 10 筆/分鐘粒度）。
   *
   * 先以 sample 預估總時間；若 > 3 秒才顯示進度遮罩 + setTimeout 分批執行。
   * 每批 200 步後讓出 UI thread，避免卡住畫面。
   */
  function runBestWindowScan(parsedLocal, params) {
    scanState.running = true;
    scanState.cancel = false;
    scanState.startTs = performance.now();
    setStatus('掃描中…', 'warn');

    const STEP = 10;                          // 步進 = 10 分鐘（10 筆/1 分鐘粒度）
    const WIN  = 1440;                        // 窗口 = 24H = 1440 分鐘
    const N = parsedLocal.datetime.length;
    if (N < WIN) {
      alert(`資料長度 ${N} 筆不足 24H（1440 分鐘），無法掃描 24H 窗口`);
      scanState.running = false;
      setStatus('掃描中止：資料不足 24H', 'err');
      return;
    }
    const totalSteps = Math.floor((N - WIN) / STEP) + 1;

    // ---- 預估時間（跑前 SCAN_SAMPLE_MS 步量時間，線性外推） ----
    const sampleN = Math.min(SCAN_SAMPLE_MS, totalSteps);
    const sampleT0 = performance.now();
    for (let i = 0; i < sampleN; i++) {
      const idxStart = i * STEP;
      const idxEnd = idxStart + WIN - 1;
      const start = parsedLocal.datetime[idxStart];
      const end = parsedLocal.datetime[idxEnd];
      const r = calculateStatistics(parsedLocal, start, end, params);
      // r 一定 ok（範圍內一定有資料）
      if (!r.ef || !r.ef.results) continue;  // 不強取，給 compiler hint
    }
    const sampleT1 = performance.now();
    const sampleElapsed = (sampleT1 - sampleT0) / 1000;
    const samplePerStep = sampleElapsed / Math.max(1, sampleN);
    const estTotalSec = samplePerStep * totalSteps;

    // 預估 < 3 秒 → 不顯示動畫（背景照跑）
    // 預估 >= 3 秒 → 顯示動畫、setTimeout 分批
    const showAnim = estTotalSec * 1000 >= SCAN_THRESHOLD_MS;
    if (showAnim) {
      showProgress(0, '操作中 — 掃描中');
    }

    // ---- 跑完整掃描 ----
    let bestEf = -Infinity;
    let bestWatt = Infinity;
    let bestStart = null;
    let bestEnd = null;
    let stepsDone = 0;
    const BATCH_SIZE = 200;

    function runBatch() {
      if (scanState.cancel) {
        scanState.running = false;
        scanState.cancel = false;
        hideProgress();
        setStatus('已取消掃描', 'warn');
        return;
      }
      const batchEnd = Math.min(stepsDone + BATCH_SIZE, totalSteps);
      for (let i = stepsDone; i < batchEnd; i++) {
        const idxStart = i * STEP;
        const idxEnd = idxStart + WIN - 1;
        const start = parsedLocal.datetime[idxStart];
        const end = parsedLocal.datetime[idxEnd];
        const r = calculateStatistics(parsedLocal, start, end, params);
        if (!r.ef || !r.ef.results) continue;
        const ef = r.ef.results['實測EF值'];
        const watt = r.power.filterWh || Infinity;
        if (ef > bestEf) {
          bestEf = ef;
          bestWatt = watt;
          bestStart = start;
          bestEnd = end;
        }
      }
      stepsDone = batchEnd;
      if (showAnim) {
        showProgress((stepsDone / totalSteps) * 100, '操作中 — 掃描中');
      }
      if (stepsDone < totalSteps) {
        setTimeout(runBatch, 0);
      } else {
        scanState.running = false;
        hideProgress();
        applyBestResult(bestStart, bestEnd, bestEf, bestWatt, totalSteps);
      }
    }

    // 第一批同步執行；若 showAnim=true，分批繼續（runBatch 自己排程）
    runBatch();
  }

  /**
   * 掃描完成：把 X-line 移到最佳區段邊界 + 結果框加註 + cursor 變綠
   */
  function applyBestResult(start, end, ef, watt, totalSteps) {
    // 1. 把結果文字框改成「最佳 24H 區段」摘要 + 完整計算結果
    const params = getParams();
    const r = calculateStatistics(parsed, start, end, params);
    const fmt = (d) => ChartUtils.formatTs(d).replace(/\//g, '/');  // MM/DD HH:MM:SS
    const banner =
      `【最佳 24H 區段】\n` +
      `時間：${fmt(start)} ~ ${fmt(end)}\n` +
      `EF：${ef}    24H 耗電：${watt} W    (掃描 ${totalSteps} 步)\n` +
      `\n` +
      `---\n\n` +
      formatResult(r);
    lastResult = banner;
    document.getElementById('resultText').textContent = lastResult;

    // 2. X-line 移到邊界（兩條 X-line 預設就是綠色，見 CSS .calc-chart-area 區塊）
    cursor.setPositions(start, end);

    // 3. status
    const elapsedSec = ((performance.now() - scanState.startTs) / 1000).toFixed(2);
    setStatus(`最佳 EF=${ef}（24H ${watt}W，掃 ${totalSteps} 步 / ${elapsedSec}s）`, 'ok');
  }

  function setStatus(text, kind) {
    const dot = document.getElementById('connDot');
    const txt = document.getElementById('connText');
    txt.textContent = text;
    dot.classList.remove('on', 'off');
    dot.classList.add(kind === 'ok' ? 'on' : 'off');
  }

  function saveResult() {
    const text = document.getElementById('resultText').textContent || '';
    if (!text || text.indexOf('請載入') === 0) {
      alert('目前沒有可儲存的結果（請先載入 CSV 並完成計算）');
      return;
    }
    const nameInput = document.getElementById('resultFilename').value.trim();
    const filename = (nameInput || 'calculator_result') + '.txt';

    const blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  // 載入範例：用 fetch 抓 index.html 的「儲存 CSV」格式範例
  // （這裡用內建範例字串，避開 CORS / 跨站台問題）
  async function loadSample() {
    const sample = generateSampleCsv();
    onCsvTextLoaded(sample, 'sample.csv');
  }

  // 內建範例：1 分鐘粒度、共 1441 筆（含一筆典型 ON/OFF 週期）
  function generateSampleCsv() {
    const start = new Date(2026, 6, 31, 16, 46, 0); // 2026/7/31 16:46
    const N = 1441; // 24h + 1 筆
    const lines = ['datetime,F,FR,R,Vbox,V,I,W'];
    for (let i = 0; i < N; i++) {
      const t = new Date(start.getTime() + i * 60 * 1000);
      const ymd = `${t.getFullYear()}/${t.getMonth()+1}/${t.getDate()}`;
      const hms = `${String(t.getHours()).padStart(2,'0')}:${String(t.getMinutes()).padStart(2,'0')}:${String(t.getSeconds()).padStart(2,'0')}`;
      const F = (-18 + Math.sin(i / 30) * 0.3).toFixed(1);
      const FR = (0.5 + Math.sin(i / 20) * 0.2).toFixed(1);
      const R = (3.3 + Math.sin(i / 40) * 0.2).toFixed(1);
      const Vbox = (5.0 + Math.sin(i / 50) * 0.5).toFixed(1);
      // 週期性 W：每 30 分鐘 ON 20 分鐘 / OFF 10 分鐘
      const cyclePos = i % 30;
      const W = cyclePos < 20 ? (110 + (cyclePos % 10) * 5).toFixed(1) : '1.5';
      lines.push(`${ymd} ${hms},${F},${FR},${R},${Vbox},${(110.1).toFixed(2)},${(0.8).toFixed(3)},${W}`);
    }
    return lines.join('\n');
  }

  // 守護：document 可能是 shim（Node.js 測試環境）或遺漏。
  // 測試環境沒有真實 DOM（getElementById 都回 null），跳過 init。
  // 瀏覽器環境（document.getElementById('paramVf') 有回 element）才跑 init。
  if (typeof document === 'undefined' || !document.getElementById('paramVf')) {
    // Node.js / 測試環境：保留 CalculatorAPI 可供外部使用
  } else if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  // 對外暴露（給測試 / 除錯用）
  window.CalculatorAPI = {
    parseCsv, parseTimestamp, parseFloatSafe,
    calculateStatistics, computeOnOff, computePower, computeEf,
    determineFridgeType, calculateEnergyAllowance, calculateFutureEnergyAllowance,
    currentEfThresholds, futureEfThresholds, calculateEfficiencyGrade,
    formatResult, generateSampleCsv,
  };
})();

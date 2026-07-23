// snapshot.js — v10：歷史備份瀏覽頁面前端邏輯
//
// 設計重點：
//   1. 與即時頁（/）完全分離，不共用全域狀態、不接 socketio
//   2. 畫圖資料走 LTTB 降取樣（順暢）；統計走 /api/snapshot/stats（SQLite 原始）
//   3. 兩條 X-line 用 CSS overlay（沿用既有 .cursor-bar）+ Chart.js 互動
//   4. wheel zoom 自己寫（不引進 chartjs-plugin-zoom）
//   5. 曲線顯示/隱藏用 Chart.js 內建 dataset.hidden + checkbox 雙入口
//
// API 互動：
//   GET /api/snapshot/archives                  → 拿備份清單
//   GET /api/snapshot/data?filename=X&max_points=Y  → 拿畫圖點
//   GET /api/snapshot/stats?filename=X&ts_min=...&ts_max=...  → 拿區間統計

(function (window, document) {
  "use strict";

  // ========== DOM ==========
  const stationSel     = document.getElementById("stationSelect");
  const archiveSel     = document.getElementById("archiveSelect");
  const loadBtn        = document.getElementById("loadBtn");
  const themeBtn       = document.getElementById("themeBtn");
  const statusBadge    = document.getElementById("statusBadge");
  const canvas         = document.getElementById("chart");
  const cursorOverlay  = document.getElementById("cursorOverlay");
  const cursorRange    = document.getElementById("cursorRange");
  const cursorLeft     = document.getElementById("cursorLeft");
  const cursorRight    = document.getElementById("cursorRight");
  const cursorLeftTime = document.getElementById("cursorLeftTime");
  const cursorRightTime= document.getElementById("cursorRightTime");
  const cursorInfo     = document.getElementById("cursorInfo");
  const cursorInfoRange= document.getElementById("cursorInfoRange");
  const resetCursorBtn = document.getElementById("resetCursorBtn");
  const checkboxesBox  = document.getElementById("curveCheckboxes");
  const allOnBtn       = document.getElementById("allOnBtn");
  const allOffBtn      = document.getElementById("allOffBtn");
  const statsTableBody = document.querySelector("#statsTable tbody");

  // ========== State ==========
  const state = {
    station:       null,
    archiveFilename: null,
    rawRows:       [],   // 從 /api/snapshot/data 拿到的點（已 LTTB）
    chart:         null, // Chart.js instance
    cursorTsLeft:  null, // Date
    cursorTsRight: null, // Date
    dragSide:      null, // 'left' | 'right' | null（正在拖哪條線）
    panStart:      null, // {mouseX, xMin, xMax} 平移起點
    colorMap:      {},   // {fieldKey: color}
    fieldOrder:    [],   // ['t01','t02',...,'t20','v','i','w']
    visibleFields: new Set(), // 顯示中
    statsTimer:    null, // debounce 統計 API
  };

  const TEMP_FIELDS = Array.from({ length: 20 }, (_, i) => `t${String(i + 1).padStart(2, "0")}`);
  const PW_FIELDS = ["v", "i", "w"];
  const FIELD_LABELS = {};
  TEMP_FIELDS.forEach((f) => { FIELD_LABELS[f] = `T${f.slice(1)}`; });
  FIELD_LABELS.v = "V"; FIELD_LABELS.i = "I"; FIELD_LABELS.w = "W";

  // ========== 主色（沿用 CSS 變數後備用，這裡給 Chart.js 用） ==========
  // 溫度線用 20 種區分色；電力 3 條用預設配色
  const TEMP_PALETTE = [
    "#1f77b4","#ff7f0e","#2ca02c","#d62728","#9467bd",
    "#8c564b","#e377c2","#7f7f7f","#bcbd22","#17becf",
    "#393b79","#637939","#8c6d31","#843c39","#7b4173",
    "#3182bd","#31a354","#756bb1","#636363","#e6550d",
  ];
  const PW_PALETTE = { v: "#3182bd", i: "#31a354", w: "#d62728" };

  // ========== 工具 ==========

  function setStatus(text, kind) {
    statusBadge.textContent = text;
    statusBadge.className = "status-badge" + (kind ? " " + kind : "");
  }

  function fmtTs(ts) {
    // ts 是 Date 或 ISO 字串
    const d = ts instanceof Date ? ts : new Date(ts);
    const pad = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}/${pad(d.getMonth() + 1)}/${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  }

  function getThemeColors() {
    const dark = document.body.dataset.theme === "dark";
    return {
      text:  dark ? "#eaf2e0" : "#2a2a1a",
      grid:  dark ? "rgba(255,255,255,0.05)" : "rgba(0,0,0,0.08)",
      surface: dark ? "#2c4521" : "#ffffff",
    };
  }

  // ========== 載入備份清單 ==========

  async function refreshArchiveList() {
    const station = stationSel.value;
    state.station = station;
    archiveSel.innerHTML = '<option value="">載入中…</option>';
    archiveSel.disabled = true;
    loadBtn.disabled = true;
    setStatus(`讀取 ${station} 備份清單…`);

    try {
      const r = await fetch(`/api/snapshot/archives?station=${encodeURIComponent(station)}`);
      const body = await r.json();
      if (!body.ok) throw new Error(body.message || body.error || "unknown");

      const list = body.archives || [];
      if (list.length === 0) {
        archiveSel.innerHTML = '<option value="">（此工位無備份檔）</option>';
        archiveSel.disabled = true;
        loadBtn.disabled = true;
        setStatus(`${station} 無備份檔`, "warn");
        return;
      }
      archiveSel.innerHTML = list.map((a) => {
        const range = a.ts_min && a.ts_max
          ? `（${a.count.toLocaleString()} 筆，${a.ts_min.slice(0, 10)} ~ ${a.ts_max.slice(0, 10)}）`
          : `（空檔，${a.count} 筆）`;
        return `<option value="${a.filename}">${a.filename} ${range}</option>`;
      }).join("");
      archiveSel.disabled = false;
      loadBtn.disabled = false;
      setStatus(`${station} 有 ${list.length} 個備份檔`, "ok");
    } catch (e) {
      console.error("refreshArchiveList failed:", e);
      archiveSel.innerHTML = '<option value="">（讀取失敗）</option>';
      setStatus(`讀取失敗: ${e.message}`, "err");
    }
  }

  // ========== 載入備份資料（畫圖） ==========

  async function loadArchive() {
    const station = stationSel.value;
    const filename = archiveSel.value;
    if (!station || !filename) return;

    state.station = station;
    state.archiveFilename = filename;
    setStatus(`讀取 ${filename}…`);
    loadBtn.disabled = true;

    try {
      const r = await fetch(`/api/snapshot/data?filename=${encodeURIComponent(filename)}&max_points=2000`);
      const body = await r.json();
      if (!body.ok) throw new Error(body.message || body.error || "unknown");
      state.rawRows = body.rows;
      setStatus(`${filename} 共 ${body.original_count.toLocaleString()} 筆（顯示 ${body.count}）`, "ok");

      buildChart();
      buildCheckboxes();
      resetCursorPositions();
      // 載入完成後自動拉一次全段統計
      await refreshStats();
    } catch (e) {
      console.error("loadArchive failed:", e);
      setStatus(`載入失敗: ${e.message}`, "err");
    } finally {
      loadBtn.disabled = false;
    }
  }

  // ========== Chart.js 建圖 ==========

  function buildChart() {
    if (state.chart) {
      state.chart.destroy();
      state.chart = null;
    }
    if (state.rawRows.length === 0) {
      setStatus("備份檔內無資料", "warn");
      return;
    }

    const c = getThemeColors();
    // datasets：每個欄位一條線
    const datasets = [];
    const fields = [...TEMP_FIELDS, ...PW_FIELDS];
    fields.forEach((f, idx) => {
      const color = TEMP_FIELDS.includes(f)
        ? TEMP_PALETTE[idx % TEMP_PALETTE.length]
        : PW_PALETTE[f];
      state.colorMap[f] = color;
      // 把 rows 攤成 {x, y}（Chart.js parsing:false 直接吃這個）
      const points = state.rawRows
        .filter((r) => r[f] !== null && r[f] !== undefined)
        .map((r) => ({ x: new Date(r.ts).getTime(), y: r[f] }));
      datasets.push({
        label: FIELD_LABELS[f] + " " + (PW_FIELDS.includes(f) ? f.toUpperCase() : ""),
        data: points,
        borderColor: color,
        backgroundColor: color + "33",
        borderWidth: 1,
        pointRadius: 0,
        pointHoverRadius: 3,
        tension: 0,
        hidden: false,
        yAxisID: PW_FIELDS.includes(f) ? "yPW" : "yT",
      });
    });
    state.fieldOrder = fields;
    state.visibleFields = new Set(fields);

    const xMin = datasets[0].data[0]?.x;
    const xMax = datasets[0].data[datasets[0].data.length - 1]?.x;

    const ctx = canvas.getContext("2d");
    state.chart = new Chart(ctx, {
      type: "line",
      data: { datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        parsing: false,
        normalized: true,
        interaction: { mode: "nearest", intersect: false },
        plugins: {
          legend: { display: false }, // checkbox + 標題列已承擔
          tooltip: {
            callbacks: {
              title: (items) => fmtTs(items[0].parsed.x),
              label: (item) => `${item.dataset.label}: ${item.parsed.y.toFixed(2)}`,
            },
          },
        },
        scales: {
          x: {
            type: "time",
            min: xMin,
            max: xMax,
            ticks: { color: c.text },
            grid:  { color: c.grid },
            time: {
              tooltipFormat: "yyyy/MM/dd HH:mm:ss",
              displayFormats: {
                second: "HH:mm:ss",
                minute: "MM/dd HH:mm",
                hour:   "MM/dd HH:mm",
                day:    "MM/dd",
              },
            },
          },
          yT: {
            type: "linear",
            position: "left",
            ticks: { color: c.text },
            grid:  { color: c.grid },
            title: { display: true, text: "溫度 (°C)", color: c.text },
          },
          yPW: {
            type: "linear",
            position: "right",
            ticks: { color: c.text },
            grid:  { drawOnChartArea: false },
            title: { display: true, text: "電力 V/A/W", color: c.text },
          },
        },
      },
    });

    bindChartEvents();
  }

  // ========== wheel zoom + drag pan ==========

  function bindChartEvents() {
    // wheel zoom（以游標位置為中心）
    canvas.addEventListener("wheel", onWheelZoom, { passive: false });
    // drag pan（在 chart 空白處拖曳）
    canvas.addEventListener("mousedown", onPanStart);
  }

  function onWheelZoom(e) {
    if (!state.chart) return;
    e.preventDefault();
    const chartArea = state.chart.chartArea;
    if (!chartArea) return;
    const rect = canvas.getBoundingClientRect();
    const mouseX = e.clientX - rect.left;
    // 確認滑鼠在 chart area 內
    if (mouseX < chartArea.left || mouseX > chartArea.right) return;
    const xScale = state.chart.scales.x;
    const xAtCursor = xScale.getValueForPixel(mouseX);
    const factor = e.deltaY > 0 ? 1.25 : 0.8; // 向下滾放大、向上縮小（常見慣例）
    const curMin = xScale.min;
    const curMax = xScale.max;
    const range = curMax - curMin;
    const newRange = range * factor;
    // 保持游標位置不動：cursorX = (mouseX - chartArea.left) / chartArea.width = (xAtCursor - newMin) / newRange
    const ratio = (xAtCursor - curMin) / range;
    let newMin = xAtCursor - newRange * ratio;
    let newMax = newMin + newRange;
    // 不允許超出原始資料範圍
    const origMin = state.chart.options.scales.x.min;
    const origMax = state.chart.options.scales.x.max;
    if (newMin < origMin) { newMin = origMin; newMax = newMin + newRange; }
    if (newMax > origMax) { newMax = origMax; newMin = newMax - newRange; }
    state.chart.options.scales.x.min = newMin;
    state.chart.options.scales.x.max = newMax;
    state.chart.update("none");
    layoutCursorBars();
  }

  function onPanStart(e) {
    if (!state.chart) return;
    // 只在 chartArea 內、且沒點到游標線時啟動
    if (e.target !== canvas) return;
    const xScale = state.chart.scales.x;
    state.panStart = {
      mouseX: e.clientX,
      xMin: xScale.min,
      xMax: xScale.max,
    };
    const onMove = (ev) => {
      if (!state.panStart) return;
      const dx = ev.clientX - state.panStart.mouseX;
      const chartArea = state.chart.chartArea;
      const pxPerMs = (state.panStart.xMax - state.panStart.xMin) / chartArea.width;
      const msShift = -dx * pxPerMs;
      let newMin = state.panStart.xMin + msShift;
      let newMax = state.panStart.xMax + msShift;
      const origMin = state.chart.options.scales.x.min ?? -Infinity;
      const origMax = state.chart.options.scales.x.max ?? Infinity;
      // 邊界檢查
      if (newMin < origMin) { newMin = origMin; newMax = newMin + (state.panStart.xMax - state.panStart.xMin); }
      if (newMax > origMax) { newMax = origMax; newMin = newMax - (state.panStart.xMax - state.panStart.xMin); }
      state.chart.options.scales.x.min = newMin;
      state.chart.options.scales.x.max = newMax;
      state.chart.update("none");
      layoutCursorBars();
    };
    const onUp = () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
      state.panStart = null;
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  }

  // ========== 游標線 (兩條 X-line) ==========

  function resetCursorPositions() {
    if (!state.chart) return;
    const xScale = state.chart.scales.x;
    const range = xScale.max - xScale.min;
    state.cursorTsLeft  = new Date(xScale.min + range * 0.25);
    state.cursorTsRight = new Date(xScale.min + range * 0.75);
    cursorOverlay.classList.add("active");
    cursorRange.hidden = false;
    cursorLeft.hidden = false;
    cursorRight.hidden = false;
    cursorInfo.hidden = false;
    layoutCursorBars();
    refreshStats();
  }

  function layoutCursorBars() {
    if (!state.chart || !state.cursorTsLeft) return;
    const xScale = state.chart.scales.x;
    const chartArea = state.chart.chartArea;
    const leftPx  = xScale.getPixelForValue(state.cursorTsLeft.getTime());
    const rightPx = xScale.getPixelForValue(state.cursorTsRight.getTime());
    const overlayRect = cursorOverlay.getBoundingClientRect();
    const chartRect = canvas.getBoundingClientRect();
    // overlay 對齊 chartArea 的相對位置
    const leftOffset  = leftPx - (chartArea.left - (chartRect.left - overlayRect.left));
    const rightOffset = rightPx - (chartArea.left - (chartRect.left - overlayRect.left));
    cursorLeft.style.left  = leftOffset  + "px";
    cursorRight.style.left = rightOffset + "px";
    cursorRange.style.left  = Math.min(leftOffset, rightOffset) + "px";
    cursorRange.style.width = Math.abs(rightOffset - leftOffset) + "px";
    cursorLeftTime.textContent  = fmtTs(state.cursorTsLeft);
    cursorRightTime.textContent = fmtTs(state.cursorTsRight);
    cursorInfoRange.textContent = `${fmtTs(state.cursorTsLeft)} ~ ${fmtTs(state.cursorTsRight)}`;
  }

  function bindCursorDrag(side, barEl) {
    barEl.addEventListener("mousedown", (e) => {
      e.preventDefault();
      e.stopPropagation();
      state.dragSide = side;
      const onMove = (ev) => {
        if (!state.chart || !state.dragSide) return;
        const chartRect = canvas.getBoundingClientRect();
        const xScale = state.chart.scales.x;
        const chartArea = state.chart.chartArea;
        const px = ev.clientX - chartRect.left;
        // 限制在 chartArea 內
        const clampedPx = Math.max(chartArea.left - chartRect.left, Math.min(chartArea.right - chartRect.left, px));
        const ms = xScale.getValueForPixel(clampedPx + (chartRect.left));
        // ↑ getValueForPixel 吃的是 canvas 座標系內的 px
        const ms2 = xScale.getValueForPixel(ev.clientX - canvas.getBoundingClientRect().left);
        const ts = new Date(ms2);
        if (state.dragSide === "left")  state.cursorTsLeft  = ts;
        if (state.dragSide === "right") state.cursorTsRight = ts;
        // 確保 left <= right
        if (state.cursorTsLeft > state.cursorTsRight) {
          if (state.dragSide === "left")  state.cursorTsLeft  = new Date(state.cursorTsRight);
          if (state.dragSide === "right") state.cursorTsRight = new Date(state.cursorTsLeft);
        }
        layoutCursorBars();
        scheduleStatsRefresh();
      };
      const onUp = () => {
        state.dragSide = null;
        window.removeEventListener("mousemove", onMove);
        window.removeEventListener("mouseup", onUp);
        // 拖完立刻拉一次（不 debounce）
        refreshStats();
      };
      window.addEventListener("mousemove", onMove);
      window.addEventListener("mouseup", onUp);
    });
    // 雙擊重置
    barEl.addEventListener("dblclick", () => {
      resetCursorPositions();
    });
  }

  bindCursorDrag("left", cursorLeft);
  bindCursorDrag("right", cursorRight);

  // ========== 統計 ==========

  function scheduleStatsRefresh() {
    if (state.statsTimer) clearTimeout(state.statsTimer);
    state.statsTimer = setTimeout(refreshStats, 250);
  }

  async function refreshStats() {
    if (!state.archiveFilename || !state.cursorTsLeft || !state.cursorTsRight) return;
    const t1 = state.cursorTsLeft.toISOString().slice(0, 19);
    const t2 = state.cursorTsRight.toISOString().slice(0, 19);
    const url = `/api/snapshot/stats?filename=${encodeURIComponent(state.archiveFilename)}&ts_min=${encodeURIComponent(t1)}&ts_max=${encodeURIComponent(t2)}`;
    try {
      const r = await fetch(url);
      const body = await r.json();
      if (!body.ok) throw new Error(body.message || body.error);
      renderStatsTable(body.stats);
    } catch (e) {
      console.error("refreshStats failed:", e);
    }
  }

  function renderStatsTable(stats) {
    const rows = [];
    TEMP_FIELDS.forEach((f) => {
      if (!state.visibleFields.has(f)) return; // 只列顯示中
      const s = stats[f] || { count: 0, avg: null, min: null, max: null };
      rows.push({ label: FIELD_LABELS[f], field: f, ...s });
    });
    PW_FIELDS.forEach((f) => {
      if (!state.visibleFields.has(f)) return;
      const s = stats[f] || { count: 0, avg: null, min: null, max: null };
      rows.push({ label: FIELD_LABELS[f], field: f, ...s });
    });
    statsTableBody.innerHTML = rows.map((r) => `
      <tr data-field="${r.field}">
        <td>${r.label}</td>
        <td class="num">${r.count.toLocaleString()}</td>
        <td class="num">${r.avg === null ? "—" : r.avg.toFixed(2)}</td>
        <td class="num">${r.max === null ? "—" : r.max.toFixed(2)}</td>
        <td class="num">${r.min === null ? "—" : r.min.toFixed(2)}</td>
      </tr>
    `).join("");
  }

  // ========== Checkbox ==========

  function buildCheckboxes() {
    const all = [...TEMP_FIELDS, ...PW_FIELDS];
    checkboxesBox.innerHTML = all.map((f) => {
      const color = state.colorMap[f] || "#888";
      return `
        <label class="snapshot-cb">
          <input type="checkbox" data-field="${f}" checked>
          <span class="swatch" style="background:${color}"></span>
          <span class="snapshot-cb-label">${FIELD_LABELS[f]}</span>
        </label>
      `;
    }).join("");
    checkboxesBox.querySelectorAll("input[type=checkbox]").forEach((cb) => {
      cb.addEventListener("change", () => {
        const f = cb.dataset.field;
        const dsIdx = state.chart.data.datasets.findIndex((d) => d.label.startsWith(FIELD_LABELS[f]));
        if (dsIdx >= 0) {
          state.chart.data.datasets[dsIdx].hidden = !cb.checked;
          state.chart.update("none");
        }
        if (cb.checked) state.visibleFields.add(f);
        else state.visibleFields.delete(f);
        refreshStats(); // 統計表只列顯示中
      });
    });
  }

  allOnBtn.addEventListener("click", () => {
    checkboxesBox.querySelectorAll("input[type=checkbox]").forEach((cb) => {
      if (!cb.checked) { cb.checked = true; cb.dispatchEvent(new Event("change")); }
    });
  });
  allOffBtn.addEventListener("click", () => {
    checkboxesBox.querySelectorAll("input[type=checkbox]").forEach((cb) => {
      if (cb.checked) { cb.checked = false; cb.dispatchEvent(new Event("change")); }
    });
  });

  // ========== 事件 ==========

  stationSel.addEventListener("change", refreshArchiveList);
  archiveSel.addEventListener("change", () => {
    loadBtn.disabled = !archiveSel.value;
  });
  loadBtn.addEventListener("click", loadArchive);
  resetCursorBtn.addEventListener("click", resetCursorPositions);
  themeBtn.addEventListener("click", () => {
    document.body.dataset.theme = document.body.dataset.theme === "dark" ? "light" : "dark";
    if (state.chart) buildChart(); // 重畫以套用主題色
  });

  // 視窗 resize 時重算游標線位置
  window.addEventListener("resize", () => {
    if (state.chart) layoutCursorBars();
  });

  // 啟動
  refreshArchiveList();
})(window, document);

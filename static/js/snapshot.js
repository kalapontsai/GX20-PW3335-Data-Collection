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
  const pwCanvas       = document.getElementById("pwChart");
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
    chart:         null, // 溫度圖 Chart.js instance
    pwChart:       null, // 電力圖 Chart.js instance（v10.1：拆上下子圖）
    cursorTsLeft:  null, // Date
    cursorTsRight: null, // Date
    dragSide:      null, // 'left' | 'right' | null（正在拖哪條線）
    panStart:      null, // {mouseX, xMin, xMax} 平移起點
    colorMap:      {},   // {fieldKey: color}
    fieldOrder:    [],   // ['t01','t02',...,'t20','v','i','w']
    visibleFields: new Set(), // 顯示中
    statsTimer:    null, // debounce 統計 API
    xMin:          null, // 當前 X 軸範圍（兩個圖共享，wheel/pan 都會更新這裡）
    xMax:          null,
    xMinOrig:      null, // 資料原始範圍（用於 zoom 邊界檢查）
    xMaxOrig:      null,
    // v10.x：備份讀取時、若有 meta 就用裡面的 alias + note 覆蓋預設別名與備註
    activeLabels:  {},   // {field: alias string}（若該 field 沒覆蓋，用 FIELD_LABELS[field]）
    activeNote:    "",   // 備份當下的備註文字（顯示在 snapshot 頁頂部）
  };

  const TEMP_FIELDS = Array.from({ length: 20 }, (_, i) => `t${String(i + 1).padStart(2, "0")}`);
  const PW_FIELDS = ["v", "i", "w"];
  const FIELD_LABELS = {};       // 預設別名（T01..T20 / V / I / W）
  TEMP_FIELDS.forEach((f) => { FIELD_LABELS[f] = `T${f.slice(1)}`; });
  FIELD_LABELS.v = "V"; FIELD_LABELS.i = "I"; FIELD_LABELS.w = "W";

  // v10.x：拿「當下生效」的 label（若 meta 有設定且非空，用 meta 的；否則用預設 FIELD_LABELS）
  function activeLabel(f) {
    const v = state.activeLabels[f];
    if (typeof v === "string" && v.trim()) return v.trim();
    return FIELD_LABELS[f];
  }

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
    // v10.1.2：簡化 statusBadge — 只在 err 顯示訊息，其餘狀態隱藏（不再顯示「讀取中…」「共 N 筆」這類）
    if (kind === "err") {
      statusBadge.textContent = text;
      statusBadge.className = "status-badge err";
      statusBadge.hidden = false;
    } else {
      statusBadge.textContent = "";
      statusBadge.className = "status-badge";
      statusBadge.hidden = true;
    }
  }

  function fmtTs(ts) {
    // ts 是 Date 或 ISO 字串
    const d = ts instanceof Date ? ts : new Date(ts);
    const pad = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}/${pad(d.getMonth() + 1)}/${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  }

  // 跟 fmtTs 一樣用本地時間，但回傳 ISO 風格字串給後端 SQL 字串比對用。
  // 用途：避免 Date.toISOString() 預設用 UTC，導致送給後端的 ts_min/ts_max
  //       跟 DB 內台北時間字串對不上、SQL count=0。
  function fmtTsIso(ts) {
    const d = ts instanceof Date ? ts : new Date(ts);
    const pad = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
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

    console.log("[snapshot] loadArchive START", filename, "archiveSel.value=", archiveSel.value);
    state.station = station;
    state.archiveFilename = filename;
    setStatus(`讀取 ${filename}…`);
    loadBtn.disabled = true;

    try {
      console.log("[snapshot] fetch START", filename);
      const r = await fetch(`/api/snapshot/data?filename=${encodeURIComponent(filename)}&max_points=2000`);
      console.log("[snapshot] fetch DONE", filename, "status=", r.status);
      const body = await r.json();
      console.log("[snapshot] body parsed, meta=", body.meta);
      if (!body.ok) throw new Error(body.message || body.error || "unknown");
      state.rawRows = body.rows;

      // v10.x：套用備份當下的 alias + note （meta 缺失時用空別名 = 退回預設 T01..T20）
      const meta = body.meta;
      state.activeLabels = {};
      state.activeNote = "";
      if (meta && typeof meta === "object") {
        const alias = meta.alias;
        if (Array.isArray(alias)) {
          TEMP_FIELDS.forEach((f, i) => {
            const v = alias[i];
            if (typeof v === "string") state.activeLabels[f] = v;
          });
        }
        if (typeof meta.note === "string") state.activeNote = meta.note;
      }
      // 頂部備註顯示
      const noteEl = document.getElementById("archiveNote");
      console.log("[snapshot] meta=", meta, "activeNote=", JSON.stringify(state.activeNote));
      if (noteEl) {
        if (state.activeNote) {
          noteEl.textContent = state.activeNote;
          noteEl.hidden = false;
        } else {
          noteEl.textContent = "";
          noteEl.hidden = true;
        }
        console.log("[snapshot] noteEl after=", { hidden: noteEl.hidden, text: noteEl.textContent });
      }

      setStatus(`${filename} 共 ${body.original_count.toLocaleString()} 筆（顯示 ${body.count}）`, "ok");

      buildChart();
      buildCheckboxes();
      resetCursorPositions();
      // 載入完成後自動拉一次全段統計
      await refreshStats();
    } catch (e) {
      console.error("[snapshot] loadArchive FAILED:", e);
      setStatus(`載入失敗: ${e.message}`, "err");
    } finally {
      loadBtn.disabled = false;
      console.log("[snapshot] loadArchive END", filename);
    }
  }

  // ========== Chart.js 建圖 ==========

  function buildChart() {
    // 清除舊圖（兩張）
    if (state.chart)   { state.chart.destroy();   state.chart   = null; }
    if (state.pwChart) { state.pwChart.destroy(); state.pwChart = null; }
    if (state.rawRows.length === 0) {
      setStatus("備份檔內無資料", "warn");
      return;
    }

    const c = getThemeColors();
    // 把資料分成溫度 20 條 + 電力 3 條兩群
    const tempDatasets = [];
    const pwDatasets   = [];
    const fields = [...TEMP_FIELDS, ...PW_FIELDS];
    state.colorMap = {};
    fields.forEach((f, idx) => {
      const color = TEMP_FIELDS.includes(f)
        ? TEMP_PALETTE[idx % TEMP_PALETTE.length]
        : PW_PALETTE[f];
      state.colorMap[f] = color;
      const points = state.rawRows
        .filter((r) => r[f] !== null && r[f] !== undefined)
        .map((r) => {
          // r.ts 可能是「YYYY-MM-DDTHH:MM:SS」ISO 字串，也可能是 Unix timestamp（純數字字串）
          // 兩種都安全轉成 ms number。
          let ms;
          if (/^\d+$/.test(r.ts)) ms = parseInt(r.ts, 10) * 1000;
          else ms = new Date(r.ts).getTime();
          return { x: ms, y: r[f] };
        });
      const ds = {
        label: activeLabel(f) + (PW_FIELDS.includes(f) ? " (" + f.toUpperCase() + ")" : ""),
        data: points,
        borderColor: color,
        backgroundColor: color + "33",
        borderWidth: 1,
        pointRadius: 0,
        pointHoverRadius: 3,
        tension: 0,
        hidden: false,
      };
      if (TEMP_FIELDS.includes(f)) tempDatasets.push(ds);
      else                         pwDatasets.push(ds);
    });
    state.fieldOrder = fields;
    state.visibleFields = new Set(fields);

    // 原始 X 軸範圍（從溫度第一筆/最後一筆拿，或 fallback 到電力）
    const refDs = tempDatasets[0] || pwDatasets[0];
    const xMin = refDs.data[0]?.x;
    const xMax = refDs.data[refDs.data.length - 1]?.x;
    state.xMinOrig = xMin;
    state.xMaxOrig = xMax;
    state.xMin = xMin;
    state.xMax = xMax;

    const xScaleConfig = (curMin, curMax) => ({
      type: "time",
      min: curMin,
      max: curMax,
      // v10.1.4：參考 index.html 主頁 ticks 設定，加 autoSkip 避免 tick 過多
      ticks: { color: c.text, maxRotation: 0, autoSkipPadding: 20, source: "auto" },
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
    });

    // ---- 上圖：溫度 ----
    const ctxT = canvas.getContext("2d");
    state.chart = new Chart(ctxT, {
      type: "line",
      data: { datasets: tempDatasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        parsing: false,
        normalized: true,
        interaction: { mode: "nearest", intersect: false },
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              title: (items) => fmtTs(items[0].parsed.x),
              label: (item) => `${item.dataset.label}: ${item.parsed.y.toFixed(2)}`,
            },
          },
        },
        scales: {
          x: xScaleConfig(xMin, xMax),
          y: {
            type: "linear",
            position: "left",
            ticks: { color: c.text },
            grid:  { color: c.grid },
            title: { display: true, text: "溫度 (°C)", color: c.text },
          },
        },
      },
    });

    // ---- 下圖：電力（V/A/W 三軸，左 I/A、左 offset W、右 V） ----
    if (pwDatasets.length > 0) {
      const ctxP = pwCanvas.getContext("2d");
      state.pwChart = new Chart(ctxP, {
        type: "line",
        data: { datasets: pwDatasets },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          animation: false,
          parsing: false,
          normalized: true,
          interaction: { mode: "nearest", intersect: false },
          plugins: {
            legend: { display: false },
            tooltip: {
              callbacks: {
                title: (items) => fmtTs(items[0].parsed.x),
                label: (item) => `${item.dataset.label}: ${item.parsed.y.toFixed(2)}`,
              },
            },
          },
          scales: {
            x: xScaleConfig(xMin, xMax),
            yI: {
              type: "linear",
              position: "left",
              ticks: { color: c.text },
              grid:  { color: c.grid },
              title: { display: true, text: "I (A)", color: c.text },
            },
            yW: {
              type: "linear",
              position: "left",
              offset: true,
              ticks: { color: c.text },
              grid:  { drawOnChartArea: false },
              title: { display: true, text: "W (W)", color: c.text },
            },
            yV: {
              type: "linear",
              position: "right",
              ticks: { color: c.text },
              grid:  { drawOnChartArea: false },
              title: { display: true, text: "V (V)", color: c.text },
            },
          },
        },
      });
    } else {
      pwCanvas.parentElement.style.display = "none";
    }

    bindChartEvents();
  }

  // ========== wheel zoom + drag pan（兩個 chart 同步） ==========

  function bindChartEvents() {
    // wheel zoom 註冊在 window，內部檢查 event target 是否在任一 chart canvas 上
    window.addEventListener("wheel", onWheelZoom, { passive: false });
    // drag pan 同樣 window 接收，裡面判斷 target 屬於哪個 canvas 或 overlay
    window.addEventListener("mousedown", onPanStart);
  }

  /**
   * 在兩個 chart 之間共用 X 軸：只改一份範圍，兩個 chart 同步重繪。
   */
  function setXRange(newMin, newMax) {
    state.xMin = newMin;
    state.xMax = newMax;
    if (state.chart) {
      state.chart.options.scales.x.min = newMin;
      state.chart.options.scales.x.max = newMax;
      state.chart.update("none");
    }
    if (state.pwChart) {
      state.pwChart.options.scales.x.min = newMin;
      state.pwChart.options.scales.x.max = newMax;
      state.pwChart.update("none");
    }
    layoutCursorBars();
  }

  function onWheelZoom(e) {
    if (!state.chart) return;
    const target = e.target;
    // 找到最近 chart pane，判斷是溫度還是電力
    const pane = target.closest && target.closest('.chart-temp, .chart-pw');
    if (!pane) return;
    const isTemp = pane.classList.contains('chart-temp');
    const isPw   = pane.classList.contains('chart-pw');
    const activeChart = isTemp ? state.chart : (isPw ? state.pwChart : null);
    if (!activeChart) return;
    const chartArea = activeChart.chartArea;
    if (!chartArea) return;
    const activeCanvas = isTemp ? canvas : pwCanvas;
    const rect = activeCanvas.getBoundingClientRect();
    const mouseX = e.clientX - rect.left;
    if (mouseX < chartArea.left || mouseX > chartArea.right) return;
    e.preventDefault();
    const xScale = activeChart.scales.x;
    const xAtCursor = xScale.getValueForPixel(mouseX);
    const factor = e.deltaY > 0 ? 0.8 : 1.25; // 向下滾放大、向上縮小（macOS/網頁地圖慣例）
    const curMin = xScale.min;
    const curMax = xScale.max;
    const range = curMax - curMin;
    const newRange = range * factor;
    const ratio = (xAtCursor - curMin) / range;
    let newMin = xAtCursor - newRange * ratio;
    let newMax = newMin + newRange;
    // 不允許超出原始資料範圍
    const origMin = state.xMinOrig;
    const origMax = state.xMaxOrig;
    if (newMin < origMin) { newMin = origMin; newMax = newMin + newRange; }
    if (newMax > origMax) { newMax = origMax; newMin = newMax - newRange; }
    setXRange(newMin, newMax);
  }

  function onPanStart(e) {
    if (!state.chart) return;
    const target = e.target;
    // 找到最近 chart pane（.chart-temp / .chart-pw 任一）
    const pane = target.closest && target.closest('.chart-temp, .chart-pw');
    if (!pane) return;
    // 若是 cursor bar/overlay 內的元素，交給 cursor 拖曳（bindCursorDrag 內 stopPropagation，但保險再判一次）
    if (cursorOverlay && cursorOverlay.contains && cursorOverlay.contains(target)) return;
    const xScale = state.chart.scales.x;
    state.panStart = {
      mouseX: e.clientX,
      xMin: state.xMin,
      xMax: state.xMax,
    };
    const onMove = (ev) => {
      if (!state.panStart) return;
      const dx = ev.clientX - state.panStart.mouseX;
      const chartArea = state.chart.chartArea;
      const pxPerMs = (state.panStart.xMax - state.panStart.xMin) / chartArea.width;
      const msShift = -dx * pxPerMs;
      let newMin = state.panStart.xMin + msShift;
      let newMax = state.panStart.xMax + msShift;
      const origMin = state.xMinOrig;
      const origMax = state.xMaxOrig;
      const span = state.panStart.xMax - state.panStart.xMin;
      if (newMin < origMin) { newMin = origMin; newMax = newMin + span; }
      if (newMax > origMax) { newMax = origMax; newMin = newMax - span; }
      setXRange(newMin, newMax);
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
    const range = state.xMax - state.xMin;
    state.cursorTsLeft  = new Date(state.xMin + range * 0.25);
    state.cursorTsRight = new Date(state.xMin + range * 0.75);
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
    const leftPx  = xScale.getPixelForValue(state.cursorTsLeft.getTime());
    const rightPx = xScale.getPixelForValue(state.cursorTsRight.getTime());
    // Chart.js getPixelForValue(value) 回傳 canvas 相對 px
    // （scale._startPixel = chartArea.left，chartArea.left 是 canvas 內 padding.left = canvas 相對）
    // CSS .cursor-overlay { inset: 8px }：游標線 CSS left 是相對於 overlay 內容區
    // 轉換：leftOffset = leftPx - 8 = canvas 相對 px - overlay padding
    // v10.1.4：原算法扣 chartArea.left 讓游標線偏左 (Y軸寬度 - padding) px
    // 參考主頁 main.js line 1453：leftCss = (chartArea.left - padding) + (leftPx - chartArea.left)
    //                            = leftPx - padding ← 證實 leftPx 是 canvas 相對 px
    const overlayPadding = 8;
    const leftOffset  = leftPx - overlayPadding;
    const rightOffset = rightPx - overlayPadding;
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
        // Chart.js getValueForPixel(px) 期望 px 是 canvas 相對 px（chartArea.left 是 canvas 內 padding，非 viewport 座標）
        // 參考主頁 main.js line 1501：const ts = xScale.getValueForPixel(xInChart);
        //                     xInChart = e.clientX - rect.left
        // v10.1.4：與主頁一致，不扣 Y 軸寬度
        // 限制在 chartArea 範圍內（避免拖到 Y 軸上還繼續觸發）
        const canvasPx = ev.clientX - chartRect.left;
        const clampedPx = Math.max(chartArea.left, Math.min(chartArea.right, canvasPx));
        const ms = xScale.getValueForPixel(clampedPx);
        const ts = new Date(ms);
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
    // 用本地時間字串送出 — 不要用 toISOString()（永遠 UTC，會跟 DB 內台北時間對不上，count=0）
    const t1 = fmtTsIso(state.cursorTsLeft);
    const t2 = fmtTsIso(state.cursorTsRight);
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
      rows.push({ label: activeLabel(f), field: f, ...s });
    });
    PW_FIELDS.forEach((f) => {
      if (!state.visibleFields.has(f)) return;
      const s = stats[f] || { count: 0, avg: null, min: null, max: null };
      rows.push({ label: activeLabel(f), field: f, ...s });
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
          <span class="snapshot-cb-label">${activeLabel(f)}</span>
        </label>
      `;
    }).join("");
    checkboxesBox.querySelectorAll("input[type=checkbox]").forEach((cb) => {
      cb.addEventListener("change", () => {
        const f = cb.dataset.field;
        // 溫度欄位看 state.chart，電力欄位看 state.pwChart
        const owner = PW_FIELDS.includes(f) ? state.pwChart : state.chart;
        if (owner) {
          const dsIdx = owner.data.datasets.findIndex((d) => d.label.startsWith(activeLabel(f)));
          if (dsIdx >= 0) {
            owner.data.datasets[dsIdx].hidden = !cb.checked;
            owner.update("none");
          }
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

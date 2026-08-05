// chart-utils.js — 共用 chart 工具（v1）
//
// 目的：
//   1. 抽 chart init / Y 軸主題色 / cursor overlay 共用 API
//   2. 讓 index.html (main.js) 跟 calculator.html (calculator.js) 共用
//   3. 不破壞既有 index.html 行為（main.js 仍能直接呼叫）
//
// 對外 API（window.ChartUtils）：
//   chartColors()                       → { text, textStrong, grid, bg }
//   buildLineChart(canvas, opts)       → Chart 實例（time scale + 線圖 dataset）
//   createCursorOverlay(opts)          → { overlay, barLeft, barRight, range, timeLeft, timeRight,
//                                           setPositions(tsL, tsR), resetPositions(span=1/3,2/3),
//                                           bindDrag(onChange), formatTs(d) }
//   roundHalfUp(n, digits=0)           → number（與 Python round 一致，5 永遠進位）
//
// v1 範圍（index.html 既有功能）：
//   - chart.init 設定：time scale + line dataset + Y 軸左側 + 主題色
//   - cursor overlay：兩條可拖曳 line + 範圍 highlight
//   - 拖曳行為：clamp 到 chartArea 內、左右互不超越、拖曳中即時更新位置
//
// 與 main.js 的差異：
//   - 不負責 socket 即時推播（main.js 自己處理 onNewSample）
//   - 不負責右側「最新讀值」表格（main.js 自己處理 updateReadoutTable）
//   - 不負責電力圖表（v7 pwChart 留在 main.js 內，未共用）
//
// chart-utils.js 內所有函式都設計成「可單獨呼叫」，
//   對既有 main.js 的零侵入改寫（之後 Phase 1B 才會把 main.js 改成呼叫這裡）。
//
// 註：v1 不做整體 Y 軸右佔位（v8.2 對齊電力圖的那段），
//   因為 calculator.html 不需要對齊電力圖。留給之後 index.html 再決定是否要遷移。

(function () {
  'use strict';

  /**
   * 從當前 body 主題取圖表顏色。
   * 對齊 main.js 既有的 chartColors() 行為。
   */
  function chartColors() {
    const cs = getComputedStyle(document.body);
    return {
      text:       cs.getPropertyValue('--text-dim').trim() || '#888',
      textStrong: cs.getPropertyValue('--text').trim()      || '#fff',
      grid:       cs.getPropertyValue('--grid').trim()      || 'rgba(0,0,0,0.1)',
      bg:         cs.getPropertyValue('--surface').trim()   || '#fff',
    };
  }

  /**
   * 建立一個 line chart 實例（time scale + 多 dataset）。
   *
   * @param {HTMLCanvasElement} canvas
   * @param {Object} opts
   * @param {Array<{label:string, color:string, hidden?:boolean}>} opts.datasets
   *        圖例設定；實際 data 由呼叫端透過 chart.data.datasets[i].data 餵入
   * @param {Object} [opts.xScale]   time scale 設定（會跟 chart.options.scales.x 合併）
   *        { min, max, tooltipFormat, displayFormats, ticks.color, grid.color }
   * @param {Object} [opts.yScale]   Y 軸設定（會跟 chart.options.scales.y 合併）
   *        { min, max, title }
   * @returns {Chart}
   */
  function buildLineChart(canvas, opts) {
    if (!canvas) throw new Error('buildLineChart: canvas is required');
    opts = opts || {};
    const c = chartColors();

    const datasets = (opts.datasets || []).map((d) => ({
      label: d.label,
      data: [],
      borderColor: d.color,
      backgroundColor: d.color,
      borderWidth: 0.9,
      pointRadius: 0.9,
      tension: 0.15,
      yAxisID: 'y',
      hidden: !!d.hidden,
      // pointIndex 留給呼叫端用（calculator / main 都靠這個找對應 channel）
      pointIndex: d.pointIndex,
    }));

    const xCfg = Object.assign({
      type: 'time',
      time: {
        tooltipFormat: 'yyyy-MM-dd HH:mm:ss',
        displayFormats: { minute: 'HH:mm', hour: 'MM-dd HH:mm', day: 'MM-dd' },
      },
      ticks: { color: c.text, maxRotation: 0, autoSkipPadding: 20, source: 'auto' },
      grid:  { color: c.grid },
    }, opts.xScale || {});

    const yCfg = Object.assign({
      type: 'linear',
      position: 'left',
      ticks: { color: c.text },
      grid:  { color: c.grid },
      title: { display: true, text: opts.yTitle || '溫度 (°C)', color: c.text },
    }, opts.yScale || {});

    return new Chart(canvas.getContext('2d'), {
      type: 'line',
      data: { datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        parsing: false,
        normalized: true,
        interaction: { mode: 'nearest', intersect: false },
        plugins: {
          legend: { display: false },
          tooltip: { callbacks: { label: (ctx) => `${ctx.dataset.label}: ${ctx.parsed.y}` } },
        },
        scales: {
          x: xCfg,
          y: yCfg,
        },
      },
    });
  }

  // ----------------------
  // Cursor overlay（兩條 X-line + range highlight）
  // ----------------------

  /**
   * 格式化時間戳為 MM/DD HH:MM:SS。對齊 main.js 既有的 _fmtTs 行為。
   */
  function formatTs(d) {
    if (!d) return '—';
    const p = (n) => String(n).padStart(2, '0');
    return `${p(d.getMonth() + 1)}/${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
  }

  /**
   * 建立 cursor overlay DOM，並綁定拖曳行為。
   *
   * @param {Object} opts
   * @param {Chart}        opts.chart       Chart.js 實例（已建好）
   * @param {HTMLElement}  opts.overlay     cursorOverlay 容器（內含 barLeft / barRight / range / timeLeft / timeRight）
   * @param {Function}     opts.onChange    (tsLeft:Date, tsRight:Date) => void  拖曳中 / setPositions 時呼叫
   * @returns {Object}   cursor controller
   *   - tsLeft, tsRight
   *   - setPositions(tsL, tsR)
   *   - resetPositions(fracL=1/3, fracR=2/3)
   *   - show() / hide()
   */
  function createCursorOverlay(opts) {
    if (!opts || !opts.chart || !opts.overlay) {
      throw new Error('createCursorOverlay: chart and overlay are required');
    }
    const chart = opts.chart;
    const overlay = opts.overlay;
    const onChange = opts.onChange || function () {};

    const barLeft  = overlay.querySelector('.cursor-bar[data-side="left"]')  || overlay.querySelector('#cursorLeft');
    const barRight = overlay.querySelector('.cursor-bar[data-side="right"]') || overlay.querySelector('#cursorRight');
    const range    = overlay.querySelector('.cursor-range')                   || overlay.querySelector('#cursorRange');
    const timeLeft  = overlay.querySelector('#cursorLeftTime');
    const timeRight = overlay.querySelector('#cursorRightTime');

    const state = {
      tsLeft: null,
      tsRight: null,
      enabled: false,  // 跟 main.js 的 cursorState.mode 對齊：true = 量測模式
    };

    function clampToChart(xInChart) {
      const chartArea = chart.chartArea;
      if (!chartArea) return null;
      if (xInChart < chartArea.left)  return chartArea.left;
      if (xInChart > chartArea.right) return chartArea.right;
      return xInChart;
    }

    function setPositions(tsL, tsR) {
      state.tsLeft = tsL instanceof Date ? tsL : new Date(tsL);
      state.tsRight = tsR instanceof Date ? tsR : new Date(tsR);
      layout();
      onChange(state.tsLeft, state.tsRight);
    }

    function resetPositions(fracL, fracR) {
      if (fracL == null) fracL = 1 / 3;
      if (fracR == null) fracR = 2 / 3;
      const xScale = chart.scales.x;
      if (!xScale) return;
      const min = xScale.min;
      const max = xScale.max;
      if (min == null || max == null || max <= min) return;
      const span = max - min;
      setPositions(new Date(min + span * fracL), new Date(min + span * fracR));
    }

    function layout() {
      const xScale = chart.scales.x;
      const chartArea = chart.chartArea;
      if (!xScale || !chartArea || !state.tsLeft || !state.tsRight) return;

      // overlay 跟 canvas 同 padding (8px)，chartArea.left/right 是 canvas 內部座標。
      const padding = 8;
      const leftPx  = xScale.getPixelForValue(state.tsLeft.getTime());
      const rightPx = xScale.getPixelForValue(state.tsRight.getTime());
      const leftCss  = (chartArea.left - padding) + (leftPx  - chartArea.left) + 'px';
      const rightCss = (chartArea.left - padding) + (rightPx - chartArea.left) + 'px';

      if (barLeft)  barLeft.style.left  = leftCss;
      if (barRight) barRight.style.left = rightCss;
      if (range) {
        range.style.left = leftCss;
        range.style.width = (rightPx - leftPx) + 'px';
      }
      if (timeLeft)  timeLeft.textContent  = formatTs(state.tsLeft);
      if (timeRight) timeRight.textContent = formatTs(state.tsRight);
    }

    function bindDrag() {
      if (!barLeft || !barRight) return;

      function onDown(e) {
        if (!state.enabled) return;
        dragging = true;
        el.setPointerCapture && el.setPointerCapture(e.pointerId != null ? e.pointerId : 0);
        e.preventDefault();
      }

      function onMove(e) {
        if (!dragging) return;
        const xScale = chart.scales.x;
        const chartArea = chart.chartArea;
        if (!xScale || !chartArea) return;
        const rect = chart.canvas.getBoundingClientRect();
        const xInChart = e.clientX - rect.left;
        const clamped = clampToChart(xInChart);
        if (clamped == null) return;
        const ts = xScale.getValueForPixel(clamped);
        const newTs = new Date(ts);
        if (el === barLeft) {
          if (state.tsRight && newTs >= state.tsRight) return;
          state.tsLeft = newTs;
        } else {
          if (state.tsLeft && newTs <= state.tsLeft) return;
          state.tsRight = newTs;
        }
        layout();
        onChange(state.tsLeft, state.tsRight);
      }

      function onUp() {
        dragging = false;
      }

      let dragging = false;
      let el = null;

      // 對 left / right 各綁一次（用 closure 區分）
      [barLeft, barRight].forEach((bar) => {
        if (!bar) return;
        const localOnDown = (e) => { if (state.enabled) { el = bar; onDown(e); } };
        const localOnMove = (e) => { if (el === bar) onMove(e); };
        const localOnUp   = (e) => { if (el === bar) { onUp(e); el = null; } };
        bar.addEventListener('mousedown', localOnDown);
        bar.addEventListener('touchstart', localOnDown, { passive: false });
        window.addEventListener('mousemove', localOnMove);
        window.addEventListener('touchmove', localOnMove, { passive: false });
        window.addEventListener('mouseup', localOnUp);
        window.addEventListener('touchend', localOnUp);
      });
    }

    function show() {
      state.enabled = true;
      overlay.classList.add('active');
      if (barLeft)  barLeft.hidden  = false;
      if (barRight) barRight.hidden = false;
      if (range)    range.hidden    = false;
    }

    function hide() {
      state.enabled = false;
      overlay.classList.remove('active');
      if (barLeft)  barLeft.hidden  = true;
      if (barRight) barRight.hidden = true;
      if (range)    range.hidden    = true;
    }

    bindDrag();

    return {
      get tsLeft()  { return state.tsLeft; },
      get tsRight() { return state.tsRight; },
      get enabled() { return state.enabled; },
      setPositions,
      resetPositions,
      layout,
      show,
      hide,
    };
  }

  // ----------------------
  // 數值工具（與 Python round 一致 — half-up）
  // ----------------------

  /**
   * Round half-up：5 永遠進位。
   * 對齊 MEMORY 政策（CSV / 報表 四捨五入 = half-up）。
   * 跟 Python 的 round() 結果一致（Python 也是 banker's，但 ROUND_HALF_UP 一致）。
   *
   * @param {number} n
   * @param {number} digits  小數位（0 = 整數）
   * @returns {number}
   */
  function roundHalfUp(n, digits) {
    if (n === null || n === undefined || Number.isNaN(n)) return n;
    if (digits == null) digits = 0;
    const sign = n < 0 ? -1 : 1;
    const abs = Math.abs(n);
    const m = Math.pow(10, digits);
    // floor(abs * m + 0.5) / m  → 半進位；負號最後乘回
    return sign * (Math.floor(abs * m + 0.5) / m);
  }

  // ----------------------
  // 對外暴露
  // ----------------------

  window.ChartUtils = {
    chartColors,
    buildLineChart,
    createCursorOverlay,
    roundHalfUp,
    formatTs,
  };
})();

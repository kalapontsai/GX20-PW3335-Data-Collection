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
//                                           setPositions(tsL, tsR), setFixedSpanMinutes(min|null),
//                                           resetPositions(span=1/3,2/3),
//                                           bindDrag(onChange), formatTs(d) }
//   另支援 opts.fixedSpanMinutes：傳入後 cursor 變成「固定寬度窗口」：
//     - 拖任一條 X-line（或中間 highlight range）都會同步移動起訖，跨度鎖在 fixedSpanMinutes
//     - setPositions(L, R) 會以 L 為錨點，自動把 R 設為 L + span（並 clamp 到 chart 範圍）
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
   * @param {number}     [opts.fixedSpanMinutes]  啟用「固定寬度窗口」模式（單位：分鐘）。
   *                     傳入後拖任一條 X-line（或中間 highlight range）都會同步平移整段窗口，
   *                     起訖永遠相隔 fixedSpanMinutes。setPositions(L, R) 也會以 L 為錨點強制設為固定寬度。
   * @returns {Object}   cursor controller
   *   - tsLeft, tsRight
   *   - setPositions(tsL, tsR)
   *   - setFixedSpanMinutes(minutes | null)  動態切換「固定寬度窗口」模式（傳 null = 恢復預設各自移動）
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

    // 固定窗口模式：null = 各自移動（預設，向後相容 index.html）
    //              數字 = 跨度鎖在 fixedSpanMinutes，拖曳會同步平移
    const fixedSpanMs = (typeof opts.fixedSpanMinutes === 'number' && opts.fixedSpanMinutes > 0)
      ? Math.round(opts.fixedSpanMinutes * 60 * 1000)
      : null;

    const state = {
      tsLeft: null,
      tsRight: null,
      enabled: false,  // 跟 main.js 的 cursorState.mode 對齊：true = 量測模式
      fixedSpanMs,
      // 拖 range（中段）平移時記錄起點，避免累積誤差
      dragOriginCenterMs: null,
    };

    function clampToChart(xInChart) {
      const chartArea = chart.chartArea;
      if (!chartArea) return null;
      if (xInChart < chartArea.left)  return chartArea.left;
      if (xInChart > chartArea.right) return chartArea.right;
      return xInChart;
    }

    /**
     * 把 [L, R] 窗口 clamp 到 chart 範圍內，必要時縮短窗口以容納資料。
     * 若資料長度 < 窗口寬度，會把窗口縮成跟資料一樣大（左 = min, 右 = max）。
     */
    function clampWindowToChart(L, R) {
      const xScale = chart.scales.x;
      if (!xScale || xScale.min == null || xScale.max == null) return { L, R };
      const min = xScale.min, max = xScale.max;
      let newL = L.getTime(), newR = R.getTime();
      let span = newR - newL;
      // 資料不足 → 直接吃完整段
      if (max - min <= span) {
        return { L: new Date(min), R: new Date(max) };
      }
      // 左邊超出 → 把窗口往左推（保留 span）
      if (newL < min) { newL = min; newR = newL + span; }
      // 右邊超出 → 把窗口往右拉（保留 span）
      if (newR > max) { newR = max; newL = newR - span; }
      return { L: new Date(newL), R: new Date(newR) };
    }

    function setPositions(tsL, tsR) {
      // 固定窗口模式：以 tsL 為錨點，自動補上固定寬度；再 clamp 到 chart 範圍
      if (state.fixedSpanMs != null && tsL != null && tsR != null) {
        const L = tsL instanceof Date ? tsL : new Date(tsL);
        let R = tsR instanceof Date ? tsR : new Date(tsR);
        // 強制寬度（以 L 為錨點）
        R = new Date(L.getTime() + state.fixedSpanMs);
        const clamped = clampWindowToChart(L, R);
        tsL = clamped.L;
        tsR = clamped.R;
      }
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
        // range（中段）拖曳用：記下拖曳起點的「窗口中點」時間戳，
        // 後續 onMove 用 delta = newTs - origin 來平移整段窗口
        if (el === barLeft) dragOriginLeftMs = state.tsLeft ? state.tsLeft.getTime() : null;
        else if (el === barRight) dragOriginRightMs = state.tsRight ? state.tsRight.getTime() : null;
        else if (el === range) dragOriginCenterMs = (state.tsLeft && state.tsRight)
          ? (state.tsLeft.getTime() + state.tsRight.getTime()) / 2
          : null;
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

        if (state.fixedSpanMs != null) {
          // === 固定窗口模式：左 / 右 / 中 三種拖法都同步平移 ===
          if (el === barLeft) {
            // 以「新的左邊」為錨點
            const newL = newTs;
            const newR = new Date(newL.getTime() + state.fixedSpanMs);
            const c = clampWindowToChart(newL, newR);
            state.tsLeft = c.L;
            state.tsRight = c.R;
          } else if (el === barRight) {
            // 以「新的右邊」為錨點
            const newR = newTs;
            const newL = new Date(newR.getTime() - state.fixedSpanMs);
            const c = clampWindowToChart(newL, newR);
            state.tsLeft = c.L;
            state.tsRight = c.R;
          } else if (el === range && dragOriginCenterMs != null) {
            // 以中點 delta 平移整段窗口
            const delta = newTs.getTime() - dragOriginCenterMs;
            if (delta !== 0) {
              let newL = new Date(state.tsLeft.getTime() + delta);
              let newR = new Date(state.tsRight.getTime() + delta);
              const c = clampWindowToChart(newL, newR);
              state.tsLeft = c.L;
              state.tsRight = c.R;
              dragOriginCenterMs = (c.L.getTime() + c.R.getTime()) / 2;
            }
          }
        } else {
          // === 預設模式：左 / 右 各自獨立移動 ===
          if (el === barLeft) {
            if (state.tsRight && newTs >= state.tsRight) return;
            state.tsLeft = newTs;
          } else if (el === barRight) {
            if (state.tsLeft && newTs <= state.tsLeft) return;
            state.tsRight = newTs;
          }
        }
        layout();
        onChange(state.tsLeft, state.tsRight);
      }

      function onUp() {
        dragging = false;
        el = null;
        dragOriginCenterMs = null;
        dragOriginLeftMs = null;
        dragOriginRightMs = null;
      }

      let dragging = false;
      let el = null;
      let dragOriginCenterMs = null;
      let dragOriginLeftMs = null;
      let dragOriginRightMs = null;

      // 對 left / right 各綁一次（用 closure 區分）
      [barLeft, barRight].forEach((bar) => {
        if (!bar) return;
        const localOnDown = (e) => { if (state.enabled) { el = bar; onDown(e); } };
        const localOnMove = (e) => { if (el === bar) onMove(e); };
        const localOnUp   = (e) => { if (el === bar) onUp(e); };
        bar.addEventListener('mousedown', localOnDown);
        bar.addEventListener('touchstart', localOnDown, { passive: false });
        window.addEventListener('mousemove', localOnMove);
        window.addEventListener('touchmove', localOnMove, { passive: false });
        window.addEventListener('mouseup', localOnUp);
        window.addEventListener('touchend', localOnUp);
      });

      // 中段 highlight range 拖曳（永遠綁定 handler，僅在固定窗口模式生效）：
      //   拖中段可以平移整段窗口，比分別拖兩條 X-line 直覺
      //   「僅固定窗口模式生效」是在 onMove 內判斷 fixedSpanMs；
      //   這樣可以在 runtime 切換模式而不必 rebind。
      if (range) {
        applyRangeCursor();
        const localOnDown = (e) => {
          if (!state.enabled || state.fixedSpanMs == null) return;
          el = range;
          range.style.cursor = 'grabbing';
          onDown(e);
        };
        const localOnMove = (e) => { if (el === range) onMove(e); };
        const localOnUp   = (e) => { if (el === range) { applyRangeCursor(); onUp(e); } };
        range.addEventListener('mousedown', localOnDown);
        range.addEventListener('touchstart', localOnDown, { passive: false });
        window.addEventListener('mousemove', localOnMove);
        window.addEventListener('touchmove', localOnMove, { passive: false });
        window.addEventListener('mouseup', localOnUp);
        window.addEventListener('touchend', localOnUp);
      }
    }

    // 根據目前模式更新 range 的滑鼠游標：固定模式 = grab，預設模式 = default
    function applyRangeCursor() {
      if (!range) return;
      range.style.cursor = (state.fixedSpanMs != null) ? 'grab' : '';
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

    /**
     * 動態切換「固定寬度窗口」模式。
     *   minutes == null 或 <= 0  → 恢復預設「左 / 右各自獨立移動」（並還原 range 的游標）
     *   minutes > 0              → 啟用固定寬度，並把現有窗口重新錨在 tsLeft，強制 tsRight = tsLeft + span
     */
    function setFixedSpanMinutes(minutes) {
      if (minutes == null || !(minutes > 0)) {
        state.fixedSpanMs = null;
        applyRangeCursor();
        return;
      }
      state.fixedSpanMs = Math.round(minutes * 60 * 1000);
      // 切回固定模式：以 tsLeft 為錨點重新設定（保留使用者目前位置，只鎖寬度）
      if (state.tsLeft && state.tsRight) {
        const newR = new Date(state.tsLeft.getTime() + state.fixedSpanMs);
        const c = clampWindowToChart(state.tsLeft, newR);
        state.tsLeft = c.L;
        state.tsRight = c.R;
        layout();
        onChange(state.tsLeft, state.tsRight);
      }
      applyRangeCursor();
    }

    return {
      get tsLeft()  { return state.tsLeft; },
      get tsRight() { return state.tsRight; },
      get enabled() { return state.enabled; },
      get fixedSpanMinutes() { return state.fixedSpanMs == null ? null : state.fixedSpanMs / 60000; },
      setPositions,
      setFixedSpanMinutes,
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

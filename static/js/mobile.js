// mobile.js — v10.3 GX20 行動簡式頁
//
// 設計：完全獨立、不引用 storage.js / main.js。
// 資料來源：
//   - 設定（別名、啟用）：GET /api/settings（一次）
//   - 讀值：socket.io "new_sample" 事件（與主頁同源）
//   - 首頁 / 切工位 fallback：GET /api/latest/<station>
//
// 為什麼不輪詢 /api/latest：socket 已經 10s 推一次，省頻寬。

(function () {
  "use strict";

  const POINTS = 20;
  const STATIONS = [];   // 由模板 render 的 <select> 自動收集
  let currentStation = null;
  let settings = null;   // {ch_alias, ch_visibility, ...}
  let channelNums = {};  // {工位1: ['0001', ...20個]}

  // ---------- DOM ----------
  const stationSel = document.getElementById("stationSelect");
  const tbody      = document.getElementById("readoutBody");
  const connDot    = document.getElementById("connDot");
  const connText   = document.getElementById("connText");
  const lastTsEl   = document.getElementById("lastTs");

  // ---------- 工具 ----------
  function fmtTemp(v) {
    // 與主頁一致：None → "—"，有值 → 一位小數
    if (v === null || v === undefined) return "—";
    if (typeof v !== "number" || !isFinite(v)) return "—";
    // 半進位（half-up）— 用 toFixed 對齊主頁 CSV 行為
    // 注意：IEEE 754 上 x.5 不保證總進位，但這頁面只顯示、無 CSV 輸出，視覺一致性優先
    return v.toFixed(1);
  }

  function setConn(connected, msg) {
    if (connected) {
      connDot.classList.remove("off");
      connDot.classList.add("on");
      connText.textContent = "已連線";
    } else {
      connDot.classList.remove("on");
      connDot.classList.add("off");
      connText.textContent = msg || "連線中";
    }
  }

  function updateLastTs(ts) {
    if (!ts) { lastTsEl.textContent = "—"; return; }
    // ts 為 ISO 字串，取 HH:MM:SS（行動版只關心時間，不顯示日期）
    const m = String(ts).match(/T(\d{2}:\d{2}:\d{2})/);
    lastTsEl.textContent = m ? m[1] : ts;
  }

  // ---------- 表格 render ----------
  function buildRows() {
    tbody.innerHTML = "";
    if (!settings || !currentStation) return;
    const vis = settings.ch_visibility[currentStation] || [];
    const alias = settings.ch_alias[currentStation] || [];
    const nums = channelNums[currentStation] || [];

    for (let i = 0; i < POINTS; i++) {
      const tr = document.createElement("tr");
      tr.dataset.idx = String(i);

      // v10.3 (圖例對齊)：停用頻道不再淡化樣式 — 圖例所有列都是同樣 style
      // ch_visibility 仍載入給未來使用，但視覺上不區分

      const tdName = document.createElement("td");
      tdName.className = "ch-name";
      // 名稱優先順序：alias → 硬體 channel number → Ch{idx+1}
      const a = (alias[i] || "").trim();
      const label = a || (nums[i] || `Ch${i + 1}`);
      tdName.textContent = label;

      const tdVal = document.createElement("td");
      tdVal.className = "ch-val none";
      tdVal.textContent = "—";
      tdVal.dataset.idx = String(i);

      tr.appendChild(tdName);
      tr.appendChild(tdVal);
      tbody.appendChild(tr);
    }
  }

  function applyTemps(temps) {
    if (!Array.isArray(temps)) return;
    const rows = tbody.querySelectorAll("tr");
    rows.forEach((tr) => {
      const i = parseInt(tr.dataset.idx, 10);
      const cell = tr.querySelector(".ch-val");
      const v = temps[i];
      if (v === null || v === undefined || (typeof v === "number" && !isFinite(v))) {
        cell.textContent = "—";
        cell.classList.add("none");
      } else {
        cell.textContent = fmtTemp(v);
        cell.classList.remove("none");
      }
    });
  }

  // ---------- 切工位 ----------
  async function switchStation(newStation) {
    if (!newStation || newStation === currentStation) return;
    currentStation = newStation;
    document.title = `[${currentStation}] GX20 行動版`;
    buildRows();
    try {
      const r = await fetch(`/api/latest/${encodeURIComponent(currentStation)}`);
      const j = await r.json();
      if (j.ok && j.payload && Array.isArray(j.payload.temps)) {
        applyTemps(j.payload.temps);
        updateLastTs(j.payload.ts);
      }
    } catch (e) {
      console.warn("[mobile] /api/latest fetch failed:", e);
    }
  }

  // ---------- 設定 / 頻道名稱 ----------
  async function loadSettings() {
    const r = await fetch("/api/settings");
    const j = await r.json();
    // /api/settings 沒有 "ok" 欄位（直接 dump settings dict）
    // 其他 API（/api/latest, /api/channels）才有 "ok"。這裡直接接 body 即可。
    settings = j;
  }
  async function loadChannelNums() {
    try {
      const r = await fetch("/api/channels");
      const j = await r.json();
      if (j.ok && j.channels) channelNums = j.channels;
    } catch (e) {
      console.warn("[mobile] /api/channels failed:", e);
    }
  }

  // ---------- socket ----------
  function bindSocket() {
    const socket = io();
    socket.on("connect",    () => setConn(true, null));
    socket.on("disconnect", () => setConn(false, "SocketIO 斷線"));
    socket.on("new_sample", (payload) => {
      if (!payload || payload.station !== currentStation) return;
      if (Array.isArray(payload.temps)) {
        applyTemps(payload.temps);
        updateLastTs(payload.ts);
      }
    });
  }

  // ---------- init ----------
  async function init() {
    // 收集 STATIONS（從 select）
    Array.from(stationSel.options).forEach((o) => STATIONS.push(o.value));

    stationSel.addEventListener("change", () => switchStation(stationSel.value));

    try { await loadSettings(); }
    catch (e) { console.error("[mobile] loadSettings failed:", e); }
    try { await loadChannelNums(); }
    catch (e) { console.error("[mobile] loadChannelNums failed:", e); }

    currentStation = stationSel.value;
    document.title = `[${currentStation}] GX20 行動版`;

    buildRows();
    bindSocket();

    // 首頁立即拉一次（避免等 socket 第一次 emit）
    try {
      const r = await fetch(`/api/latest/${encodeURIComponent(currentStation)}`);
      const j = await r.json();
      if (j.ok && j.payload && Array.isArray(j.payload.temps)) {
        applyTemps(j.payload.temps);
        updateLastTs(j.payload.ts);
      }
    } catch (e) {
      console.warn("[mobile] initial /api/latest failed:", e);
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
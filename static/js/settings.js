// settings.js — 設定頁邏輯
//
// 全部走 GX20State：
//   - 進頁面：GX20State.init()（server + session 合併，主題套用）
//   - 改值：GX20State.update(key, value) → 寫 sessionStorage + 標 dirty
//   - 「保存」：GX20State.save() → POST 到 server
// 站點選擇另外用 TAB_KEY session 暫存
//
// STATIONS 與 POINTS 由 settings.html 內嵌的 <script> 注入到 window.GX20Config

const STATIONS = (window.GX20Config && window.GX20Config.stations) || ["工位1","工位2","工位3","工位4","工位5","工位6"];
const POINTS   = (window.GX20Config && window.GX20Config.points) || 20;
let currentStation = STATIONS[0];
let channelNums = {};
const cp = new ColorPicker();

const TAB_KEY = "gx20.tab_extra.v1";
function loadTabExtra() { try { return JSON.parse(sessionStorage.getItem(TAB_KEY) || "{}"); } catch { return {}; } }
function saveTabExtra(patch) {
  const cur = loadTabExtra();
  sessionStorage.setItem(TAB_KEY, JSON.stringify(Object.assign({}, cur, patch)));
  GX20State.markDirty();
}

async function init() {
  await GX20State.init();
  const settings = GX20State.settings;
  channelNums = await loadChannels();

  // 還原站點
  const tab = loadTabExtra();
  if (tab.currentStation && STATIONS.includes(tab.currentStation)) {
    currentStation = tab.currentStation;
  }

  // 填入基本欄位
  document.getElementById("gx20_host").value        = settings.gx20_host;
  document.getElementById("gx20_port").value        = settings.gx20_port;
  document.getElementById("retention_days").value   = settings.retention_days;
  document.getElementById("max_points").value       = settings.max_points;

  // v7：PW3335 + 電力 Y 軸
  renderPw3335();
  renderPwAxisTabs();
  fillPwAxisFields(currentStation);

  // v6：Y 軸範圍 per-station。預設填入當前站位
  renderYAxisTabs();
  fillYAxisFields(currentStation);

  // 從 server 額外拉 debug flag（GX20State 不會自動合併，獨立處理）
  try {
    const r = await fetch("/api/debug");
    const j = await r.json();
    if (j.ok) {
      document.getElementById("debugLogEnabled").checked = !!j.enabled;
    }
  } catch {}

  // 基本欄位 → GX20State.update
  bindField("gx20_host",       (v) => GX20State.update("gx20_host", v));
  bindField("gx20_port",       (v) => GX20State.update("gx20_port", parseInt(v, 10) || 0));
  bindField("retention_days",  (v) => GX20State.update("retention_days", Math.max(1, Math.min(30, parseInt(v, 10) || 7))));
  bindField("max_points",      (v) => GX20State.update("max_points", Math.max(200, Math.min(10000, parseInt(v, 10) || 2000))));

  // v7：PW3335 port
  bindField("pw3335_port",     (v) => {
    const cur = GX20State.settings.pw3335 || {};
    const next = Object.assign({}, cur, { port: parseInt(v, 10) || 3300 });
    GX20State.update("pw3335", next);
  });

  // v6：Y 軸三欄位綁定到「目前站位」的 y_axis entry
  bindYAxisFields();

  // v7：電力 Y 軸欄位綁定
  bindPwAxisFields();

  // Debug log 切換 → 直接 POST /api/debug（不透過 GX20State）
  document.getElementById("debugLogEnabled").addEventListener("change", async (e) => {
    const want = e.target.checked;
    try {
      const r = await fetch("/api/debug", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: want }),
      });
      const j = await r.json();
      if (j.ok) {
        alert(want ? "Debug log 已開啟（記錄至 logs/app.log）" : "Debug log 已關閉");
      } else {
        alert("切換失敗: " + (j.error || ""));
        e.target.checked = !want;
      }
    } catch (err) {
      alert("切換失敗: " + err.message);
      e.target.checked = !want;
    }
  });

  // 站點 tab
  const tabs = document.getElementById("stationTabs");
  STATIONS.forEach(s => {
    const b = document.createElement("button");
    b.textContent = s;
    b.dataset.station = s;
    if (s === currentStation) b.classList.add("active");
    b.addEventListener("click", () => switchStation(s));
    tabs.appendChild(b);
  });

  // 全部 toggle
  document.getElementById("allToggle").addEventListener("change", (e) => {
    const on = e.target.checked;
    const vis = GX20State.settings.ch_visibility[currentStation];
    for (let i = 0; i < POINTS; i++) vis[i] = on;
    GX20State.update("ch_visibility", GX20State.settings.ch_visibility);
    document.querySelectorAll(".ch-cell .vis-chk").forEach(c => { c.checked = on; });
    syncAllToggleFromGrid();
  });

  renderChGrid();
  syncAllToggleFromGrid();

  // 頂部按鈕
  document.getElementById("saveBtn").addEventListener("click", async () => {
    try { await GX20State.save(); alert("已保存"); }
    catch (e) { alert("保存失敗: " + e.message); }
  });
  document.getElementById("clearDbBtn").addEventListener("click", async () => {
    // v5：兩段式 confirm，作用於「設定頁當前選定的工位」
    const station = currentStation;
    const archive = confirm(
      `要清除工位「${station}」的歷史資料嗎？\n\n` +
      `【確定】= 先歸檔到 data/archive/ 再清除（推薦）\n` +
      `【取消】= 繼續下一個問題（問要不要直接刪除）`
    );
    if (!confirm(
      `最後確認：清除工位「${station}」的資料？\n\n` +
      `歸檔：${archive ? "是（保留到 data/archive/）" : "否（不保留）"}\n` +
      `按下「確定」就立刻刪除，無法復原${archive ? "（但有歸檔可恢復）" : ""}。`
    )) return;
    const r = await fetch("/api/clear", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ station, archive }),
    });
    const j = await r.json();
    if (j.ok) {
      const archMsg = j.archived ? `\n歸檔：${j.archive_path}` : "\n歸檔：未保留";
      alert(`已清除「${j.station}」${archMsg}`);
    } else {
      alert("清除失敗：" + (j.error || ""));
    }
  });
  document.getElementById("themeBtn").addEventListener("click", () => {
    const next = GX20State.theme === "dark" ? "light" : "dark";
    GX20State.setTheme(next);
  });
}

function bindField(id, cb) {
  const el = document.getElementById(id);
  el.addEventListener("input", () => cb(el.value));
  el.addEventListener("change", () => cb(el.value));
}

async function loadChannels() {
  const r = await fetch("/api/channels");
  const j = await r.json();
  return (j.ok && j.channels) || {};
}

function switchStation(s) {
  currentStation = s;
  saveTabExtra({ currentStation: s });
  document.querySelectorAll("#stationTabs button").forEach(b => {
    b.classList.toggle("active", b.dataset.station === s);
  });
  renderChGrid();
  syncAllToggleFromGrid();
}

function renderChGrid() {
  const grid = document.getElementById("chGrid");
  grid.innerHTML = "";
  const settings = GX20State.settings;
  for (let i = 0; i < POINTS; i++) {
    const cell = document.createElement("div");
    cell.className = "ch-cell";

    // 標題列：#1  0001
    const num = document.createElement("div");
    num.className = "ch-cell-num";
    num.innerHTML = `<span>#${i+1}</span> <span class="ch-num-code">${channelNums[currentStation]?.[i] || ""}</span>`;
    cell.appendChild(num);

    // 顯示/隱藏
    const visLabel = document.createElement("label");
    visLabel.className = "ch-field";
    const visChk = document.createElement("input");
    visChk.type = "checkbox";
    visChk.className = "vis-chk";
    visChk.checked = !!settings.ch_visibility[currentStation][i];
    visChk.addEventListener("change", () => {
      settings.ch_visibility[currentStation][i] = visChk.checked;
      GX20State.update("ch_visibility", settings.ch_visibility);
      syncAllToggleFromGrid();
    });
    visLabel.appendChild(visChk);
    const visTxt = document.createElement("span");
    visTxt.className = "ch-field-label";
    visTxt.textContent = "顯示";
    visLabel.appendChild(visTxt);
    cell.appendChild(visLabel);

    // 別名（明確標籤 + 文字框）
    const aliasWrap = document.createElement("div");
    aliasWrap.className = "ch-field";
    const aliasTxt = document.createElement("span");
    aliasTxt.className = "ch-field-label";
    aliasTxt.textContent = "別名";
    const aliasInp = document.createElement("input");
    aliasInp.type = "text";
    aliasInp.className = "ch-alias-inp";
    // v8：別名長度上限 20 字（避免超長中文把圖表/表格撐破版）
    aliasInp.maxLength = 20;
    aliasInp.value = settings.ch_alias[currentStation][i] || "";
    aliasInp.placeholder = `Ch${i+1}`;
    aliasInp.addEventListener("input", () => {
      settings.ch_alias[currentStation][i] = aliasInp.value;
      GX20State.update("ch_alias", settings.ch_alias);
    });
    aliasWrap.appendChild(aliasTxt);
    aliasWrap.appendChild(aliasInp);
    cell.appendChild(aliasWrap);

    // 顏色（明確標籤 + 按鈕）
    const colorWrap = document.createElement("div");
    colorWrap.className = "ch-field";
    const colorTxt = document.createElement("span");
    colorTxt.className = "ch-field-label";
    colorTxt.textContent = "顏色";
    const colorBtn = document.createElement("button");
    colorBtn.type = "button";
    colorBtn.className = "color-btn";
    colorBtn.title = "點擊選顏色";
    const initColor = settings.ch_color[currentStation][i] || "#888888";
    colorBtn.dataset.color = initColor;
    colorBtn.style.background = initColor;
    cp.attach(colorBtn, initColor, (newColor) => {
      settings.ch_color[currentStation][i] = newColor;
      GX20State.update("ch_color", settings.ch_color);
    });
    colorWrap.appendChild(colorTxt);
    colorWrap.appendChild(colorBtn);
    cell.appendChild(colorWrap);

    grid.appendChild(cell);
  }
}

function syncAllToggleFromGrid() {
  const vis = GX20State.settings.ch_visibility[currentStation];
  const allOn  = vis.every(v => v);
  const allOff = vis.every(v => !v);
  const tog = document.getElementById("allToggle");
  tog.checked = allOn;
  tog.indeterminate = !allOn && !allOff;
}

// =============================================================
// v6：Y 軸範圍 per-station 處理
//   - renderYAxisTabs()      造出六個站位 tab
//   - fillYAxisFields(st)    從 settings.y_axis[st] 把值填到三個欄位
//   - bindYAxisFields()      註 input/change 監聽 → 寫回 GX20State
//   - 勾「動態縮放」時 min/max 變半透明 (disabled 概念)
// =============================================================

function renderYAxisTabs() {
  const tabs = document.getElementById("yAxisStationTabs");
  if (!tabs) return;
  tabs.innerHTML = "";
  STATIONS.forEach(s => {
    const b = document.createElement("button");
    b.textContent = s;
    b.dataset.station = s;
    if (s === currentStation) b.classList.add("active");
    b.addEventListener("click", () => {
      currentStation = s;
      saveTabExtra({ currentStation: s });
      tabs.querySelectorAll("button").forEach(x => x.classList.toggle("active", x.dataset.station === s));
      fillYAxisFields(s);
      // 切站也要重畫接點 grid（让 Y 軸 tab 跟接點 tab 同步，兩者共高 currentStation）
      // 但接點 tab 是在 #stationTabs 內造出來的，必須同步選中狀態
      const chTabs = document.getElementById("stationTabs");
      if (chTabs) {
        chTabs.querySelectorAll("button").forEach(x => x.classList.toggle("active", x.dataset.station === s));
      }
      renderChGrid();
      syncAllToggleFromGrid();
    });
    tabs.appendChild(b);
  });
}

function fillYAxisFields(st) {
  const settings = GX20State.settings;
  const entry = (settings.y_axis && settings.y_axis[st]) || { min: 0, max: 100, auto: false };
  const minEl = document.querySelector('[data-yaxis-field="min"]');
  const maxEl = document.querySelector('[data-yaxis-field="max"]');
  const autoEl = document.querySelector('[data-yaxis-field="auto"]');
  if (!minEl || !maxEl || !autoEl) return;
  minEl.value  = entry.min;
  maxEl.value  = entry.max;
  autoEl.checked = !!entry.auto;
  applyYAxisAutoState(autoEl.checked);
}

function applyYAxisAutoState(isAuto) {
  const minEl = document.querySelector('[data-yaxis-field="min"]');
  const maxEl = document.querySelector('[data-yaxis-field="max"]');
  if (!minEl || !maxEl) return;
  // 動態縮放啟用時，min/max 變半透明、不能輸入
  minEl.disabled = isAuto;
  maxEl.disabled = isAuto;
  minEl.style.opacity = isAuto ? "0.5" : "1";
  maxEl.style.opacity = isAuto ? "0.5" : "1";
}

function bindYAxisFields() {
  const minEl  = document.querySelector('[data-yaxis-field="min"]');
  const maxEl  = document.querySelector('[data-yaxis-field="max"]');
  const autoEl = document.querySelector('[data-yaxis-field="auto"]');
  if (!minEl || !maxEl || !autoEl) return;

  // 共用寫回函式：取得整個 y_axis 物件，改一站位，再 update
  const writeBack = (patch) => {
    const s = GX20State.settings;
    if (!s.y_axis) s.y_axis = {};
    if (!s.y_axis[currentStation]) {
      s.y_axis[currentStation] = { min: 0, max: 100, auto: false };
    }
    Object.assign(s.y_axis[currentStation], patch);
    GX20State.update("y_axis", s.y_axis);
  };

  minEl.addEventListener("input",  () => writeBack({ min: parseFloat(minEl.value) || 0 }));
  minEl.addEventListener("change", () => writeBack({ min: parseFloat(minEl.value) || 0 }));
  maxEl.addEventListener("input",  () => writeBack({ max: parseFloat(maxEl.value) || 0 }));
  maxEl.addEventListener("change", () => writeBack({ max: parseFloat(maxEl.value) || 0 }));
  autoEl.addEventListener("change", () => {
    const isAuto = autoEl.checked;
    applyYAxisAutoState(isAuto);
    writeBack({ auto: isAuto });
  });
}

window.addEventListener("DOMContentLoaded", init);

// =============================================================
// v7：PW3335 區塊 + 電力 Y 軸
//   - renderPw3335()      造 6 工位 IP + 啟用 checkbox 表格
//   - renderPwAxisTabs()  6 工位 tab；切換時只動電力 Y 軸欄位
//   - fillPwAxisFields()  從 settings.pw_axis[st] 把值填進去
//   - bindPwAxisFields()  input/change → GX20State.update("pw_axis", ...)
//   - 電力線顏色 (V/I/W) 3 個 color-btn 綁 colorpicker
// =============================================================

function renderPw3335() {
  const settings = GX20State.settings;
  const pw = settings.pw3335 || { port: 3300, hosts: {}, colors: {} };
  // port
  document.getElementById("pw3335_port").value = pw.port || 3300;
  // 6 工位 IP（v10.2：拿掉「啟用」checkbox）
  const grid = document.getElementById("pwGrid");
  grid.innerHTML = "";
  STATIONS.forEach(s => {
    const row = document.createElement("div");
    row.className = "pw-grid-row";
    const lab = document.createElement("label");
    lab.className = "station-label";
    lab.textContent = s;
    const ip = document.createElement("input");
    ip.type = "text";
    ip.className = "pw-ip-inp";
    ip.value = (pw.hosts && pw.hosts[s]) || "";
    ip.placeholder = "192.168.1.x";
    ip.addEventListener("input", () => {
      const cur = GX20State.settings.pw3335 || {};
      const hosts = Object.assign({}, (cur.hosts || {}));
      hosts[s] = ip.value.trim();
      GX20State.update("pw3335", Object.assign({}, cur, { hosts }));
    });
    row.appendChild(lab);
    row.appendChild(ip);
    grid.appendChild(row);
  });
  // 3 個電力線顏色
  for (const key of ["V", "I", "W"]) {
    const btn = document.getElementById("pwColor" + key);
    if (!btn) continue;
    const init = (pw.colors && pw.colors[key]) || "#888888";
    btn.dataset.color = init;
    btn.style.background = init;
    cp.attach(btn, init, (newColor) => {
      const cur = GX20State.settings.pw3335 || {};
      const colors = Object.assign({}, (cur.colors || {}));
      colors[key] = newColor;
      GX20State.update("pw3335", Object.assign({}, cur, { colors }));
    });
  }
}

function renderPwAxisTabs() {
  const tabs = document.getElementById("pwAxisStationTabs");
  if (!tabs) return;
  tabs.innerHTML = "";
  STATIONS.forEach(s => {
    const b = document.createElement("button");
    b.textContent = s;
    b.dataset.station = s;
    if (s === currentStation) b.classList.add("active");
    b.addEventListener("click", () => {
      // 不改 currentStation（避免把溫度 Y 軸 tab 跟接點 tab 一起切走）
      // 只在電力 Y 軸區塊內部追蹤
      tabs.querySelectorAll("button").forEach(x => x.classList.toggle("active", x.dataset.station === s));
      fillPwAxisFields(s);
    });
    tabs.appendChild(b);
  });
}

function fillPwAxisFields(st) {
  const settings = GX20State.settings;
  const entry = (settings.pw_axis && settings.pw_axis[st]) || {
    v: { min: 0, max: 230, auto: false },
    i: { min: 0, max: 5,   auto: false },
    w: { min: 0, max: 250, auto: false },
  };
  // 向後相容：舊資料是 iw 共用軸 → 拿到 i/w 任一不存在就退回 iw
  if (entry.iw && (!entry.i || !entry.w)) {
    entry.i = entry.i || { min: entry.iw.min, max: entry.iw.max, auto: entry.iw.auto };
    entry.w = entry.w || { min: entry.iw.min, max: entry.iw.max, auto: entry.iw.auto };
  }
  const vMinEl  = document.querySelector('[data-pwaxis-field="v-min"]');
  const vMaxEl  = document.querySelector('[data-pwaxis-field="v-max"]');
  const iMinEl  = document.querySelector('[data-pwaxis-field="i-min"]');
  const iMaxEl  = document.querySelector('[data-pwaxis-field="i-max"]');
  const wMinEl  = document.querySelector('[data-pwaxis-field="w-min"]');
  const wMaxEl  = document.querySelector('[data-pwaxis-field="w-max"]');
  const vAutoEl = document.querySelector('[data-pwaxis-field="v-auto"]');
  const iAutoEl = document.querySelector('[data-pwaxis-field="i-auto"]');
  const wAutoEl = document.querySelector('[data-pwaxis-field="w-auto"]');
  if (!vMinEl || !vMaxEl || !iMinEl || !iMaxEl || !wMinEl || !wMaxEl || !vAutoEl || !iAutoEl || !wAutoEl) return;
  vMinEl.value  = entry.v.min;
  vMaxEl.value  = entry.v.max;
  iMinEl.value  = entry.i.min;
  iMaxEl.value  = entry.i.max;
  wMinEl.value  = entry.w.min;
  wMaxEl.value  = entry.w.max;
  vAutoEl.checked = !!entry.v.auto;
  iAutoEl.checked = !!entry.i.auto;
  wAutoEl.checked = !!entry.w.auto;
  // 動態縮放啟用時 → 對應欄位半透明
  applyPwAxisAutoState(entry.v.auto, entry.i.auto, entry.w.auto);
}

function applyPwAxisAutoState(vAuto, iAuto, wAuto) {
  const vMinEl  = document.querySelector('[data-pwaxis-field="v-min"]');
  const vMaxEl  = document.querySelector('[data-pwaxis-field="v-max"]');
  const iMinEl  = document.querySelector('[data-pwaxis-field="i-min"]');
  const iMaxEl  = document.querySelector('[data-pwaxis-field="i-max"]');
  const wMinEl  = document.querySelector('[data-pwaxis-field="w-min"]');
  const wMaxEl  = document.querySelector('[data-pwaxis-field="w-max"]');
  if (vMinEl) { vMinEl.disabled = vAuto; vMinEl.style.opacity = vAuto ? "0.5" : "1"; }
  if (vMaxEl) { vMaxEl.disabled = vAuto; vMaxEl.style.opacity = vAuto ? "0.5" : "1"; }
  if (iMinEl) { iMinEl.disabled = iAuto; iMinEl.style.opacity = iAuto ? "0.5" : "1"; }
  if (iMaxEl) { iMaxEl.disabled = iAuto; iMaxEl.style.opacity = iAuto ? "0.5" : "1"; }
  if (wMinEl) { wMinEl.disabled = wAuto; wMinEl.style.opacity = wAuto ? "0.5" : "1"; }
  if (wMaxEl) { wMaxEl.disabled = wAuto; wMaxEl.style.opacity = wAuto ? "0.5" : "1"; }
}

function bindPwAxisFields() {
  const vMinEl = document.querySelector('[data-pwaxis-field="v-min"]');
  const vMaxEl = document.querySelector('[data-pwaxis-field="v-max"]');
  const iMinEl = document.querySelector('[data-pwaxis-field="i-min"]');
  const iMaxEl = document.querySelector('[data-pwaxis-field="i-max"]');
  const wMinEl = document.querySelector('[data-pwaxis-field="w-min"]');
  const wMaxEl = document.querySelector('[data-pwaxis-field="w-max"]');
  const vAutoEl = document.querySelector('[data-pwaxis-field="v-auto"]');
  const iAutoEl = document.querySelector('[data-pwaxis-field="i-auto"]');
  const wAutoEl = document.querySelector('[data-pwaxis-field="w-auto"]');
  if (!vMinEl || !vMaxEl || !iMinEl || !iMaxEl || !wMinEl || !wMaxEl || !vAutoEl || !iAutoEl || !wAutoEl) return;

  // 取得目前 tab 的站位（電力 Y 軸 tab 自己的，不一定等同 currentStation）
  let activeStation = STATIONS[0];
  const tabs = document.getElementById("pwAxisStationTabs");
  function getActivePwAxisStation() {
    if (tabs) {
      const a = tabs.querySelector("button.active");
      if (a) return a.dataset.station;
    }
    return activeStation;
  }
  if (tabs) {
    tabs.addEventListener("click", (e) => {
      if (e.target && e.target.dataset && e.target.dataset.station) {
        activeStation = e.target.dataset.station;
      }
    });
  }

  const writeBack = (patch) => {
    const s = GX20State.settings;
    if (!s.pw_axis) s.pw_axis = {};
    const st = getActivePwAxisStation();
    if (!s.pw_axis[st]) {
      s.pw_axis[st] = {
        v: { min: 0, max: 230, auto: false },
        i: { min: 0, max: 5,   auto: false },
        w: { min: 0, max: 250, auto: false },
      };
    }
    const cur = s.pw_axis[st];
    if ("v_min"  in patch) cur.v.min  = patch.v_min;
    if ("v_max"  in patch) cur.v.max  = patch.v_max;
    if ("i_min"  in patch) cur.i.min  = patch.i_min;
    if ("i_max"  in patch) cur.i.max  = patch.i_max;
    if ("w_min"  in patch) cur.w.min  = patch.w_min;
    if ("w_max"  in patch) cur.w.max  = patch.w_max;
    if ("v_auto" in patch) cur.v.auto = patch.v_auto;
    if ("i_auto" in patch) cur.i.auto = patch.i_auto;
    if ("w_auto" in patch) cur.w.auto = patch.w_auto;
    GX20State.update("pw_axis", s.pw_axis);
  };

  const num = (el) => parseFloat(el.value) || 0;
  vMinEl.addEventListener("input",  () => writeBack({ v_min: num(vMinEl) }));
  vMinEl.addEventListener("change", () => writeBack({ v_min: num(vMinEl) }));
  vMaxEl.addEventListener("input",  () => writeBack({ v_max: num(vMaxEl) }));
  vMaxEl.addEventListener("change", () => writeBack({ v_max: num(vMaxEl) }));
  iMinEl.addEventListener("input",  () => writeBack({ i_min: num(iMinEl) }));
  iMinEl.addEventListener("change", () => writeBack({ i_min: num(iMinEl) }));
  iMaxEl.addEventListener("input",  () => writeBack({ i_max: num(iMaxEl) }));
  iMaxEl.addEventListener("change", () => writeBack({ i_max: num(iMaxEl) }));
  wMinEl.addEventListener("input",  () => writeBack({ w_min: num(wMinEl) }));
  wMinEl.addEventListener("change", () => writeBack({ w_min: num(wMinEl) }));
  wMaxEl.addEventListener("input",  () => writeBack({ w_max: num(wMaxEl) }));
  wMaxEl.addEventListener("change", () => writeBack({ w_max: num(wMaxEl) }));
  vAutoEl.addEventListener("change", () => {
    const v = vAutoEl.checked;
    applyPwAxisAutoState(v, iAutoEl.checked, wAutoEl.checked);
    writeBack({ v_auto: v });
  });
  iAutoEl.addEventListener("change", () => {
    const i = iAutoEl.checked;
    applyPwAxisAutoState(vAutoEl.checked, i, wAutoEl.checked);
    writeBack({ i_auto: i });
  });
  wAutoEl.addEventListener("change", () => {
    const w = wAutoEl.checked;
    applyPwAxisAutoState(vAutoEl.checked, iAutoEl.checked, w);
    writeBack({ w_auto: w });
  });
}

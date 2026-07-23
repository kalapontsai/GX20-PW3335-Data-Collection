// storage.js — 跨頁設定儲存層
//
// 兩種儲存：
//   - sessionStorage["gx20.tab_state.v1"]  → 每個分頁一份，「未保存」的設定
//   - server (POST /api/settings)          → 按下「保存」後才寫入
//
// 進入頁面時：
//   1. 從 server 拉預設值（baseline）
//   2. 若 sessionStorage 有，用 session 的覆蓋
//   3. 套用主題（theme 也存在 session）
//
// 使用者改任何值 → sessionStorage + 標記 dirty
// 按「保存」   → POST 到 server + 清 dirty

(function (window) {
  "use strict";

  const SESSION_KEY = "gx20.tab_state.v1";

  // ---------- theme 套用 ----------
  function applyTheme(theme) {
    document.body.setAttribute("data-theme", theme || "light");
  }

  function detectInitialTheme() {
    // 跟系統 prefers-color-scheme
    if (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches) {
      return "dark";
    }
    return "light";
  }

  // ---------- session 讀寫 ----------
  function loadSession() {
    try { return JSON.parse(sessionStorage.getItem(SESSION_KEY) || "{}"); }
    catch { return {}; }
  }
  function saveSession(patch) {
    const cur = loadSession();
    const next = Object.assign({}, cur, patch);
    sessionStorage.setItem(SESSION_KEY, JSON.stringify(next));
    window.GX20State.markDirty();
  }
  function clearSession() {
    sessionStorage.removeItem(SESSION_KEY);
  }

  // ---------- dirty 標記 ----------
  // 任何分頁自己改的，與 server 不同，就是 dirty
  // 用 sessionStorage 存一份「已保存的快照」，按下保存就更新快照

  function snapshotEqual(a, b) {
    return JSON.stringify(a) === JSON.stringify(b);
  }

  function markDirtyIfChanged(sess) {
    const saved = sess._saved || null;
    const cur = {
      ch_visibility: sess.ch_visibility,
      ch_alias:      sess.ch_alias,
      ch_color:      sess.ch_color,
      gx20_host:     sess.gx20_host,
      gx20_port:     sess.gx20_port,
      y_axis:        sess.y_axis,            // v6：per-station
      rate_window_min: sess.rate_window_min,
      avg_window_min:  sess.avg_window_min,
      retention_days:  sess.retention_days,
      max_points:      sess.max_points,
      chart_x_minutes: sess.chart_x_minutes,
      pw3335:          sess.pw3335,          // v7：PW3335 整體設定
      pw_axis:         sess.pw_axis,         // v7：電力 Y 軸 per-station
      notes:           sess.notes,           // v10.x：備註欄 per-station
      theme:         sess.theme,
    };
    if (saved === null) {
      // 首次進來還沒保存過
      window.GX20State.dirty = false;     // 視為乾淨（與 server 預設一致）
      return;
    }
    window.GX20State.dirty = !snapshotEqual(cur, saved);
  }

  // ---------- debounce 自動保存（v8.1） ----------
  // 舊行為：使用者按「保存」才 POST /api/settings
  // 問題：主畫面三 select (X軸/速率/平均) onChange 走 update() → 只寫 sessionStorage，
  //       關掉分頁就消失；其他瀏覽器/電腦也看不到
  // 新行為：任何 update() 結尾 scheduleSave() → 300ms debounce 後自動 POST server
  //         「保存」按鈕保留為「強制立即 flush」入口（手動按也算 dirty 自動 save 之外的雙保險）
  const SAVE_DEBOUNCE_MS = 300;
  let _saveTimer = null;
  function _scheduleSave() {
    if (_saveTimer) clearTimeout(_saveTimer);
    _saveTimer = setTimeout(() => {
      _saveTimer = null;
      // 避免 init() 過程中還沒 ready 就誤觸
      if (window.GX20State && typeof window.GX20State.save === "function") {
        window.GX20State.save().catch(err => {
          // POST 失敗不擋 UI；client 端 sessionStorage 還有值，下次重試
          console.warn("[GX20State] auto-save failed:", err);
        });
      }
    }, SAVE_DEBOUNCE_MS);
  }

  // ---------- UI 反映 dirty ----------
  function refreshSaveButtons() {
    const btns = document.querySelectorAll("[data-role=save-btn]");
    btns.forEach(b => {
      if (window.GX20State.dirty) {
        b.classList.add("btn-dirty");
        b.textContent = "保存 ●";
      } else {
        b.classList.remove("btn-dirty");
        b.textContent = "保存";
      }
    });
  }

  // ---------- 對外介面 ----------
  const GX20State = {
    settings: null,             // 合併後的「目前生效」設定
    baseline: null,             // server 端的 baseline
    sess: loadSession(),        // session 暫存
    dirty: false,
    theme: null,
    isLocal: true,              // v8.1.2：init 時由 server 端告知是否本機 (body.is_local)

    /**
     * 初始化：拉 server → 套 session → 套主題
     * 回傳合併後的 settings 物件
     */
    async init() {
      // 1. server
      const r = await fetch("/api/settings");
      const srv = await r.json();
      this.baseline = srv;
      // v8.1.2：server 端告知是否本機（看 client IP 判定）
      this.isLocal = srv.is_local !== false;  // 預設 true（防呆）

      // 2. session 預設值
      this.sess = loadSession();
      this.theme = this.sess.theme || detectInitialTheme();
      applyTheme(this.theme);

      // 3. 合併：session 為主，未設定的從 server 補
      this.settings = Object.assign({}, srv);
      for (const k of Object.keys(this.sess)) {
        if (k.startsWith("_") || k === "theme") continue;
        if (this.sess[k] !== undefined) this.settings[k] = this.sess[k];
      }

      // 4. dirty 檢查
      markDirtyIfChanged(this.sess);
      refreshSaveButtons();

      // v8.1.2：拿掉 v8.1.1 的 _migrateLegacySessionIfNeeded()。
      // 原因：v8.1.1 migrate legacy session 會「意外覆寫 server 設定」
      //       （跨工位污染 — sess 內只有工位 4，merge 後 settings.ch_alias 只剩工位 4，
      //        整包 POST 出去後 server 6 工位都被設成工位 4 的值）。
      // v8.1.2 改採「遠端鎖死」策略：只有 OTA 本機 (127.0.0.1) 可變更設定。
      // 遠端瀏覽器 init() 不會觸發任何 POST，sessionStorage 殘留只影響本機的 UX，
      // 不會污染 server 端的 single source of truth。

      if (window.console && console.debug) {
        console.debug("[GX20State] init: sess keys=", Object.keys(this.sess),
          "settings.ch_visibility[工位1][0..4]=",
          this.settings.ch_visibility["工位1"].slice(0,5));
      }

      return this.settings;
    },

    /**
     * 使用者改了某個欄位 → 同步進 settings + 寫 session + 標 dirty
     * 傳 key, value；對 ch_visibility/alias/color 會以「整個工位物件」覆寫
     */
    update(key, value) {
      // 用深拷貝避免外部變動了傳入的物件後、sessionStorage 還是指到同一個
      const deep = JSON.parse(JSON.stringify(value));
      this.settings[key] = deep;
      this.sess[key] = deep;
      sessionStorage.setItem(SESSION_KEY, JSON.stringify(this.sess));
      this.dirty = true;
      refreshSaveButtons();
      // v8.1：自動 debounce 寫回 server，跨瀏覽器/跨電腦都看得到
      _scheduleSave();
    },

    /**
     * 切換主題（立即生效）
     */
    setTheme(theme) {
      this.theme = theme;
      this.sess.theme = theme;
      sessionStorage.setItem(SESSION_KEY, JSON.stringify(this.sess));
      applyTheme(theme);
      // 主題變更不視為 dirty（純 UI），但要刷新按鈕狀態
      refreshSaveButtons();
    },

    /**
     * 「保存」：把目前 session 寫到 server
     */
    async save() {
      // 整包 POST
      const payload = {
        gx20_host:      this.settings.gx20_host,
        gx20_port:      this.settings.gx20_port,
        y_axis:         this.settings.y_axis,        // v6：per-station
        rate_window_min: this.settings.rate_window_min,
        avg_window_min:  this.settings.avg_window_min,
        retention_days: this.settings.retention_days,
        max_points: this.settings.max_points,
        chart_x_minutes: this.settings.chart_x_minutes ?? 0,
        ch_visibility:  this.settings.ch_visibility,
        ch_alias:       this.settings.ch_alias,
        ch_color:       this.settings.ch_color,
        pw3335:         this.settings.pw3335,        // v7：PW3335
        pw_axis:        this.settings.pw_axis,       // v7：電力 Y 軸
        notes:          this.settings.notes,         // v10.x：備註欄 per-station
        theme:          this.theme,
      };
      const r = await fetch("/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const j = await r.json();
      if (!j.ok) throw new Error(j.error || "save failed");
      // ✅ 關鍵修正：把實際設定值也寫回 sessionStorage 的頂層 key
      // 否則 init() 合併時讀不到 ch_visibility/alias/color，全退回 server 預設
      this.sess.ch_visibility  = payload.ch_visibility;
      this.sess.ch_alias       = payload.ch_alias;
      this.sess.ch_color       = payload.ch_color;
      this.sess.gx20_host      = payload.gx20_host;
      this.sess.gx20_port      = payload.gx20_port;
      this.sess.y_axis         = payload.y_axis;     // v6
      this.sess.rate_window_min = payload.rate_window_min;
      this.sess.avg_window_min  = payload.avg_window_min;
      this.sess.retention_days  = payload.retention_days;
      this.sess.max_points      = payload.max_points;
      this.sess.chart_x_minutes = payload.chart_x_minutes;
      this.sess.pw3335          = payload.pw3335;     // v7
      this.sess.pw_axis         = payload.pw_axis;    // v7
      this.sess.notes           = payload.notes;      // v10.x
      this.sess.theme          = payload.theme;
      // 記下「已保存的快照」供 dirty 比較
      this.sess._saved = {
        ch_visibility: payload.ch_visibility,
        ch_alias:      payload.ch_alias,
        ch_color:      payload.ch_color,
        gx20_host:     payload.gx20_host,
        gx20_port:     payload.gx20_port,
        y_axis:        payload.y_axis,             // v6
        rate_window_min: payload.rate_window_min,
        avg_window_min:  payload.avg_window_min,
        retention_days:  payload.retention_days,
        max_points:      payload.max_points,
        chart_x_minutes: payload.chart_x_minutes,
        pw3335:          payload.pw3335,           // v7
        pw_axis:         payload.pw_axis,          // v7
        notes:           payload.notes,            // v10.x
        theme:         payload.theme,
      };
      sessionStorage.setItem(SESSION_KEY, JSON.stringify(this.sess));
      this.baseline = Object.assign({}, this.settings);
      this.dirty = false;
      refreshSaveButtons();
      return j;
    },

    markDirty() { this.dirty = true; refreshSaveButtons(); },
  };

  window.GX20State = GX20State;
})(window);

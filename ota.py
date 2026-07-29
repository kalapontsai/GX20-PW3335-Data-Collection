# -*- coding: utf-8 -*-
"""
ota.py
======
OTA (Over-The-Air) 更新模組。

提供能力：
  - 寫入白名單（防止目錄穿越攻擊）
  - Token 認證（環境變數 / 設定檔 / 自動產生）
  - Atomic file write（先寫 .tmp 再 rename）
  - 寫入前自動備份到 config/ota_backup/<timestamp>/<relative_path>
  - 自我重啟（透過 detached subprocess spawn + os._exit）

Endpoint（由 app.py 註冊）：
  GET  /api/admin/status     檢查 OTA 狀態、回 token 設定狀態
  POST /api/admin/ota        上傳單檔（multipart）
  POST /api/admin/restart    觸發自我重啟
  POST /api/admin/ota_bundle 一次推多檔（JSON + base64）

安全設計：
  - 認證：HTTP header `X-OTA-Token: <token>`，缺 / 錯直接 401
  - Token 來源（依序）：
      1) 環境變數 GX20_OTA_TOKEN
      2) config/ota_token 檔（純文字）
      3) 自動產生並寫入 config/ota_token，回傳時只回 fingerprint（hash 前 8 碼）
  - 寫入路徑必須在 ALLOWED_TARGETS 白名單內
  - 寫入路徑解析後必須在 APP_ROOT 內（防 ../ 攻擊）
"""

import base64
import datetime
import hashlib
import logging
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
from typing import Optional, Tuple

log = logging.getLogger("gx20.ota")

# ---------- 路徑 ----------

APP_ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(APP_ROOT, "config")
OTA_TOKEN_PATH = os.path.join(CONFIG_DIR, "ota_token")
OTA_BACKUP_DIR = os.path.join(CONFIG_DIR, "ota_backup")

# ---------- 寫入白名單 ----------
# 相對於 APP_ROOT 的路徑前綴；全部路徑都會被 normalize 後再允許檢查
ALLOWED_TARGETS = (
    # 前端
    "static/js/",
    "static/css/",
    "static/vendor/",
    "templates/",
    # 後端核心
    "app.py",
    "config.py",
    "storage.py",
    "gx20_reader.py",
    "lttb.py",
    "run.py",
    # OTA 自己
    "ota.py",
    # 工具
    "ota_push.py",
    "ota_watchdog.py",
    "ota_watchdog.bat",
    "start_forever.bat",
)

# 拒絕的副檔名（避免不小心上傳執行檔）
BLOCKED_EXTS = (".pyc", ".pyo", ".pyd", ".so", ".dll", ".exe", ".bat", ".sh", ".ps1")

# ---------- OTA 端點 IP 白名單（v8.4+）----------
# 只允許特定 IP 呼叫 /api/admin/*。
# 寫死，不走環境變數：換 IP 一定要改 code + commit，避免默默擴大攻擊面。
# - 127.0.0.1 / ::1：本機迴路（直接在 GX20 主機上瀏覽器）
# - 10.35.32.11：WSL 端（管理者從 WSL 推 OTA 的固定 IP）
# 新增 IP 請走 git commit + code review，不要在這裡加註解規避。
OTA_ALLOWED_IPS = ("127.0.0.1", "::1", "10.35.32.11")


def is_allowed_ip(remote_addr: Optional[str]) -> bool:
    """檢查 remote_addr 是否在 OTA 白名單。"""
    if not remote_addr:
        return False
    return remote_addr in OTA_ALLOWED_IPS


# ============================================================
# Token 管理
# ============================================================

def _read_env_token() -> Optional[str]:
    return os.environ.get("GX20_OTA_TOKEN") or None


def _read_file_token() -> Optional[str]:
    if not os.path.exists(OTA_TOKEN_PATH):
        return None
    try:
        with open(OTA_TOKEN_PATH, "r", encoding="utf-8") as f:
            t = f.read().strip()
        return t or None
    except Exception as e:
        log.warning("讀取 OTA token 失敗: %s", e)
        return None


def _ensure_file_token() -> str:
    """若 token 檔不存在，自動產生並寫入。回傳 token 字串。"""
    os.makedirs(CONFIG_DIR, exist_ok=True)
    cur = _read_file_token()
    if cur:
        return cur
    token = secrets.token_urlsafe(32)
    with open(OTA_TOKEN_PATH, "w", encoding="utf-8") as f:
        f.write(token)
    # 限制權限（Windows 會忽略 chmod，但 Linux/WSL 有效）
    try:
        os.chmod(OTA_TOKEN_PATH, 0o600)
    except Exception:
        pass
    log.info("已自動產生 OTA token，寫入 %s", OTA_TOKEN_PATH)
    return token


def get_token() -> str:
    """取得目前生效的 token（依優先序：env > 檔）。"""
    return _read_env_token() or _read_file_token() or _ensure_file_token()


def token_fingerprint(token: str) -> str:
    """回傳 token 的不可逆摘要（給使用者對照用，不洩漏完整 token）。"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:8]


def check_token(provided: Optional[str]) -> bool:
    """驗證提供的 token 是否正確（恆定時間比對）。"""
    if not provided:
        return False
    expected = get_token()
    return secrets.compare_digest(provided, expected)


# ============================================================
# 路徑白名單
# ============================================================

def is_allowed_target(target: str) -> bool:
    """
    檢查 target 路徑是否允許寫入。
    - 必須是相對路徑
    - 不允許 .. 或絕對路徑
    - 必須落在 ALLOWED_TARGETS 任一前綴
    - 副檔名不可在 BLOCKED_EXTS
    """
    if not target:
        return False
    # 拒絕絕對路徑（含 Windows 磁碟機代號）
    if os.path.isabs(target) or (len(target) >= 2 and target[1] == ":"):
        return False
    # 拒絕 .. 跳脫
    norm = os.path.normpath(target).replace("\\", "/")
    if norm.startswith("../") or "/../" in norm or norm.startswith(".."):
        return False
    # 副檔名檢查：先用 BLOCKED_EXTS 過滤，但白名單具名檔（不是前綴）可以豁免
    # 例如 ota_watchdog.bat / start_forever.bat 雖然是 .bat，
    # 但 ALLOWED_TARGETS 內明列了這些檔名 → 允許
    _, ext = os.path.splitext(norm)
    # 白名單前置檢查：如果 target 本身就是 ALLOWED_TARGETS 內的具名檔，後面都跳過
    is_exact_match = norm in ALLOWED_TARGETS
    if ext.lower() in BLOCKED_EXTS and not is_exact_match:
        return False
    # 白名單前綴
    for allowed in ALLOWED_TARGETS:
        if allowed.endswith("/"):
            if norm.startswith(allowed):
                return True
        else:
            if norm == allowed:
                return True
    return False


def resolve_target(target: str) -> str:
    """把相對路徑解析成絕對路徑，確保在 APP_ROOT 內。"""
    abs_path = os.path.abspath(os.path.join(APP_ROOT, target))
    abs_root = os.path.abspath(APP_ROOT) + os.sep
    if not (abs_path + os.sep).startswith(abs_root):
        raise ValueError(f"路徑逃脫 APP_ROOT: {target}")
    return abs_path


# ============================================================
# 備份與 Atomic 寫入
# ============================================================

def _backup_existing(target_abs: str) -> Optional[str]:
    """若檔案已存在，備份到 OTA_BACKUP_DIR/<ts>/<rel>。回傳備份路徑或 None。"""
    if not os.path.exists(target_abs):
        return None
    rel = os.path.relpath(target_abs, APP_ROOT)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = os.path.join(OTA_BACKUP_DIR, ts)
    os.makedirs(backup_dir, exist_ok=True)
    backup_path = os.path.join(backup_dir, rel.replace("/", os.sep))
    os.makedirs(os.path.dirname(backup_path), exist_ok=True)
    shutil.copy2(target_abs, backup_path)
    log.info("備份 %s → %s", rel, backup_path)
    return backup_path


def atomic_write(target_abs: str, content: bytes) -> Tuple[bool, str]:
    """
    Atomic write：先寫 <target>.tmp，fsync 後 rename 覆蓋。
    回傳 (success, message)。
    """
    target_dir = os.path.dirname(target_abs)
    os.makedirs(target_dir, exist_ok=True)
    tmp_path = target_abs + ".tmp"
    try:
        with open(tmp_path, "wb") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, target_abs)
        return True, "ok"
    except Exception as e:
        log.error("atomic_write 失敗: %s", e)
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass
        return False, str(e)


def save_file(target: str, content: bytes) -> dict:
    """
    主流程：白名單檢查 → 備份 → atomic write。
    回傳 dict 給 Flask 端 jsonify。
    """
    if not is_allowed_target(target):
        return {"ok": False, "error": f"target 不在白名單: {target}"}
    try:
        abs_path = resolve_target(target)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    backup_path = _backup_existing(abs_path)
    ok, msg = atomic_write(abs_path, content)
    if not ok:
        return {"ok": False, "error": f"寫入失敗: {msg}"}
    return {
        "ok": True,
        "saved": target,
        "abs_path": abs_path,
        "backup": backup_path,
        "size": len(content),
    }


# ============================================================
# 自我重啟
# ============================================================

def schedule_restart(delay_sec: int = 2) -> dict:
    """
    排程自我重啟：
      只讓主進程在 <delay> 秒後退出，由 ota_watchdog.bat 接手重啟。
    不要自己 spawn 新 python app.py：會跟 watch dog 撞 port / 撞 5000 佔用。

    適用環境：手動 cmd 跑 `ota_watchdog.bat`，
    或用 `start /b ota_watchdog.bat` 在背景跑（不被視窗關閉影響）。
    """
    try:
        log.info("已排程重啟（%d 秒後退出，由 watch dog 接手）", delay_sec)
        def _do_exit():
            time.sleep(max(0.5, delay_sec))
            log.info("OTA 重啟：主進程退出中（watch dog 會接手）…")
            os._exit(0)
        threading.Thread(target=_do_exit, daemon=True).start()
        return {"ok": True, "restart_in_sec": delay_sec}
    except Exception as e:
        log.exception("排程重啟失敗: %s", e)
        return {"ok": False, "error": str(e)}


# ============================================================
# 工具
# ============================================================

def status() -> dict:
    """回傳 OTA 模組狀態（給 /api/admin/status 用）。"""
    token = get_token()
    import time as _t
    boot = getattr(status, "_boot_time", None)
    if boot is None:
        boot = _t.time()
        status._boot_time = boot
    return {
        "ok": True,
        "ota_version": 2,
        "token_source": (
            "env" if _read_env_token()
            else "file" if _read_file_token()
            else "auto-generated"
        ),
        "token_fingerprint": token_fingerprint(token),
        "app_root": APP_ROOT,
        "backup_dir": OTA_BACKUP_DIR,
        "allowed_targets_count": len(ALLOWED_TARGETS),
        "uptime_seconds": round(_t.time() - boot, 1),
    }


# ============================================================
# 清 log 檔
# ============================================================

def clear_log_file() -> dict:
    """
    清空 logs/app.log 與所有備份 (app.log.1 ~ app.log.5)。
    - 不刪檔，只 truncate 到 0 bytes
    - 不需要 restart（logger 的 file handler 下次 emit 時會自動重新打開）
    - 用 os.truncate 而非 open('w')：避免在手打開時另一個 fd 還在寫
    """
    log_path = os.path.join(APP_ROOT, "logs", "app.log")
    cleared = []
    errors = []
    # 主檔 + 備份 (RotatingFileHandler 備份慣例是 .1 ~ .5)
    candidates = [log_path] + [f"{log_path}.{i}" for i in range(1, 10)]
    for p in candidates:
        if not os.path.exists(p):
            continue
        try:
            with open(p, "ab") as f:
                size = os.path.getsize(p)
                # 先 close fh 抓到的大小，再 truncate
            with open(p, "rb+") as f:
                f.truncate(0)
            cleared.append({"path": p, "size_before": size})
            log.info("clear_log_file: 清空 %s（%d bytes）", p, size)
        except Exception as e:
            errors.append({"path": p, "error": str(e)})
            log.warning("clear_log_file: 清空 %s 失敗: %s", p, e)
    # 強制讓 root logger 的所有 handler 重設 fd（避免 OS 層 cache）
    try:
        import logging as _logging
        for h in _logging.getLogger().handlers:
            try:
                if hasattr(h, "stream") and h.stream and not h.stream.closed:
                    h.flush()
            except Exception:
                pass
    except Exception:
        pass
    return {
        "ok": len(errors) == 0,
        "cleared": cleared,
        "errors": errors,
        "log_path": log_path,
    }

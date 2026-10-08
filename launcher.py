# -*- coding: utf-8 -*-
"""
launcher.py
===========
GX20 + PW3335 Web Monitor 啟動器（PyInstaller 包成 gx20.exe 給終端使用者用）。

點擊 gx20.exe 時的行為：

1. 用 Named Mutex 偵測是否已有實例
   - 若無 → 開對話框詢問「是否啟動程式？」(Yes/No)
       Yes → 以 background 模式 fork 真正的 server（pythonw 跑 app.py 等價行為），
              關閉對話框，離開 launcher
       No  → 直接離開 launcher
   - 若有 → 開對話框詢問「是否終止背景執行？」(Yes/No)
       Yes → 對該 mutex owner 發送 shutdown signal（透過 HTTP GET /api/admin/shutdown，
              OTA token 從 %APPDATA%/GX20-PW3335/ota_token 讀），
              等 server 退出後關閉對話框，離開 launcher
       No  → 直接離開 launcher

2. 對話框一律是 modal Tkinter：簡單、單檔、零外部相依（tkinter 是 Python 內建）。

3. PyInstaller --onefile --windowed 包成單一 gx20.exe，背景跑時不彈 console。

為什麼需要這個 launcher，而不是直接 --windowed pythonw app.py：
- pythonw app.py 一旦執行就沒有「單一實例」保護，
  連點兩下會開兩個 server 搶 port 5000。
- 使用者要「先確認才啟動」或「先確認才終止」的對話框 UX。
- 終端使用者拿到的是 gx20.exe，點兩下就走完「確認 → 背景跑 → 關視窗」。

設計：v1 採「Mutex + HTTP shutdown」雙保險。
- Mutex：偵測「是否已有 server 跑起來」
- HTTP /api/admin/shutdown：發送終止指令（沿用 v4 OTA 既有 endpoint）
  註：v4 既有 /api/admin/restart（exit 0 → watchdog 重啟），
      我們要的「終止」不是 restart → 改用 /api/admin/shutdown（會在 app.py 內補上）。
      詳見 app.py patch。

作者：Kadela + 大寶協作
授權：與主專案一致
"""

import json
import os
import sys
import time
import tkinter as tk
from tkinter import messagebox
from urllib import request as urlrequest
from urllib.error import URLError


# ---------------------------------------------------------------------------
# 常數
# ---------------------------------------------------------------------------

# 與 app.py / ota.py 一致；Mutex 名稱要 Global\ 才能跨 session 偵測
MUTEX_NAME = "Global\\GX20PW3335Monitor"
SERVER_PORT = 5000
# v4 OTA token 存放位置（沿用既有慣例）
OTA_TOKEN_PATH = os.path.join(
    os.environ.get("APPDATA", os.path.expanduser("~")),
    "GX20-PW3335", "ota_token",
)


# ---------------------------------------------------------------------------
# Windows-only 工具：判斷 mutex / 啟動背景 process
# ---------------------------------------------------------------------------

def _is_windows() -> bool:
    return sys.platform == "win32"


def _try_acquire_mutex() -> bool:
    """嘗試取得 Named Mutex。
    - 拿得到 → 沒有其他實例在跑；我們 release 後回傳 False
      （這個函式只「試探」，拿到也不留著；後續啟動 server 的 process 才是真正的 owner）
    - 拿不到 → 已有實例在跑
    """
    if not _is_windows():
        # 非 Windows 環境（跑在 Linux runner 測試時）一律視為「無實例」
        return True  # True = 我能拿 = 無實例
    try:
        import win32event  # type: ignore
        import win32api    # type: ignore
        import winerror    # type: ignore
        mutex = win32event.CreateMutex(None, False, MUTEX_NAME)
        err = win32api.GetLastError()
        if err == winerror.ERROR_ALREADY_EXISTS:
            # 已有實例
            try:
                win32api.CloseHandle(mutex)
            except Exception:
                pass
            return False
        # 我能拿 = 無實例；立刻釋放，server 端會重新搶
        try:
            win32api.CloseHandle(mutex)
        except Exception:
            pass
        return True
    except ImportError:
        # 沒裝 pywin32 → fallback 到「用 port 5000 是否在 listen」當偵測
        return _is_port_free()


def _is_port_free() -> bool:
    """Fallback：用 socket 嘗試 bind port 5000 判斷。"""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", SERVER_PORT))
        return True  # bind 成功 = 沒人用 = 無實例
    except OSError:
        return False
    finally:
        try:
            s.close()
        except Exception:
            pass


def _read_ota_token() -> str | None:
    """從 %APPDATA%/GX20-PW3335/ota_token 讀 OTA token；找不到回 None。"""
    try:
        if not os.path.exists(OTA_TOKEN_PATH):
            return None
        with open(OTA_TOKEN_PATH, "r", encoding="utf-8") as f:
            tok = f.read().strip()
        return tok or None
    except Exception:
        return None


def _call_shutdown() -> tuple[bool, str]:
    """POST /api/admin/shutdown。回 (success, message)。"""
    tok = _read_ota_token()
    if not tok:
        return False, "找不到 OTA token（檔案不存在或為空）"
    url = f"http://127.0.0.1:{SERVER_PORT}/api/admin/shutdown"
    req = urlrequest.Request(url, method="POST")
    req.add_header("X-OTA-Token", tok)
    req.add_header("Content-Type", "application/json")
    try:
        with urlrequest.urlopen(req, timeout=3, data=b"{}") as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return True, f"server 回應 {resp.status}: {body[:120]}"
    except URLError as e:
        return False, f"無法連到 server：{e}"
    except Exception as e:
        return False, f"呼叫失敗：{e}"


def _launch_server_detached() -> tuple[bool, str]:
    """啟動真正的 server 在背景執行（detached process），關閉 launcher 時不會被殺。

    PyInstaller --onefile 包成 gx20.exe 時，sys.executable 就是 gx20.exe 自己；
    我們用環境變數 GX20_MODE=server 通知 exe 進入「server 模式」（跳過 launcher、
    直接跑 watchdog + app.py）。

    為什麼不直接 subprocess.Popen + DETACHED_PROCESS：
        - gx20.exe 是 frozen binary，跑 subprocess.Popen([sys.executable, ...])
          在 PyInstaller --onefile 環境下，child 會重新走一遍 bootloader，
          環境變數可正確傳遞，比 spawn 一個新的 python.exe 還可靠。
    """
    if not _is_windows():
        # 開發模式（Linux runner 測試）只回訊息不真的啟動
        return True, "非 Windows 環境，略過實際啟動（dev 模式）"

    try:
        import subprocess
        env = os.environ.copy()
        env["GX20_MODE"] = "server"

        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP = 脫離 parent
        DETACHED_PROCESS = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        flags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP

        # 關閉 stdin/stdout/stderr 避免卡住
        creationflags = flags
        subprocess.Popen(
            [sys.executable],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=creationflags,
            cwd=os.path.dirname(os.path.abspath(__file__)),
        )
        return True, "已背景啟動 server"
    except Exception as e:
        return False, f"啟動失敗：{e}"


# ---------------------------------------------------------------------------
# Tkinter 對話框
# ---------------------------------------------------------------------------

def _ask(title: str, message: str) -> bool:
    """顯示 Yes/No 對話框，回傳 True=Yes / False=No。"""
    root = tk.Tk()
    root.withdraw()  # 隱藏主視窗
    root.attributes("-topmost", True)  # 置頂
    try:
        ans = messagebox.askyesno(title, message)
    finally:
        try:
            root.destroy()
        except Exception:
            pass
    return ans


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def main() -> int:
    if os.environ.get("GX20_MODE") == "server":
        # 內部 fork：被 launcher 拉起來跑真正的 watchdog + app.py
        # （不視窗化、跑 watchdog 邏輯；保留舊有 OTA 自我重啟鏈）
        # 為避免無限遞迴：再進 server mode 就直接 import app.main
        try:
            # 模擬 ota_watchdog.bat 的行為：
            #   - code 0     = 正常重啟 → 立即重啟
            #   - code != 0  = crash     → 3 秒後重啟
            #   - SHUTDOWN_EXIT_CODE (42) = launcher 要求的「完全關閉」→ 不重啟，break
            import app as _app_mod
            import ota as _ota_mod
            SHUTDOWN_EXIT_CODE = getattr(_ota_mod, "SHUTDOWN_EXIT_CODE", 42)
            while True:
                try:
                    _app_mod.main()
                    code = 0
                except SystemExit as e:
                    code = e.code if isinstance(e.code, int) else 0
                except KeyboardInterrupt:
                    print("[launcher/server] Ctrl+C, 離開 watchdog", file=sys.stderr)
                    return 0
                except Exception as e:  # noqa: BLE001
                    print(f"[launcher/server] app.main 崩潰：{e}", file=sys.stderr)
                    code = 1
                # launcher 要求的關閉：不要重啟
                if code == SHUTDOWN_EXIT_CODE:
                    print(f"[launcher/server] 收到 SHUTDOWN_EXIT_CODE={SHUTDOWN_EXIT_CODE}, 關閉並離開", file=sys.stderr)
                    return 0
                # 其他：code 0 = 立即重啟；code != 0 = 3 秒後重啟
                if code == 0:
                    print("[launcher/server] code=0, 立即重啟", file=sys.stderr)
                else:
                    print(f"[launcher/server] code={code}, 3 秒後重啟", file=sys.stderr)
                    time.sleep(3)
        except Exception as e:  # noqa: BLE001
            print(f"[launcher/server] 啟動失敗：{e}", file=sys.stderr)
            return 1
        return 0

    # ----- launcher 模式 -----
    has_instance = not _try_acquire_mutex()

    if not has_instance:
        # 沒人跑 → 問要不要啟動
        ans = _ask(
            "GX20 + PW3335 Web Monitor",
            "目前沒有 GX20 監視器在背景執行。\n\n"
            "是否要啟動？\n\n"
            "（選 Yes 會在背景啟動，server 跑在 127.0.0.1:5000）",
        )
        if not ans:
            return 0
        ok, msg = _launch_server_detached()
        if not ok:
            _ask(
                "啟動失敗",
                f"無法啟動 server：\n\n{msg}\n\n請檢查：\n"
                "  1) 是否有管理員權限\n"
                "  2) 5000 port 是否被其他程式佔用\n"
                "  3) 確認 gx20.exe 放在有寫入權限的位置",
            )
            return 1
        # 給 server 1 秒時間起來（不要等太久，server 啟動是 daemon thread）
        time.sleep(1.0)
        return 0
    else:
        # 已有實例 → 問要不要終止
        ans = _ask(
            "GX20 + PW3335 Web Monitor",
            "GX20 監視器目前正在背景執行。\n\n"
            "是否要終止？\n\n"
            "（選 Yes 會呼叫 /api/admin/shutdown 安全退出）",
        )
        if not ans:
            return 0
        ok, msg = _call_shutdown()
        if not ok:
            _ask("終止失敗", f"無法終止 server：\n\n{msg}\n\n可改用：\n"
                            "  工作管理員 → 結束 gx20.exe / pythonw.exe")
            return 1
        return 0


if __name__ == "__main__":
    sys.exit(main())

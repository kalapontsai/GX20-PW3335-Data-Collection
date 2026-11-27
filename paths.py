# -*- coding: utf-8 -*-
"""
paths.py
========
統一解析應用程式目錄，相容一般 Python 執行與 Nuitka standalone 打包。

- get_bundle_dir(): 唯讀資源所在目錄（templates/、static/）。
  - Nuitka 編譯後位於執行檔所在目錄（例如 D:\\sampo\\GX20\\dist）。
  - 一般 Python 執行位於專案根目錄。
- get_app_root(): 執行期可寫資料根目錄（data/、config/、logs/）。
  - 若環境變數 GX20_ROOT 有設定，優先使用。
  - 若為 Nuitka standalone 且執行檔位於名為 dist 的子目錄，則回傳上一層目錄
    （例如 D:\\sampo\\GX20\\dist\\gx20_monitor.exe → D:\\sampo\\GX20）。
  - 其餘情況回傳 get_bundle_dir()。
"""

import os
import sys


def is_compiled() -> bool:
    """判斷是否以 Nuitka / frozen 可執行檔模式執行。"""
    return "__compiled__" in globals() or bool(getattr(sys, "frozen", False))


def get_bundle_dir() -> str:
    """回傳 static/ 與 templates/ 所在的唯讀資源目錄。"""
    if is_compiled():
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def get_app_root() -> str:
    """回傳 data/、config/、logs/ 所在的執行期根目錄。"""
    env_root = os.environ.get("GX20_ROOT", "").strip()
    if env_root:
        return os.path.abspath(env_root)
    bundle_dir = get_bundle_dir()
    if is_compiled() and os.path.basename(bundle_dir).lower() == "dist":
        return os.path.dirname(bundle_dir)
    return bundle_dir

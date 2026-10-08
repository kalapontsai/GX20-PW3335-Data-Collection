# -*- mode: python ; coding: utf-8 -*-
# ============================================================
# gx20.spec — PyInstaller 設定
#
# 把整個 GX20 + PW3335 Web Monitor 包成單一 gx20.exe（windows-x64）
#
# 用法（在本機開發）：
#   pip install pyinstaller
#   pyinstaller gx20.spec --clean
#
# 產物：dist/gx20.exe（單檔，約 25 MB）
#
# GitHub Action 會跑同樣指令並上傳 artifacts。
# 詳見 .github/workflows/build-windows.yml
# ============================================================

# --onefile：打成單一 exe
# --windowed：背景跑不彈 console（用 launcher.py 的 Tkinter 對話框，
#             進 server mode 後 stdout/stderr 都 DEVNULL，不會視窗化）
# --name gx20：產出檔名
# --add-data 與 --collect-all：Flask-SocketIO / eventlet / engineio 需要
#                              連同 metadata 一起包，否則執行期 ImportError
block_cipher = None


a = Analysis(
    ['launcher.py'],
    pathex=[],
    binaries=[],
    datas=[
        # templates / static / config 範例都要包進 exe，server 開機時才找得到
        ('templates', 'templates'),
        ('static',    'static'),
        ('config/settings.example.json', 'config/settings.example.json'),
    ],
    hiddenimports=[
        # Flask-SocketIO / eventlet / engineio 的子模組（避免 ImportError）
        'flask_socketio',
        'engineio.async_drivers.threading',
        'socketio',
        # 我們自己的模組
        'app', 'storage', 'gx20_reader', 'pw3335_reader', 'lttb',
        'ota', 'config', 'whitelist',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 不打包的（節省體積）
        'tkinter.test',
        'unittest',
        'pydoc',
        'doctest',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='gx20',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,             # 用 UPX 壓縮（如果系統裝了 UPX）
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,        # 不彈 console（背景跑）
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,            # 之後可加 .ico
)

# -*- mode: python ; coding: utf-8 -*-

import os

from PyInstaller.utils.hooks import collect_data_files

# 本 spec 位于 deploy/ 下（2026-09 从仓库根移入），而 public\ 与 desktop_launcher.py
# 仍在仓库根。PyInstaller 解析 datas / 脚本路径时以调用时的 CWD 为准，因此这里显式
# 用 SPECPATH 推出仓库根，保证在任意目录执行 pyinstaller 都能打对包。
PROJECT_ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

block_cipher = None
datas = [
    (os.path.join(PROJECT_ROOT, "public"), "public"),
]
datas += collect_data_files("tzdata")


a = Analysis(
    [os.path.join(PROJECT_ROOT, "desktop_launcher.py")],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "dbfread",
        "msoffcrypto",
        "openpyxl",
        "pymysql",
        "pypinyin",
        "sqlalchemy",
        "xlrd",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="DataConverterTool",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="DataConverterTool",
)

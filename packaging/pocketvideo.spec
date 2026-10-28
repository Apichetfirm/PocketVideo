# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller specification for PocketVideo Windows x64 Desktop Application."""

from pathlib import Path
import os

repo_root = Path(SPECPATH).resolve().parent
src_root = repo_root / "src"
vendor_ffmpeg_bin = repo_root / "vendor" / "ffmpeg" / "bin"

binaries_list = []
for exe_name in ("ffmpeg.exe", "ffprobe.exe"):
    candidate = vendor_ffmpeg_bin / exe_name
    if candidate.exists():
        binaries_list.append((str(candidate), "bin"))

datas_list = [
    (str(repo_root / "LICENSE"), "."),
    (str(repo_root / "THIRD_PARTY_NOTICES.md"), "."),
    (str(repo_root / "README.md"), "."),
]

block_cipher = None

a = Analysis(
    [str(src_root / "pocketvideo" / "main.py")],
    pathex=[str(src_root)],
    binaries=binaries_list,
    datas=datas_list,
    hiddenimports=[
        "PySide6.QtCore",
        "PySide6.QtGui",
        "PySide6.QtWidgets",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "scipy", "pandas"],
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
    name="PocketVideo",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch="x86_64",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="PocketVideo",
)

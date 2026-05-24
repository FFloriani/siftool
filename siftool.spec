# -*- mode: python ; coding: utf-8 -*-
import os
import sys
from pathlib import Path

# Get tkinterdnd2 module path dynamically
# We must add the local venv path if it's not active in sys.path
venv_site = Path("E:/Trabalho/Floriani/vscode/exifcleaner/.venv/Lib/site-packages")
if venv_site.exists() and str(venv_site) not in sys.path:
    sys.path.insert(0, str(venv_site))

import tkinterdnd2
dnd_path = os.path.dirname(tkinterdnd2.__file__)
tkdnd_folder = os.path.join(dnd_path, 'tkdnd')

block_cipher = None

a = Analysis(
    ['siftool.py'],
    pathex=[],
    binaries=[],
    datas=[
        (tkdnd_folder, 'tkinterdnd2/tkdnd'),
        ('siftool.ico', '.')
    ],
    hiddenimports=['tkinterdnd2', 'piexif', 'PIL'],
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
    name='siftool',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # Console = False hides the console window for a clean GUI app launch
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='siftool.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='siftool',
)

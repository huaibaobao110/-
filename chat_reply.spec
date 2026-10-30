# -*- mode: python ; coding: utf-8 -*-
import sys
from PyInstaller.utils.hooks import collect_all

block_cipher = None

datas = [
    ('ocr_win.ps1', '.'),
    ('config.example.json', '.'),
]
hiddenimports = [
    'PIL._tkinter_finder',
    'customtkinter',
    'darkdetect',
]
if sys.platform == 'win32':
    hiddenimports += [
        'win32timezone',
        'win32clipboard',
        'win32api',
        'win32con',
        'win32gui',
        'win32process',
    ]

tmp_ret = collect_all('customtkinter')
datas += tmp_ret[0]

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
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
    name='ChatReplyHelper',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='ChatReplyHelper',
)

if sys.platform == 'darwin':
    app = BUNDLE(
        coll,
        name='ChatReplyHelper.app',
        icon=None,
        bundle_identifier='com.local.chatreplyhelper',
    )

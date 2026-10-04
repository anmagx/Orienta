# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for a portable, windowed Orienta executable."""

from pathlib import Path


ROOT = Path(SPECPATH).parent
ICON = ROOT / "src" / "img" / "icon.ico"

datas = [
    (str(ICON), "src/img"),
    (str(ROOT / "src" / "img" / "orienta_logo.png"), "src/img"),
    (str(ROOT / "src" / "themes" / "dark.qss"), "src/themes"),
    (str(ROOT / "src" / "themes" / "light.qss"), "src/themes"),
]

a = Analysis(
    [str(ROOT / "orienta.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Orienta",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ICON),
)

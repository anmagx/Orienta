# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for a portable, windowed Orienta executable."""

from pathlib import Path
import sys

from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo,
    StringFileInfo,
    StringStruct,
    StringTable,
    VarFileInfo,
    VarStruct,
    VSVersionInfo,
)


ROOT = Path(SPECPATH).parent
sys.path.insert(0, str(ROOT))

from src.config.config import APP_NAME, APP_VERSION


EXECUTABLE_NAME = f"Orienta-{APP_VERSION}"

version_parts = APP_VERSION.split(".")
if not 1 <= len(version_parts) <= 4 or any(
    not part.isascii() or not part.isdigit() for part in version_parts
):
    raise ValueError(
        f"APP_VERSION must contain one to four numeric components: {APP_VERSION!r}"
    )

version_numbers = tuple(int(part) for part in version_parts)
if any(part > 0xFFFF for part in version_numbers):
    raise ValueError(f"APP_VERSION components must be at most 65535: {APP_VERSION!r}")

version_numbers += (0,) * (4 - len(version_numbers))

ICON = ROOT / "src" / "img" / "icon.ico"

datas = [
    (str(ICON), "src/img"),
    (str(ROOT / "src" / "img" / "orienta_logo.png"), "src/img"),
    (str(ROOT / "src" / "themes" / "dark.qss"), "src/themes"),
    (str(ROOT / "src" / "themes" / "light.qss"), "src/themes"),
]

version_info = VSVersionInfo(
    ffi=FixedFileInfo(
        filevers=version_numbers,
        prodvers=version_numbers,
        mask=0x3F,
        flags=0,
        OS=0x40004,
        fileType=0x1,
        subtype=0,
        date=(0, 0),
    ),
    kids=[
        StringFileInfo(
            [
                StringTable(
                    "040904B0",
                    [
                        StringStruct("FileDescription", APP_NAME),
                        StringStruct("FileVersion", APP_VERSION),
                        StringStruct("InternalName", APP_NAME),
                        StringStruct("OriginalFilename", f"{EXECUTABLE_NAME}.exe"),
                        StringStruct("ProductName", APP_NAME),
                        StringStruct("ProductVersion", APP_VERSION),
                    ],
                )
            ]
        ),
        VarFileInfo([VarStruct("Translation", [1033, 1200])]),
    ],
)

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
    name=EXECUTABLE_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    version=version_info,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ICON),
)

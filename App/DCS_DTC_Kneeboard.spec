# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the DCS MiG-29 DTC Kneeboard Utility (single-file exe).

Targets PyInstaller >= 6.0 (no bytecode cipher / no a.zipfiles, per 6.x).

Build (run from the App folder):
    pyinstaller DCS_DTC_Kneeboard.spec --noconfirm --clean

Output:
    dist/DCS_DTC_Kneeboard.exe

Bundled into the exe (extracted to sys._MEIPASS at runtime):
    * fonts/*.ttf        - kneeboard_renderer + app_icon load these by name.
    * hook_template.lua  - hook_manager copies it into Saved Games on launch.
    * icon.ico           - window title-bar icon (also embedded as the exe icon).
    * customtkinter assets - theme JSON + widget assets the toolkit needs.
"""

import os

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# Resolve the App folder robustly, regardless of where pyinstaller is invoked.
# PyInstaller injects SPECPATH (the spec's directory); fall back if absent.
try:
    BASE_DIR = SPECPATH  # type: ignore[name-defined]  # noqa: F821 - injected by PyInstaller
except NameError:
    try:
        BASE_DIR = os.path.dirname(os.path.abspath(SPEC))  # type: ignore[name-defined]  # noqa: F821
    except NameError:
        BASE_DIR = os.path.abspath(os.getcwd())

# --- data files (source_path, dest_dir_in_bundle) --------------------------
datas = [
    (os.path.join(BASE_DIR, "fonts"), "fonts"),
    (os.path.join(BASE_DIR, "hook_template.lua"), "."),
]

# customtkinter ships JSON themes + assets that must travel with the exe.
datas += collect_data_files("customtkinter")

# Bundle the window icon as a runtime asset too (main.py reads it from _MEIPASS).
icon_file = os.path.join(BASE_DIR, "icon.ico")
icon_arg = icon_file if os.path.isfile(icon_file) else None
if icon_arg:
    datas.append((icon_file, "."))

# --- hidden imports --------------------------------------------------------
# pystray selects its platform backend (e.g. _win32) at runtime; collecting its
# submodules makes sure PyInstaller bundles the backend it cannot see statically.
hiddenimports = collect_submodules("pystray")

a = Analysis(
    ["main.py"],
    pathex=[BASE_DIR],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="DCS_DTC_Kneeboard",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,                 # GUI app: no console window (see BUILD.md to debug)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon_arg,
)

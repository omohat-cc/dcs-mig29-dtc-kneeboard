# Building the DCS MiG-29 DTC Kneeboard Utility

This packages the Python source into a single Windows executable,
`DCS_DTC_Kneeboard.exe`, with the fonts, the confirmation sound, the Lua hook
template, the window icon and customtkinter's assets all embedded. No Python
install is needed on the target machine.

> Build on **Windows** to produce the Windows exe. PyInstaller is not a
> cross-compiler: a build on macOS/Linux produces a binary for *that* OS, not
> for Windows.

---

## 1. Prerequisites

- Windows 10/11
- Python 3.11+ (64-bit), with `py` or `python` on `PATH`
- The project dependencies plus the dev tooling:

```bat
cd App
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt -r requirements-dev.txt
```

`requirements-dev.txt` pulls in `pyinstaller>=6.0.0`. The spec is written for
PyInstaller 6.x.

---

## 2. One-time: generate the icon

The window/exe icon (`icon.ico`) and a PNG preview (`icon.png`) are produced
from `app_icon.py`:

```bat
python generate_icon.py
```

This writes `icon.ico` and `icon.png` into the `App` folder. The build still
works if the icon is missing (the spec falls back to no icon), but generating it
gives the exe and window a proper icon. The icon is committed, so you only need
to re-run this if you change the design in `app_icon.py`.

---

## 3. Build the exe

From the `App` folder:

```bat
pyinstaller DCS_DTC_Kneeboard.spec --noconfirm --clean
```

Output:

```
App\dist\DCS_DTC_Kneeboard.exe      <- ship this
App\build\                          <- intermediates (safe to delete)
```

`--clean` clears PyInstaller's cache; `--noconfirm` overwrites a previous
`dist`/`build` without prompting.

### What the spec bundles

| Item | Source | Why |
|---|---|---|
| `fonts/*.ttf` | `App/fonts/` | `kneeboard_renderer` and `app_icon` load these by name |
| `sounds/*.wav` | `App/sounds/` | the confirmation sound `sound.play_sound` plays on each render |
| `hook_template.lua` | `App/hook_template.lua` | `hook_manager` copies it into Saved Games on launch |
| `icon.ico` | `App/icon.ico` | window title-bar icon (also embedded as the exe icon) |
| customtkinter assets | the installed package | theme JSON + widget assets the toolkit needs at runtime |
| pystray backend | `collect_submodules("pystray")` | the platform tray backend PyInstaller cannot see statically |

At runtime these are unpacked to a temporary folder and resolved through
`sys._MEIPASS` (handled in `kneeboard_renderer`, `hook_manager`, `app_icon` and
`main`).

---

## 4. First run of the exe

- `config.json` is written next to the exe on first launch (it is **not**
  bundled, so the user keeps their settings beside the exe).
- A diagnostic log, `dtc_kneeboard_utility.log`, is written next to the exe.
- If any of the three DCS paths cannot be auto-detected, the first-run dialog
  prompts for them.

Distribute just the single exe. Everything else is either embedded or generated
on first run.

---

## 5. Reference: one-line build without the spec

The spec is the supported path (it handles the customtkinter assets, the tray
backend and the icon). If you ever need the raw command, customtkinter requires
its package data to be added explicitly:

```bat
pyinstaller --noconfirm --clean --onefile --windowed ^
  --name DCS_DTC_Kneeboard ^
  --icon icon.ico ^
  --add-data "fonts;fonts" ^
  --add-data "sounds;sounds" ^
  --add-data "hook_template.lua;." ^
  --add-data "icon.ico;." ^
  --collect-data customtkinter ^
  --collect-submodules pystray ^
  main.py
```

(On macOS/Linux the `--add-data` separator is `:` not `;`.)

---

## 6. Troubleshooting

- **A traceback flashes and the exe exits.** Temporarily set `console=False` to
  `console=True` in `DCS_DTC_Kneeboard.spec`, rebuild, and run from a terminal
  to read the error.
- **`KneeboardRenderError: Missing bundled font(s)`.** The `fonts/` folder was
  not bundled, check the `datas` entry in the spec and that `App/fonts/`
  contains all eight TTFs.
- **Tray icon missing / close button quits instead of minimising.** The tray is
  only enabled on Windows; confirm `collect_submodules("pystray")` is in the
  spec so the Win32 backend is bundled.
- **customtkinter `FileNotFoundError` for a theme JSON.** `--collect-data
  customtkinter` (in the spec via `collect_data_files`) is missing, re-add it.
- **Antivirus flags the onefile exe.** A known false positive for PyInstaller
  one-file bootloaders. Prefer a signed build, or a one-folder build
  (`COLLECT`) if the SmartScreen warning is a problem for distribution.

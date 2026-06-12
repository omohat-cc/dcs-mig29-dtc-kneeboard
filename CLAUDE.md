# CLAUDE.md — DCS MiG-29 DTC Kneeboard Utility

Project guidance for Claude Code. Read this first, then the technical spec
(`docs/technical-spec.md`), which is the authoritative design.

## What this is

A two-component utility that auto-generates a kneeboard JPG summarising the
MiG-29A's DTC (Data Transfer Cartridge) configuration in DCS World:

1. **DCS Lua hook** (`App/hook_template.lua`) — runs inside DCS, detects a
   MiG-29 spawn and writes a small trigger file. Does the absolute minimum.
2. **External watcher/processor** (the Python app in `App/`) — a Windows
   customtkinter GUI that watches for the trigger, extracts the DTC from DCS's
   binary temp files, resolves it, and renders the kneeboard image.

## Status (2026-06-01)

Working end-to-end on Windows: spawning a MiG-29 in DCS generates the kneeboard.
All modules, the GUI (`main.py`) and PyInstaller `--onefile` packaging are done.
Developed on macOS; built and run on a Windows gaming PC. Remaining work is
enhancements and broader live testing.

## Conventions (non-negotiable)

- **UK English** (colour, behaviour, minimise). **No em dashes** — use commas,
  parentheses or hyphens. Spec-defined glyphs (e.g. the `—` NO_VALUE placeholder)
  are exempt.
- **Type hints** on all functions; **docstrings** on public functions.
- **Logging** via the `logging` module, never `print` (module `_main()` CLIs aside).
- **All file I/O** wrapped in try/except with a meaningful message. The watcher
  and GUI are long-running and must never crash on bad input.
- **Runtime third-party deps are ONLY**: `customtkinter`, `Pillow`, `slpp`,
  `pystray`. Everything else is stdlib. Check the existing stack before adding any
  dependency.
- Paths via `pathlib`/`os`. Windows is the target but development is on macOS, so
  guard any Windows-only API (e.g. the registry read in `config.py`) behind a
  `platform.system()` check.

## Layout

```
DTC Kneeboard Utility/
├── CLAUDE.md                     <- this file
├── README.md                     public-facing overview
├── LICENSE                       GPL-3.0
├── App/                          <- all source + build files
│   ├── main.py                   GUI entry point (customtkinter window, tray, startup wiring)
│   ├── app_icon.py               PIL-only programmatic app/tray icon
│   ├── generate_icon.py          writes icon.ico / icon.png
│   ├── config.py                 load/save config.json + DCS path auto-detection
│   ├── hook_manager.py           install/update the DCS Lua hook (version-checked)
│   ├── trigger_watcher.py        daemon thread: poll trigger -> run the full pipeline
│   ├── bin_parser.py             extract the DTC JSON from ~tr*.bin temp files
│   ├── dtc_processor.py          resolve DTC JSON -> display-ready sections (ProcessedDTC)
│   ├── beacon_parser.py          parse beacons.lua -> {freq_khz: station} ADF lookup
│   ├── kneeboard_renderer.py     Pillow: draw the 1536x2048 JPEG ("Omar's Grid")
│   ├── hook_template.lua         the Lua hook source (bundled; copied into Saved Games)
│   ├── fonts/                    8 bundled TTFs + their OFL-*.txt licence texts
│   ├── icon.ico / icon.png       window + exe icon (committed assets)
│   ├── DCS_DTC_Kneeboard.spec    PyInstaller --onefile spec
│   ├── BUILD.md                  how to build the exe on Windows
│   ├── requirements.txt          runtime deps (defines what PyInstaller bundles)
│   ├── requirements-dev.txt      + pytest, ruff, pyinstaller
│   └── test files/               test suite (custom Checks harness; fixtures/ is a
│                                 git-ignored local folder/symlink for DCS-owned samples)
├── docs/
│   ├── technical-spec.md         authoritative design spec
│   ├── dtc-format-reference.md   MiG-29 DTC format reference
│   ├── beacon-parser-findings.md beacons.lua parsing write-up
│   ├── development-history/      prompt/output history from the AI-assisted build
│   └── images/                   reference screenshots
├── tools/dcs-probe/              Lua probes used to locate the DTC temp files
├── Designs/                      kneeboard visual design (the renderer mirrors this)
├── .github/workflows/build.yml   CI: lint + tests, then PyInstaller exe (artifact/release)
├── Probe logs/                   real DCS ~tr*.bin captures used as test fixtures (large; git-ignored)
└── Crash Logs/                   DCS crash dumps (not project source; git-ignored)
```

## Architecture and data flow

```
DCS: player spawns in a MiG-29
  -> hook_template.lua writes  <Saved Games>\DCS\Logs\dtc_kneeboard_trigger.json
  -> trigger_watcher (polls every 2s) detects it, waits 3s for DCS to finish writing, then:
        bin_parser.extract_dtc_from_directory(temp)    ~tr*.bin  -> DTC JSON dict
        dtc_processor.process_dtc(dict)                  -> ProcessedDTC (all sections)
        trigger_watcher.resolve_adf_beacon_names(...)    fills ADF names from beacons.lua
        kneeboard_renderer.render_kneeboard(...)          -> 000_dtc_config.jpg
  -> DCS refreshes the kneeboard on the next spawn

main.py wires config + hook_manager + trigger_watcher behind the GUI and tray.
```

## Non-obvious gotchas (read before editing the relevant area)

1. **Logging -> GUI is a single bridge.** `main.py` attaches one
   `QueueLogHandler` to the root logger; every module's log records flow into the
   status panel. Do NOT also pass a `log_callback` to `TriggerWatcher` or
   `install_or_update_hook` — they already log via their own loggers, so a
   callback would print every line twice.
2. **Never store the AppConfig as `self.config`** on a Tk/CTk widget — Tk widgets
   own a `.config()` method and shadowing it breaks the toolkit. `main.py` uses
   `self.app_config`.
3. **System tray is Windows/Linux only.** pystray's macOS backend needs the main
   thread, which Tk owns, so `_tray_supported()` disables it on macOS (close then
   quits instead of minimising). Tray menu callbacks run on pystray's thread and
   must not touch Tk — they enqueue a callable onto `_command_queue`, drained by
   the same 100 ms GUI pump (`_poll_queues`).
4. **beacons.lua is a Lua *script*, not a table literal** — `slpp` cannot read it
   raw (it has `dofile`/`local` statements, `_('NAME')` gettext wrappers, bare
   `BEACON_TYPE_*` constants, `;` separators). `beacon_parser` extracts and
   sanitises the `beacons` table, then uses slpp, with a pure-regex fallback. NDB =
   any `BEACON_TYPE_*HOMER*`. The file stores Hz; the lookup key is kHz. Full
   write-up: `docs/beacon-parser-findings.md`.
5. **ADF beacon names are resolved in `trigger_watcher.resolve_adf_beacon_names`**
   (mutates the ProcessedDTC), NOT in `dtc_processor`, which leaves `name=None`.
   The renderer uses `.name` if set, else the raw `"342 kHz AM"` fallback.
6. **bin_parser record prefix is a 2-byte big-endian length, not a literal "N ".**
   Strip it only when it equals `len(run) - 2` (self-validating). See the module
   docstring.
7. **The output path is exact:** `<Saved Games>\Kneeboard\MiG-29 Fulcrum\000_dtc_config.jpg`
   (`Kneeboard` no 's', `MiG-29 Fulcrum` with a space, `000_` prefix sorts the
   page first).
8. **PyInstaller:** `fonts/` and `hook_template.lua` are DATA, not imports, so the
   spec bundles them explicitly; `kneeboard_renderer`, `hook_manager` and
   `app_icon` resolve `sys._MEIPASS` at runtime.

## Build, run, test

- **Build the exe (Windows, from `App/`):**
  `pyinstaller DCS_DTC_Kneeboard.spec --noconfirm --clean` -> `dist/DCS_DTC_Kneeboard.exe`.
  Full details and troubleshooting in `App/BUILD.md`.
- **Run from source (Windows):** `python main.py`.
- **Tests (macOS or Windows, from `App/`):** `python "test files/test_integration.py"`
  (also `test_config.py`, `test_hook_manager.py`, etc.). Custom `Checks` harness,
  no pytest required; fixtures that are absent skip cleanly. beacons.lua fixtures
  are DCS-owned and live outside the repo: set `DCS_BEACONS_DIR` or put/symlink
  them at `App/test files/fixtures/` (git-ignored).
- **CI (`.github/workflows/build.yml`):** lint + tests then a PyInstaller build on
  `windows-latest`. Manual runs upload the exe as an artifact; pushing a `v*` tag
  attaches it to a draft GitHub release. This replaces building on the gaming PC.
- **Lint / syntax (macOS dev):** `.venv/bin/python -m ruff check .` and
  `python -m py_compile <files>`.
- **macOS dev limitation:** the `App/.venv` here (Homebrew Python 3.14) has **no
  `_tkinter`** and does **not** include customtkinter/pystray/pyinstaller, so the
  GUI cannot be launched on the Mac. Verify GUI/`main.py` changes with
  `py_compile` + `ruff` (and, if useful, a faked-`tkinter` import smoke test);
  do live GUI runs and the PyInstaller build on the Windows PC.

## Runtime file locations

- `config.json` and `dtc_kneeboard_utility.log` are written **next to the exe**
  (next to the source in dev). Keep the exe in a user-writable folder (not
  `C:\Program Files\`) so settings persist.
- DCS hook: `<Saved Games>\DCS\Scripts\Hooks\dtc_kneeboard_hook.lua` (installed or
  updated on launch).
- Kneeboard output: `<Saved Games>\Kneeboard\MiG-29 Fulcrum\000_dtc_config.jpg`.

## Deeper docs

- Authoritative spec: `docs/technical-spec.md`
- Build instructions: `App/BUILD.md`
- beacons.lua parsing findings: `docs/beacon-parser-findings.md`

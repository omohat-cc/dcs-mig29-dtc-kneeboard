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

## Status (2026-06-24)

Working end-to-end on Windows: spawning a MiG-29 in DCS generates the kneeboard,
live-tested in single-player and on a populated multiplayer server. All modules,
the GUI (`main.py`) and PyInstaller `--onefile` packaging are done. Developed on
macOS; built via GitHub Actions (Windows runner). **Released as v1.1.0; v1.2.0
(ground-polling auto-regenerate) is being cut.** Recent enhancements (all
live-confirmed): the multiplayer hook guard (v1.1), reading the *newest* DTC copy
from the temp file, in-memory render dedupe, version-stamped CI builds, the ADF
frequency cross-check shown beneath resolved beacon names, an audible
confirmation sound on kneeboard generation, real-world emitter subtitles on the
SPO-15 threats, an on-demand **Regenerate Kneeboard** button, and an **Exit
button plus single-instance guard** (gotcha 14).

**v1.2.0 - ground-polling auto-regenerate (Item 5, live-confirmed):** the hook
(v1.2) runs an air/ground state machine off the local AGL and publishes a phase
to `dtc_kneeboard_state.json`; while parked the app change-checks the DTC temp
file (~5s) and regenerates on a real edit, pausing airborne. A 3-state **DTC
Watch** label shows it. This **replaced** the planned global regenerate hotkey
(collision risk with DCS/SRS/TacView/VoiceAttack). The generation sound now fires
only on a genuine content change (not a forced re-render of identical content).
**Key live finding (gotcha 15):** the app renders correctly, but DCS caches the
native kneeboard page and only reloads it on respawn, so the live ground-watch is
only visible through a tool that reads the file live (e.g. OpenKneeboard); the
README documents this limitation. Remaining: broader multiplayer testing.

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
│   ├── app_version.py            single source of __version__ (CI stamps it per build)
│   ├── app_icon.py               PIL-only programmatic app/tray icon
│   ├── generate_icon.py          writes icon.ico / icon.png
│   ├── config.py                 load/save config.json + DCS path auto-detection
│   ├── hook_manager.py           install/update the DCS Lua hook (version-checked)
│   ├── trigger_watcher.py        daemon thread: poll trigger -> run the full pipeline
│   ├── bin_parser.py             extract the DTC JSON from ~tr*.bin temp files
│   ├── dtc_processor.py          resolve DTC JSON -> display-ready sections (ProcessedDTC)
│   ├── beacon_parser.py          parse beacons.lua -> {freq_khz: station} ADF lookup
│   ├── kneeboard_renderer.py     Pillow: draw the 1536x2048 JPEG ("Developer's Grid")
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
   When `.name` is set the renderer shows it with the raw `"342 kHz AM"` frequency
   drawn beneath in a smaller font (a cross-check, since a resolved name can differ
   from the common airfield name); when unset it shows the raw frequency alone.
   `_adf_freq_label` formats that frequency string for both paths.
6. **bin_parser record prefix is a 2-byte big-endian length, not a literal "N ".**
   Strip it only when it equals `len(run) - 2` (self-validating). See the module
   docstring.
7. **The output path is exact:** `<Saved Games>\Kneeboard\MiG-29 Fulcrum\000_dtc_config.jpg`
   (`Kneeboard` no 's', `MiG-29 Fulcrum` with a space, `000_` prefix sorts the
   page first).
8. **PyInstaller:** `fonts/` and `hook_template.lua` are DATA, not imports, so the
   spec bundles them explicitly; `kneeboard_renderer`, `hook_manager` and
   `app_icon` resolve `sys._MEIPASS` at runtime.
9. **One temp file holds MANY DTC copies, appended oldest-first.** DCS writes a
   fresh serialisation every time the cartridge changes (e.g. applying a config in
   the spawn-selector DTC manager), so a single `~tr*.bin` accumulates dozens of
   copies (a real capture had 20). `bin_parser._find_json_object` returns the
   NEWEST complete copy (falling back if the last is truncated); reading the first
   copy returns a stale config, so a mid-session DTC change is never picked up.
   The log line `Using DTC copy N of M (newest valid)` shows which was used.
   **Re-verified 2026-06-24** against a real 13-copy capture (copies 1-7 = config
   A, copies 8-13 = config B): the parser returned copy 13 and the render
   reflected config B. If the in-sim kneeboard still shows the old config, that is
   DCS's page cache (gotcha 15), NOT a stale read - diagnose by opening the JPEG
   file directly, not by assuming the parser regressed.
10. **The MiG-29 spawn hook (v1.1) ignores non-local slot changes.** In MP, DCS
    calls `onPlayerChangeSlot(id)` for EVERY player; each call re-armed the
    deferred poll, which re-detected our (still MiG-29) local unit and re-wrote the
    trigger, spamming it every 30-60s. The hook guards on `net.get_my_player_id()`
    (pcall-wrapped; falls through to arm in single-player, where `net` is absent).
11. **Render dedupe is in-memory only, deliberately not persisted.** The watcher
    keeps a SHA-256 of the resolved DTC (`ProcessedDTC.to_dict()` carries no
    timestamp, so identical configs hash identically) and skips the render when it
    is unchanged. Not saved to disk: the first spawn after each launch always
    renders (so it can never get "stuck"), and no working file is left in the
    kneeboard folder (a stale sidecar from an earlier build is deleted on sight).
    `generate_kneeboard(force=True)` bypasses it (for the future manual regenerate).
12. **The generation sound is Windows-only and fires only on a genuine DTC
    content change.** `main.py` passes `TriggerWatcher` an `on_generated`
    callback; the watcher invokes it (in `_notify_generated`) only when the
    resolved DTC fingerprint actually changed, never on a dedupe skip, and never
    on a forced re-render of identical content (so a redundant Regenerate click,
    or an identical respawn, stays silent rather than sounding a false "done").
    The callback runs on the
    watcher's daemon thread and calls `sound.play_sound`, which uses stdlib
    `winsound` with `SND_ASYNC` (non-blocking, so it never touches Tk and needs no
    marshalling) and is a logged no-op off Windows. The asset is the bundled
    `sounds/kneeboard_generated.wav` (winsound needs a PCM WAV); winsound plays to
    the Windows *default* output device, so in VR the headset must be that device.
    Any playback failure is caught and logged, never raised.
13. **The manual regenerate path runs off the Tk thread and reuses the sound
    callback.** The "Regenerate Kneeboard" button (`main.py._on_regenerate_clicked`)
    calls `watcher.generate_kneeboard(force=True)` on a short daemon worker thread
    (so the temp-dir scan and Pillow render never freeze the GUI), then marshals the
    result back onto the Tk loop via the existing `_command_queue` pump
    (`_on_regenerate_done`). `force=True` bypasses the in-memory dedupe (the user
    explicitly asked for a fresh page). The button is disabled while a rebuild is in
    flight (an `_regenerating` flag, set/cleared only on the Tk thread). Do NOT play
    the confirmation sound in the handler: a forced render of *changed* content
    still fires the watcher's `on_generated` callback (gotcha 12), so playing it
    here would double up; a forced render of unchanged content deliberately stays
    silent. A `None` return surfaces a friendly summary in the status log;
    `generate_kneeboard` has already logged the specific reason on the line above.
14. **Close (X) minimises to the tray; only the Exit button (or the tray's Quit)
    actually quits, and a single-instance guard stops duplicates.** Because X only
    withdraws to the tray (correct for a background watcher), it is easy to forget a
    copy is running and launch another. Two instances both poll the one trigger
    file; whichever polls first consumes (deletes) it and renders, while the *other*
    instance's status log stays silent - the symptom is "the status window did not
    show the spawn, but the sound played and the JPG appeared". The smoking gun in
    the log is two different PyInstaller `_MEIPASS` temp dirs in the "playing
    confirmation sound (...)" lines: one process keeps ONE `_MEIPASS` for its whole
    life, so two distinct dirs means two processes. Fixes (both in `main.py`): a red
    **Exit** button at the top-right of the Settings card that calls `_quit_app`, and
    `_acquire_single_instance()` - a named Windows mutex (`SINGLE_INSTANCE_MUTEX`,
    held for the process lifetime and released by the OS on exit, so there is no
    stale lock). A second launch shows a native "already running" message box and
    exits. Windows-only and never raises (a detection failure must not stop the app
    starting). The mutex name is version-independent, so every guard-bearing build
    blocks every other; only pre-guard builds (before v1.1.0) can still run in
    parallel.
15. **DCS caches the kneeboard page image; a mid-session update needs a page-flip
    to show.** The app writes `000_dtc_config.jpg` correctly, but DCS only
    re-reads a kneeboard page on a page-turn / kneeboard toggle (RSHIFT+K) /
    respawn (design doc 4.5). So after a ground auto-regenerate or a manual
    Regenerate, the *file* is current but the *in-sim page* keeps showing the
    cached (usually the spawn = "first") config until you flip the page. This
    masquerades as "the app is reading the first/stale DTC copy" (gotcha 9) when
    it is not: confirmed 2026-06-24 from a user log + the attached temp file -
    `bin_parser` returned copy 13 of 13 (newest) and the rendered JPEG differed
    from the spawn copy, yet the cockpit page still showed the spawn config.
    **To diagnose any "kneeboard didn't change" report, open the JPEG file
    directly** (`<Saved Games>\Kneeboard\MiG-29 Fulcrum\000_dtc_config.jpg`,
    Windows Photos); if it shows the new config, it is DCS's cache, not the app.
    The watcher logs a page-flip reminder after each real update. There is no
    app-side way to force DCS to reload (DCS owns the page lifecycle).

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
- **Build versioning:** every CI build is version-stamped so downloads are
  distinguishable. The base lives in `app_version.py` (`__version__`); the "Stamp
  build version" step derives the full string (manual: `0.1.0-dev.<run>.g<sha>`;
  `v*` tag: `X.Y.Z`), rewrites `app_version.py` so it is baked into the exe (shown
  in the title bar + startup log), and names the artifact and exe with it. Bump
  the `0.1.0` base in `app_version.py` to start a new series.
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

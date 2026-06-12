# Enhancement handoff prompts (post first Windows test)

Four enhancements, to be done **one per chat, in order**. Each prompt below is
self-contained: open a new Claude Code chat **in the `DTC Kneeboard Utility`
folder** (so `CLAUDE.md` auto-loads) and paste everything between the
`--- PROMPT START ---` / `--- PROMPT END ---` markers as the first message.

**Recommended order: 1 → 2 → 3 → 4.** Why this order and why separate chats:
- **Separate prompts (not one big one):** each chat stays focused and the context
  window does not fill with the other three tasks. The tasks are also dependent.
- **Dependencies:** Item 2 (sound) should land before Item 3 so the regenerate
  button can play it. Item 3 (manual regenerate path) **must** land before Item 4,
  because the hotkey reuses it.
- **Where each is verified:** Item 1 is renderer-only and fully testable on the
  **Mac** (rendering is Pillow-only, no GUI). Items 2, 3, 4 touch the GUI / Windows
  APIs and need a **Windows** run to verify (Mac can only `py_compile` + `ruff`).

After each item, review the change and tell that chat to commit (your standing
rule is no commits without an explicit ask).

---

## Item 1 — Show ADF frequency next to the resolved station name

--- PROMPT START ---
Enhancement for the MiG-29 DTC Kneeboard Utility. Read `CLAUDE.md` first, then
`App/kneeboard_renderer.py`.

**Background (already investigated, do not re-investigate from scratch):** On a
Syria kneeboard, the ADF station for Rene Mouwad Air Base resolved to "KLEYATE".
This is correct, not a bug: in `Syria_beacons.lua` the NDB at Rene Mouwad
(display_name `KLEYATE`, callsign `RA`, type `BEACON_TYPE_AIRPORT_HOMER`,
frequency 450000 Hz = 450 kHz, positionGeo 34.586N/36.003E) IS Rene Mouwad /
Kleyate / Qlayaat Air Base (same airfield, different name). There is no 450 kHz
collision in Syria, so resolution is deterministic.

**Task:** When an ADF beacon name IS resolved, also display its frequency in a
small font (about 65-75% of the station-name size, e.g. name 30 px -> freq ~21 px),
so the pilot can cross-check the frequency on the in-game map if they do not
recognise the resolved name. When the name is NOT resolved, keep the existing raw
`"450 kHz AM"` display.

**Where:** `App/kneeboard_renderer.py`, `_draw_adf_section` (the inner/outer beacon
cells) and possibly `_draw_cell_name`. The beacon dict already carries `freq`
(int kHz), `modulation`, `name`. Font sizes are constants near the top
(`FS_NAME = 30`, `FS_MOD = 21`). The ADF row height is `DEFAULT_ROW_H = 81`, so
there is room for a small freq sub-line under a single-line name; handle the case
where the name wraps to two lines so the freq does not overflow the row.

**Optional (recommend deferring, mention but do not build unless asked):** a static
override table mapping a frequency or beacon name to a preferred display name. Not
needed for correctness; the frequency display solves the real problem.

**Constraints:** runtime deps stay Pillow-only here; UK English, no em dashes, type
hints, docstrings, file I/O in try/except.

**Verify on the Mac (no Windows needed):** run `App/render_test_kneeboards.py`,
which renders the sample DTCs into `App/test_output/`, and eyeball the ADF section
on the Syria/ADF sample. Also `ruff check` and `py_compile`. Then ask me to commit.
--- PROMPT END ---

---

## Item 2 — Play a sound when a kneeboard is generated

--- PROMPT START ---
Enhancement for the MiG-29 DTC Kneeboard Utility. Read `CLAUDE.md` first, then
`App/trigger_watcher.py` and `App/main.py`.

**Goal:** Play a short confirmation sound whenever a kneeboard is successfully
generated, so the pilot gets audible confirmation in VR (the app window is not
visible in the headset).

**Design (keep the watcher GUI-free and testable):**
- Add an optional `on_generated` callback to `TriggerWatcher` (alongside the
  existing constructor params), invoked with the output `Path` after a successful
  `generate_kneeboard()` render. Do NOT play sound from inside the watcher pipeline
  directly; the watcher must stay importable headless.
- In `App/main.py`, pass an `on_generated` callback that plays the sound. Marshal it
  safely (the watcher runs on a daemon thread): play on a short worker thread or via
  the existing `_command_queue` pump rather than blocking.
- **Sound playback uses stdlib only** (runtime deps stay customtkinter/Pillow/slpp/
  pystray). On Windows use `winsound.PlaySound(path, SND_FILENAME | SND_ASYNC)`.
  Guard with `platform.system() == "Windows"`; on other platforms no-op (log debug).
- **Sound asset:** add a short WAV (a soft two-tone chime, ~0.3-0.5 s). Either ship a
  small `notify.wav` in `App/` or synthesise it once with the stdlib `wave` module in
  a tiny `generate_sound.py` (mirroring how `app_icon.py`/`generate_icon.py` work).
  Bundle it in `App/DCS_DTC_Kneeboard.spec` `datas` and resolve it at runtime via
  `sys._MEIPASS` (see `_resource_dir()` in `main.py`).

**Constraints:** UK English, no em dashes, type hints, docstrings, file I/O in
try/except, never crash the watcher/GUI on a sound failure.

**Verify:** `ruff check` + `py_compile` on the Mac. Live audible test on Windows.
Then ask me to commit. (Note: this lands before Item 3 so the regenerate button can
reuse the same sound.)
--- PROMPT END ---

---

## Item 3 — UI button to manually regenerate the kneeboard

--- PROMPT START ---
Enhancement for the MiG-29 DTC Kneeboard Utility. Read `CLAUDE.md` first, then
`App/main.py` and `App/trigger_watcher.py`. Do this AFTER Item 2 (sound) is done.

**Goal:** A button in the GUI that regenerates the kneeboard on demand, for when the
pilot edits the DTC in the cockpit after spawning. Plus a graceful message when no
DTC data is found.

**Design:**
- Add a "Regenerate Kneeboard" button to the window (e.g. in the settings card or a
  small controls row).
- On click, call `self.watcher.generate_kneeboard()` (it already scans the temp dir
  for the most recent `~tr*.bin`, no trigger needed, and returns the output `Path`
  or `None`). Run it on a **short worker thread** so the Tk loop does not freeze
  during the scan + render, then marshal the result back via the existing
  `_command_queue` pump.
- Disable the button while a regenerate is in flight; re-enable on completion.
- **Success:** log the path and play the Item 2 sound.
- **Failure (`None` returned):** show a graceful, specific message both in the status
  log and as a small popup/dialog, e.g. "No DTC data found - spawn into a MiG-29 in
  DCS first, then regenerate." `generate_kneeboard()` already returns `None` and logs
  the reason for: temp path not configured, no DTC `.bin` found, or a processing/
  render error - surface a friendly summary rather than a stack trace.

**Caveat to note in the UI/log copy:** manual regenerate uses the most recent temp
`.bin` by modification time. Whether DCS rewrites that file when the DTC is edited in
the cockpit is unverified (spec section 10, research item #3), so a regenerate may
reflect the last-written DTC state.

**Constraints:** UK English, no em dashes, type hints, docstrings, file I/O in
try/except, no new runtime deps, GUI must not block or crash.

**Verify:** `ruff check` + `py_compile` on the Mac; live click-test on Windows. Then
ask me to commit. (Item 4 will reuse this regenerate path.)
--- PROMPT END ---

---

## Item 4 — Configurable global keyboard shortcut to regenerate

--- PROMPT START ---
Enhancement for the MiG-29 DTC Kneeboard Utility. Read `CLAUDE.md` first, then
`App/main.py` and `App/config.py`. Do this LAST, AFTER Item 3, because it reuses the
manual-regenerate path from Item 3.

**Goal:** A configurable keyboard shortcut that regenerates the kneeboard even when
the app is NOT focused (the pilot is in DCS/VR and cannot click the button).

**Critical design point:** A normal Tkinter key binding only fires when the app has
focus, so it will NOT work in VR. This must be a **system-wide / global hotkey**.
With the no-extra-dependency constraint (runtime deps stay customtkinter/Pillow/slpp/
pystray - do NOT add `keyboard`/`pynput`), implement it on Windows with the stdlib
`ctypes` + the Win32 `RegisterHotKey` API plus a dedicated daemon thread running a
Win32 message loop (`GetMessageW`) to catch `WM_HOTKEY`. Guard everything behind
`platform.system() == "Windows"`; on other platforms, no-op with a logged note.

**Requirements:**
- Store the shortcut in `config.json` (extend `AppConfig` in `App/config.py`:
  add the field, `to_json_dict`, `from_dict`; consider bumping `CONFIG_VERSION`).
  Example value: `"regen_hotkey": "ctrl+alt+k"`. Write a small parser that maps the
  string to Win32 modifier flags (MOD_CONTROL/MOD_ALT/MOD_SHIFT) + a virtual-key code.
- Add a settings field in the GUI to view/edit the shortcut (a simple text entry is
  fine; a "press keys to capture" capture box is a nice-to-have).
- On hotkey fire, call the SAME regenerate routine added in Item 3 (sound + graceful
  error included). Marshal onto the Tk loop via the `_command_queue` pump - the
  message-loop thread must not touch Tk directly.
- Register on startup / when settings are saved; **unregister on quit** and re-register
  when the shortcut changes. If `RegisterHotKey` fails (another app already owns that
  combo), surface a clear, non-fatal error in the status log and keep running.

**Constraints:** UK English, no em dashes, type hints, docstrings, file I/O in
try/except, no new runtime deps, never crash the GUI.

**Verify:** `ruff check` + `py_compile` on the Mac (cannot exercise the hotkey there);
full live test on Windows, including pressing the shortcut while DCS is focused. Then
ask me to commit. Flag any pitfalls you hit (hotkey already registered, thread
lifecycle, unregister-on-quit).
--- PROMPT END ---

# Enhancement handoff prompts (post first Windows test)

Five enhancements, to be done **one per chat, in order**. Each prompt below is
self-contained: open a new Claude Code chat **in the `DTC Kneeboard Utility`
folder** (so `CLAUDE.md` auto-loads) and paste everything between the
`--- PROMPT START ---` / `--- PROMPT END ---` markers as the first message.

**Recommended order: 1 → 2 → 3 → 4 → 5.** Why this order and why separate chats:
- **Separate prompts (not one big one):** each chat stays focused and the context
  window does not fill with the other tasks.
- **Dependencies:**
  - **Item 1 (stop redundant regeneration)** changes the core watcher pipeline and
    adds a `force` flag the later items use, so it goes first.
  - **Item 3 (sound)** should land before **Item 4 (regenerate button)** so the
    button can play it, and the sound must respect Item 1's dedupe (only play on an
    actual render, not a skipped trigger).
  - **Item 4 (manual regenerate path)** must land before **Item 5 (hotkey)**, which
    reuses it.
- **Where each is verified:**
  - **Item 1:** the dedupe / early-out logic is testable on the **Mac** (the watcher
    imports headless; the integration-test harness can drive it twice and assert the
    second run is skipped). The trigger-frequency root cause and any hook change need
    a live **Windows/DCS** session and the hook log.
  - **Item 2** is renderer-only and fully testable on the **Mac** (Pillow, no GUI).
  - **Items 3, 4, 5** touch the GUI / Windows APIs and need a **Windows** run (Mac can
    only `py_compile` + `ruff`).

After each item, review the change and tell that chat to commit (your standing
rule is no commits without an explicit ask).

---

## Item 1 — Stop redundant kneeboard regeneration (content dedupe + trigger root-cause)

--- PROMPT START ---
Enhancement for the MiG-29 DTC Kneeboard Utility. Read `CLAUDE.md` first, then
`App/trigger_watcher.py` and `App/hook_template.lua`.

**Problem (observed in live testing):** the watcher logs "Trigger detected" every
30-60 seconds, and each time it scans the temp dir, parses the DTC and re-renders
the kneeboard even when nothing has changed. This is wasteful and, on lower-spec
PCs, the periodic render (Pillow draw + JPEG encode on a background thread) can
cause a slight stutter in DCS.

**Key insight (already reasoned, start here, do not rediscover):** by design the
hook writes the trigger ONCE per slot change then stops (`hook_template.lua`:
`onPlayerChangeSlot` arms a deferred poll; on a MiG-29 it calls `writeTrigger` then
`stopPolling`). For the trigger to reappear every 30-60s, `onPlayerChangeSlot` must
be re-firing. **Confirmed cause: multiplayer** (the user saw the 30-60s triggers in
MP). DCS calls `onPlayerChangeSlot` for EVERY player's slot change, not just the local
player, and each call re-arms the hook, which re-checks the LOCAL unit type (still
MiG-29) and re-writes the trigger. So on a populated server it spams at roughly the
rate other players change slots.

**Step 1 - investigate the root cause first.** On Windows, read
`<Saved Games>\DCS\Logs\dtc_kneeboard_hook.log`. The hook logs one line per
`onPlayerChangeSlot` ("onPlayerChangeSlot(id=...): starting deferred poll") and one
per trigger write. The user has confirmed this was MULTIPLAYER, so expect the `id` to
vary across many different players (each one re-arming the hook). Use the log to
confirm that mechanism and gauge the firing rate before changing the hook.

**Step 2 - fix at the right layer (layered, in priority order):**

(A) **Watcher-side content dedupe [REQUIRED - the robust core, always correct].**
In `App/trigger_watcher.py`, after the DTC is fully resolved (after
`resolve_adf_beacon_names`) and BEFORE `render_kneeboard`, compute a stable
fingerprint, e.g. `hashlib.sha256` of `json.dumps(processed.to_dict(), sort_keys=True)`.
Note `ProcessedDTC.to_dict()` does NOT include the render timestamp, so identical
DTCs hash identically. Keep the last fingerprint (in memory on the watcher is fine;
persisting a sidecar next to the output JPEG so it survives app restarts is an
optional nice-to-have). If the new fingerprint equals the last, SKIP the render and
log "DTC unchanged; kneeboard not regenerated." Only render and update the stored
fingerprint when it differs. This alone fixes the "regenerating identical pages"
waste and is always safe (a real DTC edit changes the hash and regenerates).

(B) **Watcher-side cheap early-out [RECOMMENDED].** Before extracting, capture the
chosen `~tr*.bin`'s `(path, st_mtime, st_size)`; if unchanged since the last
successful generation, skip the whole extract+parse step (not just the render).
Key this on "the file changed", NOT on "same flight" - a pilot can edit the DTC
mid-flight and legitimately want a new page, which a pure "same-flight, skip" rule
would wrongly suppress.

(C) **Hook-side root-cause fix [confirmed needed - multiplayer].**
In `hook_template.lua`, in `onPlayerChangeSlot(id)`, ignore slot changes that are not
the local player: use the hooks `net` API (e.g. `net.get_my_player_id()`; confirm the
exact call name in the DCS hooks environment) and return early when `id` is not the
local id. pcall-guard it and fall through to current behaviour if `net` is unavailable
(e.g. single-player, where it should still work). Do NOT add a "skip if unit type
unchanged" guard at the hook - the watcher content dedupe (A) already covers
identical-DTC respawns, and a hook-level unit-type skip would wrongly suppress a
respawn where the pilot DID change the DTC. Bump the version comment on line 1 (e.g.
`v1.1`) so `hook_manager` auto-updates the installed hook. Keep ALL existing safety
constraints (pcall every DCS call, never call `getMissionLoaded`, etc.).

**Maps to the two ideas originally raised:** idea 2 ("compare to the last generated
Gold/Master page") IS (A), the content fingerprint - endorsed as the core. Idea 1
("same plane/flight -> skip the temp search") is handled by (B)/(C) but re-keyed on
"the file/slot actually changed" rather than flight identity, so a mid-flight DTC
edit is still caught.

**Interaction with later items (note in code/comments):** the `on_generated`
notification and sound (Item 3) must fire only on an ACTUAL render, not on a dedupe
skip. Add a `force: bool = False` parameter to `generate_kneeboard()` so the manual
regenerate (Items 4/5) can bypass the dedupe and always rebuild.

**Constraints:** no new runtime deps (`hashlib`, `os`, `json` are stdlib); UK English,
no em dashes, type hints, docstrings, file I/O in try/except; the watcher stays
importable headless and must never crash.

**Verify:** the dedupe / early-out logic is testable on the Mac - drive
`poll_once()` / `generate_kneeboard()` twice against the same fixture via the harness
in `App/test files/test_integration.py` and assert the second run skips the render
(returns None or a "skipped" result) and does not rewrite the JPEG. Add a focused
test. `ruff check` + `py_compile`. The hook change and the real trigger-frequency
confirmation need a live Windows/DCS session and the hook log. Then ask me to commit.
--- PROMPT END ---

---

## Item 2 — Show ADF frequency next to the resolved station name

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

## Item 3 — Play a sound when a kneeboard is generated

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
- **Respect Item 1's dedupe:** the `on_generated` callback (and the sound) must fire
  only on an ACTUAL render, never when Item 1 skipped the trigger as unchanged.
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
Then ask me to commit. (Note: this lands before Item 4 so the regenerate button can
reuse the same sound.)
--- PROMPT END ---

---

## Item 4 — UI button to manually regenerate the kneeboard

--- PROMPT START ---
Enhancement for the MiG-29 DTC Kneeboard Utility. Read `CLAUDE.md` first, then
`App/main.py` and `App/trigger_watcher.py`. Do this AFTER Item 3 (sound) is done.

**Goal:** A button in the GUI that regenerates the kneeboard on demand, for when the
pilot edits the DTC in the cockpit after spawning. Plus a graceful message when no
DTC data is found.

**Design:**
- Add a "Regenerate Kneeboard" button to the window (e.g. in the settings card or a
  small controls row).
- On click, call `self.watcher.generate_kneeboard(force=True)` - pass the `force`
  flag added in Item 1 so a manual regenerate ALWAYS rebuilds, bypassing the dedupe
  (the user explicitly asked for a fresh page). It already scans the temp dir for the
  most recent `~tr*.bin`, no trigger needed, and returns the output `Path` or `None`.
  Run it on a **short worker thread** so the Tk loop does not freeze during the scan +
  render, then marshal the result back via the existing `_command_queue` pump.
- Disable the button while a regenerate is in flight; re-enable on completion.
- **Success:** log the path and play the Item 3 sound.
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
ask me to commit. (Item 5 will reuse this regenerate path.)
--- PROMPT END ---

---

## Item 5 — Configurable global keyboard shortcut to regenerate

--- PROMPT START ---
Enhancement for the MiG-29 DTC Kneeboard Utility. Read `CLAUDE.md` first, then
`App/main.py` and `App/config.py`. Do this LAST, AFTER Item 4, because it reuses the
manual-regenerate path from Item 4.

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
- On hotkey fire, call the SAME regenerate routine added in Item 4 (force rebuild,
  sound + graceful error included). Marshal onto the Tk loop via the `_command_queue`
  pump - the message-loop thread must not touch Tk directly.
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

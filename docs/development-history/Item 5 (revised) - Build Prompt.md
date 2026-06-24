# Item 5 (revised) - Build prompt for Claude Code

Open a new Claude Code chat **in the `DTC Kneeboard Utility` folder** (so
`CLAUDE.md` auto-loads) and paste everything between the markers as the first
message.

This is ONE prompt covering both the hook and the app, on purpose: the state-file
contract spans both, so splitting them would leave each half un-integration-testable.
It is staged internally (A hook, B app, C config, D GUI label, E tests) to keep it
orderly.

Decisions already locked (do not re-litigate): edits are made via the DCS **DTC
manager** panel and DO rewrite the temp `.bin` (verified); ground poll interval
**5s**, configurable; on-window label has **3 states** (Waiting / Ground / Paused).

---

--- PROMPT START ---
Enhancement for the MiG-29 DTC Kneeboard Utility (this is the revised Item 5; it
REPLACES the global-hotkey Item 5 in `docs/development-history/Enhancement
Handoffs.md`). 

**Read first, in this order:** `CLAUDE.md` (especially the gotchas list),
`docs/development-history/Item 5 (revised) - Ground-Polling Auto-Regenerate
Design.md` (the authoritative design for this change), then
`App/hook_template.lua`, `App/trigger_watcher.py`, `App/config.py`, `App/main.py`,
and the test files in `App/test files/`. Do not start coding until you have read
the design doc; it explains the rationale and the edge cases.

## Goal

While the player is parked on the ground in a MiG-29, the app should watch the DTC
temp file and auto-regenerate the kneeboard when the DTC changes (e.g. after the
player applies a new program in the DCS DTC manager mid-session). Once airborne it
pauses; on landing it resumes. The window shows a 3-state label. This removes the
need for the global hotkey (which risked colliding with DCS/SRS/TacView/VoiceAttack
key handling).

Air/ground is decided IN THE HOOK by reading the local aircraft's height via the
Export API (available inside the hooks Lua state), NOT via `onGameEvent`
takeoff/landing (those are unreliable for a client in multiplayer - see design doc
section 3.2). The hook publishes the phase in a small state file; the app reads it.

## The state-file contract (hook writes, app reads)

Path: `<Saved Games>\DCS\Logs\dtc_kneeboard_state.json`. Small JSON, written with
the SAME atomic write-tmp-then-rename pattern as the existing trigger:

```json
{ "phase": "ground" | "air" | "none", "aircraft": "MiG-29S", "agl_m": 2.4, "ts": "2026-06-24T09:15:03Z" }
```

`ts` is ISO 8601 UTC. The app treats a missing file, or a `ts` older than
`state_stale_seconds` (default 30), as "no live state" -> label **Waiting** and
trigger-only behaviour (exactly like today).

## A. Hook (`App/hook_template.lua`) -> bump to v1.2

1. Bump line 1 comment and `VERSION` to `1.2`, and add a `Changes:` note (mirror
   the v1.1 note style) describing the air/ground state machine + state file.
2. Add named tuning constants near the existing ones:
   `STATE_POLL_FRAMES = 60` (~1s), `AGL_AIR_M = 30`, `AGL_GROUND_M = 10`,
   `AGL_CONFIRM_SAMPLES = 3`, `STATE_HEARTBEAT_FRAMES = 600` (~10s).
3. New state vars: `migActive` (bool), `phase` ("ground"/"air"/"none"),
   `aglStreak` (consecutive-sample counter), and independent frame counters for the
   state poll and the heartbeat. Keep these SEPARATE from the existing detection
   `pollActive`/`frameCount`/`pollCount` so detection and the state machine do not
   interfere.
4. Add `safeGetAGL()`: `pcall(Export.LoGetAltitudeAboveGroundLevel)`, returns a
   number or nil. (Optionally also a `safeGetSelfData()` for a fallback, but AGL is
   enough for v1.) Every Export call pcall-guarded, like every DCS call here.
5. Add `writeState(phase, aglMetres)`: atomic write of the JSON above to
   `Logs/dtc_kneeboard_state.json` (reuse the `writeTrigger` tmp+rename+pcall
   pattern; share a helper if clean). A write failure logs and is swallowed.
6. Wire the transitions:
   - In `onPlayerChangeSlot(id)`: KEEP the existing v1.1 local-player guard exactly
     as-is and FIRST (this is gotcha 10 - do not weaken it). On a local slot change,
     set `migActive=false`, `phase="none"`, `writeState("none")` (so the label shows
     **Waiting** during slot selection / loading), THEN arm the deferred detection
     poll as today.
   - In the existing `poll()` MiG-29 success branch (right where it currently writes
     the trigger): set `migActive=true`, `phase="ground"`, reset `aglStreak`,
     `writeState("ground")`. Leave the trigger write unchanged.
   - In `poll()`'s non-MiG-29 branch: set `migActive=false`, `phase="none"`,
     `writeState("none")`.
   - Add `onSimulationStop` (register it): set `migActive=false`, `phase="none"`,
     best-effort `writeState("none")`.
7. In `onSimulationFrame`, ADD an independent block (do not nest it inside the
   `pollActive` detection block): while `migActive`, every `STATE_POLL_FRAMES`,
   read `agl = safeGetAGL()`:
   - `agl == nil` -> leave `phase` unchanged (safe default keeps it on "ground").
   - Hysteresis: if `phase ~= "air"` and `agl > AGL_AIR_M` for `AGL_CONFIRM_SAMPLES`
     consecutive reads -> `phase="air"`, `writeState("air", agl)`. If
     `phase ~= "ground"` and `agl < AGL_GROUND_M` for `AGL_CONFIRM_SAMPLES`
     consecutive reads -> `phase="ground"`, `writeState("ground", agl)`. Track the
     streak so a single bump cannot flip the phase.
   - Also re-write the state file on the `STATE_HEARTBEAT_FRAMES` cadence (so `ts`
     stays fresh and the app knows DCS is alive) while `migActive`.
   Guard the whole block in pcall so it can never propagate into the sim loop.
8. KEEP every existing safety constraint: pcall every DCS/Export call, never
   `DCS.getMissionLoaded()`, no `io.popen`, no `lfs.dir()` outside DCS, and DO NOT
   touch `Export.lua` (we only CALL `Export.*` from this hook). 

## B. App watcher (`App/trigger_watcher.py`)

1. Add a `read_state()` that loads/parses `dtc_kneeboard_state.json` and returns the
   phase, applying the `state_stale_seconds` staleness rule (missing/old -> a phase
   like `"none"`/`"unknown"`). Wrap I/O in try/except; never raise.
2. Keep the existing trigger handling unchanged (spawn -> `generate_kneeboard()`).
3. Add the ground change-check on a 5s cadence (independent of the 2s trigger
   poll - track a `last_ground_check` timestamp; only run when
   `now - last_ground_check >= ground_poll_seconds` AND phase == "ground"):
   - First check whether an mtime/size early-out already exists in this module
     (Item 1B may or may not have landed). If it exists, reuse it; otherwise add a
     cheap check here: `os.scandir` the temp dir, filter `~tr*.bin` in the existing
     size band, pick the newest by `st_mtime`, capture `(name, st_mtime, st_size)`.
     Do NOT read file contents in this cheap check.
   - Compare to the last-seen tuple (store it on the watcher). Unchanged -> do
     nothing. Changed -> call `self.generate_kneeboard(force=False)` so the Item 1A
     content fingerprint dedupe STILL applies (a re-applied identical DTC must not
     redraw; only a real content change should). Update the stored tuple after any
     generation (trigger-driven OR ground-driven) so the two paths stay in sync and
     never double-fire for the same write.
4. Add an optional `on_phase_change: Callable[[str], None]` constructor param (same
   shape/safety as `on_generated`): call it when the derived phase changes. It must
   not raise; catch and log. The watcher must NOT touch any GUI directly.
5. Emit the status-log lines from design doc section 4.6 on transitions
   ("MiG-29 on the ground - watching DTC for changes (every 5s).", "Airborne - DTC
   watch paused.", "Back on the ground - DTC watch resumed.", "DTC change detected
   on the ground - regenerating kneeboard.", "No live DCS state - watching for the
   next spawn trigger only."). Log via the module logger (gotcha 1: do NOT add a
   second log_callback path).

## C. Config (`App/config.py`)

Add `ground_poll_seconds` (default `5.0`, clamp to a floor of `2.0`) and
`state_stale_seconds` (default `30.0`) to `AppConfig`. Update `to_json_dict` and
`from_dict`, bump `CONFIG_VERSION`, and make sure an OLD `config.json` (without the
new keys) still loads with the defaults (back-compat). No GUI field for the interval
in this item (config.json only); a settings entry can come later.

## D. GUI label (`App/main.py`)

Add a small always-visible **DTC Watch** label (in the controls row near the
Regenerate button is fine) with three states:
- **Waiting** - not slotted in a MiG-29, or no live/fresh state file.
- **Ground** - in a MiG-29, on the ground, watching the temp file.
- **Paused** - in a MiG-29, airborne.

Map: state phase `ground` -> Ground; `air` -> Paused; `none`/missing/stale ->
Waiting. Pass an `on_phase_change` callback to `TriggerWatcher` that ENQUEUES the
label update onto the existing `_command_queue` (drained by the 100ms `_poll_queues`
pump) - the watcher thread must never touch Tk directly (gotcha 3). Give the states
distinct colours if easy (e.g. green Ground / amber Paused / grey Waiting). Do NOT
store `AppConfig` as `self.config` (gotcha 2: use `self.app_config`).

## Constraints

UK English, no em dashes (commas/parentheses/hyphens), type hints, docstrings, all
file I/O in try/except, NO new runtime deps (stdlib + the existing
customtkinter/Pillow/slpp/pystray only), the watcher stays importable headless and
must never crash, and the hook must never propagate an error into DCS.

## E. Tests + the regression run (REQUIRED - report results to me)

**Add new focused tests** (Mac-runnable, headless) in `App/test files/`:
- State-driven ground poll: with a synthetic `dtc_kneeboard_state.json` = `ground`
  and a CHANGED `~tr*.bin` fixture, `poll_once()` (or the new ground-check method)
  regenerates; with an UNCHANGED `.bin` it skips (no parse/render); with phase
  `air` / `none` / missing / stale it does NOT run the ground change-check.
- `force=False` on the ground path still dedupes: a changed file whose RESOLVED DTC
  content is identical must NOT rewrite the JPEG (Item 1A still in force).
- `on_phase_change` fires once per actual phase transition (ground->air->ground),
  not on every poll.
- Config round-trip: new fields serialise/deserialise; an old config.json (no new
  keys) loads with defaults; the interval floor clamp works.

**Then run the FULL regression suite and tell me the results.** These guard the
previously fixed bugs - I want to know any of them regress:

1. `App/test files/test_integration.py` - **in-memory render dedupe (gotcha 11):**
   identical DTC twice -> the second render is skipped / returns None and the JPEG
   is not rewritten. Confirm the new ground path did not break this.
2. `App/test files/test_bin_parser.py` - **newest-DTC-copy selection (gotcha 9):**
   the parser still returns the NEWEST complete copy from a multi-copy `.bin`.
3. `App/test files/test_dtc_processor.py` and `test_beacon_parser.py` - DTC
   resolution and ADF beacon resolution unchanged.
4. `App/test files/test_config.py` - config load/save/auto-detect, INCLUDING the new
   fields and old-config back-compat.
5. `App/test files/test_hook_manager.py` - **hook install/update (version logic):**
   confirm a v1.1 installed hook is recognised as OUTDATED and updated to v1.2
   (check for any hardcoded version string in the test or in `hook_manager.py` and
   update it; verify the version compare treats 1.2 > 1.1).
6. `ruff check .` clean, and `python -m py_compile` on every changed `.py`.

Run order on the Mac (from `App/`): the four/five `test files/*.py` scripts (custom
Checks harness, no pytest), then `ruff check .`, then `py_compile`. Report pass/fail
per file.

**Cannot be tested on the Mac - give me a Windows/DCS live-test checklist** (the
hook, the label and the end-to-end need the gaming PC):
- a) Hook log shows AGL readings and the phase transitions; on a populated MP server
  the v1.1 local-player guard still holds (no trigger/state spam from other players'
  slot changes - this is the gotcha 10 regression, only verifiable live).
- b) Spawn cold on a MiG-29 -> label goes Waiting then Ground; kneeboard generates
  once (trigger path). 
- c) In the pit, open the DTC manager, apply a changed program -> within ~5s the
  status log shows "DTC change detected" and the kneeboard regenerates (and the
  generation sound plays once - gotcha 12, not on a no-op).
- d) Take off -> label flips to Paused, watch stops. Land -> back to Ground, watch
  resumes.
- e) Switch to a non-MiG-29 / spectator -> label returns to Waiting. Close DCS ->
  within `state_stale_seconds` the label returns to Waiting.

Do not commit anything until I have reviewed and explicitly asked you to commit
(standing rule). Flag any pitfalls you hit (Export returning nil in menus,
hysteresis tuning, the heartbeat cadence, the version-compare in hook_manager).
Make me a test-build I can download from Github to test on my DCS PC
--- PROMPT END ---

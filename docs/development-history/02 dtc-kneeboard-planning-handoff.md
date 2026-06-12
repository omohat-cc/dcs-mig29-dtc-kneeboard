# DCS MiG-29 DTC Kneeboard Utility — Planning Session Handoff

> **Purpose:** This document captures all decisions locked so far in the planning of a utility that reads MiG-29A DTC config from DCS World and generates custom kneeboard images. It is intended to be pasted into a new Claude chat to continue planning from where this session left off.
>
> **Attached files (or in project files) you must include when starting the new chat:**
> 1. `DCS_DTC_Research_Findings.md` — comprehensive research on how to access DTC data at runtime (DCS scripting environments, binary temp file extraction, recommended architecture). **Read this entire document before proceeding — it is the technical foundation for everything below.**
> 2. `DCS_MiG29_DTC_Format_Reference.md` — complete reference for the .dtc JSON format (field definitions, constraints, formatting rules). Available in project files.
> 3. `SPO15___Points___ADF_-_Contention_SARH_Era1.dtc` — a real DTC file with 79 Points, 20 Radio channels, 4 ADF channels, CMDS, and SPO-15 config. Use as test data.
> 4. `Aerodrome_Point_Test_DTC.dtc` — test DTC showing the structural difference between Point-sourced and built-in aerodromes.
> 5. `RSBN_Test_DTC.dtc` — test DTC showing a populated RSBN entry (Krasnodar-Center, Caucasus map).

---

## What this utility does (one paragraph)

A two-component system: a lightweight Lua hook inside DCS that fires a trigger signal when the player spawns in a MiG-29, and an external watcher/processor that detects the trigger, reads the DTC data from DCS's binary temp files, parses the embedded JSON, and generates a kneeboard image containing a comprehensive summary of the DTC configuration. The kneeboard is written to the DCS user kneeboard directory so it appears in-cockpit on the next spawn/respawn.

---

## Area 1: DTC Data Access — LOCKED

### Architecture: Two components

**Component 1: DCS Lua Hook (trigger only)**
- Location: `Saved Games\DCS\Scripts\Hooks\dtc_kneeboard_hook.lua`
- Listens for `onPlayerChangeSlot()` callback
- Uses deferred execution via `onSimulationFrame()` countdown (600+ frames)
- Polls `DCS.getPlayerUnitType()` with retry logic (may return empty string if called too early; retry every N frames up to a max attempt limit)
- Matches on `MiG-29` prefix (covers MiG-29 Fulcrum, MiG-29A, MiG-29S, MiG-29G variants)
- On match: writes a small trigger file to `Saved Games\DCS\Logs\` containing timestamp and optionally mission name / theatre
- Writes trigger file atomically (write to temp name, then rename)
- Does nothing else. No file parsing, no image generation, no heavy work inside DCS.

**Component 2: External Watcher/Processor**
- Runs as a background process, started by the player before launching DCS
- Watches for the trigger file (polling or filesystem watcher)
- On trigger: waits a few seconds (for DCS to finish writing temp files), then scans `%LOCALAPPDATA%\Temp\DCS\`
- Identifies the DTC-bearing `.bin` file by reading the first 64KB and checking for both `MiG-29` and `"data"` strings
- Extracts embedded JSON from the binary container (walk byte-by-byte, collect printable ASCII runs >50 chars, strip `N ` prefix from each run, concatenate, parse JSON)
- Generates kneeboard image(s) from the parsed DTC data
- Writes output to `Saved Games\DCS\Kneeboard\MiG-29 Fulcrum\`

### Key technical findings from research
- DTC data is NOT accessible through any DCS Lua API (getCurrentMission has no DTC keys, net.dostring_in returns nil, Export.lua never fired in testing)
- DTC JSON is embedded as plaintext in binary `~trXXXXXXXX.bin` temp files at `%LOCALAPPDATA%\Temp\DCS\`
- Files are NOT locked by DCS during runtime — can be read concurrently
- The binary file contains ~7 identical copies of the full DTC JSON; only need to parse the first
- JSON spans 6-7 text runs of ~20KB each, separated by 40-byte binary record boundaries
- DTC temp files are 917KB-983KB in size (non-DTC files are either much larger or much smaller)

### DCS scripting environment constraints (from research)
- `DCS.getMissionLoaded()` crashes DCS — NEVER call
- C-backed DCS functions crash without correct args — pcall cannot catch
- `io.popen` is nil in hooks
- `lfs.dir()` is VFS-wrapped — cannot list OS directories outside DCS
- `DCS.getPlayerUnitType()` timing-sensitive — needs retry/polling pattern

### Kneeboard output path note
- The research doc says `Saved Games\DCS\Kneeboards\MiG-29A Fulcrum\` but ED forum sources confirm the correct path is `Saved Games\DCS\Kneeboard\MiG-29 Fulcrum\` (no 's' on Kneeboard, no 'A' in aircraft name). Needs verification during build.

---

## Area 2: Functional Scope — LOCKED

### Active program only
The kneeboard displays only the selected program (`data.SelectedProgram`, e.g., `"Program_1"`). Other programs are ignored.

### Sections included in v1 (in display order TBD during kneeboard design)

**1. Waypoints (max 3)**
- Display: waypoint number + resolved name
- Name resolution: look up the waypoint's `id` (e.g., `"PNT79"`) in the Points array of the same program. Use the Point's `note` field if non-empty; otherwise fall back to the `id`.
- Every waypoint always references a Point (confirmed by DTC tool behaviour).

**2. Airdromes (max 3)**
- Display: airdrome slot + resolved name
- Name resolution branches on the `type` field:
  - `type: "Airdrome"` (built-in selection): use the `name` field directly (e.g., "Abu al-Duhur"). Full name, no truncation.
  - `type: "Point"` (sourced from Points library): look up the Point by `id` in the Points array, use the Point's `note` field. Fall back to `id` if `note` is empty.
- Built-in aerodromes also have `runways`, `selectedRunwaySide`, `unit_name` — available for future use but not displayed in v1.

**3. RSBN (max 3)**
- Display: RSBN slot + station name + channel number
- RSBN entries have a `name` field with the full station name and a `channel` field with the numeric channel.
- Example: "RSBN1: Krasnodar-Center (Ch 40)"

**4. ADF (4 channels, inner + outer each)**
- Display: channel number + inner/outer + resolved station name
- Name resolution: match the ADF `freq` value against the beacons.lua database for the active map
- Beacon database source: parsed from the player's DCS install at `<DCS_INSTALL>\Mods\terrains\<map>\beacons.lua`
- Map determined from DTC's `data.terrain` field
- Failure mode: if no beacon matches the frequency, display the raw frequency and modulation (e.g., "342 kHz AM")
- Example display: "ADF 1 In: Rayak / ADF 1 Out: Tiyas"

**5. Radio (20 channels)**
- Display: channel number + frequency + modulation (AM/FM)
- All 20 channels shown (Channel_0 through Channel_19)
- Modulation: 1 = AM, 0 = FM
- Example: "CH00: 124 AM"

**6. CMDS (Flare Program)**
- Display: resolved values (not raw indices)
- All indices are **1-based** (first dropdown option = index 1)
- Lookup tables (confirmed by manual testing in DTC Manager UI):

```
idxFlareBurstCountI (flares per burst, program I):
  1→1, 2→2, 3→3, 4→4
  Formula: value = index

idxFlareBurstCountII (flares per burst, program II):
  1→1, 2→2, 3→3, 4→4
  Formula: value = index

idxFlareBurstInterval (seconds between flares in a burst):
  1→0.5s, 2→1.0s, 3→1.5s, 4→2.0s, 5→2.5s, 6→3.0s, 7→3.5s, 8→4.0s
  Formula: value = index × 0.5

idxFlareSalvoCountAir (salvos for air threat program):
  1→1, 2→2, 3→3, 4→4, 5→5, 6→6, 7→7, 8→8
  Formula: value = index

idxFlareSalvoCountZrk (salvos for SAM/AAA program):
  1→6, 2→8, 3→10, 4→12, 5→14, 6→16, 7→18, 8→20, 9→24, 10→28, 11→32
  No clean formula — hardcode as array: [6, 8, 10, 12, 14, 16, 18, 20, 24, 28, 32]

idxFlareSalvoInterval (seconds between salvos):
  1→5s, 2→6s, 3→7s, 4→8s, 5→9s, 6→10s
  Formula: value = index + 4
```

**7. SPO-15 LW (Launch Warnings)**
- Display: resolved labels for each of the 6 threat type positions
- `LW_indices` is a 6-element array. Each value maps as: 1=Off, 2=On, 3=Lock
- The 6 positions (in order) correspond to threat types: П, 3, X, H, F, C
- Example: `[3, 2, 1, 2, 3, 1]` → "П: Lock, 3: On, X: Off, H: On, F: Lock, C: Off"

### Sections explicitly excluded from v1
- **Points** — too many (up to 99+), they are a selection library not active nav data
- **Route** — typically empty
- **TargetPoint** — could be added later
- **WeaponSettings** — only contains `trainingMode` boolean, low value
- **Mirror settings** — metadata, not operationally relevant in cockpit

### v2 feature (not in MVP)
- User-configurable section selection: the utility settings allow the player to enable/disable which DTC sections appear on the kneeboard & perhaps add other sections like Route or TargetPoint

### Multi-map support
- The utility supports all DCS maps (Syria, Caucasus, PersianGulf, Nevada, Sinai, Kola, MarianaIslands, and any future maps)
- Map is determined from the DTC's `data.terrain` field
- ADF beacon lookup is the only map-dependent feature; it reads `beacons.lua` dynamically from the player's DCS install directory
- All other sections are map-agnostic

---

## Remaining Areas to Work Through

### Area 3: Trigger and Lifecycle
- What triggers kneeboard generation (trigger file watcher, polling interval, filesystem events)?
- What happens on respawn or slot change (regenerate? clean up old kneeboards?)
- Does the utility auto-start at login, or does the player launch it manually?
- What happens when DCS isn't running?
- How does the Lua hook handle mission switches (new mission = new temp files)?

### Area 4: Technology and Architecture
- Language for the external watcher/processor (Python, Rust, Go, PowerShell, C#)
- Tradeoffs: runtime dependencies, binary size, performance overhead, cross-compilation (dev on Mac, target Windows)
- How to package for distribution (single exe, installer, portable folder)
- Binary temp file parser implementation approach
- beacons.lua parser (it's Lua table syntax, not JSON)
- Image generation library selection

### Area 5: Configuration and UX
- What settings the player needs to configure:
  - DCS install directory (for beacons.lua ADF lookup)
  - DCS Saved Games directory (for hook installation, kneeboard output, trigger file)
  - Which sections to display (v2, but config schema should support it from v1)
- How the player interacts with the utility (system tray app, CLI, config file, simple GUI)
- First-run setup experience
- Hook installation: does the utility install/update the Lua hook automatically, or is it manual?

### Area 6: Kneeboard Design
- Page layout, section ordering, typography, visual style
- Resolution: 1536x2048 (standard DCS kneeboard) — needs confirmation
- How to handle page overflow (e.g., if all sections are populated, does it fit one page or need multiple?)
- Visual style: match existing DCS kneeboard aesthetic, or use the aviation-chart style from the existing kneeboard skill?
- Header: DTC profile name, map, program number?

### Outstanding Research Items
1. **beacons.lua format:** Confirm the exact Lua table structure and parsing approach needed. Sample the file from one map to validate.
2. **Kneeboard output path verification:** Confirm whether `Kneeboard\MiG-29 Fulcrum\` or `Kneeboards\MiG-29A Fulcrum\` is correct (contradictory sources).

---

## Environment Reference

| Item | Path / Value |
|---|---|
| DCS install | `D:\DCS World` |
| Saved Games | `C:\Users\Omar\Saved Games\DCS\` |
| DCS temp dir | `C:\Users\Omar\AppData\Local\Temp\DCS\` |
| Kneeboard output | `C:\Users\Omar\Saved Games\DCS\Kneeboard\MiG-29 Fulcrum\` (needs verification) |
| Hook scripts | `C:\Users\Omar\Saved Games\DCS\Scripts\Hooks\` |
| DCS version | 2.9.26.23303 |
| Dev machine | MacBook Air (development in Claude Code) |
| Target machine | Windows gaming PC (testing and runtime) |

---

## How to Continue

Work through Areas 3-6 sequentially. Do not move to the next area until the user confirms they are ready. For each area, grill the user with specific questions, identify gaps, and lock decisions before moving on.

After all areas are locked, produce the final deliverables:
1. A complete technical specification document for the utility
2. A Claude Code prompt (or set of prompts) to build it
3. Any research prompts needed for outstanding items (CMDS tables, beacons.lua parsing)

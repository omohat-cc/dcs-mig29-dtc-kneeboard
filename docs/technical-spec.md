# DCS MiG-29 DTC Kneeboard Utility — Technical Specification

> **Version:** 1.1
> **Date:** 30 May 2026
> **Status:** All design areas locked. Ready for build.
>
> **Changelog:**
> - v1.1 (30 May 2026): Output path confirmed as `Kneeboard\MiG-29 Fulcrum\`; output file renamed `dtc_config.jpg` → `000_dtc_config.jpg` (sections 3, 10).
> - v1.0 (9 May 2026): Initial specification; all design areas locked.

---

## 1. Overview

A two-component utility that automatically generates a custom kneeboard image summarising the MiG-29A's DTC (Data Transfer Cartridge) configuration in DCS World. The kneeboard appears in the Mig-29A Kneeboard folder in ~/DCS Saved Games/DCS on spawn, giving the pilot an at-a-glance reference of their pre-programmed navigation, radio, countermeasure, and defensive settings.

### Components

1. **DCS Lua Hook** — A lightweight script inside DCS that detects when the player spawns in a MiG-29 and writes a trigger file.
2. **External Watcher/Processor** — A standalone Windows GUI application that detects the trigger, reads DTC data from DCS temp files, parses it, and generates a kneeboard image.

### Key principle

The Lua hook does the absolute minimum (detect aircraft type, write a file). All heavy work (filesystem scanning, binary parsing, image generation) happens in the external process, outside DCS's constrained Lua sandbox.

---

## 2. Component 1: DCS Lua Hook

### Location

```
%USERPROFILE%\Saved Games\DCS\Scripts\Hooks\dtc_kneeboard_hook.lua
```

### Behaviour

1. Registers callbacks via `DCS.setUserCallbacks()`:
   - `onPlayerChangeSlot()` — fires on every slot selection/change, including respawns
   - `onSimulationFrame()` — used for deferred execution

2. On `onPlayerChangeSlot()`, starts a deferred polling sequence via `onSimulationFrame()`:
   - Waits 600+ frames before first check
   - Polls `DCS.getPlayerUnitType()` every 60 frames
   - Retries up to 30 times (covers ~30 seconds of attempts)
   - If the return value is empty string, keeps retrying

3. On successful type check:
   - If type string starts with `MiG-29` (prefix match — covers "MiG-29 Fulcrum", "MiG-29A", "MiG-29S", "MiG-29G"):
     - Writes a trigger file to `Saved Games\DCS\Logs\dtc_kneeboard_trigger.json`
     - File contents: `{"timestamp": "<ISO8601>", "aircraft": "<type_string>", "mission": "<mission_name>", "theatre": "<theatre_id>"}`
     - Write is atomic: write to `.tmp` file, then rename to final name
   - If type does NOT start with `MiG-29`: no action (stale kneeboard from previous spawn is left in place — this is intentional)

4. Stops polling after successful write or after max retries exhausted.

### Safety constraints

- NEVER calls `DCS.getMissionLoaded()` (crashes DCS)
- NEVER calls C-backed functions without correct arguments
- Guards all DCS API calls with `pcall`
- Does NOT use `io.popen` (nil in hooks context)
- Does NOT use `lfs.dir()` on paths outside DCS (VFS-wrapped)
- Does NOT touch `Export.lua` — completely separate Lua context
- Logs diagnostics to `Saved Games\DCS\Logs\dtc_kneeboard_hook.log` via `io.open`

### Hook versioning

The hook file includes a version comment on line 1:

```lua
-- dtc_kneeboard_hook v1.0
```

The external utility checks this version on startup and updates the hook automatically if a newer version is bundled.

---

## 3. Component 2: External Watcher/Processor

### Technology stack

| Component | Choice |
|---|---|
| Language | Python 3.11+ |
| GUI framework | customtkinter |
| Image generation | Pillow (PIL) |
| Lua table parser | slpp (fallback: regex) |
| Packaging | PyInstaller `--onefile` single exe |
| Config format | JSON, stored next to exe |

### Startup flow

1. Exe launches, GUI window opens (NOT minimised to tray).
2. Reads `config.json` from same directory as exe. If missing, runs first-time setup:
   a. Auto-detects DCS install directory (Windows registry `HKLM\SOFTWARE\Eagle Dynamics\DCS World`, then scans common paths: `C:\Program Files\Eagle Dynamics\DCS World`, `D:\DCS World`, `E:\DCS World`)
   b. Auto-detects DCS Saved Games directory (`%USERPROFILE%\Saved Games\DCS\`)
   c. Auto-detects DCS temp directory (`%LOCALAPPDATA%\Temp\DCS\`)
   d. Any paths that fail auto-detection: shows a popup notification listing which paths were not found, with "Browse" buttons to manually select each one
   e. Writes `config.json` with detected/selected paths
3. Checks for Lua hook at `<saved_games>\Scripts\Hooks\dtc_kneeboard_hook.lua`:
   - If missing: installs it, logs "Installed DCS hook script to [path]"
   - If present but outdated (version mismatch): updates it, logs "Updated DCS hook script to v[X]"
   - If present and current: no action
4. Enters watch mode. GUI shows status: "Watching for MiG-29 DTC trigger..."

### GUI layout

Built with customtkinter. Single window with:

- **Settings section (top):**
  - DCS install path — text field + Browse button, auto-filled
  - DCS Saved Games path — text field + Browse button, auto-filled
  - DCS temp directory — text field + Browse button, auto-filled
  - Save Settings button

- **Status/log section (bottom):**
  - Scrolling text log area showing timestamped activity messages
  - Messages: "Watching for trigger...", "Trigger detected", "Scanning temp files...", "DTC found in [filename]", "Kneeboard generated → [path]", errors

- **Window behaviour:**
  - Close (X) button: minimises to system tray, does not quit
  - System tray icon: left-click restores window, right-click menu has Restore / Quit
  - On launch: window opens visible (not minimised)

### Trigger detection

- Polls for trigger file at `<saved_games>\Logs\dtc_kneeboard_trigger.json` every 2 seconds
- On detection: reads the file, deletes it, then proceeds to DTC extraction
- Waits 3 seconds after trigger detection before scanning temp files (allows DCS to finish writing)

### DTC extraction from binary temp files

1. Scans `<temp_dir>` for files matching `~tr*.bin`
2. Filters to files between 500KB and 2MB (DTC files are 917KB-983KB)
3. Sorts by modification time, most recent first
4. For each candidate, reads first 64KB and checks for BOTH strings: `MiG-29` AND `"data"`
5. First match is the DTC file. If no match found across all candidates, logs error and aborts.
6. Extracts JSON from the binary file:
   a. Walk byte-by-byte through the file
   b. Collect runs of printable ASCII characters (bytes 32-126, plus newline 10, tab 9)
   c. Discard runs shorter than 50 characters
   d. Strip leading `N ` prefix (literal `N` + space) from each run
   e. Concatenate all cleaned runs
   f. Find the first `{"data":` substring — this is the start of the JSON
   g. Find the matching closing brace (brace-depth counting)
   h. Parse with `json.loads()`
7. If parsing fails, logs error with details and aborts.

### DTC data processing

1. Read `data.SelectedProgram` to identify the active program (e.g., `"Program_1"`)
2. Extract only the active program's data
3. For each section, resolve display values:

**Waypoints (max 3):**
- For each waypoint in `Waypoints` array:
  - Look up `waypoint.id` in the `Points` array of the same program
  - Display name = matched Point's `note` field if non-empty, else `waypoint.id`
  - Display: `waypoint.num` + resolved name (e.g., "WPT1: Abu al-Duhur")

**Airdromes (max 3):**
- For each airdrome in `Airdromes` array:
  - If `type == "Airdrome"`: use `name` field directly (full name, e.g., "Abu al-Duhur")
  - If `type == "Point"`: look up by `id` in Points array, use Point's `note` field
  - Display runway from `selectedRunwaySide` if present; else "—"

**RSBN (max 3):**
- For each entry in `RSBN` array:
  - Display: `num` + `name` + "Ch " + `channel` (e.g., "RSBN1: Krasnodar-Center Ch 40")

**ADF (4 channels, 8 entries):**
- For each of channels 1-4, read Inner and Outer:
  - Match `freq` against beacons.lua database for the active map
  - Map determined from `data.terrain` (e.g., "Syria" → `<dcs_install>\Mods\terrains\Syria\beacons.lua`)
  - If match found: display the beacon/airport name, with the raw frequency +
    modulation (e.g., "342 kHz AM") drawn beneath it in a smaller font as a
    cross-check (a resolved name can differ from the common airfield name, so the
    frequency lets the pilot verify it against the in-game map)
  - If no match: display raw frequency + modulation (e.g., "342 kHz AM") as the value
- Beacon matching: parse beacons.lua to build a freq → name lookup table

**Radio (20 channels):**
- For each Channel_0 through Channel_19:
  - Display: channel number + frequency (MHz, 3 decimal places) + modulation ("AM" if 1, "FM" if 0)
  - Format: "CH 00  124.000  AM"

**CMDS (6 parameters):**
- Resolve raw index values to display values using these lookup tables:

```python
CMDS_TABLES = {
    'idxFlareBurstCountI':   lambda i: i,           # value = index
    'idxFlareBurstCountII':  lambda i: i,           # value = index
    'idxFlareBurstInterval': lambda i: f"{i * 0.5}s",  # index × 0.5
    'idxFlareSalvoCountAir': lambda i: i,           # value = index
    'idxFlareSalvoCountZrk': lambda i: [6,8,10,12,14,16,18,20,24,28,32][i-1],  # hardcoded array, 1-based
    'idxFlareSalvoInterval': lambda i: f"{i + 4}s",  # index + 4
}
```

Display labels:
- idxFlareBurstCountI → "Burst Count I"
- idxFlareBurstCountII → "Burst Count II"
- idxFlareBurstInterval → "Burst Interval"
- idxFlareSalvoCountAir → "Salvo Count (Air)"
- idxFlareSalvoCountZrk → "Salvo Count (SAM)"
- idxFlareSalvoInterval → "Salvo Interval"

**SPO-15 LW (6 entries):**
- `LW_indices` is a 6-element array from the CMDS object
- Each value maps: 1 = Off, 2 = On, 3 = Lock
- The 6 positions correspond to threat types (in order): П (P), З (3), Х (X), Н (H), Ф (F), С (C)

### beacons.lua parsing

- File location: `<dcs_install>\Mods\terrains\<terrain>\beacons.lua`
- Terrain name from DTC's `data.terrain` field
- Parser: `slpp` library (Lua table → Python dict). If slpp fails on edge cases, fall back to regex extraction.
- Build a lookup dictionary: `{frequency_khz: station_name}` for NDB-type beacons
- Cache the parsed result per terrain (only re-parse if terrain changes)

### Kneeboard output

- **Path:** `<saved_games>\Kneeboard\MiG-29 Fulcrum\000_dtc_config.jpg`
  - Note: `Kneeboard` (no 's'), `MiG-29 Fulcrum` (no 'A', with space)
  - The `000_` filename prefix sorts the page first in DCS's kneeboard page order
  - Create the directory if it doesn't exist
- **Behaviour:** Overwrites any existing `000_dtc_config.jpg` on each generation
- **Format:** JPEG, 1536 × 2048 pixels
- DCS refreshes user kneeboard pages on respawn, so the new image appears on the next spawn

### Empty section handling

When a section has no configured data (empty array or all-default values), the section is still rendered with its header band intact, but the body shows "NO CONFIG" in the empty-state pattern (see design spec). This maintains consistent layout for muscle memory.

---

## 4. Kneeboard Visual Design Specification

### Canvas

- 1536 × 2048 px, portrait, JPEG output
- Background: #FFFFFF (white)

### Fonts

| Role | Font family | Weight | Fallback |
|---|---|---|---|
| Display/headers | Barlow Condensed | 700/800 | Oswald |
| Body text | Barlow | 500/600/700 | Open Sans |
| Data/monospace | JetBrains Mono | 500/700 | Roboto Mono |

All three are Google Fonts. Download TTF files and bundle with the exe. All numeric data uses tabular (monospaced) figures for column alignment.

### Colour palette

| Token | Hex | Used for |
|---|---|---|
| ink | #15171A | Primary text, section header fills, structural rules |
| ink-2 | #2C2F35 | Secondary text, eyebrow labels |
| ink-3 | #8A8E95 | "Off" status, NO CONFIG label |
| col-header | #ECEEF1 | Column-header strip fill |
| rule | #C8C2B6 | Dotted dividers |
| data-red | #C1273B | SPO-15 Lock state |
| data-green | #1F8A3F | SPO-15 On state |

### Page frame

| Element | Value |
|---|---|
| Outer margin (all sides) | 48 px |
| Section gap | 24 px |
| Structural border | 2.0 px solid #15171A |
| Inner divider | 1.5 px solid #15171A |
| Row separator | 1 px dotted #C8C2B6 (6px on / 6px off) |
| Column separator | 1 px dotted #C8C2B6 |

### Header strip (~150 px height)

- **Page tag (left side):** Background #15171A, padded, text "MIG-29A" in Barlow Condensed 800 36px white, all-caps
- **Title:** "DTC · PROGRAM [N]" in Barlow Condensed 800 56px #15171A, all-caps
- **Subtitle:** "Data Transfer Cartridge Configuration Summary" in Barlow 500 18px #2C2F35
- **Meta cells (right side):**
  - "THEATRE" label: Barlow 700 12px tracked 0.18em upper #2C2F35; value: JetBrains Mono 500 22px #15171A
  - "GENERATED" label: same style; value: JetBrains Mono 500 22px #15171A (format: "DD Mon YYYY HH:MM UTC")

### Section cards

- **Border:** 2 px solid #15171A
- **Header band:** 64 px height, fill #15171A
  - Title: Barlow Condensed 800 28px white, tracked 0.04em, all-caps, padded 22px L/R
  - Subtitle: Barlow 500 14px white, tracked 0.18em, opacity 0.70
- **Column-header strip:** Fill #ECEEF1, height 40px, text Barlow 700 13px #2C2F35 tracked 0.18em upper
- **Default row height:** 56 px min
- **Compact row (Radio):** 40 px min
- **Row padding:** 8px top/bottom, 18px left/right

### Cell typography

| Style | Font | Weight | Size | Colour |
|---|---|---|---|---|
| Slot tag (WPT1, CH 00) | JetBrains Mono | 500 | 24 px | #15171A |
| Resolved name | Barlow | 600 | 26 px | #15171A |
| Secondary value (freq, rwy) | JetBrains Mono | 500 | 22-24 px | #15171A |
| Modulation tag (AM/FM) | JetBrains Mono | 500 | 18 px | #2C2F35 |
| ADF freq cross-check (under resolved name) | JetBrains Mono | 500 | 18 px (≈70% of name) | #2C2F35 |
| SPO-15 Cyrillic glyph | Barlow Condensed | 800 | 36 px | #15171A |
| SPO-15 Latin (parens) | JetBrains Mono | 500 | 16 px | #2C2F35 |

### SPO-15 status chips

Pill-style fixed-width chip (min 90px wide, 36px tall, no border radius). Text: Barlow Condensed 800 18px tracked 0.12em upper.

| State | Fill | Text colour | Border |
|---|---|---|---|
| Lock | #FFFFFF | #C1273B | 1.5 px #C1273B |
| On | #FFFFFF | #1F8A3F | 1.5 px #1F8A3F |
| Off | #FFFFFF | #8A8E95 | 1.5 px #8A8E95 |

### Empty-state pattern

- Top hatched rule (6px on / 6px off, 1px, #C8C2B6)
- Centred "NO CONFIG" in Barlow Condensed 800 32px #8A8E95 tracked 0.16em upper
- Optional hint "NOT PROGRAMMED" in Barlow 500 14px tracked 0.12em #8A8E95
- Bottom hatched rule
- Section header band remains unchanged

### Page layout (Omar's Grid)

```
[              Header (full width)                    ]

[ Waypoints    |  Aerodromes     |  RSBN              ]
[ 3 rows       |  3 rows        |  3 rows             ]

[ ADF (2/3 width)                |  (empty space)     ]
[ 4 rows, inner/outer            |                    ]

[ SPO-15       |  CMDS           |  Radio             ]
[ 6 rows       |  6 rows        |  20 rows (compact)  ]
```

**Row 1 (navigation triplet):** ~420 px total height. Three equal-width columns for Waypoints, Airdromes, RSBN.

**Row 2 (ADF):** ADF spans approximately columns 1-2 width. Column 3 area is empty space (no box drawn).

**Row 3 (defensive + radio):** SPO-15 and CMDS in columns 1-2. Radio occupies column 3 and is the tallest section, driving the row height. Radio uses compact 40px rows to fit all 20 channels.

---

## 5. Configuration

### config.json schema

```json
{
    "version": 1,
    "dcs_install_path": "D:\\DCS World",
    "dcs_saved_games_path": "C:\\Users\\Omar\\Saved Games\\DCS",
    "dcs_temp_path": "C:\\Users\\Omar\\AppData\\Local\\Temp\\DCS",
    "hook_version": "1.0"
}
```

### Auto-detection logic

| Path | Detection method | Fallback |
|---|---|---|
| DCS install | Windows registry `HKLM\SOFTWARE\Eagle Dynamics\DCS World` → `Path` value. Then scan: `C:\Program Files\Eagle Dynamics\DCS World`, `D:\DCS World`, `E:\DCS World`, `D:\Games\DCS World`. Validate by checking for `bin\DCS.exe`. | GUI popup with Browse button |
| DCS Saved Games | `%USERPROFILE%\Saved Games\DCS\`. Validate by checking the directory exists. | GUI popup with Browse button |
| DCS temp | `%LOCALAPPDATA%\Temp\DCS\`. Validate by checking the directory exists (may not exist until DCS runs once). | GUI popup with Browse button |

---

## 6. Lifecycle and Edge Cases

| Scenario | Behaviour |
|---|---|
| Player spawns in MiG-29 | Hook writes trigger → watcher generates kneeboard |
| Player respawns in same MiG-29 | Hook fires again → watcher overwrites kneeboard |
| Player switches to different aircraft | Hook does nothing (no trigger). Old kneeboard remains in `MiG-29 Fulcrum` kneeboard folder. |
| Player switches to different MiG-29 variant | Hook fires (prefix match) → watcher regenerates |
| New mission loaded | Hook fires on new slot selection. Watcher picks up new temp files (most recent .bin by mtime). |
| DCS not running | Watcher polls harmlessly, no trigger file appears |
| Watcher not running | Hook writes trigger file, but nothing processes it. On next watcher launch, stale trigger file is ignored (check timestamp age, discard if >5 minutes old). |
| Multiple .bin files from previous missions | Watcher uses most recently modified file matching the heuristic |
| beacons.lua not found | ADF section falls back to raw frequency display |
| DTC has no data in a section | Section renders with "NO CONFIG" empty state |

---

## 7. File and directory structure

```
DTC_Kneeboard_Utility/
├── dtc_kneeboard.exe              # PyInstaller single-file executable
├── config.json                     # Auto-generated on first run
├── README.md                       # User documentation
└── fonts/                          # Bundled TTF fonts (embedded in exe)
    ├── BarlowCondensed-Bold.ttf
    ├── BarlowCondensed-ExtraBold.ttf
    ├── Barlow-Medium.ttf
    ├── Barlow-SemiBold.ttf
    ├── Barlow-Bold.ttf
    ├── JetBrainsMono-Medium.ttf
    └── JetBrainsMono-Bold.ttf
```

### Source code structure (pre-packaging)

```
src/
├── main.py                         # Entry point, GUI setup, tray icon
├── config.py                       # Config loading, saving, auto-detection
├── hook_manager.py                 # Hook installation and version checking
├── trigger_watcher.py              # Polls for trigger file
├── bin_parser.py                   # Binary temp file scanner and JSON extractor
├── dtc_processor.py                # DTC JSON processing and value resolution
├── beacon_parser.py                # beacons.lua parser (slpp-based)
├── kneeboard_renderer.py           # Pillow-based kneeboard image generation
├── resources/
│   ├── hook_template.lua           # Lua hook source (bundled, copied to DCS on install)
│   └── fonts/                      # TTF font files
└── tests/
    ├── test_bin_parser.py          # Tests against sample .bin files
    ├── test_dtc_processor.py       # Tests against sample .dtc files
    ├── test_beacon_parser.py       # Tests against sample beacons.lua
    ├── test_kneeboard_renderer.py  # Visual regression tests
    └── fixtures/                   # Sample .dtc, .bin, beacons.lua files
```

---

## 8. Development and testing plan

### Phase 1: Core pipeline (Mac development)

Build and test against sample data without DCS integration:

1. `dtc_processor.py` — Parse .dtc JSON files, resolve all section values. Test against the three sample DTCs.
2. `beacon_parser.py` — Parse beacons.lua, build freq→name lookup. Test against Syria beacons.lua.
3. `kneeboard_renderer.py` — Generate kneeboard image from processed DTC data. Visual verification.
4. `bin_parser.py` — Extract JSON from binary .bin files. Test against sample temp directory (copied from Windows PC).

### Phase 2: Watcher and trigger (Mac development)

5. `trigger_watcher.py` — File polling loop. Test with manually created trigger files.
6. `config.py` — Config loading/saving. Test path validation.
7. Integration test: trigger file → bin parsing → DTC processing → kneeboard generation.

### Phase 3: GUI and hook (Windows development)

8. `main.py` — customtkinter GUI with settings, log, tray icon.
9. `hook_manager.py` — Hook install/update logic.
10. End-to-end testing on Windows gaming PC with live DCS.

### Phase 4: Packaging

11. PyInstaller `--onefile` packaging with bundled fonts and hook template.
12. Test exe on clean Windows machine (no Python installed).

---

## 9. Dependencies

### Python packages

```
customtkinter>=5.2.0
Pillow>=10.0.0
slpp>=1.2.3
pystray>=0.19.0      # System tray icon
```

### System requirements

- Windows 10/11 (target runtime)
- DCS World 2.9+ with MiG-29A Fulcrum module
- No Python installation required (bundled in exe)

---

## 10. Outstanding research items

1. **Kneeboard output path verification:** ~~Confirm at runtime whether `Kneeboard\MiG-29 Fulcrum\` or `Kneeboards\MiG-29A Fulcrum\` is the correct path.~~ **Resolved (30 May 2026):** confirmed as `Kneeboard\MiG-29 Fulcrum\`, output file `000_dtc_config.jpg` (see section 3).

2. **beacons.lua structure validation:** Parse a real beacons.lua file from the Syria map during Phase 1 to confirm slpp handles it correctly. If it fails, implement regex fallback.

3. **Temp file lifecycle timing:** During Phase 3 testing, confirm when DCS writes/updates the temp .bin files (mission load vs. slot entry vs. DTC change). This determines whether the 3-second post-trigger delay is sufficient.

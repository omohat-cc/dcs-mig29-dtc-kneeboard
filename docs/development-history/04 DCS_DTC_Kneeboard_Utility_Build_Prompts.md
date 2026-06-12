# DCS MiG-29 DTC Kneeboard Utility — Claude Code Build Prompts

> **How to use:** Run these prompts sequentially in Claude Code sessions. Each prompt builds on the output of the previous one. Attach the technical spec and relevant reference files to each session.

---

## Session 1: Core DTC Processing Pipeline

### Files to attach
- `DCS_DTC_Kneeboard_Utility_Technical_Spec.md` (the full technical spec)
- `DCS_MiG29_DTC_Format_Reference.md` (DTC format reference)
- `SPO15___Points___ADF_-_Contention_SARH_Era1.dtc` (sample DTC with 79 Points, Radio, ADF, CMDS, SPO-15)
- `Aerodrome_Point_Test_DTC.dtc` (sample DTC with Airdromes and Waypoints)
- `RSBN_Test_DTC.dtc` (sample DTC with RSBN entry)

### Prompt

```
Read the attached technical specification document completely before starting. This is the spec for a DCS World utility that generates kneeboard images from MiG-29A DTC configuration data.

Build the core DTC processing module (`dtc_processor.py`) that:

1. Takes a parsed DTC JSON dict as input (from a .dtc file or extracted from a binary .bin temp file — at this stage we're working with .dtc files directly)
2. Identifies the active program from `data.SelectedProgram`
3. Extracts and resolves all 7 sections as described in the spec (Section 3: "DTC data processing"):
   - Waypoints (resolve names via Points lookup)
   - Airdromes (branch on type: "Airdrome" uses name directly, "Point" looks up in Points array)
   - RSBN (name + channel)
   - ADF (frequency + placeholder for beacon name resolution — return raw freq for now, beacon lookup comes later)
   - Radio (20 channels, freq with 3 decimal places, AM/FM)
   - CMDS (resolve all 6 indices using the lookup tables in the spec)
   - SPO-15 LW (resolve 6 indices to Off/On/Lock with threat type labels)

4. Returns a structured Python dict with all resolved data, ready for the kneeboard renderer to consume.

Also build a test script (`test_dtc_processor.py`) that runs the processor against all three sample .dtc files and prints the resolved output for each section. Verify:
- The Contention DTC resolves Radio (20 channels), ADF (4 channels), CMDS (6 params), SPO-15 (6 entries)
- The Aerodrome Test DTC resolves 1 Waypoint (name from Points lookup) and 2 Airdromes (one Point-type, one Airdrome-type)
- The RSBN Test DTC resolves 1 RSBN entry (Krasnodar-Center, Ch 40)
- Empty sections return a flag indicating "no config" rather than an empty list

Output structure should be a clean Python dataclass or typed dict. Use type hints throughout. No external dependencies beyond stdlib for this module. Put this in the Mig-29 DTC/DTC Kneeboard Utility/App folder
```

---

## Session 2: Binary Temp File Parser

### Files to attach
- `DCS_DTC_Kneeboard_Utility_Technical_Spec.md`
- `DCS_DTC_Research_Findings.md` (research findings with binary format details)
- Sample temp directory from Windows PC (copy `~tr*.bin` and `~tr*.txt` files)
- Output from Session 1 (`dtc_processor.py`)

### Prompt

```
Read the attached technical specification (Section 3, "DTC extraction from binary temp files") and the research findings document (Section 2, "Where DTC data actually lives") completely before starting.

Build the binary temp file parser module (`bin_parser.py`) that:

1. Scans a given directory for files matching `~tr*.bin`
2. Filters to files between 500KB and 2MB
3. Sorts by modification time (most recent first)
4. For each candidate, reads the first 64KB and checks for both `MiG-29` and `"data"` strings
5. For the first matching file, extracts the embedded DTC JSON using the algorithm from the research doc:
   a. Walk byte-by-byte
   b. Collect runs of printable ASCII (bytes 32-126, plus newline/tab)
   c. Discard runs shorter than 50 characters
   d. Strip leading `N ` prefix from each run
   e. Concatenate cleaned runs
   f. Find `{"data":` substring
   g. Brace-depth match to find the complete JSON object
   h. Parse with json.loads()
6. Returns the parsed JSON dict (same format as a .dtc file's content)

Also build a test script that:
- Runs against a sample temp directory (the attached .bin files from a real DCS session)
- Prints the extracted DTC profile name, map, active program, and point count
- Compares the extracted JSON structure against what dtc_processor.py expects
- Handles edge cases: no matching files found, corrupted binary, JSON parse failure

Make the module robust: wrap everything in try/except, log errors clearly, return None on failure rather than crashing. This runs as part of a background utility — it must never crash.
```

---

## Session 3: beacons.lua Parser

### Files to attach
- `DCS_DTC_Kneeboard_Utility_Technical_Spec.md`
- A copy of `beacons.lua` from `D:\DCS World\Mods\terrains\Syria\beacons.lua` (copy from Windows PC)

### Prompt

```
Read the attached technical spec (Section 3, "beacons.lua parsing") before starting.

Build the beacon parser module (`beacon_parser.py`) that:

1. Takes a path to a beacons.lua file
2. Parses the Lua table structure using the `slpp` library (pip install slpp)
3. Extracts all NDB-type beacons with their frequency and station name
4. Builds a lookup dictionary: `{frequency_khz: station_name}`
5. Provides a lookup function: given a frequency, return the station name or None

Also build a wrapper function that:
- Takes the DCS install path and terrain name (e.g., "Syria")
- Constructs the beacons.lua path: `<dcs_install>/Mods/terrains/<terrain>/beacons.lua`
- Parses and caches the result (only re-parse if terrain changes)
- Returns the lookup dictionary

Test against the attached beacons.lua:
- Print all NDB beacons found (name + frequency)
- Verify that the ADF frequencies from the Contention DTC sample (342, 351, 337, 450 kHz) resolve to station names
- If slpp fails on any part of the file, document the failure and implement a regex fallback for the specific structure we need

Important: beacons.lua is a Lua file, not JSON. The slpp library converts Lua table literals to Python dicts. If the file uses Lua features beyond simple tables (function calls, variables, comments), slpp may choke. Inspect the file structure first and document what you find.
```

---

## Session 4: Kneeboard Renderer

### Files to attach
- `DCS_DTC_Kneeboard_Utility_Technical_Spec.md` (specifically Section 4: Kneeboard Visual Design Specification)
- `MiG-29_DTC_Kneeboard__standalone_.html` (Claude Design reference — open in browser and screenshot for visual reference)
- `MiG-29_DTC_Kneeboard___Layout_Explorations.pdf` (design mockups and Pillow replication spec on pages 3-4)
- Output from Session 1 (`dtc_processor.py`)
- All three sample .dtc files

### Prompt

```
Read the attached technical specification Section 4 (Kneeboard Visual Design Specification) and the PDF pages 3-4 (Pillow Replication Spec) completely before starting. These contain the exact pixel-level specifications for the kneeboard design.

Build the kneeboard renderer module (`kneeboard_renderer.py`) that:

1. Takes the processed DTC data dict (output of dtc_processor.py) as input
2. Generates a 1536x2048 JPEG kneeboard image using Pillow
3. Follows the design spec exactly — fonts, colours, spacing, layout

Implementation details:

**Fonts:** Download these TTF files from Google Fonts and store in a `fonts/` directory:
- Barlow Condensed ExtraBold (800)
- Barlow Condensed Bold (700)
- Barlow Medium (500)
- Barlow SemiBold (600)
- Barlow Bold (700)
- JetBrains Mono Medium (500)
- JetBrains Mono Bold (700)

**Layout (Omar's Grid):**
- Header: full width, ~150px height
- Row 1: Waypoints | Airdromes | RSBN (three equal columns, ~420px total)
- Row 2: ADF spanning ~2/3 width | empty space right
- Row 3: SPO-15 | CMDS | Radio (Radio is tallest, drives row height, uses compact 40px rows)

**Section rendering:**
- Each section is a "card" with a dark header band (#15171A, 64px) and a white body
- Column headers in light grey strip (#ECEEF1, 40px)
- Data rows with dotted separators between them
- Empty sections show the "NO CONFIG" empty-state pattern (hatched rules + centred text)

**SPO-15 status chips:**
- Lock: white fill, red border/text (#C1273B)
- On: white fill, green border/text (#1F8A3F)
- Off: white fill, grey border/text (#8A8E95)

Build a test script that:
- Processes each of the three sample .dtc files through dtc_processor.py
- Generates a kneeboard image for each
- Also generates a "fully populated" test case by combining data from all three DTCs (to verify layout when all sections have data)
- Also generates a "fully empty" test case (all sections show NO CONFIG)
- Saves all outputs as JPG files for visual inspection

The renderer must handle variable content gracefully — the layout structure stays fixed (muscle memory), but row counts within sections vary. Radio always has 20 rows. Other sections may have 0-3 entries.
```

---

## Session 5: Trigger Watcher and Integration

### Files to attach
- `DCS_DTC_Kneeboard_Utility_Technical_Spec.md`
- All modules from Sessions 1-4

### Prompt

```
Read the attached technical spec Sections 2 (Lua Hook), 5 (Configuration), and 6 (Lifecycle) before starting.

Build three modules:

**1. trigger_watcher.py**
- Polls for trigger file at `<saved_games>/Logs/dtc_kneeboard_trigger.json` every 2 seconds
- On detection: reads the JSON, checks timestamp (discard if older than 5 minutes), deletes the file
- Waits 3 seconds post-trigger for DCS to finish writing temp files
- Calls bin_parser to find and extract DTC data
- Calls dtc_processor to resolve all sections
- Calls beacon_parser for ADF name resolution
- Calls kneeboard_renderer to generate the image
- Writes output to `<saved_games>/Kneeboard/MiG-29 Fulcrum/dtc_config.jpg`
- Runs as a background thread (will be called from the GUI main loop)
- Emits log messages via a callback function (for the GUI log panel)
- Must never crash — all errors caught and logged

**2. config.py**
- Loads/saves config.json from the same directory as the executable
- Auto-detection logic for all three paths (see spec Section 5):
  - DCS install: Windows registry, then common paths, validate with bin/DCS.exe
  - Saved Games: %USERPROFILE%\Saved Games\DCS\, validate directory exists
  - Temp: %LOCALAPPDATA%\Temp\DCS\, validate directory exists (may not exist until DCS first runs)
- Returns a config dataclass with all paths
- Reports which paths were auto-detected vs. need manual entry

**3. hook_manager.py**
- Checks if the Lua hook exists at `<saved_games>/Scripts/Hooks/dtc_kneeboard_hook.lua`
- If missing: writes it from a bundled template
- If present: reads the version comment on line 1, compares to bundled version, updates if outdated
- Creates the `Scripts/Hooks/` directory if it doesn't exist
- The hook template is the Lua code described in spec Section 2

Also create the Lua hook template (`hook_template.lua`) implementing the behaviour described in spec Section 2:
- onPlayerChangeSlot callback
- Deferred polling via onSimulationFrame (600 frame initial delay, then poll every 60 frames, max 30 retries)
- MiG-29 prefix match on getPlayerUnitType()
- Atomic trigger file write
- Diagnostic logging to dtc_kneeboard_hook.log
- All DCS API calls wrapped in pcall

Write integration tests that simulate the full pipeline: create a mock trigger file → parse a sample .bin or .dtc → process → render → verify output image exists.
```

---

## Session 6: GUI and Packaging

### Files to attach
- `DCS_DTC_Kneeboard_Utility_Technical_Spec.md`
- All modules from Sessions 1-5

### Prompt

```
Read the attached technical spec Section 5 (Configuration and UX) before starting.

Build the main application (`main.py`) using customtkinter:

**GUI layout:**
- Window title: "DCS MiG-29 DTC Kneeboard Utility"
- Settings section (top):
  - Three path fields (DCS Install, Saved Games, Temp Dir), each with a label, text entry, and "Browse" button
  - Auto-filled with detected values on first run
  - "Save Settings" button
- Status section (bottom):
  - Scrolling log text area (read-only) with timestamps
  - Shows activity: "Watching for trigger...", "Trigger detected", "Scanning...", "Kneeboard generated → [path]"

**Window behaviour:**
- Close (X) minimises to system tray (do NOT quit)
- System tray icon (use pystray): left-click restores, right-click menu has Restore / Quit
- On launch: window opens visible (not minimised)

**Startup sequence:**
1. Load or create config (show Browse dialogs for any failed auto-detections)
2. Check/install Lua hook (log the action)
3. Start trigger watcher in background thread
4. Enter main loop

**First-run popup:**
If any path auto-detection fails, show a dialog (customtkinter CTkToplevel) listing which paths were not found, with Browse buttons for each. Block until resolved.

**Threading:**
- Trigger watcher runs in a daemon thread
- Log messages passed to GUI via thread-safe queue
- GUI polls the queue in its main loop (every 100ms) and appends to the log widget

After the GUI is working, create a PyInstaller spec file for `--onefile` packaging:
- Bundle all font TTF files
- Bundle the Lua hook template
- Set the exe name to `DCS_DTC_Kneeboard.exe`
- Include customtkinter's assets (customtkinter requires special PyInstaller handling — check their docs)
- Set a Windows icon if possible

Document the PyInstaller build command in a `BUILD.md` file.
```

---

## Notes for all sessions

- Target OS is Windows but development happens on Mac. Use `os.path` / `pathlib` for all paths. Avoid Windows-specific APIs until Session 5+ (where registry access etc. is needed — guard with platform checks).
- Type hints on all functions.
- Docstrings on all public functions.
- Logging via Python `logging` module (not print statements).
- All file I/O wrapped in try/except with meaningful error messages.
- No external dependencies beyond: `customtkinter`, `Pillow`, `slpp`, `pystray`. Everything else from stdlib.

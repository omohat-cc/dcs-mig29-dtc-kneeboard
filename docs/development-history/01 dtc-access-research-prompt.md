# DTC Access Research Prompt — DCS MiG-29A Kneeboard Utility

Use this prompt in a Claude Code session on your Windows gaming PC (where DCS is installed).

---

## Context

I'm building a utility that reads MiG-29A DTC (Data Transfer Cartridge) config data from DCS World and generates custom kneeboard images. Before building the full utility, I need to determine HOW to access the DTC data at runtime. This task is purely a research spike — build small test scripts, I'll run them in DCS, and report back what works.

## Background: How DCS exposes data to external tools

DCS World has several scripting/export contexts that external tools can hook into:

1. **Export.lua** (`Saved Games\DCS\Scripts\Export.lua`) — runs in the export context every frame. Has access to `LoGet*` functions (LoGetSelfData, LoGetRoute, etc.). Tools like SRS and force feedback software use this. The file typically sources other scripts via `dofile()` so multiple tools can coexist.

2. **Hooks** (`Saved Games\DCS\Scripts\Hooks\*.lua`) — run in the hooks environment. Have access to `DCS.*` functions (DCS.getCurrentMission, DCS.getModelTime, etc.) and callbacks like `onSimulationStart`, `onSimulationStop`, `onPlayerChangeSlot`. Tools like Tacview use this. Any `.lua` file dropped in the Hooks folder is auto-loaded.

3. **Unpacked mission temp directory** (`%USERPROFILE%\AppData\Local\Temp\DCS\`) — when DCS loads a .miz mission file, it unzips it here. The DTC data may be embedded in the mission Lua tables inside this unpacked directory. The `lfs` (Lua File System) module is typically available in hooks for reading files.

## What the DTC file looks like

The DTC is a JSON file with this structure (abbreviated):
```json
{
    "data": {
        "mirror_ADF": false,
        "mirror_NAV": false,
        "mirror_Radio": false,
        "name": "My DTC Profile",
        "Program_1": {
            "ADF": { "Channel_1_Inner": {"freq": 342, "modulation": 1}, ... },
            "Airdromes": [ ... ],
            "CMDS": { "idxFlareBurstCountI": 1, ..., "LW_indices": [3,2,1,2,3,1] },
            "Points": [ {"alt":2000, "id":"PNT1", "name":"PNT1", "note":"AbDuhur", ...}, ... ],
            "Radio": { "Channel_0": {"freq": 124, "modulation": 1}, ... },
            "Route": [ ... ],
            "RSBN": [ ... ],
            "TargetPoint": { ... },
            "Waypoints": [ ... ],
            "WeaponSettings": { "trainingMode": false }
        },
        "Program_2": { ... },
        "Program_3": { ... },
        "SelectedProgram": "Program_1",
        "terrain": "Syria",
        "type": "MiG-29 Fulcrum"
    },
    "name": "My DTC Profile",
    "type": "MiG-29 Fulcrum"
}
```

When the player loads a DTC in the briefing screen and spawns, this data is loaded into the aircraft. The question is: can we read it back out via any of the three access methods above?

## Your task: build three test probes

Create three independent test scripts. Each one attempts to extract DTC-related data through a different method and writes everything it finds to a log file at `Saved Games\DCS\Logs\dtc_probe_log.txt`. I will run all three simultaneously during a DCS session where I spawn in a MiG-29A with a DTC loaded.

### Probe 1: Export.lua probe

Create a file: `Saved Games\DCS\Scripts\dtc_export_probe.lua`

This script should:
- Hook into the Export.lua lifecycle (LuaExportStart, LuaExportAfterNextFrame, LuaExportStop)
- On start, attempt to call every potentially relevant LoGet* function and log the results. Key ones to try:
  - `LoGetRoute()` — may return waypoint/navigation data
  - `LoGetSelfData()` — aircraft type/position (to confirm we're in a MiG-29)
  - `LoGetNavigationInfo()` — if it exists
  - `LoGetMCPState()` — mission computer state, if accessible
  - `LoGetPayloadInfo()` — weapon loadout
  - Any other LoGet* functions you can find documented
- Log the full return value of each function (serialise tables recursively to the log)
- Only needs to run for the first 30 seconds after spawn then stop polling (to avoid performance impact)
- Must coexist with existing Export.lua content (append/dofile pattern, don't overwrite)

Also create a small snippet to add to `Export.lua` that sources this probe:
```lua
local probe = lfs.writedir() .. "Scripts\\dtc_export_probe.lua"
local f = io.open(probe, "r")
if f then f:close(); dofile(probe) end
```

### Probe 2: Hooks probe

Create a file: `Saved Games\DCS\Scripts\Hooks\dtc_hooks_probe.lua`

This script should:
- Register callbacks for: `onSimulationStart`, `onSimulationStop`, `onPlayerChangeSlot`, `onMissionLoadEnd` (and any other relevant callbacks)
- In each callback, attempt to call every potentially useful DCS.* function and log results:
  - `DCS.getCurrentMission()` — the big one; may contain the full mission table including DTC data
  - `DCS.getMissionFilename()` — path to the loaded .miz
  - `DCS.getAvailableCoalitions()`
  - `DCS.getAvailableSlots(coalition)`
  - `DCS.getMissionOptions()`
  - `DCS.getMissionDescription()`
  - Any other DCS.* functions that might expose aircraft or DTC config
- Also try to access the `net` environment if available (net.get_player_info, net.get_slot, etc.)
- Serialise and log everything found
- Specifically look for anything containing "dtc", "DTC", "cartridge", "Program_1", "ADF", "CMDS", "Points", "Waypoints" in the returned data (search recursively through tables)

### Probe 3: Temp directory probe

Create a file: `Saved Games\DCS\Scripts\Hooks\dtc_tempdir_probe.lua`

This script should:
- On `onMissionLoadEnd` (or whichever fires after the mission is unpacked), use `lfs` to scan `%USERPROFILE%\AppData\Local\Temp\DCS\`
- List all files and subdirectories recursively (up to 3 levels deep) and log the tree
- Look for any file that might contain DTC data — specifically:
  - Any `.dtc` files
  - The `mission` file (Lua table) — try to read it and search for DTC-related keys
  - Any file containing "dtc", "cartridge", "Program" in its name or content
  - The `dictionary` file if present
- If it finds the `mission` file, read it and log the first 5000 characters (to see structure)
- Also check if there are any files that appeared AFTER mission load that weren't there before (snapshot the directory listing at hook load vs at mission load)

### Shared utilities

All three probes should share a common logging approach:
- Write to: `<lfs.writedir()>\Logs\dtc_probe_log.txt` (which resolves to `Saved Games\DCS\Logs\dtc_probe_log.txt`)
- Each log entry: `[TIMESTAMP] [PROBE_NAME] message`
- Include a recursive table serialiser that handles circular references (DCS tables sometimes have these)
- Append mode (don't overwrite between probes)
- Write a `=== PROBE START ===` header when each probe initialises so I can see which ones loaded

### Installation and removal

Create:
1. An install script (PowerShell or batch) for Windows that:
   - Backs up existing `Export.lua` if present
   - Creates the three probe files in the correct locations
   - Adds the dofile snippet to Export.lua (or creates it if missing)
   - Prints confirmation of what was installed and where

2. An uninstall script for WIndows that:
   - Removes the three probe files
   - Removes the dofile snippet from Export.lua (restoring backup if available)
   - Does NOT delete the log file (I need to read it)

## What I will do with the output

After running DCS with these probes active:
1. Load a multiplayer or singleplayer mission
2. Select the MiG-29A with a DTC loaded
3. Spawn into the aircraft
4. Wait 60 seconds
5. Quit the mission

Then I'll bring the contents of `dtc_probe_log.txt` back to report what each probe found. Based on that, we'll determine the best data access method for the full utility.

## Important constraints

- These are throwaway research scripts, not production code. Prioritise comprehensive logging over elegance.
- DCS Lua is 5.1-based. No Lua 5.2+ features.
- `lfs` (LuaFileSystem) is available in hooks but may not be in the export context. Use `os.getenv("USERPROFILE")` as fallback for path construction.
- The probe scripts MUST NOT interfere with existing DCS functionality. They are read-only/logging-only.
- Do not modify any DCS game files. Only touch files under `Saved Games\DCS\`.
- The `Saved Games\DCS\` path is the standard location but confirm it exists. On some installs it might be `Saved Games\DCS.openbeta\` — handle both.
- Keep probe scripts short and focused. If a function call errors, catch it with pcall and log the error rather than crashing.
- I want you to create all of these files in a folder within DCS/Mig-29 DTC/DTC Kneeboard Utility/ called "DCS Probe"

# DCS MiG-29 DTC Access - Research Findings

> **Purpose:** This document captures all findings from a research spike into how to read MiG-29A DTC (Data Transfer Cartridge) configuration data from DCS World at runtime. It is intended to be consumed by a separate planning/build session. Read the entire document before starting implementation.
>
> **DCS version tested:** 2.9.26.23303
>
> **Date:** 7 May 2026

---

## 1. DCS scripting environment - what works and what doesn't

DCS World exposes three independent Lua scripting contexts. Each has different capabilities and different sandboxing constraints. All three were probed across multiple test runs (11 crashes in total to arrive at stable configurations).

### 1.1 Hooks context (`Scripts/Hooks/*.lua`)

This is the recommended context for the DCS-side trigger component. Files placed in `Saved Games/DCS/Scripts/Hooks/` are loaded automatically on DCS startup.

**What works:**

- `DCS.setUserCallbacks(table)` registers callback functions. The following callbacks are confirmed stable:
  - `onPlayerChangeSlot()` fires when the player selects or changes a slot (including initial slot selection in multiplayer).
  - `onSimulationFrame()` fires every simulation frame. Use this for deferred execution via countdown pattern.
- `DCS.getPlayerUnitType()` returns the aircraft type string (e.g. `"MiG-29 Fulcrum"`, `"MiG-29A"`). **Confirmed working in round 1** when the player was fully loaded into the slot.
- `DCS.getPlayerUnit()` returns the unit ID (numeric). **Confirmed working in round 1** (returned `10`).
- `DCS.getPlayerCoalition()` returns coalition string (e.g. `"blue"`).
- `DCS.getMissionName()`, `DCS.getMissionFilename()`, `DCS.getTheatreID()`, `DCS.isMultiplayer()` all work.
- `DCS.getCurrentMission()` returns a deep Lua table of the full mission structure (coalitions, countries, groups, units). No DTC data was found in this table, but it's useful for mission context.
- `lfs.writedir()` returns the Saved Games DCS path (e.g. `C:\Users\<user>\Saved Games\DCS\`).
- `io.open()` works for reading and writing files within and outside the DCS directory tree.
- `os.getenv()` works (e.g. `LOCALAPPDATA`, `USERPROFILE`).
- `os.date()`, `os.clock()` work normally.

**What does NOT work (and will crash or fail silently):**

- **`DCS.getMissionLoaded()` crashes DCS** with a NULL Lua state access violation. NEVER call this function. It is not recoverable with pcall.
- **C-implemented DCS functions crash when called without arguments.** For example, calling `DCS.getUnitType()` with no args triggers an ACCESS_VIOLATION inside the C layer. pcall cannot catch these. Always check function signatures before calling. If in doubt, do not call.
- **`io.popen` is nil.** It does not exist in the hooks Lua environment. Any code that calls `io.popen()` will error. Always check `type(io.popen) == "function"` before use.
- **`lfs.dir()` is wrapped by DCS's Virtual File System (VFS).** When called on paths outside DCS's internal structure (like `%LOCALAPPDATA%\Temp\DCS\`), it errors with `ed_lua_vfs.c:lfs_dir_walk`. You cannot use it to list arbitrary OS directories.
- **`net.dostring_in()` returns nil for all environments.** We tested `mission`, `server`, `export`, and `config` environments with various probe code. Every single call returned nil. This function is not useful for accessing DTC data.
- **`DCS.toggleDTC` exists as a function** but its purpose is unknown (potentially toggling the DTC UI panel). Not called during testing to avoid side effects. Do not call it.
- **`DCS.getPlayerUnitType()` and `DCS.getPlayerUnit()` can return empty strings** if called too soon after `onPlayerChangeSlot`. In round 2, even with a 1800-frame delay (~30 seconds), both returned empty. In round 1 (local multiplayer, different timing), they returned valid values. The deferred countdown approach works but timing is not guaranteed, especially on multiplayer servers. The trigger logic should retry or use a polling pattern.

### 1.2 Export context (`Scripts/Export.lua`)

Export.lua is meant for external device integration and provides `LoGet*` functions for cockpit instrument data.

**Status: Never fired in any test run.**

Despite installing a v2 Export.lua snippet with diagnostic breadcrumb logging (logs a line immediately when the snippet is reached, before any dofile), and running 3 separate test sessions, no `[EXPORT]` or `[EXPORT_LOADER]` log entries ever appeared. Possible causes:

- Export.lua may not be loaded in multiplayer contexts, or may only activate after the player is fully in the 3D world.
- There may be a conflict with other mods or scripts affecting Export.lua loading.

**Recommendation:** Do not rely on Export.lua for the trigger mechanism. Hooks are more reliable and fire earlier in the lifecycle.

### 1.3 DCS Lua sandbox summary

| Capability | Hooks | Export | Notes |
|---|---|---|---|
| `io.open` (read/write files) | YES | Untested (never fired) | Works for any OS path |
| `io.popen` (shell commands) | NO (nil) | - | Not available in DCS Lua |
| `os.execute` | Untested | - | May work but not confirmed |
| `os.getenv` | YES | - | |
| `lfs.dir` | BLOCKED (VFS) | - | Only works on DCS internal paths |
| `lfs.writedir` | YES | - | Returns Saved Games path |
| `lfs.attributes` | Likely VFS-wrapped | - | Not tested on external paths |
| `DCS.getPlayerUnitType` | YES (timing-sensitive) | N/A | |
| `net.dostring_in` | Returns nil | N/A | Useless for DTC data |

### 1.4 Crash log and stability notes

The research went through 11 crashes before reaching stable probe configurations. Key lessons:

1. **Never call `DCS.getMissionLoaded()`.** Instant crash, no recovery.
2. **Never call C-backed DCS functions without correct arguments.** The C layer does not validate and will segfault. pcall does not protect against this.
3. **Deferred execution is essential.** Code in hooks callbacks that runs immediately on `onPlayerChangeSlot` executes before DCS has finished internal state transitions. Use the countdown pattern via `onSimulationFrame` with at least 1800 frames (~30s) of delay.
4. **Self-contained hook scripts are stable.** The original architecture of hook -> dofile(worker) introduced unnecessary complexity. Inline code in the hook file itself is simpler and equally stable.
5. **The `io.popen` nil trap.** Early probe versions crashed because they called `io.popen()` before checking if it existed. Always guard with `type(io.popen) == "function"`.

---

## 2. Where DTC data actually lives

### 2.1 NOT in any DCS Lua API

After exhaustive probing of all three scripting contexts, the DTC configuration is definitively **not accessible through DCS Lua APIs**. Specifically:

- `getCurrentMission()` contains mission structure, units, payloads, and Radio configs, but no DTC keys (no Programs, Points, Waypoints, CMDS, ADF, RSBN, etc.).
- `net.dostring_in` returns nil for all environments.
- No global variables matching DTC/cartridge patterns exist (only ammunition cartridge constants like `cartridge_30mm`).
- The `LoGet*` export functions were unreachable (Export.lua never fired).

### 2.2 YES in DCS temp directory binary files

DTC data is serialised by DCS into binary temp files at:

```
%LOCALAPPDATA%\Temp\DCS\
```

For example: `C:\Users\<user>\AppData\Local\Temp\DCS\`

**File characteristics:**

- Filenames follow the pattern `~trXXXXXXXX.bin` (hex ID) and `~trXXXXXXXX.txt` (always empty).
- DCS creates approximately 3 `.bin` files and 2 `.txt` files per mission load. The exact number and hex IDs vary.
- Files start with a `BIN` magic byte sequence (e.g. `BIN'`, `BIN\xe9`, `BIN9`).
- File sizes observed: DTC files are ~917KB to ~983KB. Non-DTC files can be much larger (26MB track/replay) or smaller (131KB telemetry).
- **Files are NOT locked while DCS is running.** They can be read by external processes concurrently (confirmed by opening in BBEdit during active mission).

### 2.3 How to identify the DTC file

Read the first 64KB of each `.bin` file and check for BOTH of these strings:

1. `MiG-29` (or more specifically `MiG-29 Fulcrum`)
2. `"data"`

Only the DTC-bearing file will match both criteria. Tested across 5 `.bin` files from 2 separate sessions:

| File | Size | MiG-29 | "data" | Contains DTC? |
|---|---|---|---|---|
| ~tr0000003D.bin | 983KB | YES | YES | **YES** |
| ~tr00000829.bin | 917KB | YES | YES | **YES** |
| ~tr00005738.bin | 917KB | YES | YES | **YES** |
| ~tr00000222.bin | 26MB | NO | NO | No (track/replay recording) |
| ~tr00007FCA.bin | 131KB | NO | NO | No (binary telemetry) |

### 2.4 How to extract JSON from the binary file

The DTC JSON is embedded as plaintext within a binary container. The structure is:

- The binary file contains **multiple text runs** of ~20,000 bytes each, separated by **40-byte binary record boundaries**.
- Each text run starts with a single prefix character `N` followed by a space, then the JSON content continues from where the previous run left off.
- The JSON spans 6-7 text runs per DTC block (~113KB of JSON total).
- The file contains **7 identical copies** of the full DTC JSON (likely one per possible slot or program configuration). You only need to parse the first one.

**Extraction algorithm:**

1. Read the file (or at least the first ~130KB for one complete JSON block).
2. Walk byte-by-byte. Collect runs of printable ASCII characters (bytes 32-126, plus newline/tab).
3. Discard runs shorter than ~50 characters (noise).
4. Strip the leading `N ` prefix from each run.
5. Concatenate the cleaned runs. The result is valid JSON starting with `{"data":`.
6. Find the matching closing brace and parse with any JSON library.

**Binary separator format (40 bytes between text runs):**

```
00000001 00000006 1d5a643b 47cdd440 03000000 77c20100 324e0000 4b010000 0000141d 00008000
```

The first 4 bytes are a sequential counter (00000001, 00000002, ...). The rest appears to be metadata (possibly record offsets/sizes). These separators are what breaks `lfs.dir` and makes naive text extraction fail; a byte-level extraction handles them correctly.

---

## 3. Recommended architecture

### 3.1 Design principle

Use DCS hooks **only** as a lightweight trigger. Move **all** heavy work (directory scanning, file parsing, kneeboard generation) to an external process that is not constrained by DCS's Lua sandbox.

### 3.2 Component 1: DCS Hook (trigger only)

A minimal `Scripts/Hooks/` Lua script that:

1. Listens for `onPlayerChangeSlot()`.
2. Uses a deferred countdown via `onSimulationFrame()` (600+ frames).
3. Calls `DCS.getPlayerUnitType()` to check if the player is in a `MiG-29` variant.
4. If yes, writes a small trigger file to `Saved Games/DCS/Logs/` containing a timestamp (and optionally mission name, theatre).
5. Does nothing else.

**Important implementation notes for the hook:**

- `DCS.getPlayerUnitType()` may return empty string if called too early. Implement a retry/polling loop within `onSimulationFrame` - check every N frames until it returns a non-empty value, with a maximum attempt limit.
- The type string for the MiG-29A on tested servers was `"MiG-29 Fulcrum"`. Other variants may return `"MiG-29A"`, `"MiG-29S"`, `"MiG-29G"`. Match on `MiG-29` prefix to be safe.
- Guard every function call. If `DCS.getPlayerUnitType` is nil or errors, catch with pcall and log.
- The trigger file should be written atomically (write to temp name, then rename) to avoid the external watcher reading a partially-written file.

### 3.3 Component 2: External watcher/processor

A standalone script (language TBD by planning session) that:

1. Watches for the trigger file (polling or filesystem watcher).
2. When triggered, scans `%LOCALAPPDATA%\Temp\DCS\` using native OS filesystem calls.
3. Reads the first 64KB of each `.bin` file to identify the DTC file (check for `MiG-29` + `"data"`).
4. Extracts and parses the DTC JSON using the algorithm described in section 2.4.
5. Generates kneeboard image(s) from the parsed data.
6. Writes output to `Saved Games\DCS\Kneeboards\MiG-29A Fulcrum\` (DCS loads kneeboards from disk dynamically).

**Key considerations:**

- The external process should be started by the user before playing and run persistently.
- A short delay after the trigger (a few seconds) is recommended to ensure DCS has finished writing the temp files.
- The temp directory contents change per mission load. After a mission switch, the old files may be replaced.

---

## 4. Raw DTC JSON structure (sample)

This is a parsed and trimmed sample from `~tr00000829(contention).bin`, showing the complete structure with arrays abbreviated. The full JSON is ~113KB.

```json
{
  "data": {
    "name": "SPO15 + ADF - Contention SARH Era1 - South to North",
    "type": "MiG-29 Fulcrum",
    "terrain": "Syria",
    "SelectedProgram": "Program_1",
    "mirror_ADF": false,
    "mirror_NAV": false,
    "mirror_Radio": false,
    "Program_1": {
      "ADF": {
        "Channel_1_Inner": { "freq": 342, "modulation": 1 },
        "Channel_1_Outer": { "freq": 342, "modulation": 1 },
        "Channel_2_Inner": { "freq": 625, "modulation": 0 },
        "Channel_2_Outer": { "freq": 625, "modulation": 0 },
        "Channel_3_Inner": { "freq": 300, "modulation": 0 },
        "Channel_3_Outer": { "freq": 300, "modulation": 0 },
        "Channel_4_Inner": { "freq": 300, "modulation": 0 },
        "Channel_4_Outer": { "freq": 300, "modulation": 0 }
      },
      "Airdromes": [
        {
          "alt": 0,
          "id": "ARD1",
          "name": "Wujah Al Hajar",
          "note": "",
          "num": "ARD1",
          "runways": [
            { "channel": -1, "direction": -361, "display_name": "Wujah Al Hajar", "id": 1, "text": "02" },
            { "channel": -1, "direction": -361, "display_name": "Wujah Al Hajar", "id": 2, "text": "20" }
          ],
          "selectedRunwaySide": "02",
          "speed": 790,
          "type": "Airdrome",
          "unit_name": "Wujah Al Hajar",
          "x": -81524.375,
          "y": -22832.533203
        }
      ],
      "CMDS": {
        "idxFlareBurstCountI": 1,
        "idxFlareBurstCountII": 1,
        "idxFlareBurstInterval": 1,
        "idxFlareSalvoCountAir": 1,
        "idxFlareSalvoCountZrk": 1,
        "idxFlareSalvoInterval": 1,
        "LW_indices": [3, 2, 1, 2, 3, 1]
      },
      "Points": [
        {
          "alt": 2000,
          "id": "PNT1",
          "name": "PNT1",
          "note": "AbDuhur",
          "num": "PNT1",
          "speed": 790,
          "terrainAltitude": 407,
          "x": 76048.957031,
          "y": 111344.925781
        }
      ],
      "Radio": {
        "Channel_0": { "freq": 124, "modulation": 1 },
        "Channel_1": { "freq": 264, "modulation": 1 }
      },
      "Route": [],
      "RSBN": [],
      "TargetPoint": {
        "altitude": 804,
        "name": "PNT1",
        "x": -61778.598338532,
        "y": 90553.821611674
      },
      "Waypoints": [
        {
          "alt": 2000,
          "geo": true,
          "id": "PNT79",
          "name": "PNT79",
          "note": "",
          "num": "WPT1",
          "speed": 790,
          "x": -265352.879373,
          "y": -2119.938105
        }
      ],
      "WeaponSettings": {
        "trainingMode": false
      }
    },
    "Program_2": { "...same structure as Program_1, typically mostly empty..." },
    "Program_3": { "...same structure as Program_1, typically mostly empty..." }
  },
  "name": "",
  "type": ""
}
```

**Notes on the JSON structure:**

- `data.name` is the DTC profile name set by the user.
- `data.type` is always `"MiG-29 Fulcrum"` for the MiG-29.
- `data.terrain` is the map name (e.g. `"Syria"`).
- `data.SelectedProgram` indicates which of the 3 programs is active.
- Each Program contains: `ADF` (8 channels), `Airdromes` (array), `CMDS` (countermeasures), `Points` (up to 99 nav points), `Radio` (20 channels), `Route`, `RSBN`, `TargetPoint`, `Waypoints` (up to 3), `WeaponSettings`.
- The `Points` array is the richest data source for kneeboard content. In the tested DTC, it contained 79 named navigation points with coordinates, altitudes, speeds, and notes.
- Radio channels use `modulation: 1` for AM and `modulation: 0` for FM.
- The `x` and `y` coordinates are DCS internal map coordinates (not lat/lon). Conversion to lat/lon requires map-specific projection transforms.
- The outer `"name"` and `"type"` at the root level are empty strings (the meaningful data is all under `"data"`).

---

## 5. Environment reference

| Item | Path / Value |
|---|---|
| DCS install | `D:\DCS World` |
| Saved Games | `C:\Users\<user>\Saved Games\DCS\` |
| DCS temp dir | `C:\Users\<user>\AppData\Local\Temp\DCS\` |
| Kneeboard output | `C:\Users\<user>\Saved Games\DCS\Kneeboards\MiG-29A Fulcrum\` |
| Hook scripts | `C:\Users\<user>\Saved Games\DCS\Scripts\Hooks\` |
| Probe log | `C:\Users\<user>\Saved Games\DCS\Logs\dtc_probe_log.txt` |
| DCS version | 2.9.26.23303 |

---

## 6. Open questions for planning

1. **Trigger timing reliability.** `getPlayerUnitType()` returned empty in round 2 (multiplayer server) but worked in round 1 (local multiplayer). The hook trigger may need a retry/polling pattern rather than a single deferred check.
2. **Temp file lifecycle.** When exactly does DCS write/update the temp files? Is it on mission load, on slot entry, or on DTC change? This affects when the external watcher should scan.
3. **Multiple MiG-29 variants.** The type string may vary (`MiG-29 Fulcrum`, `MiG-29A`, `MiG-29S`, `MiG-29G`). The hook should match on `MiG-29` prefix.
4. **Coordinate conversion.** DTC points use DCS internal x/y, not lat/lon. If the kneeboard needs geographic display, a map projection conversion is required (map-specific).
5. **DTC profile changes mid-mission.** If the player modifies their DTC after slotting in, the temp file may or may not be updated. This needs testing.
6. **Kneeboard output format.** DCS expects kneeboard images as `.jpg` or `.png` at specific resolutions. The existing kneeboard generation skill uses 1536x2048 JPG.

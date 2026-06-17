# DCS MiG-29 DTC File Format Reference

This document captures everything learned about the MiG-29 Data Transfer Cartridge (.dtc) file format used in DCS World. The DTC is a core feature built by Eagle Dynamics that allows pilots to pre-programme navigation, radio, countermeasures, and weapon settings before they spawn into the aircraft.

---

## File Overview

- Extension: `.dtc`
- Encoding: JSON (valid, parseable by any JSON library)
- Formatting: Custom right-aligned style (NOT standard `json.dump` output - see Formatting section)
- Character encoding: UTF-8, no BOM

## Top-Level Structure

```
{
    "data": {
        <mirror settings>
        "name": "<profile name>",
        "Program_1": { <program contents> },
        "Program_2": { <program contents> },
        "Program_3": { <program contents> },
        "SelectedProgram": "Program_1",
        "terrain": "<map name>",
        "type": "MiG-29 Fulcrum"
    },
    "name": "<profile display name>",
    "type": "MiG-29 Fulcrum"
}
```

### Mirror Settings (top of `data` object)

These control whether settings from one program are mirrored to others:

| Key | Type | Purpose |
|-----|------|---------|
| `mirror_ADF` | bool | Mirror ADF radio settings across programs |
| `mirror_NAV` | bool | Mirror navigation settings across programs |
| `mirror_Radio` | bool | Mirror radio channel presets across programs |

### Terrain Values

The `terrain` field must match the DCS map name exactly. Known values:
- `"Syria"`
- `"Caucasus"`
- `"PersianGulf"`
- `"Nevada"`
- `"Sinai"`
- `"Kola"`
- `"MarianaIslands"`

## Program Structure

Each Program (1-3) contains these sections in alphabetical order:

| Section | Type | Purpose |
|---------|------|---------|
| `ADF` | object | ADF radio direction finder channels (4 inner/outer pairs) |
| `Airdromes` | array | Pre-programmed airdrome approach data (max 3) |
| `CMDS` | object | Countermeasure dispensing system settings |
| `Points` | array | Navigation points of interest (unlimited) |
| `Radio` | object | Radio frequency presets (channels 0-19) |
| `Route` | array | Route waypoints for the flight plan |
| `RSBN` | array | RSBN navigation system presets |
| `TargetPoint` | object/array | Designated target point(s) |
| `Waypoints` | array | Active waypoints loaded into the nav system (max ~6) |
| `WeaponSettings` | object | Weapon delivery settings |

Only one program is active at a time (set by `SelectedProgram`). The pilot selects which program to load at spawn.

---

## Points Section (detailed)

Points are the primary focus of the tactical injection workflow. They serve as a library of named locations the pilot can select from at spawn to load into the nav computer as waypoints.

### Point Object Schema

```json
{
    "alt": 2000,
    "id": "PNT1",
    "name": "PNT1",
    "note": "Shayrat",
    "num": "PNT1",
    "speed": 790,
    "terrainAltitude": 804,
    "x": -61834.462889942,
    "y": 90576.579259823
}
```

### Field Definitions

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| `alt` | int | Positive metres | Flight altitude at this point (default: 2000m) |
| `id` | string | Max 7 chars | Unique identifier, format `PNTn` where n is sequential |
| `name` | string | Max 7 chars | Display name in DTC tool (matches `id`) |
| `note` | string | Max 7 chars | Human-readable location name (shown in DTC UI) |
| `num` | string | Max 7 chars | Point number reference (matches `id`) |
| `speed` | int | km/h | Target speed at this point (default: 790 km/h) |
| `terrainAltitude` | int | Metres above sea level | Terrain elevation at the point coordinates |
| `x` | float | DCS internal coords | North-south position (positive = north) |
| `y` | float | DCS internal coords | East-west position (positive = east) |

### Important Notes

- `id`, `name`, and `num` are always identical (e.g., all set to `"PNT1"`)
- `note` is the field that shows the human-readable name (e.g., airfield name)
- The 7-character limit on `note` means names must be abbreviated (e.g., "Abu al-Duhur" becomes "AbDuhur", "FARP Alpha" becomes "FARP-A")
- `terrainAltitude` is an integer (rounded from source data)
- `x` and `y` can have variable decimal precision (up to 12 digits observed)
- There is no hard limit on the number of Points (79 tested and working)

---

## Coordinate System

DCS uses an internal Cartesian coordinate system per map (NOT latitude/longitude):

| Axis | Direction | Unit |
|------|-----------|------|
| `x` | North-south | Metres (positive = north of map origin) |
| `y` | East-west | Metres (positive = east of map origin) |

Each map has its own origin point. Coordinates are consistent within a given map terrain.

### Relationship to Beacons File

The `beacons.lua` files (found in DCS game files at `DCS World/Mods/terrains/<map>/beacons.lua`) store positions as:

```lua
position = { x_value, altitude_metres, y_value }
```

So: `position[0]` = DTC `x`, `position[1]` = terrain altitude, `position[2]` = DTC `y`

### Relationship to pydcs Library

The `pydcs` Python library provides airfield coordinates that map directly to DTC `x` and `y`. However, pydcs does NOT provide terrain altitude data.

### Relationship to .miz Mission Files

DCS mission files (.miz) are ZIP archives. Inside, `mission` is a Lua table. Static objects (including FARPs/helipads) store coordinates as:

```lua
["x"] = <value>,  -- maps directly to DTC x
["y"] = <value>,  -- maps directly to DTC y
```

FARPs are stored at altitude 0 in the mission file (DCS auto-places them on terrain at runtime). Terrain altitude must be estimated from nearby known elevations.

---

## Other Sections (brief reference)

### ADF (Automatic Direction Finder)

4 channels, each with Inner and Outer beacon:
```json
"Channel_1_Inner": { "freq": 342, "modulation": 1 }
```
- `freq`: frequency in kHz
- `modulation`: 1 = AM (standard for NDBs)

### Airdromes

Up to 3 pre-programmed approach airdromes:
```json
{
    "alt": 0,
    "id": "ARD4",
    "name": "An Nasiriyah",
    "note": "",
    "num": "ARD1",
    "runways": [ { "channel": -1, "direction": -361, "display_name": "...", "id": 1, "text": "04" } ],
    "selectedRunwaySide": "04",
    "speed": 790,
    "type": "Airdrome",
    "unit_name": "An Nasiriyah",
    "x": -124683.738281,
    "y": 85510.820313
}
```
- `id` format: `ARDn` (sequential from existing highest)
- `num` format: `ARDn` (sequential from 1, represents slot position)
- Can be emptied by setting to `[  ]` (note: two spaces between brackets)

### CMDS (Countermeasure Dispensing System)

```json
{
    "idxFlareBurstCountI": 1,
    "idxFlareBurstCountII": 1,
    "idxFlareBurstInterval": 1,
    "idxFlareSalvoCountAir": 1,
    "idxFlareSalvoCountZrk": 1,
    "idxFlareSalvoInterval": 1,
    "LW_indices": [ 3, 2, 1, 2, 3, 1 ]
}
```
- `LW_indices` is always an inline array (single line, spaces after `[` and before `]`)

### Radio

20 channels (Channel_0 through Channel_19):
```json
"Channel_0": { "freq": 124, "modulation": 1 }
```
- `freq`: MHz (integer)
- `modulation`: 1 = AM

### Waypoints

Active navigation waypoints loaded into the cockpit:
```json
{
    "alt": 2000,
    "geo": true,
    "id": "PNT1",
    "name": "PNT1",
    "note": "",
    "num": "WPT1",
    "speed": 790,
    "x": -61834.462889942,
    "y": 90576.579259823
}
```
- `geo`: boolean, always `true` for geographic waypoints
- `id`: references the Point it was sourced from
- `num`: format `WPTn` (sequential)
- Maximum ~6 waypoints in the MiG-29 nav system

### TargetPoint

Can be a single object or an empty array:
```json
{ "altitude": 804, "name": "PNT1", "x": -61778.598, "y": 90553.821 }
```
Note: uses `altitude` (not `alt`) and coordinates may differ slightly from the source Point.

### WeaponSettings

```json
{ "trainingMode": false }
```

---

## Formatting Specification

The DTC tool uses a non-standard JSON formatting style. Standard `json.dump(indent=N)` will NOT produce valid formatting. The DTC tool will still read the file, but visual consistency matters for diffing and version control.

### Core Rule: Right-Aligned Keys

All keys are right-aligned so that the colon (`:`) falls at a consistent column position within each nesting level. The colon position shifts rightward as nesting depth increases.

### Measured Column Positions (from real files)

There are two nesting levels with different colon positions:

**Section-level keys** (Points, Airdromes, Radio, CMDS, etc. within a Program):

| Element | Column Position | Description |
|---------|----------------|-------------|
| Colon after key | 55 (0-indexed) | The `:` character for section keys |
| Example | `"Points"` starts at col 47 | 55 - len(`"Points"`) = 47 |
| Example | `"Airdromes"` starts at col 44 | 55 - len(`"Airdromes"`) = 44 |

**Field-level keys** (fields within a Point, Airdrome, or nested object):

| Element | Column Position | Description |
|---------|----------------|-------------|
| Colon after key | 78 (0-indexed) | The `:` character in `"key": value` inside objects |
| Close brace indent | 57 spaces | Leading spaces before `}, {` separators and `} ]` terminators |

### Formatting Algorithm

For a key-value line inside a Point/Airdrome object (field-level):

```
FIELD_COLON_POS = 78
leading_spaces = FIELD_COLON_POS - len('"key"')
line = leading_spaces * ' ' + '"key"' + ': ' + formatted_value + ','
```

For section-level keys (Points, Airdromes, Radio, etc.):

```
SECTION_COLON_POS = 55
leading_spaces = SECTION_COLON_POS - len('"SectionName"')
line = leading_spaces * ' ' + '"SectionName"' + ': ' + value
```

### Examples of Correct Formatting

```
                                               "Points": [ {
                                                                         "alt": 2000,
                                                                          "id": "PNT1",
                                                                        "name": "PNT1",
                                                                        "note": "Shayrat",
                                                                         "num": "PNT1",
                                                                       "speed": 790,
                                                             "terrainAltitude": 804,
                                                                           "x": -61834.462889942,
                                                                           "y": 90576.579259823
                                                         }, {
                                                                         "alt": 2000,
                                                                          "id": "PNT2",
```

### Formatting Rules Summary

1. Keys are quoted strings, right-padded with leading spaces so colon aligns
2. String values are quoted; numbers are bare
3. Last field in an object has NO trailing comma
4. Object separators: `}, {` with leading indent (57 spaces at Points level)
5. Array terminators: `} ]` followed by `,` (unless last in parent)
6. Empty arrays: `[  ]` (two spaces, not `[]`)
7. Inline arrays (e.g., `LW_indices`): `[ 3, 2, 1, 2, 3, 1 ]` on one line
8. Each nesting level shifts the colon position further right (deeper = more indent)

### Editing Strategy

Because of the non-standard formatting, the safest approach for programmatic editing is **text splicing**:

1. Read the original file as raw text (NOT as parsed JSON)
2. Locate the section boundaries (e.g., find `"Points": [` and its closing `] ,` or `] }`)
3. Generate the replacement section with correct formatting
4. Splice the new text into the original, preserving everything else unchanged

This avoids re-serialising the entire file and accidentally breaking formatting in sections you didn't intend to touch.

---

## File Naming Conventions

DTC files are named descriptively based on their contents:
- `SPO15 + ADF - Contention SARH Era1.dtc` (SPO-15 RWR + ADF presets for Contention server)
- `SPO15 + Points + ADF - Contention SARH E1- Syria.dtc` (same, with Points added; Syria theatre)

Pattern: `<RWR config> + <added features> - <server/scenario>.dtc`

---

## Data Sources for Populating Points

### Airfield Coordinates

Priority order:
1. **pydcs Python library** (`pip install pydcs`) - provides x,y for ~59 Syria airfields
2. **beacons.lua** (game files) - provides x,y,altitude for airfields with navaids (~28 on Syria)
3. **Mission file (.miz)** - can contain airfield objects if placed by mission designer

### FARP Coordinates

FARPs only exist within specific mission files:
1. Extract from `.miz` archive (it's a ZIP)
2. Parse the `mission` Lua table for static helicopter pad objects
3. FARPs follow naming pattern: `"FARP [NATO alphabet] ([number]) (Pad [n])"`
4. Use only Pad 1 coordinates (Pad 2+ are adjacent duplicates)

### Terrain Altitude

- For airfields with navaids: use `position[1]` from beacons.lua (exact)
- For airfields without navaids: use known elevation data or IDW interpolation
- For FARPs: use inverse-distance-weighted interpolation from 4 nearest known airfield elevations (approximate, ~50-150m accuracy in hilly terrain)

---

## Constraints and Limits

| Constraint | Value | Notes |
|------------|-------|-------|
| Programs per DTC | 3 | Only 1 active at spawn |
| Max Waypoints | ~6 | Loaded into cockpit nav computer |
| Max Airdromes | 3 | Approach airdromes |
| Max Points | Unlimited (tested 79) | Library for waypoint selection |
| Name/ID/Num length | 7 characters | Hard limit in DTC tool UI |
| Note length | 7 characters | Displayed as location label |
| Coordinate precision | Variable | Up to 12 decimal digits |
| terrainAltitude | Integer | Metres above sea level, rounded |

---

## Version Notes

- This reference was built from DTC files used with the DCS MiG-29 module as of April-May 2026
- The DTC tool is community-developed; format may evolve with updates
- The right-aligned formatting appears to be generated by the DTC tool's internal serialiser
- All observations are from the Syria map; other maps use the same format but different coordinate origins

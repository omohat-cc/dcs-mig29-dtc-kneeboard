# DCS MiG-29A DTC Kneeboard — Design Brief

## What this is

I need a kneeboard page design for DCS World (a military flight simulator). The kneeboard is a small reference card displayed inside the cockpit that pilots glance at during flight. This specific page summarises the pilot's pre-programmed DTC (Data Transfer Cartridge) configuration for the MiG-29A fighter aircraft.

The page will be generated automatically by a Python utility using Pillow (PIL). I need you to design the visual layout so I can replicate it exactly in code. That means I need precise specifications: font choices, font sizes in pixels, colour hex values, line weights, margins, padding, and spacing.

## Dimensions and format

- **Canvas size:** 1536 x 2048 pixels (portrait), output as JPG
- **Background:** White
- **Viewing context:** The image is displayed on a small virtual kneeboard inside a 3D cockpit. It needs to be legible at reduced scale, so err toward larger font sizes and/or high contrast. Think "designed to be read at arm's length in a moving vehicle."

## Style reference

I have uploaded a reference image of a real-world aerodrome frequencies chart in the design system for this project. Please match that aesthetic closely:

- Structured table/grid layout with clean ruled lines
- Header bands with background fills to separate sections
- Compact but readable aviation-style typography (monospaced or semi-monospaced)
- Black/dark text on white background
- Colour used sparingly and only for functional differentiation (e.g., section header bands, status indicators)
- No decorative elements that don't serve a functional purpose
- The overall feel should be "professional aviation reference document," not "video game UI"

## Header content

The top of the page should contain:

| Field | Sample value |
|---|---|
| Title | MiG-29 DTC Configuration |
| Active Program | Program 1 |
| Map | Syria |
| Generated | 08 May 2026 14:32 UTC |

## Section definitions and sample data

The kneeboard displays 7 sections. Each section has a fixed maximum number of entries. When a section has no configured data, it displays "No Config" in place of the entries.

### 1. Waypoints (max 3 entries)

Active navigation waypoints loaded into the cockpit nav computer. Each entry shows the waypoint slot number and the resolved location name.

**Sample data:**

| Slot | Name |
|---|---|
| WPT1 | Abu al-Duhur |

(Only 1 of 3 slots populated in this example.)

### 2. Airdromes (max 3 entries)

Pre-programmed approach airdromes for recovery. Each entry shows the slot number, resolved name, and runway if available.

**Sample data:**

| Slot | Name | Runway |
|---|---|---|
| ARD1 | Abu al-Duhur | — |
| ARD2 | Wujah Al Hajar | Rwy 02 |
| ARD3 | Shayrat | — |

(3 of 3 slots populated.)

### 3. RSBN (max 3 entries)

RSBN radio navigation stations. Each entry shows the slot, station name, and channel number.

**Sample data (populated):**

| Slot | Station | Channel |
|---|---|---|
| RSBN1 | Krasnodar-Center | Ch 40 |

(1 of 3 slots populated.)

**Please also show an empty-state version of RSBN with the label "No Config" so I can see how unpopulated sections look.**

### 4. ADF (4 channels, inner + outer each = 8 entries)

Automatic Direction Finder radio channels. Each channel has an Inner and Outer beacon, resolved to an airport/station name via frequency lookup.

**Sample data:**

| Channel | Inner | Outer |
|---|---|---|
| ADF 1 | Rayak | Bassel |
| ADF 2 | Tiyas | Damascus |
| ADF 3 | Hama | Aleppo |
| ADF 4 | Palmyra | Beirut |

### 5. Radio Frequencies (20 channels)

UHF/VHF radio presets. Each entry shows channel number, frequency in MHz, and modulation type.

**Sample data:**

| CH | Freq | Mod |
|---|---|---|
| 00 | 124 | AM |
| 01 | 264 | AM |
| 02 | 265 | AM |
| 03 | 256 | AM |
| 04 | 254 | AM |
| 05 | 250 | AM |
| 06 | 270 | AM |
| 07 | 257 | AM |
| 08 | 255 | AM |
| 09 | 262 | AM |
| 10 | 259 | AM |
| 11 | 268 | AM |
| 12 | 269 | AM |
| 13 | 260 | AM |
| 14 | 263 | AM |
| 15 | 261 | AM |
| 16 | 267 | AM |
| 17 | 258 | AM |
| 18 | 253 | AM |
| 19 | 251 | AM |

### 6. Flare Program / CMDS (6 parameters)

Countermeasure dispensing system configuration, shown as resolved values (not raw index numbers).

**Sample data:**

| Parameter | Value |
|---|---|
| Burst Count I | 1 |
| Burst Count II | 1 |
| Burst Interval | 0.5s |
| Salvo Count (Air) | 1 |
| Salvo Count (SAM) | 6 |
| Salvo Interval | 5s |

### 7. SPO-15 Launch Warnings (6 threat types)

RWR (Radar Warning Receiver) launch warning configuration. Each threat type is set to Off, On, or Lock.

**Sample data:**

| Threat | Status |
|---|---|
| П (P) | Lock |
| З (3) | On |
| Х (X) | Off |
| Н (H) | On |
| Ф (F) | Lock |
| С (C) | Off |

The Cyrillic letters are the primary labels (П, З, Х, Н, Ф, С), with Latin equivalents in parentheses as a secondary reference.

Status values should be visually distinct: consider using colour or weight to differentiate Lock / On / Off states at a glance.

## Layouts to design

I need **four different layout variations** of this kneeboard, each rendered as a full 1536x2048 mockup with the sample data above. All four share the same header and data; they differ only in how the sections are arranged on the page.

### Layout 1: "Developer's Grid"

```
[              Header (full width)              ]
[ Waypoints  |  Aerodromes  |      RSBN        ]
[ (3 entries)   (3 entries)     (3 entries)     ]
[                                               ]
[    ADF (8 entries)         |    (empty)       ]
[    spans ~2/3 width        |                  ]
[                                               ]
[  SPO-15    |    CMDS       |     Radio        ]
[ (6 entries)  (6 entries)     (20 entries)      ]
```

Three-column grid. Top row is navigation (WPT/ARD/RSBN). ADF spans roughly two-thirds width in the middle row. Bottom row has SPO-15, CMDS, and Radio in three columns. Radio is the tallest section and drives the bottom row height.

### Layout 2: "Radio Right Column"

```
[              Header (full width)              ]
[  WPT  | ARD  | RSBN  |                       ]
[  (3)    (3)    (3)    |                       ]
[                       |      Radio            ]
[     ADF (8 entries)   |   (20 entries)        ]
[     spans left 2/3    |   spans full          ]
[                       |   right column        ]
[  SPO-15   |   CMDS    |   height              ]
[  (6)        (6)       |                       ]
```

Radio gets a dedicated right column spanning the full page height below the header. The left two-thirds handles navigation (top), ADF (middle), and SPO-15/CMDS (bottom). Radio is always in the same fixed position on the right edge, like a frequency card.

### Layout 3: "Horizontal Bands"

```
[              Header (full width)              ]
[--- NAV BAND ---------------------------------]
[ Waypoints  |  Aerodromes  |      RSBN        ]
[                                               ]
[--- COMMS BAND --------------------------------]
[      ADF (8 entries)   |   Radio (20 entries) ]
[      left half         |   right half         ]
[                                               ]
[--- DEF BAND ---------------------------------]
[      SPO-15 (6)        |   CMDS (6)          ]
```

Three horizontal bands grouped by function: Navigation (top), Communications (middle), Defensive Systems (bottom). Each band has a labelled header strip. ADF and Radio sit side by side in the comms band. Radio could use two sub-columns internally (CH0-9 / CH10-19) to stay compact.

### Layout 4: "Compact Grid + Full-Width Radio"

```
[              Header (full width)              ]
[ Waypoints  |  Aerodromes  |      RSBN        ]
[ (3 entries)   (3 entries)     (3 entries)     ]
[                                               ]
[    ADF     |   SPO-15     |      CMDS        ]
[ (8 entries)  (6 entries)     (6 entries)      ]
[                                               ]
[        Radio (full width, 2 sub-columns)      ]
[        CH00-09  |  CH10-19                    ]
```

Uniform three-column grid for the top two rows, then Radio takes the full page width at the bottom, split into two side-by-side columns (CH00-09 on the left, CH10-19 on the right). Very structured and maximises Radio readability.

## What I need back

For each of the 4 layouts:

1. **A full rendered mockup** at 1536x2048 pixels with the sample data populated, white background, matching the aerodrome frequency chart reference style.

2. **A design specification** I can use to replicate the design in Python Pillow, including:
   - Font family and size (in pixels) for each text element (title, section headers, data entries, labels)
   - Colour hex values for all elements (text, header band fills, grid lines, status indicators)
   - Line weights for rules/borders (in pixels)
   - Margins, padding, and spacing (in pixels) — outer margins, section gaps, row heights, column widths
   - Any special treatment for status values (Lock/On/Off colour coding)

3. **An empty-state example** for at least one section (RSBN) showing the "No Config" label treatment.

## Key constraints

- Legibility is the top priority. This is read at a glance in a cockpit, not studied at a desk.
- Larger fonts are better. The page has relatively low data density compared to a full navigation kneeboard, so there is room to be generous with sizing.
- The layout must work when ALL sections are populated (the sample data above) and when some sections show "No Config." The grid structure should remain consistent regardless of content to build muscle memory.
- Monospaced or tabular-aligned numbers for frequencies and channel numbers.
- No rounded corners, gradients, shadows, or other decorative UI elements. This is a reference document, not an app screen.

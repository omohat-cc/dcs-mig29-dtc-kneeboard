# beacons.lua parsing - findings

Resolves spec outstanding research item #2 ("beacons.lua structure validation").
Tested against the two attached sample files (renamed copies of real `beacons.lua`):

- `Syria_beacons.lua`   - 119 beacon entries, 24 NDBs
- `Caucus_Beacons.lua`  - 164 beacon entries, 82 NDBs

## Can slpp parse the raw file?

**No, not directly.** A DCS `beacons.lua` is a Lua *script*, not a table literal.
`slpp.decode` on the raw file fails because the file contains:

| Construct | Example | Problem for slpp |
|---|---|---|
| Statements before the data | `dofile(...)`, `local _ = gettext.translate` | slpp decodes a single value, not a script |
| gettext calls | `display_name = _('BANIAS')` | function call, not a literal |
| External constants | `type = BEACON_TYPE_AIRPORT_HOMER` | bare identifier defined in `BeaconTypes.lua` (never loaded) |
| Semicolon separators | `callsign = 'BAN';` | Lua allows `;` in tables; some slpp builds want `,` |

## What works

slpp parses the data correctly **after targeted preprocessing** of the extracted
`beacons = { ... }` table (see `_sanitise_for_slpp` in `beacon_parser.py`):

1. Extract the `beacons` table by brace-matching (string-aware).
2. Strip the gettext wrapper: `_('NAME')` -> `'NAME'`.
3. Quote the bare constants: `= BEACON_TYPE_X` -> `= 'BEACON_TYPE_X'`.
4. Normalise separators: `;` -> `,`, then drop the resulting trailing commas.

The installed slpp release actually tolerated the trailing commas, but they are
stripped anyway for portability across slpp versions on the Windows target.

## Regex fallback

A pure-regex path (`_parse_with_regex`) is used if slpp is missing or raises. It
splits the file into one record per beacon (anchored on `display_name`) and pulls
out `type`, `frequency`, `display_name`, `callsign`. It does not depend on the
Lua being well-formed, so it survives constructs that defeat any table parser.

**Both paths were validated to produce identical lookups** (24 NDBs Syria,
82 NDBs Caucasus). See `test_beacon_parser.py`, which cross-checks them.

## NDB selection

DCS has no literal "NDB" type. NDBs are the homer beacons (LF/MF, ADF-tunable).
The parser selects any type whose name contains `HOMER`:

- `BEACON_TYPE_AIRPORT_HOMER`, `BEACON_TYPE_AIRPORT_HOMER_WITH_MARKER`
- `BEACON_TYPE_HOMER`
- `BEACON_TYPE_ILS_FAR_HOMER`, `BEACON_TYPE_ILS_NEAR_HOMER` (locator NDBs, present on Caucasus)

Excluded: VOR / VOR_DME / DME / TACAN / VORTAC / RSBN / ILS localiser+glideslope /
PRMG / broadcast.

## Key behaviours

- **Units:** beacons.lua stores frequency in **Hz** (e.g. `342000.0`); the lookup
  key is **kHz** (`342.0`). The DTC supplies ADF frequencies in kHz, so they match
  directly. Non-integer kHz exist and are preserved (e.g. 372.5 Syria; 300.5 / 309.5
  Caucasus).
- **Station name:** `display_name` if present, else `callsign`, else the frequency.
- **Frequency collisions:** two NDBs can share a frequency. First-in-file wins and
  the dropped alternative is logged at WARNING (no silent loss). Syria has 2
  collisions (432 kHz, 365 kHz); Caucasus has ~15.

## Verified ADF resolution (Contention DTC sample)

| Freq | Resolves to |
|---|---|
| 342 kHz | DAMASCUS |
| 351 kHz | BEIRUT |
| 337 kHz | PALMYRA |
| 450 kHz | KLEYATE |

## How to run

```bash
# from the App folder
python3 -m venv .venv
.venv/bin/python -m pip install slpp
.venv/bin/python test_beacon_parser.py          # full report + assertions
.venv/bin/python beacon_parser.py "<path>/beacons.lua" 342 351   # ad-hoc lookup
```

## Assumptions / open questions

- **Collision policy** is first-in-file. If the DTC ever needs the geographically
  correct station, the bin/dtc processor could disambiguate by waypoint proximity
  (positions are present in beacons.lua but not currently retained).
- ILS locator homers (FAR/NEAR) are treated as NDBs. They are ADF-tunable, so this
  is correct, but if only airfield NDBs are wanted the keyword set can be narrowed.

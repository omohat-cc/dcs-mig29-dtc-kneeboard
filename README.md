# DCS MiG-29A DTC Kneeboard Utility

Auto-generates a single kneeboard page summarising all your MiG-29A's Data
Transfer Cartridge (DTC) information every time you spawn into the jet in DCS
World. No more screenshotting the mission planner: the moment you occupy a
MiG-29A slot, the utility reads the DTC you loaded and renders it as a clean,
readable page for the in-game kneeboard.

<p align="center">
  <img src="docs/images/dtc-kneeboard-sample.jpg" width="380"
       alt="Example generated kneeboard page for a MiG-29A DTC: tactical points, radio channels, ADF beacons and SPO-15 settings" />
  <br>
  <em>An auto-generated kneeboard page for a loaded DTC.</em>
</p>

## What it shows

A single 1536x2048 kneeboard page with the contents of your loaded DTC,
including navigation/tactical points, radio-navigation channels, ADF beacons
(resolved to station names for the current map, each shown with its frequency
for cross-checking), SPO-15 launch warning settings, and countermeasure
settings.

## Why use this instead of the ED default kneeboard pages

ED recently added more pages to their generated kneeboard. Previously they only
showed the waypoints, aerodromes, and RSBN stations. Now they also added SPO-15
and radio-channel pages, but they spread the information out over five or six
pages. This utility puts all the DTC information onto a single, well-laid-out
page.

It also resolves the raw ADF frequencies to airport names, meaning you can use
the 8 ADF stations as bearing-only waypoints to complement the 6
bearing-and-distance waypoints/aerodromes.

## How it works

The utility has two small parts:

1. **A DCS hook** (installed automatically into
   `Saved Games\DCS\Scripts\Hooks\`) detects when you spawn into a MiG-29 and
   writes a tiny trigger file. That is all it does.
2. **A companion Windows app** (system-tray, customtkinter GUI) watches for the
   trigger, extracts the DTC data from DCS's temp files, resolves beacon names
   from the map's `beacons.lua`, renders the kneeboard JPG and saves it to
   `Saved Games\Kneeboard\MiG-29 Fulcrum\000_dtc_config.jpg`. It plays a short
   confirmation sound on each new render, so you get audible feedback in VR.

DCS picks up the refreshed page on your next spawn. Nothing is injected into
the mission and no game files are modified; the hook uses DCS's official
`Scripts/Hooks` mechanism, the same one used by SRS, Tacview and OpenKneeboard.

The app sits in the system tray while you fly: closing the window (X) minimises
it there, the **Exit** button (or the tray's Quit) closes it for good, and only
one copy runs at a time.

### Live DTC watch (auto-regenerate on the ground)

While you are parked on the ground in a MiG-29, the app watches the DTC for
changes and regenerates the kneeboard automatically (about every 5 seconds), so
editing the cartridge in the in-cockpit **DTC manager** refreshes the page
without a respawn. It pauses once you are airborne and resumes when you land; a
small **DTC Watch** indicator shows the state (Waiting / Ground / Paused). You
can still press **Regenerate Kneeboard** to force a rebuild at any time.

> **Important - the built-in DCS kneeboard does not refresh mid-mission.** DCS
> caches each kneeboard page image and only re-reads it when you **respawn**; a
> page-turn or kneeboard toggle does not refresh it. So although the app writes
> the updated page within a few seconds, the *native* DCS kneeboard keeps showing
> the version from your last spawn. The live ground-watch is therefore only
> useful if you view the page through a tool that reads the file live, such as
> [OpenKneeboard](https://openkneeboard.com/) (which most VR pilots already use)
> - it picks up each regeneration straight away. With the native kneeboard, just
> respawn to load the new page (it regenerates on spawn anyway).

## Installation

1. Download `DCS_DTC_Kneeboard.exe` from the
   [latest release](../../releases/latest).
2. Put it in any user-writable folder such as Documents or Downloads (not
   `C:\Program Files`) and run it. `config.json` and a log file are written
   next to the exe.
3. On first run the app auto-detects your DCS install, Saved Games and temp
   paths (and prompts for anything it cannot find), then installs/updates the
   hook.
4. Start DCS, slot into a MiG-29A with a DTC loaded, and check your kneeboard
   after giving the app a few seconds to read the config and generate a page
   (you can watch the status window to see when the page is ready, or just
   listen for the printer sound that confirms the kneeboard has been generated).

> **SmartScreen / antivirus note:** the exe is an unsigned single-file build,
> which Windows sometimes flags. Build it yourself from source (below) or check
> the release's CI build provenance if in doubt.

### Running from source

```
cd App
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

Requires Python 3.11+ on Windows. See [App/BUILD.md](App/BUILD.md) to build
the exe yourself; releases are built automatically by
[GitHub Actions](.github/workflows/build.yml).

## Compatibility

- Windows 10/11, DCS World standalone (registry/path auto-detection; Steam
  installs can be configured manually in the app).
- The Eagle Dynamics MiG-29A Fulcrum module.
- Tested against DCS 2.9.x. DCS updates that change the DTC temp-file format
  may break extraction until the parser is updated.

## Development

- Authoritative design: [docs/technical-spec.md](docs/technical-spec.md)
- DTC format notes: [docs/dtc-format-reference.md](docs/dtc-format-reference.md)
- beacons.lua parsing write-up:
  [docs/beacon-parser-findings.md](docs/beacon-parser-findings.md)
- Tests live in `App/test files/` (plain Python scripts, no pytest needed).
  Fixtures that contain DCS-owned content (real `beacons.lua` files, DTC
  captures) are not distributed; the relevant checks skip cleanly when the
  fixtures are absent. Set `DCS_BEACONS_DIR` or populate
  `App/test files/fixtures/` to enable them locally.

Adaptations for other aircraft are welcome: the spawn detection, DTC
extraction and rendering layers are deliberately separated, so an F-16 or
F/A-18 variant mostly means a new processor/renderer pair.

## Licence and credits

- Licensed under the [GNU GPL v3.0](LICENSE).
- Bundled fonts (Barlow, Barlow Condensed, JetBrains Mono, Oswald) are used
  under the SIL Open Font License 1.1; see the `OFL-*.txt` files in
  [App/fonts/](App/fonts/).
- Kneeboard visual language inspired by "Aerodrome Data and Frequencies" by
  DimOn (Dima Kozyrev).
- Not affiliated with or endorsed by Eagle Dynamics. DCS World is a trademark
  of Eagle Dynamics SA.

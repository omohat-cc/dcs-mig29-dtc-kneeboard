# Where DCS World Stores Dynamically Generated Kneeboard Pages at Runtime

## Direct Answer

DCS World does not write dynamically generated aircraft kneeboard pages (such as the MiG-29A's DTC waypoint list) as discrete `.png` or `.jpg` files to a user-accessible cache on disk. Instead, these pages are **rendered in memory at runtime by Lua scripts + a C++ indicator pipeline** each time the kneeboard is drawn. The closest thing to a "dynamic kneeboard file" on disk is the **unpacked mission working directory** that DCS creates when it loads a `.miz`, located at:

```
%USERPROFILE%\AppData\Local\Temp\DCS\          (stable branch)
%USERPROFILE%\AppData\Local\Temp\DCS.openbeta\ (open beta, historically)
```

This is where DCS unzips the currently loaded `tempMission.miz` and can read mission-embedded kneeboard images from the `KNEEBOARD/IMAGES/` subfolder. It is NOT, however, where the DTC/waypoint-driven dynamic pages themselves are written — those are drawn straight to the kneeboard texture from Lua each frame. Below is the full picture, with sources.

---

## 1. The Four Places DCS Actually Looks for Kneeboard Content

A well-sourced ED forum thread enumerates the load order and locations DCS reads when building an aircraft's kneeboard stack in-flight ([ED Forums – Altering the page order on the kneeboard](https://forum.dcs.world/topic/187590-altering-the-page-order-on-the-kneeboard)):

1. **Aircraft-module pages (Lua + images) shipped with the module** — `...\Eagle Dynamics\DCS World\Mods\aircraft\<Aircraft>\Cockpit\...\KNEEBOARD\pages\` (and variants like `...\Cockpit\Scripts\KNEEBOARD\` for some aircraft). These are the built-in pages, many of which are driven by Lua scripts rather than flat images.
2. **User per-aircraft pages** — `%USERPROFILE%\Saved Games\DCS\Kneeboard\<AircraftFolderName>\` (or `DCS.openbeta\Kneeboard\...`). For the MiG-29A Fulcrum full-fidelity module, the correct folder name is literally `MiG-29 Fulcrum` (with a space, no hyphen after 29), as confirmed by SlipHavoc on the ED forum ([Kneeboard folder – DCS: MiG-29A Fulcrum](https://forum.dcs.world/topic/379001-kneeboard-folder/)).
3. **Terrain kneeboards** — `...\DCS World\Mods\terrains\<Map>\Kneeboard\`.
4. **Mission-embedded images** — inside the `.miz` under `KNEEBOARD/IMAGES/` (and `KNEEBOARD/<aircraft>/IMAGES/` for per-airframe mission pages) ([Airgoons Kneeboards wiki](https://www.airgoons.com/w/Kneeboards)).

Finally, at the end of the stack, DCS appends a **procedurally generated "Flight Plan Map" page** that overlays waypoints on a terrain map ([Airgoons Kneeboards wiki](https://www.airgoons.com/w/Kneeboards)).

OpenKneeboard's documentation is explicit that these dynamic Lua-driven pages are *not* written out as image files: "Dynamic (Lua) kneeboard pages are not supported at all, and are unlikely to be supported in the future. This includes most of the kneeboard pages for the F14, F16, and F18." ([OpenKneeboard DCS Tabs troubleshooting](https://openkneeboard.com/troubleshooting/dcs-tabs/)).

## 2. How Dynamic Pages (Including MiG-29A DTC Pages) Are Actually Generated

The mechanism is a hybrid C++/Lua pipeline. The public init script that ED ships demonstrates how this works; see the community mirror at [TDC-bob/mod-kneeboard-export – `init.lua`](https://github.com/TDC-bob/mod-kneeboard-export/blob/master/DCS/Scripts/Aircrafts/_Common/Cockpit/KNEEBOARD/indicator/init.lua):

```lua
scan_path(get_terrain_related_data("KNEEBOARD"))
scan_path(LockOn_Options.common_script_path.."KNEEBOARD/indicator/CUSTOM")
scan_path(lfs.writedir().."KNEEBOARD")
...
if '.lua' == ext then
    page_subsets[#page_subsets + 1] = fn;
    pages[#pages + 1] = {BASE,#page_subsets,OVERLAY2}
elseif '.dds' == ext or '.bmp' == ext or '.jpg' == ext or '.png' == ext or '.tga' == ext then
    custom_images[#custom_images + 1] = fn --they will generate from C++
end
```

Two things to note:

- The `--they will generate from C++` comment, left by Eagle Dynamics in their own source, confirms that images are composed by the native engine into the kneeboard texture. They are *not* materialized to a file on disk before display.
- Lua pages use a tiny framework of `add_text`, `add_picture`, and `CreateElement` calls (documented minimally in `definitions.lua`). The Lua script is re-evaluated when the page is loaded, so the "page" exists only as elements in the kneeboard indicator's display list. This is corroborated by the ED-forum "LUA framework for kneeboard pages?" thread ([forum.dcs.world/topic/146963](https://forum.dcs.world/topic/146963-lua-framework-for-kneeboard-pages)) and the "writing custom kneeboard" discussion in which Kneeboard Builder's author explains: "It is possible to create a kneeboard page using static data on mission load... Interactive kneeboards like in the Mi-8 aren't possible (as far as I know) since the kneeboard redraw function is hard coded into the aircraft modules and can't seem to be accessed via lua only." ([ED Forums – writing custom kneeboard](https://forum.dcs.world/topic/191759-writing-custom-kneeboard)).

So for the MiG-29A, the Lua that builds the DTC waypoint summary page lives inside the module at roughly:

```
...\Eagle Dynamics\DCS World\Mods\aircraft\MiG-29-Fulcrum\Cockpit\...\KNEEBOARD\
```

(the exact subpath varies by module; the MiG-21 uses `...\Mods\aircraft\MIG-21bis\Cockpit\KNEEBOARD\pages\` per a community install instruction ([MiG-21 Operating Procedures kneeboard user file](https://www.digitalcombatsimulator.com/fr/files/2319241)), and the AJS-37 uses `...\Mods\aircraft\AJS37\Cockpit\scripts\KNEEBOARD\pages\` per the CombatFlite Viggen kneeboard tutorial ([combatflite.com/blog/viggenkneeboard](https://www.combatflite.com/blog/viggenkneeboard))). ED's own forum post confirms the MiG-29A module folder is `...\Mods\aircraft\MiG-29-Fulcrum\...` ([DCS: MiG-29A Fulcrum forum](https://forum.dcs.world/forum/1217-dcs-mig-29a-fulcrum/)).

The DTC data itself is stored inside the mission `.miz` or as a separate `.dtc` file, not as a pre-rendered PNG. Per ED's official DTC Quick Start guide: "Saving the DTC file saves it within the DCS mission (.miz) file... Also in the File menu, you can Import and Export DTC files." ([ED Forums – Quick Start Guide Data Transfer Cartridge](https://forum.dcs.world/topic/371995-quick-start-guide-data-transfer-cartridge-dtc)). When you spawn, the aircraft's Lua reads the currently-loaded DTC partition and the Lua-driven kneeboard page queries that in-memory state to draw the waypoint list.

## 3. The `%TEMP%\DCS[.openbeta]` Mission Working Directory

The one place DCS *does* write kneeboard-adjacent files to disk at runtime is the unzipped mission working directory. DCS Scripting team member "Flappie" (an ED community moderator) has posted repeatedly that this folder is where DCS extracts the loaded mission:

> "There seems to be an error in your temp folder... `C:\Users\***\AppData\Local\Temp\DCS.openbeta\/~mis00003C6F.ini`. This folder is used by the game to unzip missions in order to load them." — [ED Forums, "black screen when looking at kneeboard"](https://forum.dcs.world/topic/302894-black-screen-when-looking-at-kneeboard)

And the official DCS support FAQ documents `%USERPROFILE%\AppData\Local\Temp\DCS\` as the temp folder that holds, among other things, `LastMissionTrack.trk` and `autoupdate_templog.txt` ([DCS Support – Writing a support ticket](https://www.digitalcombatsimulator.com/en/support/faq/support_ticket/)).

A Mudspike thread confirms the contents: "`C:\Users<your id>\AppData\Local\Temp\DCS.openbeta`... it's a layout similar to a miz file, but expanded/un-zipped" ([Mudspike Forums – DCS World VERY Long load times](https://forums.mudspike.com/t/dcs-world-very-long-load-times-possible-solution/13266)). Because the `.miz` structure is preserved verbatim, **any mission-embedded kneeboard images (`KNEEBOARD/IMAGES/*.png`, `KNEEBOARD/<aircraft>/IMAGES/*.png`) from the mission currently loaded will physically exist on disk inside this temp folder while the mission is running.** After mission exit they are typically cleaned up, though stale copies are known to linger (which is why Kneeboard Builder's changelog explicitly mentions it "Automatically clears old temp missions from `../AppData/Temp/DCS` folder to prevent old missions from displaying in Dynamic Kneeboards" — [dcskneeboardbuilder.com/download.html](https://dcskneeboardbuilder.com/download.html)).

The exact path you should look at while a mission is running:

```
C:\Users\<user>\AppData\Local\Temp\DCS\             (stable)
C:\Users\<user>\AppData\Local\Temp\DCS.openbeta\    (when open beta existed)
```

Look for a subtree mirroring the `.miz` layout, and specifically `KNEEBOARD\IMAGES\` and any `KNEEBOARD\<aircraft>\IMAGES\` subfolder. You will also find a file named `tempMission.miz` (the currently loaded mission) and files like `~misXXXXXXXX.ini` ([ED Forums, DCS hangs loading mission](https://forum.dcs.world/topic/276709-dcs-ob-273-hangs-while-loading-mission)).

**However**, this folder will NOT contain a pre-rendered PNG of the MiG-29A's DTC waypoint kneeboard page. That page exists only as the result of Lua execution against the in-memory DTC state; there is no corresponding file in temp.

## 4. Module- and Use-Case-by-Use-Case Breakdown

### MiG-29A Fulcrum (full-fidelity) — DTC waypoint kneeboard
- **Source of page code**: Lua + common indicator framework at `%DCS_INSTALL%\Mods\aircraft\MiG-29-Fulcrum\Cockpit\...\KNEEBOARD\` plus `%DCS_INSTALL%\Scripts\Aircrafts\_Common\Cockpit\KNEEBOARD\indicator\` ([TDC-bob mirror](https://github.com/TDC-bob/mod-kneeboard-export/blob/master/DCS/Scripts/Aircrafts/_Common/Cockpit/KNEEBOARD/indicator/init.lua); [DCS: MiG-29A Fulcrum forum sticky](https://forum.dcs.world/forum/1217-dcs-mig-29a-fulcrum/)).
- **Source of data rendered on the page**: DTC partition stored inside the loaded `.miz` (or a `.dtc` imported at briefing/ground-crew time) ([ED DTC Quick Start Guide](https://forum.dcs.world/topic/371995-quick-start-guide-data-transfer-cartridge-dtc)).
- **Disk cache of the rendered page**: **None.** It is drawn on the fly to the kneeboard texture by the engine. DCS changelogs also confirm this is an active area of fixes: "Fixed: MiG-29A Data from Program 1 tab is displayed in Program2 tab in some cases" ([DCS 2.9.21.16362 changelog](https://www.digitalcombatsimulator.com/en/news/changelog/release/2.9.21.16362/)).
- **Custom user overrides**: drop PNG/JPG into `%USERPROFILE%\Saved Games\DCS\Kneeboard\MiG-29 Fulcrum\` — note the exact folder name ([SlipHavoc on ED Forums](https://forum.dcs.world/topic/379001-kneeboard-folder/); [AKA_Relent's kneeboard package](https://www.digitalcombatsimulator.com/fr/files/3347292)).
- A forum question asking about route visibility on the kneeboard ([Route on Kneebaord – DCS: MiG-29A Fulcrum](https://forum.dcs.world/topic/382140-route-on-kneebaord/)) confirms the MiG-29A only displays configured RSBN, PNT and airfield points from the DTC, not the Mission Editor's flight plan — consistent with the page being populated live from DTC state.

### F/A-18C Hornet
- Built-in Lua pages live under `...\Mods\aircraft\FA-18C\Cockpit\...\KNEEBOARD\` and are dynamically rendered. ED's recent changelogs mention DTC-driven kneeboard updates for the Hornet ("MiG-29: Added Radio and ADF tabs. F-16 and F/A-18: With the new RWR DTC addition..." — [DCS changelog](https://www.digitalcombatsimulator.com/en/news/changelog/!/)).
- User pages go under `%USERPROFILE%\Saved Games\DCS\Kneeboard\FA-18C_hornet\` ([ED Forums – F/A-18C Kneeboard](https://forum.dcs.world/topic/179345-fa-18c-kneeboard/)).
- No rendered-page cache on disk; OpenKneeboard explicitly cannot mirror these ([openkneeboard.com/troubleshooting/dcs-tabs](https://openkneeboard.com/troubleshooting/dcs-tabs/)).

### F-16C Viper
- Same pattern. Dynamic kneeboard pages are Lua-rendered. User overrides in `Saved Games\DCS\Kneeboard\F-16C_50\` ([F-16C Kneeboard Checklists user file](https://www.digitalcombatsimulator.com/en/files/3317321/)). DTC content (ED's native DTC) lives in the `.miz`; the third-party **dcs-dtc** tool stores presets as JSON under `Documents\DCS-DTC\` and writes the in-cockpit kneeboard "synced notes" page via its Lua hooks ([github.com/the-paid-actor/dcs-dtc](https://github.com/the-paid-actor/dcs-dtc)).

### A-10C / A-10C II
- Dynamic kneeboard pages also Lua-rendered. A-10C examples have included LASTE wind pages computed from the mission's weather, which Kneeboard Builder specifically read out of the unzipped `tempMission.miz` in `%TEMP%\DCS` to build similar pages outside DCS ([Kneeboard Builder download/changelog](https://dcskneeboardbuilder.com/download.html); OpenKneeboard also reads mission data this way and mentions A-10C LASTE wind calculations at bullseye as a built-in feature — [openkneeboard.com/features/dcs](https://openkneeboard.com/features/dcs/)).

### AJS-37 Viggen / MiG-21bis / Mi-8
- These three modules are the canonical historical examples of Lua-driven dynamic pages ("aircraft-specific pages like the Viggen's target parameter page are created through LUA scripting" — [Airgoons](https://www.airgoons.com/w/Kneeboards)). Pages live under `...\Mods\aircraft\<module>\Cockpit\scripts\KNEEBOARD\pages\` and again are not cached to disk as images ([MiG-21 kneeboard install instructions](https://www.digitalcombatsimulator.com/fr/files/2319241); [CombatFlite Viggen tutorial](https://www.combatflite.com/blog/viggenkneeboard)).

## 5. Practical Guidance for Developers Who Want to Intercept or Replicate These Pages

1. **If you need the DTC waypoint data itself**, read it out of the `.miz` (or the exported `.dtc` file). `.miz` is a ZIP; DCS unpacks the active one to `%USERPROFILE%\AppData\Local\Temp\DCS\` while a mission is running ([ED Forums – Flappie, temp folder explanation](https://forum.dcs.world/topic/302894-black-screen-when-looking-at-kneeboard)). The `dictionary` and aircraft group table inside `mission` hold waypoint coordinates, names, and DTC partitions ([Hoggit Wiki – Miz mission structure](https://wiki.hoggitworld.com/view/Miz_mission_structure)).
2. **If you want to grab the rendered kneeboard in real time**, your best options are (a) the DCS Export API plus Lua hooks (used by dcs-dtc, TheWay, and OpenKneeboard — all requiring `lfs` and writing into `%USERPROFILE%\Saved Games\DCS\Scripts\` and `Scripts\Hooks\` ([OpenKneeboard Lua API](https://openkneeboard.com/api/lua/); [github.com/okopanja/DCSTheWay](https://github.com/okopanja/DCSTheWay)); or (b) screen-capture. There is no official file on disk you can simply copy.
3. **If you want custom kneeboard pages that overlay / replace the dynamic one**, drop your PNG/JPG into `%USERPROFILE%\Saved Games\DCS\Kneeboard\MiG-29 Fulcrum\` — the module refreshes this folder each time you spawn ([SlipHavoc DCS-Kneeboards repo](https://github.com/SlipHavoc/DCS-Kneeboards): "They are refreshed every time you spawn, so you can change and update the kneeboard folders without exiting the game, you just need to respawn the plane.").
4. **If you want your own Lua page that reads the DTC/mission**: write a `.lua` page into the user Kneeboard directory; DCS will load it via the `scan_path(lfs.writedir().."KNEEBOARD")` call in the common indicator init ([TDC-bob mirror](https://github.com/TDC-bob/mod-kneeboard-export/blob/master/DCS/Scripts/Aircrafts/_Common/Cockpit/KNEEBOARD/indicator/init.lua)). You're limited to `add_text`/`add_picture` style definitions plus whatever globals the module exposes ([ED Forums – BJ's semi-dynamic flight plan kneeboard page](https://forum.dcs.world/topic/147636-bjs-semi-dynamic-flight-plan-kneeboard-page/)).

## 6. Summary Table of Runtime Paths

| Kind of page | Where it lives on disk at runtime | Notes |
|---|---|---|
| Module built-in images (static) | `...\Eagle Dynamics\DCS World\Mods\aircraft\<Module>\Cockpit\...\KNEEBOARD\pages\` | Part of module install; read directly by engine ([Airgoons](https://www.airgoons.com/w/Kneeboards)) |
| Module built-in Lua pages (dynamic — MiG-29A DTC, Viggen target params, Mi-8 startup, F-16/F/A-18 mission data etc.) | Same folder, as `.lua` scripts | **Output is NOT written to disk**. Rendered by C++ from Lua `add_text`/`add_picture` calls each redraw ([TDC-bob init.lua](https://github.com/TDC-bob/mod-kneeboard-export/blob/master/DCS/Scripts/Aircrafts/_Common/Cockpit/KNEEBOARD/indicator/init.lua); [OpenKneeboard DCS tabs](https://openkneeboard.com/troubleshooting/dcs-tabs/)) |
| User custom pages | `%USERPROFILE%\Saved Games\DCS\Kneeboard\<AircraftFolder>\` (`MiG-29 Fulcrum` for full-fidelity MiG-29A) | Refreshed on respawn ([SlipHavoc/DCS-Kneeboards](https://github.com/SlipHavoc/DCS-Kneeboards); [ED Forum](https://forum.dcs.world/topic/379001-kneeboard-folder/)) |
| Terrain/map pages | `...\DCS World\Mods\terrains\<Map>\Kneeboard\` | Airfield cards etc. ([Airgoons](https://www.airgoons.com/w/Kneeboards)) |
| Mission-embedded images | Inside `.miz` under `KNEEBOARD/IMAGES/` and `KNEEBOARD/<aircraft>/IMAGES/` | Present on disk while loaded: `%USERPROFILE%\AppData\Local\Temp\DCS\` mirrors the unzipped miz ([ED Forums Flappie](https://forum.dcs.world/topic/302894-black-screen-when-looking-at-kneeboard)) |
| Auto-generated flight-plan/map overlay | Generated procedurally, never written as a file | Can be themed with a mod (e.g., [Better Kneeboard Map Overlay](https://www.digitalcombatsimulator.com/en/files/3325958/)) |
| Third-party DTC (dcs-dtc) synced-notes kneeboard | Preset JSON at `Documents\DCS-DTC\...`; Lua hook writes the page state via DCS Export/Hooks at `Saved Games\DCS\Scripts\` | Ctrl+Shift+K opens it in-game ([github.com/the-paid-actor/dcs-dtc](https://github.com/the-paid-actor/dcs-dtc)) |

## 7. Bottom Line

- If you are hunting for an actual `.png`/`.jpg`/`.dds` file on disk that represents the MiG-29A's runtime-generated DTC waypoint kneeboard page, **you will not find one**. That page is rendered in-engine from Lua and never serialized to disk. This is confirmed both by ED's own comment (`--they will generate from C++`) in the kneeboard indicator init.lua and by OpenKneeboard's stated inability to mirror Lua-driven pages.
- The one on-disk location related to runtime kneeboard activity is the unpacked mission working directory at `%USERPROFILE%\AppData\Local\Temp\DCS\` (and historically `\DCS.openbeta\`), which contains the active `tempMission.miz` extracted to disk plus any mission-embedded kneeboard images in `KNEEBOARD/IMAGES/` and `KNEEBOARD/<aircraft>/IMAGES/` subfolders. This is the path you should clean when stale mission kneeboards appear, and is the path that tools like Kneeboard Builder read for mission-derived dynamic pages.
- To capture a dynamic page externally your only realistic options are (a) read the underlying data (DTC/waypoints/weather) from the `.miz` or through the DCS Lua Export/Hooks API, or (b) screen capture.

### Primary References

- ED Forum, "Altering the page order on the kneeboard" – load order and source directories: <https://forum.dcs.world/topic/187590-altering-the-page-order-on-the-kneeboard>
- ED Forum, "Kneeboard folder – DCS: MiG-29A Fulcrum" – confirms `MiG-29 Fulcrum` user folder: <https://forum.dcs.world/topic/379001-kneeboard-folder/>
- ED Forum, "black screen when looking at kneeboard" – Flappie documents `%TEMP%\DCS.openbeta\` as the mission unzip directory: <https://forum.dcs.world/topic/302894-black-screen-when-looking-at-kneeboard>
- ED Forum, "DCS hangs loading mission" – confirms `tempMission.miz` in the same temp path: <https://forum.dcs.world/topic/276709-dcs-ob-273-hangs-while-loading-mission>
- ED official DCS support FAQ – `%USERPROFILE%\AppData\Local\Temp\DCS\`: <https://www.digitalcombatsimulator.com/en/support/faq/support_ticket/>
- ED Forum, DTC Quick Start Guide – DTC data stored in `.miz` or `.dtc`, not as images: <https://forum.dcs.world/topic/371995-quick-start-guide-data-transfer-cartridge-dtc>
- TDC-bob mirror of ED kneeboard init.lua – confirms Lua/C++ pipeline and `scan_path(lfs.writedir().."KNEEBOARD")`: <https://github.com/TDC-bob/mod-kneeboard-export/blob/master/DCS/Scripts/Aircrafts/_Common/Cockpit/KNEEBOARD/indicator/init.lua>
- OpenKneeboard DCS integration – dynamic Lua pages are not serialized: <https://openkneeboard.com/features/dcs/> and <https://openkneeboard.com/troubleshooting/dcs-tabs/>
- Kneeboard Builder changelog – auto-clears `../AppData/Temp/DCS`: <https://dcskneeboardbuilder.com/download.html>
- Airgoons Kneeboards wiki – canonical summary of kneeboard directories: <https://www.airgoons.com/w/Kneeboards>
- Mudspike Forums – un-zipped `.miz` layout in the temp folder: <https://forums.mudspike.com/t/dcs-world-very-long-load-times-possible-solution/13266>
- dcs-dtc README – third-party DTC preset storage under `Documents\DCS-DTC\`: <https://github.com/the-paid-actor/dcs-dtc>
- SlipHavoc/DCS-Kneeboards – user kneeboards refreshed on respawn: <https://github.com/SlipHavoc/DCS-Kneeboards>
- ED Forum "writing custom kneeboard" – redraw is hard-coded in module C++: <https://forum.dcs.world/topic/191759-writing-custom-kneeboard>
- Hoggit Wiki – Miz mission structure: <https://wiki.hoggitworld.com/view/Miz_mission_structure>
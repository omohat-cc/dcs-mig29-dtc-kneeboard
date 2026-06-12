DTC Access Research Probe
=========================

Three throwaway Lua probes that log everything they can find about DTC data
access in DCS World. Run them once, collect the log, then uninstall.

Files
-----
dtc_export_probe.lua    Probe 1 - Export.lua context (LoGet* functions)
dtc_hooks_probe.lua     Probe 2 - Hooks context (DCS.* and net.* functions)
dtc_tempdir_probe.lua   Probe 3 - Hooks context (temp directory scanner)
export_lua_snippet.lua  Snippet appended to Export.lua to load Probe 1
install.ps1             PowerShell installer
uninstall.ps1           PowerShell uninstaller

Install
-------
1. Open PowerShell
2. cd to this folder
3. Run: .\install.ps1
   (If blocked by execution policy: Set-ExecutionPolicy -Scope Process Bypass)

Test procedure
--------------
1. Launch DCS World
2. Load a mission (singleplayer or multiplayer)
3. Select MiG-29A with a DTC loaded
4. Spawn into the aircraft
5. Wait 60 seconds
6. Quit the mission
7. Open: Saved Games\DCS\Logs\dtc_probe_log.txt

Uninstall
---------
Run: .\uninstall.ps1
The log file is intentionally preserved.

-- dtc_kneeboard_hook v1.0
-- DCS MiG-29 DTC Kneeboard Utility - spawn-detection hook.
--
-- Detects when the local player occupies a MiG-29 and writes a small trigger
-- file that the external watcher/processor picks up. The hook does the absolute
-- minimum inside DCS's constrained Lua sandbox; all heavy work (filesystem
-- scanning, binary parsing, image generation) happens in the external process.
--
-- The version comment on line 1 above is read by the external utility to decide
-- whether to update this file. Keep it as the first line.
--
-- Safety constraints (technical spec, section 2):
--   * Every DCS API call is wrapped in pcall.
--   * Never calls DCS.getMissionLoaded() (crashes DCS).
--   * Does NOT use io.popen (nil in the hooks context).
--   * Does NOT use lfs.dir() on paths outside DCS (only lfs.writedir()).
--   * Does NOT touch Export.lua - a completely separate Lua context.
--   * Diagnostics go to Saved Games\DCS\Logs\dtc_kneeboard_hook.log via io.open.

local VERSION = "1.0"

-- Deferred-poll tuning, in simulation frames (the sim loop ticks roughly once
-- per frame, so ~60 frames is ~1 second at 60 fps).
local INITIAL_DELAY_FRAMES = 600   -- wait this many frames after a slot change
local POLL_INTERVAL_FRAMES = 60    -- then poll the unit type every this many
local MAX_RETRIES          = 30    -- give up after this many polls (~30 seconds)
local AIRCRAFT_PREFIX      = "MiG-29"  -- prefix match: 29A / 29S / 29G / Fulcrum

-- Polling state. A slot change (re)starts the sequence; it stops on the first
-- definitive unit-type result or once the retries are exhausted.
local pollActive = false
local frameCount = 0
local pollCount  = 0


-- ---------------------------------------------------------------------------
-- Logging (best-effort; a logging failure must never be fatal)
-- ---------------------------------------------------------------------------
local function logPath()
    -- lfs.writedir() returns the Saved Games\DCS directory with a trailing
    -- separator. This is the one lfs call we make, and it is local to DCS.
    return lfs.writedir() .. "Logs/dtc_kneeboard_hook.log"
end

local function log(msg)
    pcall(function()
        local handle = io.open(logPath(), "a")
        if handle then
            handle:write(os.date("!%Y-%m-%d %H:%M:%S") .. "Z  " .. tostring(msg) .. "\n")
            handle:close()
        end
    end)
end


-- ---------------------------------------------------------------------------
-- Safe wrappers around DCS API calls (all guarded by pcall)
-- ---------------------------------------------------------------------------
local function safeGetPlayerUnitType()
    local ok, result = pcall(DCS.getPlayerUnitType)
    if ok and type(result) == "string" then
        return result
    end
    if not ok then
        log("getPlayerUnitType raised: " .. tostring(result))
    end
    return ""
end

local function safeGetMissionName()
    local ok, result = pcall(DCS.getMissionName)
    if ok and type(result) == "string" then
        return result
    end
    return ""
end

local function safeGetTheatre()
    -- getCurrentMission() returns a large table but is available in the hooks
    -- environment. It is only called once, on a confirmed MiG-29 spawn, and is
    -- hard-guarded so any failure degrades to an empty theatre string.
    local ok, result = pcall(function()
        local mission = DCS.getCurrentMission()
        if type(mission) == "table" and type(mission.mission) == "table" then
            return mission.mission.theatre or ""
        end
        return ""
    end)
    if ok and type(result) == "string" then
        return result
    end
    return ""
end


-- ---------------------------------------------------------------------------
-- Trigger file writing (atomic: write .tmp, then rename)
-- ---------------------------------------------------------------------------
local function jsonEscape(value)
    local s = tostring(value or "")
    s = s:gsub("\\", "\\\\")
    s = s:gsub('"', '\\"')
    s = s:gsub("\n", "\\n")
    s = s:gsub("\r", "\\r")
    s = s:gsub("\t", "\\t")
    return s
end

local function writeTrigger(aircraftType)
    local writedir  = lfs.writedir()
    local finalPath = writedir .. "Logs/dtc_kneeboard_trigger.json"
    local tmpPath   = finalPath .. ".tmp"

    local timestamp = os.date("!%Y-%m-%dT%H:%M:%SZ")  -- ISO 8601, UTC
    local mission   = safeGetMissionName()
    local theatre   = safeGetTheatre()

    local json = string.format(
        '{"timestamp": "%s", "aircraft": "%s", "mission": "%s", "theatre": "%s"}',
        jsonEscape(timestamp),
        jsonEscape(aircraftType),
        jsonEscape(mission),
        jsonEscape(theatre)
    )

    local ok, err = pcall(function()
        local handle = io.open(tmpPath, "w")
        if not handle then
            error("could not open " .. tmpPath .. " for writing")
        end
        handle:write(json)
        handle:close()
        -- os.rename will not overwrite an existing destination on Windows, so
        -- clear any stale trigger first (ignored if it is not there).
        os.remove(finalPath)
        local renamed, renameErr = os.rename(tmpPath, finalPath)
        if not renamed then
            error("rename failed: " .. tostring(renameErr))
        end
    end)

    if ok then
        log("Wrote trigger for '" .. aircraftType .. "' (mission='" .. mission ..
            "', theatre='" .. theatre .. "')")
    else
        log("Trigger write FAILED: " .. tostring(err))
        pcall(os.remove, tmpPath)
    end
end


-- ---------------------------------------------------------------------------
-- Polling
-- ---------------------------------------------------------------------------
local function stopPolling(reason)
    pollActive = false
    if reason then
        log("Polling stopped: " .. reason)
    end
end

local function poll()
    pollCount = pollCount + 1
    if pollCount > MAX_RETRIES then
        stopPolling("max retries (" .. MAX_RETRIES .. ") reached without a unit type")
        return
    end

    local unitType = safeGetPlayerUnitType()
    if unitType == "" then
        -- Not in a unit yet (or the call failed); keep retrying next interval.
        return
    end

    if unitType:sub(1, #AIRCRAFT_PREFIX) == AIRCRAFT_PREFIX then
        log("MiG-29 detected: '" .. unitType .. "' on poll " .. pollCount)
        writeTrigger(unitType)
    else
        -- Different aircraft: do nothing. Any stale kneeboard from a previous
        -- MiG-29 spawn is intentionally left in place.
        log("Non-MiG-29 unit ('" .. unitType .. "'); no action")
    end
    -- Either branch is a definitive result, so stop polling.
    stopPolling()
end


-- ---------------------------------------------------------------------------
-- Callbacks
-- ---------------------------------------------------------------------------
local handler = {}

function handler.onPlayerChangeSlot(id)
    -- Fires on every slot selection/change, including respawns. Restart the
    -- deferred poll each time; the unit type is not reliable yet, so we only
    -- arm the timer here and read the type later in onSimulationFrame.
    pollActive = true
    frameCount = 0
    pollCount  = 0
    log("onPlayerChangeSlot(id=" .. tostring(id) .. "): starting deferred poll")
end

function handler.onSimulationFrame()
    if not pollActive then
        return
    end
    frameCount = frameCount + 1
    if frameCount < INITIAL_DELAY_FRAMES then
        return
    end
    if ((frameCount - INITIAL_DELAY_FRAMES) % POLL_INTERVAL_FRAMES) ~= 0 then
        return
    end
    -- Guard the poll so a failure can never propagate into the sim loop.
    local ok, err = pcall(poll)
    if not ok then
        log("poll() raised: " .. tostring(err))
        stopPolling("poll error")
    end
end


-- ---------------------------------------------------------------------------
-- Registration (guarded so a failure cannot break DCS startup)
-- ---------------------------------------------------------------------------
local okReg, errReg = pcall(function()
    DCS.setUserCallbacks(handler)
end)

if okReg then
    log("dtc_kneeboard_hook v" .. VERSION .. " loaded")
else
    log("setUserCallbacks FAILED: " .. tostring(errReg))
end

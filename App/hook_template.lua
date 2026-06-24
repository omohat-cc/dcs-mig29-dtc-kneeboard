-- dtc_kneeboard_hook v1.2
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
-- Changes:
--   v1.2 - Added an air/ground state machine so the app can auto-regenerate the
--          kneeboard after a mid-session DTC edit while parked, and pause while
--          airborne. While in a MiG-29, onSimulationFrame samples our own height
--          above ground (Export.LoGetAltitudeAboveGroundLevel, local ownship
--          truth, reliable on a MP client unlike takeoff/landing events) on a
--          ~1s cadence and, with hysteresis, publishes "ground"/"air"/"none" to
--          a small state file (dtc_kneeboard_state.json) using the same atomic
--          write as the trigger, plus a ~10s heartbeat so the app can tell DCS
--          is still alive. A nil reading (menus, spectating, ownship export off)
--          leaves the phase unchanged, so the app keeps polling on the ground.
--          The state machine is fully independent of the v1.1 detection poll.
--   v1.1 - In multiplayer DCS calls onPlayerChangeSlot for EVERY player's slot
--          change, not just ours. Each call re-armed the deferred poll, which
--          re-checked our (still MiG-29) local unit and re-wrote the trigger -
--          so on a populated server the trigger was rewritten every 30-60s.
--          v1.1 ignores slot changes that are not the local player.
--
-- Safety constraints (technical spec, section 2):
--   * Every DCS / Export API call is wrapped in pcall.
--   * Never calls DCS.getMissionLoaded() (crashes DCS).
--   * Does NOT use io.popen (nil in the hooks context).
--   * Does NOT use lfs.dir() on paths outside DCS (only lfs.writedir()).
--   * Only CALLS Export.* (available in the hooks state); does NOT modify the
--     separate Export Lua context.
--   * Diagnostics go to Saved Games\DCS\Logs\dtc_kneeboard_hook.log via io.open.

local VERSION = "1.2"

-- Deferred-poll tuning, in simulation frames (the sim loop ticks roughly once
-- per frame, so ~60 frames is ~1 second at 60 fps).
local INITIAL_DELAY_FRAMES = 600   -- wait this many frames after a slot change
local POLL_INTERVAL_FRAMES = 60    -- then poll the unit type every this many
local MAX_RETRIES          = 30    -- give up after this many polls (~30 seconds)
local AIRCRAFT_PREFIX      = "MiG-29"  -- prefix match: 29A / 29S / 29G / Fulcrum

-- Air/ground state-machine tuning (v1.2). Frame counts assume ~60 fps; the
-- thresholds use two bands (hysteresis) so a bump or the takeoff roll cannot
-- flap the phase. AGL is metres above ground level.
local STATE_POLL_FRAMES      = 60    -- sample our height about once a second
local AGL_AIR_M              = 30    -- climb above this (confirmed) -> airborne
local AGL_GROUND_M          = 10    -- drop below this (confirmed) -> on ground
local AGL_CONFIRM_SAMPLES   = 3     -- consecutive samples needed to switch phase
local STATE_HEARTBEAT_FRAMES = 600   -- re-write the state file at least this often

-- Polling state. A slot change (re)starts the sequence; it stops on the first
-- definitive unit-type result or once the retries are exhausted.
local pollActive = false
local frameCount = 0
local pollCount  = 0

-- Air/ground state-machine state, kept SEPARATE from the detection poll above so
-- the two never interfere. migActive gates the whole state machine; phase is the
-- last published value; aglStreak counts consecutive confirming samples; the two
-- frame counters drive the sample and heartbeat cadences independently.
local migActive       = false
local phase           = "none"   -- "ground" / "air" / "none"
local currentAircraft = ""        -- unit type, for the state file's "aircraft"
local aglStreak       = 0
local lastAgl         = nil       -- last AGL reading, for diagnostics/heartbeat
local statePollFrames = 0
local heartbeatFrames = 0


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

local function safeGetMyPlayerId()
    -- net.get_my_player_id() is the hooks-environment call for the LOCAL
    -- player's id. It is absent/irrelevant in single-player, so any failure
    -- returns nil and the caller falls back to its pre-v1.1 behaviour.
    local ok, result = pcall(function()
        return net.get_my_player_id()
    end)
    if ok and type(result) == "number" then
        return result
    end
    return nil
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

local function safeGetAGL()
    -- Export.* is available in the hooks Lua state. LoGetAltitudeAboveGroundLevel
    -- returns metres above terrain for our own aircraft (local ownship truth).
    -- It can be nil in menus/spectator or if the server disables ownship export,
    -- so any failure returns nil and the caller leaves the phase unchanged.
    local ok, result = pcall(function()
        return Export.LoGetAltitudeAboveGroundLevel()
    end)
    if ok and type(result) == "number" then
        return result
    end
    return nil
end


-- ---------------------------------------------------------------------------
-- Trigger and state file writing (atomic: write .tmp, then rename)
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

-- Shared atomic write: write a .tmp file then rename it into place. Returns
-- ok, err (never raises). Used by both the trigger and the state file.
local function atomicWrite(finalPath, contents)
    local tmpPath = finalPath .. ".tmp"
    local ok, err = pcall(function()
        local handle = io.open(tmpPath, "w")
        if not handle then
            error("could not open " .. tmpPath .. " for writing")
        end
        handle:write(contents)
        handle:close()
        -- os.rename will not overwrite an existing destination on Windows, so
        -- clear any stale file first (ignored if it is not there).
        os.remove(finalPath)
        local renamed, renameErr = os.rename(tmpPath, finalPath)
        if not renamed then
            error("rename failed: " .. tostring(renameErr))
        end
    end)
    if not ok then
        pcall(os.remove, tmpPath)
    end
    return ok, err
end

local function writeTrigger(aircraftType)
    local finalPath = lfs.writedir() .. "Logs/dtc_kneeboard_trigger.json"

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

    local ok, err = atomicWrite(finalPath, json)
    if ok then
        log("Wrote trigger for '" .. aircraftType .. "' (mission='" .. mission ..
            "', theatre='" .. theatre .. "')")
    else
        log("Trigger write FAILED: " .. tostring(err))
    end
end

-- Publish the current air/ground phase for the external app. aglMetres may be
-- nil (unknown), in which case 0.0 is written so the JSON stays valid. A write
-- failure is logged and swallowed; the app tolerates a missing/stale file.
local function writeState(phaseValue, aglMetres)
    local finalPath = lfs.writedir() .. "Logs/dtc_kneeboard_state.json"
    local timestamp = os.date("!%Y-%m-%dT%H:%M:%SZ")  -- ISO 8601, UTC
    local aglNum    = tonumber(aglMetres) or 0.0

    local json = string.format(
        '{"phase": "%s", "aircraft": "%s", "agl_m": %.1f, "ts": "%s"}',
        jsonEscape(phaseValue),
        jsonEscape(currentAircraft),
        aglNum,
        jsonEscape(timestamp)
    )

    local ok, err = atomicWrite(finalPath, json)
    if not ok then
        log("State write FAILED: " .. tostring(err))
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
        -- Activate the air/ground state machine. A ramp or runway start is on
        -- the ground; an air start (rare) self-corrects on the first AGL read.
        migActive       = true
        phase           = "ground"
        currentAircraft = unitType
        aglStreak       = 0
        statePollFrames = 0
        heartbeatFrames = 0
        writeState("ground", nil)
    else
        -- Different aircraft: do nothing. Any stale kneeboard from a previous
        -- MiG-29 spawn is intentionally left in place. Stand the state machine
        -- down so the app shows Waiting.
        log("Non-MiG-29 unit ('" .. unitType .. "'); no action")
        migActive       = false
        phase           = "none"
        currentAircraft = ""
        writeState("none", nil)
    end
    -- Either branch is a definitive result, so stop polling.
    stopPolling()
end


-- ---------------------------------------------------------------------------
-- Air/ground state machine (runs every frame while migActive)
-- ---------------------------------------------------------------------------
local function updateState()
    statePollFrames = statePollFrames + 1
    heartbeatFrames = heartbeatFrames + 1

    -- Sample our height roughly once a second and apply hysteresis. A single
    -- streak counter suffices: at any phase only one direction is "armed".
    if statePollFrames >= STATE_POLL_FRAMES then
        statePollFrames = 0
        local agl = safeGetAGL()
        if agl ~= nil then
            lastAgl = agl
            if phase ~= "air" and agl > AGL_AIR_M then
                aglStreak = aglStreak + 1
                if aglStreak >= AGL_CONFIRM_SAMPLES then
                    phase = "air"
                    aglStreak = 0
                    writeState("air", agl)
                    log(string.format("Phase -> air (agl=%.1f m)", agl))
                end
            elseif phase ~= "ground" and agl < AGL_GROUND_M then
                aglStreak = aglStreak + 1
                if aglStreak >= AGL_CONFIRM_SAMPLES then
                    phase = "ground"
                    aglStreak = 0
                    writeState("ground", agl)
                    log(string.format("Phase -> ground (agl=%.1f m)", agl))
                end
            else
                -- In the hysteresis band, or already in the target phase.
                aglStreak = 0
            end
        end
        -- agl == nil: leave the phase unchanged (safe default keeps "ground").
    end

    -- Heartbeat: refresh the state file periodically so the app's staleness
    -- check sees a live hook, and the log shows recent AGL readings.
    if heartbeatFrames >= STATE_HEARTBEAT_FRAMES then
        heartbeatFrames = 0
        writeState(phase, lastAgl)
        log(string.format("Heartbeat: phase=%s agl=%s", phase,
            lastAgl and string.format("%.1f m", lastAgl) or "nil"))
    end
end


-- ---------------------------------------------------------------------------
-- Callbacks
-- ---------------------------------------------------------------------------
local handler = {}

function handler.onPlayerChangeSlot(id)
    -- Fires on every slot selection/change. In multiplayer DCS calls this for
    -- EVERY player, so ignore changes that are not the local player - otherwise
    -- each one re-arms the poll and re-writes the trigger for our unchanged
    -- local unit (the v1.1 fix). If the local id cannot be determined (e.g.
    -- single-player, where net is unavailable), fall through and arm as before.
    local myId = safeGetMyPlayerId()
    if myId ~= nil and id ~= nil and id ~= myId then
        return
    end

    -- Stand the state machine down for the duration of slot selection / loading
    -- so the app shows Waiting until the new unit is confirmed (poll() turns it
    -- back on if the new slot is a MiG-29).
    migActive       = false
    phase           = "none"
    currentAircraft = ""
    writeState("none", nil)

    -- Restart the deferred poll; the unit type is not reliable yet, so we only
    -- arm the timer here and read the type later in onSimulationFrame.
    pollActive = true
    frameCount = 0
    pollCount  = 0
    log("onPlayerChangeSlot(id=" .. tostring(id) .. "): starting deferred poll")
end

function handler.onSimulationFrame()
    -- Detection poll (v1.1, unchanged): only while armed by a slot change.
    if pollActive then
        frameCount = frameCount + 1
        if frameCount >= INITIAL_DELAY_FRAMES
           and ((frameCount - INITIAL_DELAY_FRAMES) % POLL_INTERVAL_FRAMES) == 0 then
            -- Guard the poll so a failure can never propagate into the sim loop.
            local ok, err = pcall(poll)
            if not ok then
                log("poll() raised: " .. tostring(err))
                stopPolling("poll error")
            end
        end
    end

    -- Air/ground state machine (v1.2): independent of the detection poll, runs
    -- only while in a MiG-29. Fully pcall-guarded for the same reason.
    if migActive then
        local ok, err = pcall(updateState)
        if not ok then
            log("updateState() raised: " .. tostring(err))
        end
    end
end

function handler.onSimulationStop()
    -- Mission ended / returned to menu: stand the state machine down so the app
    -- shows Waiting. Best-effort (writeState never raises).
    migActive       = false
    phase           = "none"
    currentAircraft = ""
    writeState("none", nil)
    log("onSimulationStop: state machine stood down")
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

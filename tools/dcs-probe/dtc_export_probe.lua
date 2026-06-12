-- =============================================================================
-- DTC Export Probe v3 (round 2)
-- v2 never fired in round 1. v3 adds:
--   1. Immediate breadcrumb log at file-load time (before any callbacks)
--   2. Extended probe duration (60s instead of 30s)
--   3. Diagnostic info about the Export.lua loading context
-- =============================================================================

local PROBE_NAME = "EXPORT"
local LOG_FILE = nil
local startTime = nil
local PROBE_DURATION = 60  -- extended from 30s
local initialised = false
local frameCount = 0
local LOG_INTERVAL = 300

local DTC_PATTERNS = {
    "dtc", "DTC", "cartridge", "Program_1", "Program_2", "Program_3",
    "ADF", "CMDS", "Points", "Waypoints", "RSBN", "TargetPoint",
    "WeaponSettings", "SelectedProgram", "mirror_"
}

-- ---------------------------------------------------------------------------
-- Logging
-- ---------------------------------------------------------------------------

local function getLogPath()
    local ok, _lfs = pcall(require, "lfs")
    if ok and _lfs and _lfs.writedir then
        return _lfs.writedir() .. "Logs\\dtc_probe_log.txt"
    end
    local profile = os.getenv("USERPROFILE")
    if profile then
        return profile .. "\\Saved Games\\DCS\\Logs\\dtc_probe_log.txt"
    end
    return "dtc_probe_log.txt"
end

local function log(msg)
    if not LOG_FILE then
        LOG_FILE = io.open(getLogPath(), "a")
    end
    if not LOG_FILE then return end
    local ts = os.date("%Y-%m-%d %H:%M:%S")
    LOG_FILE:write("[" .. ts .. "] [" .. PROBE_NAME .. "] " .. msg .. "\n")
    LOG_FILE:flush()
end

-- *** IMMEDIATE BREADCRUMB - logs as soon as this file is dofile'd ***
log("=== EXPORT PROBE FILE LOADED (v3) ===")
log("_VERSION = " .. tostring(_VERSION))
log("LuaExportStart exists (before override): " .. tostring(type(LuaExportStart)))
log("LuaExportAfterNextFrame exists (before override): " .. tostring(type(LuaExportAfterNextFrame)))
log("LuaExportStop exists (before override): " .. tostring(type(LuaExportStop)))

-- Check what globals exist at load time
local loadTimeGlobals = {}
for k, v in pairs(_G) do
    if type(k) == "string" and type(v) == "function" and k:find("^Lo") then
        loadTimeGlobals[#loadTimeGlobals + 1] = k
    end
end
table.sort(loadTimeGlobals)
log("Lo* functions at load time (" .. #loadTimeGlobals .. "): " .. table.concat(loadTimeGlobals, ", "))

-- ---------------------------------------------------------------------------
-- Serialiser (shallow, safe)
-- ---------------------------------------------------------------------------

local function serialise(val, indent, seen, maxDepth)
    if indent == nil then indent = 0 end
    if maxDepth == nil then maxDepth = 4 end
    if seen == nil then seen = {} end
    if indent > maxDepth then return "<max depth>" end
    if val == nil then return "nil" end
    local t = type(val)
    if t == "string" then
        if #val > 500 then return string.format("%q", val:sub(1, 500)) .. "...[TRUNC]" end
        return string.format("%q", val)
    end
    if t == "number" then return tostring(val) end
    if t == "boolean" then return tostring(val) end
    if t == "function" then return "<function>" end
    if t == "userdata" then return "<userdata>" end
    if t ~= "table" then return "<" .. t .. ">" end
    if seen[val] then return "<circular>" end
    seen[val] = true
    local parts = {}
    local pad = string.rep("  ", indent + 1)
    local count = 0
    for k, v in pairs(val) do
        count = count + 1
        if count > 100 then
            parts[#parts + 1] = pad .. "... [" .. count .. "+ entries, TRUNCATED]"
            break
        end
        local keyStr = type(k) == "string" and k or ("[" .. tostring(k) .. "]")
        parts[#parts + 1] = pad .. keyStr .. " = " .. serialise(v, indent + 1, seen, maxDepth)
    end
    if #parts == 0 then return "{}" end
    return "{ " .. table.concat(parts, ", ") .. " }"
end

-- ---------------------------------------------------------------------------
-- DTC search
-- ---------------------------------------------------------------------------

local function matchesDTC(keyStr)
    for i = 1, #DTC_PATTERNS do
        if string.find(keyStr, DTC_PATTERNS[i], 1, true) then return true end
    end
    return false
end

local function searchForDTCKeys(tbl, path, results, seen, depth)
    if path == nil then path = "" end
    if results == nil then results = {} end
    if seen == nil then seen = {} end
    if depth == nil then depth = 0 end
    if depth > 6 then return results end
    if type(tbl) ~= "table" then return results end
    if seen[tbl] then return results end
    seen[tbl] = true
    for k, v in pairs(tbl) do
        local keyStr = tostring(k)
        local fullPath = path .. "." .. keyStr
        if matchesDTC(keyStr) then
            local valStr = serialise(v, 2)
            if #valStr > 400 then valStr = valStr:sub(1, 400) .. "...[TRUNC]" end
            results[#results + 1] = "MATCH at " .. fullPath .. " = " .. valStr
        end
        if type(v) == "table" then
            searchForDTCKeys(v, fullPath, results, seen, depth + 1)
        end
    end
    return results
end

-- ---------------------------------------------------------------------------
-- Safe call wrapper
-- ---------------------------------------------------------------------------

local function tryCall(funcName)
    local fn = _G[funcName]
    if not fn then return nil, funcName .. " not in _G" end
    local ok, result = pcall(fn)
    if ok then return result, nil end
    return nil, tostring(result)
end

local function callAndLog(funcName)
    local result, err = tryCall(funcName)
    if result ~= nil then
        local rStr = serialise(result, 1)
        if #rStr > 1000 then rStr = rStr:sub(1, 1000) .. "...[TRUNC]" end
        log(funcName .. "() = " .. rStr)
        if type(result) == "table" then
            local hits = searchForDTCKeys(result, funcName)
            if #hits > 0 then
                log("*** DTC DATA IN " .. funcName .. " ***")
                for i = 1, #hits do log("  " .. hits[i]) end
            end
        end
    else
        log(funcName .. "() = nil/ERROR: " .. tostring(err))
    end
    return result
end

-- ---------------------------------------------------------------------------
-- Export lifecycle hooks
-- ---------------------------------------------------------------------------

local _origStart = LuaExportStart
local _origFrame = LuaExportAfterNextFrame
local _origStop = LuaExportStop

function LuaExportStart()
    if _origStart then _origStart() end

    startTime = os.clock()
    initialised = true

    log("=== EXPORT PROBE START (v3 round 2) ===")
    log("Probe duration: " .. PROBE_DURATION .. "s")

    -- Enumerate LoGet* functions
    local loGetFuncs = {}
    for k, v in pairs(_G) do
        if type(k) == "string" and type(v) == "function" and k:find("^LoGet") then
            loGetFuncs[#loGetFuncs + 1] = k
        end
    end
    table.sort(loGetFuncs)
    log("LoGet* functions (" .. #loGetFuncs .. "): " .. table.concat(loGetFuncs, ", "))

    -- Check for interesting globals
    log("--- Interesting globals ---")
    for k, v in pairs(_G) do
        if type(k) == "string" then
            local kl = k:lower()
            if kl:find("dtc") or kl:find("mission") or kl:find("kneeboard")
               or kl:find("cartridge") or kl:find("program") then
                log("  " .. k .. " = " .. type(v))
            end
        end
    end

    -- Call key functions
    log("--- Initial LoGet* calls ---")
    callAndLog("LoGetSelfData")
    callAndLog("LoGetRoute")
    callAndLog("LoGetNavigationInfo")
    callAndLog("LoGetPayloadInfo")
    callAndLog("LoGetControlPanel_HSI")
    callAndLog("LoGetRadioBeaconsStatus")
    callAndLog("LoGetCountermeasures")
    callAndLog("LoGetMCPState")
    callAndLog("LoGetEngineInfo")
    callAndLog("LoGetMechInfo")
    callAndLog("LoGetSnares")

    log("--- Export probe init complete ---")
end

function LuaExportAfterNextFrame()
    if _origFrame then _origFrame() end
    if not initialised then return end

    local elapsed = os.clock() - startTime
    if elapsed > PROBE_DURATION then return end

    frameCount = frameCount + 1
    if frameCount % LOG_INTERVAL ~= 0 then return end

    log(string.format("--- Periodic poll at %.1fs (frame %d) ---", elapsed, frameCount))
    callAndLog("LoGetRoute")
    callAndLog("LoGetSelfData")
    callAndLog("LoGetNavigationInfo")
    callAndLog("LoGetControlPanel_HSI")
    callAndLog("LoGetRadioBeaconsStatus")
    callAndLog("LoGetPayloadInfo")
end

function LuaExportStop()
    if initialised then
        log(string.format("--- Export probe stopping (%.1fs, %d frames) ---",
            os.clock() - startTime, frameCount))
        log("=== EXPORT PROBE END ===")
        if LOG_FILE then LOG_FILE:close(); LOG_FILE = nil end
    end
    if _origStop then _origStop() end
end

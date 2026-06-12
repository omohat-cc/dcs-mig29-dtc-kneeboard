-- =============================================================================
-- DTC Hooks Worker v5 - ULTRA-LIGHTWEIGHT
-- Previous versions crashed DCS by doing heavy Lua work (serializing huge
-- tables from getCurrentMission, recursive scanning, etc.) during the
-- onMissionLoadEnd/onSimulationStart FSM state transition.
-- This version: minimal work, no deep serialization, no heavy allocations.
-- =============================================================================

local PROBE = "HOOKS"
local logPath = lfs.writedir() .. "Logs\\dtc_probe_log.txt"

local function w(msg)
    local f = io.open(logPath, "a")
    if not f then return end
    f:write("[" .. os.date("%Y-%m-%d %H:%M:%S") .. "] [" .. PROBE .. "] " .. msg .. "\n")
    f:close()
end

w("=== HOOKS WORKER START ===")

-- 1. List all DCS functions (names only, no calling)
if DCS then
    local fns = {}
    for k, v in pairs(DCS) do
        if type(v) == "function" then fns[#fns + 1] = k end
    end
    table.sort(fns)
    w("DCS functions (" .. #fns .. "): " .. table.concat(fns, ", "))

    -- 2. Key existence checks (no calls, just log whether they exist)
    local keyFns = {
        "toggleDTC", "getCurrentMission", "getMissionFilename",
        "getMissionName", "getFarpsAndCarriersMissionData",
        "getAvailableCoalitions", "getAvailableSlots",
        "getPlayerUnit", "getPlayerUnitType", "getPlayerCoalition",
    }
    for i = 1, #keyFns do
        local name = keyFns[i]
        local exists = DCS[name] ~= nil
        w("DCS." .. name .. " exists: " .. tostring(exists))
    end

    -- 3. Only call tiny-return functions (strings, numbers, booleans)
    local ok, mf = pcall(DCS.getMissionFilename)
    if ok and mf then w("DCS.getMissionFilename() = " .. tostring(mf)) end

    local ok2, mn = pcall(DCS.getMissionName)
    if ok2 and mn then w("DCS.getMissionName() = " .. tostring(mn)) end

    local ok3, mp = pcall(DCS.isMultiplayer)
    if ok3 then w("DCS.isMultiplayer() = " .. tostring(mp)) end

    local ok4, sv = pcall(DCS.isServer)
    if ok4 then w("DCS.isServer() = " .. tostring(sv)) end

    local ok5, th = pcall(DCS.getTheatreID)
    if ok5 and th then w("DCS.getTheatreID() = " .. tostring(th)) end

    local ok6, pc = pcall(DCS.getPlayerCoalition)
    if ok6 and pc then w("DCS.getPlayerCoalition() = " .. tostring(pc)) end

    local ok7, pu = pcall(DCS.getPlayerUnit)
    if ok7 and pu then w("DCS.getPlayerUnit() = " .. tostring(pu)) end

    local ok8, pt = pcall(DCS.getPlayerUnitType)
    if ok8 and pt then w("DCS.getPlayerUnitType() = " .. tostring(pt)) end

    local ok9, ml = pcall(DCS.getMissionLoaded)
    if ok9 then w("DCS.getMissionLoaded() = " .. tostring(ml)) end

    local ok10, pa = pcall(DCS.getPause)
    if ok10 then w("DCS.getPause() = " .. tostring(pa)) end

    -- 4. getCurrentMission - ONLY check top-level keys (no deep serialize)
    local ok_cm, cm = pcall(DCS.getCurrentMission)
    if ok_cm and cm then
        w("DCS.getCurrentMission() returned table")
        if type(cm) == "table" then
            local topKeys = {}
            for k, v in pairs(cm) do
                topKeys[#topKeys + 1] = tostring(k) .. "=" .. type(v)
            end
            w("  top keys: " .. table.concat(topKeys, ", "))
            -- Check mission sub-table top keys
            if cm.mission then
                local mKeys = {}
                for k, v in pairs(cm.mission) do
                    mKeys[#mKeys + 1] = tostring(k) .. "=" .. type(v)
                end
                w("  mission keys: " .. table.concat(mKeys, ", "))
            end
            -- Specifically look for DTC-related top-level keys
            local dtcKeys = {"dtc", "DTC", "dataCartridge", "cartridge"}
            for i = 1, #dtcKeys do
                if cm[dtcKeys[i]] then
                    w("  *** FOUND cm." .. dtcKeys[i] .. " = " .. type(cm[dtcKeys[i]]))
                end
                if cm.mission and cm.mission[dtcKeys[i]] then
                    w("  *** FOUND cm.mission." .. dtcKeys[i] .. " = " .. type(cm.mission[dtcKeys[i]]))
                end
            end
        end
    elseif not ok_cm then
        w("DCS.getCurrentMission() ERROR: " .. tostring(cm))
    end

    -- 5. getFarpsAndCarriersMissionData - shallow log only
    local ok_fc, fc = pcall(DCS.getFarpsAndCarriersMissionData)
    if ok_fc and fc and type(fc) == "table" then
        local farpCount = 0
        local carrierCount = 0
        if fc.farps and type(fc.farps) == "table" then
            for _ in pairs(fc.farps) do farpCount = farpCount + 1 end
        end
        if fc.carriers and type(fc.carriers) == "table" then
            for _ in pairs(fc.carriers) do carrierCount = carrierCount + 1 end
        end
        w("DCS.getFarpsAndCarriersMissionData(): farps=" .. farpCount .. ", carriers=" .. carrierCount)
    end
end

-- 6. net namespace - names only
local netNs = rawget(_G, "net")
if netNs then
    local nfns = {}
    for k, v in pairs(netNs) do
        if type(v) == "function" then nfns[#nfns + 1] = k end
    end
    table.sort(nfns)
    w("net functions (" .. #nfns .. "): " .. table.concat(nfns, ", "))

    local ok_pid, pid = pcall(netNs.get_my_player_id)
    if ok_pid and pid then w("net.get_my_player_id() = " .. tostring(pid)) end
end

-- 7. Global DTC scan - names and types only, no serialization
w("--- Global DTC scan ---")
local dtcGlobals = 0
for k, v in pairs(_G) do
    if type(k) == "string" then
        local kl = k:lower()
        if string.find(kl, "dtc") or string.find(kl, "cartridge") or string.find(kl, "kneeboard") then
            w("  _G[" .. k .. "] = " .. type(v))
            dtcGlobals = dtcGlobals + 1
        end
    end
end
if dtcGlobals == 0 then w("  No DTC/kneeboard globals found") end

w("=== HOOKS WORKER END ===")

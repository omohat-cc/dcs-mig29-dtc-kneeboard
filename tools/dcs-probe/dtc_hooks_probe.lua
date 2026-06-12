-- DTC Hooks Probe (v8 - round 2: deep mission walk + net.dostring_in)
-- v7 confirmed: no crashes, 1800-frame delay safe, inline code safe.
-- v8 adds:
--   1. Deep walk into getCurrentMission().mission.coalition to find unit data
--   2. net.dostring_in probes into mission/server Lua environments
--   3. DCS.toggleDTC investigation
--   4. Scan mission.coalition.[side].country[N].plane.group[N].units[N]
-- Safety: same pattern as v7. No getMissionLoaded. No C-arg functions.

local wd = lfs.writedir()
local logPath = wd .. "Logs\\dtc_probe_log.txt"
local countdown = nil
local done = false
local p = {}

function p.onPlayerChangeSlot()
    if not done then countdown = 1800 end
end

function p.onSimulationFrame()
    if not countdown then return end
    countdown = countdown - 1
    if countdown > 0 then return end
    countdown = nil
    done = true

    local f = io.open(logPath, "a")
    if not f then return end
    local function w(msg)
        f:write("[" .. os.date("%Y-%m-%d %H:%M:%S") .. "] [HOOKS-R2] " .. msg .. "\n")
    end

    w("=== HOOKS PROBE START (v8 round 2) ===")

    local ok, err = pcall(function()

        -- =====================================================================
        -- 1. Basic context (same as v7, for log consistency)
        -- =====================================================================
        local ok1, v1 = pcall(DCS.getMissionFilename)
        if ok1 and v1 then w("getMissionFilename = " .. tostring(v1)) end
        local ok2, v2 = pcall(DCS.getMissionName)
        if ok2 and v2 then w("getMissionName = " .. tostring(v2)) end
        local ok3, v3 = pcall(DCS.getPlayerUnitType)
        if ok3 and v3 then w("getPlayerUnitType = " .. tostring(v3)) end
        local ok4, v4 = pcall(DCS.getPlayerUnit)
        if ok4 and v4 then w("getPlayerUnit = " .. tostring(v4)) end
        local ok5, v5 = pcall(DCS.getPlayerCoalition)
        if ok5 and v5 then w("getPlayerCoalition = " .. tostring(v5)) end

        -- =====================================================================
        -- 2. DCS.toggleDTC investigation
        -- =====================================================================
        w("--- toggleDTC investigation ---")
        w("DCS.toggleDTC type: " .. type(DCS.toggleDTC))
        -- DO NOT call it yet - it might toggle something destructive.
        -- Instead, check if there are related DTC functions we missed.
        local dtcFns = {}
        for k, v in pairs(DCS) do
            if type(v) == "function" then
                local kl = k:lower()
                if kl:find("dtc") or kl:find("cartridge") or kl:find("kneeboard")
                   or kl:find("data") or kl:find("load") or kl:find("program") then
                    dtcFns[#dtcFns + 1] = k
                end
            end
        end
        table.sort(dtcFns)
        w("DCS functions matching dtc/cartridge/kneeboard/data/load/program (" .. #dtcFns .. "): " .. table.concat(dtcFns, ", "))

        -- =====================================================================
        -- 3. Deep walk: getCurrentMission().mission.coalition
        -- Goal: find unit-level keys that might contain DTC data
        -- Path: mission.coalition.[blue/red].country[N].plane.group[N].units[N]
        -- =====================================================================
        w("--- Deep mission coalition walk ---")
        local okCm, cm = pcall(DCS.getCurrentMission)
        if not okCm or not cm or type(cm) ~= "table" then
            w("getCurrentMission failed or not table")
        else
            local mission = cm.mission
            if not mission or type(mission) ~= "table" then
                w("cm.mission is nil or not table")
            else
                local coalition = mission.coalition
                if not coalition or type(coalition) ~= "table" then
                    w("mission.coalition is nil or not table")
                else
                    for sideName, sideData in pairs(coalition) do
                        w("coalition." .. tostring(sideName) .. " type=" .. type(sideData))
                        if type(sideData) == "table" then
                            -- Log top keys of each side
                            local sideKeys = {}
                            for k in pairs(sideData) do sideKeys[#sideKeys + 1] = tostring(k) end
                            w("  keys: " .. table.concat(sideKeys, ", "))

                            -- Walk countries
                            local countries = sideData.country
                            if countries and type(countries) == "table" then
                                for ci, country in pairs(countries) do
                                    local cName = country.name or tostring(ci)
                                    -- Log country top keys
                                    local cKeys = {}
                                    for k in pairs(country) do cKeys[#cKeys + 1] = tostring(k) end
                                    w("  country[" .. tostring(ci) .. "] " .. cName .. " keys: " .. table.concat(cKeys, ", "))

                                    -- Walk plane groups
                                    local plane = country.plane
                                    if plane and type(plane) == "table" and plane.group then
                                        for gi, group in pairs(plane.group) do
                                            local gName = group.name or tostring(gi)
                                            local gKeys = {}
                                            for k in pairs(group) do gKeys[#gKeys + 1] = tostring(k) end
                                            w("    plane.group[" .. tostring(gi) .. "] '" .. gName .. "' keys: " .. table.concat(gKeys, ", "))

                                            -- Walk units in group
                                            if group.units and type(group.units) == "table" then
                                                for ui, unit in pairs(group.units) do
                                                    local uName = unit.name or tostring(ui)
                                                    local uType = unit.type or "?"
                                                    local uKeys = {}
                                                    for k in pairs(unit) do uKeys[#uKeys + 1] = tostring(k) end
                                                    table.sort(uKeys)
                                                    w("      unit[" .. tostring(ui) .. "] '" .. uName .. "' (" .. uType .. ") keys: " .. table.concat(uKeys, ", "))

                                                    -- Look for DTC-related keys at unit level
                                                    for k, v in pairs(unit) do
                                                        local kl = tostring(k):lower()
                                                        if kl:find("dtc") or kl:find("cartridge") or kl:find("program")
                                                           or kl:find("radio") or kl:find("payload") or kl:find("kneeboard")
                                                           or kl:find("adf") or kl:find("cmds") or kl:find("rsbn")
                                                           or kl:find("waypoint") or kl:find("route") then
                                                            local vStr
                                                            if type(v) == "table" then
                                                                -- Shallow serialize (keys only, no deep walk)
                                                                local vKeys = {}
                                                                for vk in pairs(v) do vKeys[#vKeys + 1] = tostring(vk) end
                                                                vStr = "table{" .. table.concat(vKeys, ", ") .. "}"
                                                            else
                                                                vStr = tostring(v)
                                                                if #vStr > 200 then vStr = vStr:sub(1, 200) .. "..." end
                                                            end
                                                            w("      *** MATCH unit." .. tostring(k) .. " = " .. vStr)
                                                        end
                                                    end
                                                end
                                            end
                                        end
                                    end

                                    -- Also check helicopter groups
                                    local heli = country.helicopter
                                    if heli and type(heli) == "table" and heli.group then
                                        for gi, group in pairs(heli.group) do
                                            local gName = group.name or tostring(gi)
                                            w("    helicopter.group[" .. tostring(gi) .. "] '" .. gName .. "'")
                                            if group.units and type(group.units) == "table" then
                                                for ui, unit in pairs(group.units) do
                                                    local uType = unit.type or "?"
                                                    if uType:lower():find("mig") or uType:lower():find("29") then
                                                        local uKeys = {}
                                                        for k in pairs(unit) do uKeys[#uKeys + 1] = tostring(k) end
                                                        w("      heli unit '" .. (unit.name or "?") .. "' (" .. uType .. ") keys: " .. table.concat(uKeys, ", "))
                                                    end
                                                end
                                            end
                                        end
                                    end
                                end
                            end
                        end
                    end
                end

                -- =====================================================================
                -- 3b. Check for DTC keys anywhere in top-level mission table
                -- =====================================================================
                w("--- Mission-level DTC key scan ---")
                local dtcFound = 0
                for k, v in pairs(mission) do
                    local kl = tostring(k):lower()
                    if kl:find("dtc") or kl:find("cartridge") or kl:find("program")
                       or kl:find("kneeboard") then
                        w("  mission." .. tostring(k) .. " = " .. type(v))
                        dtcFound = dtcFound + 1
                    end
                end
                if dtcFound == 0 then w("  No DTC keys at mission level") end
            end
        end

        -- =====================================================================
        -- 4. net.dostring_in - probe other Lua environments
        -- This is the big one. We can execute code in:
        --   "server"  - server scripting environment
        --   "mission" - mission scripting environment (where triggers run)
        --   "export"  - export environment (LoGet*)
        --   "config"  - config environment
        -- =====================================================================
        w("--- net.dostring_in probes ---")
        local netNs = rawget(_G, "net")
        if not netNs or not netNs.dostring_in then
            w("net.dostring_in not available")
        else
            local envs = {"mission", "server", "export", "config"}
            for _, envName in ipairs(envs) do
                -- Probe 1: list globals containing dtc/kneeboard/cartridge
                local code1 = [[
                    local results = {}
                    for k, v in pairs(_G) do
                        if type(k) == "string" then
                            local kl = k:lower()
                            if kl:find("dtc") or kl:find("cartridge") or kl:find("kneeboard")
                               or kl:find("program") then
                                results[#results + 1] = k .. "=" .. type(v)
                            end
                        end
                    end
                    if #results == 0 then return "none" end
                    table.sort(results)
                    return table.concat(results, ", ")
                ]]
                local ok_d1, r1 = pcall(netNs.dostring_in, envName, code1)
                if ok_d1 then
                    w("  " .. envName .. " DTC globals: " .. tostring(r1))
                else
                    w("  " .. envName .. " DTC globals ERROR: " .. tostring(r1))
                end

                -- Probe 2: list all function names (to find DTC-related APIs)
                local code2 = [[
                    local fns = {}
                    for k, v in pairs(_G) do
                        if type(v) == "function" and type(k) == "string" then
                            local kl = k:lower()
                            if kl:find("dtc") or kl:find("kneeboard") or kl:find("cartridge")
                               or kl:find("radio") or kl:find("payload") or kl:find("weapon")
                               or kl:find("program") or kl:find("navigation") or kl:find("waypoint")
                               or kl:find("route") then
                                fns[#fns + 1] = k
                            end
                        end
                    end
                    if #fns == 0 then return "none" end
                    table.sort(fns)
                    return table.concat(fns, ", ")
                ]]
                local ok_d2, r2 = pcall(netNs.dostring_in, envName, code2)
                if ok_d2 then
                    w("  " .. envName .. " relevant fns: " .. tostring(r2))
                else
                    w("  " .. envName .. " relevant fns ERROR: " .. tostring(r2))
                end

                -- Probe 3: check for LoGet* in export env, trigger/mist in mission env
                if envName == "export" then
                    local code3 = [[
                        local fns = {}
                        for k, v in pairs(_G) do
                            if type(v) == "function" and type(k) == "string" and k:find("^Lo") then
                                fns[#fns + 1] = k
                            end
                        end
                        table.sort(fns)
                        return "LoGet count=" .. #fns .. ": " .. table.concat(fns, ", ")
                    ]]
                    local ok_d3, r3 = pcall(netNs.dostring_in, envName, code3)
                    if ok_d3 then
                        w("  export LoGet: " .. tostring(r3))
                    else
                        w("  export LoGet ERROR: " .. tostring(r3))
                    end
                end

                if envName == "mission" then
                    -- Check for trigger/mist/env functions
                    local code4 = [[
                        local results = {}
                        local check = {"trigger", "mist", "env", "coalition", "Unit", "Group",
                                       "world", "timer", "land", "atmosphere", "coord"}
                        for _, name in ipairs(check) do
                            local v = rawget(_G, name)
                            if v then
                                results[#results + 1] = name .. "=" .. type(v)
                            end
                        end
                        return table.concat(results, ", ")
                    ]]
                    local ok_d4, r4 = pcall(netNs.dostring_in, envName, code4)
                    if ok_d4 then
                        w("  mission env namespaces: " .. tostring(r4))
                    else
                        w("  mission env namespaces ERROR: " .. tostring(r4))
                    end

                    -- Try to get player unit data through mission scripting
                    local code5 = [[
                        local ok, err = pcall(function()
                            -- Try to find the player's unit and inspect it
                            local results = {}
                            if Unit and Unit.getByName then
                                results[#results + 1] = "Unit.getByName exists"
                            end
                            if Group and Group.getByName then
                                results[#results + 1] = "Group.getByName exists"
                            end
                            -- Check for DCS singleton
                            if world then
                                local wFns = {}
                                for k, v in pairs(world) do
                                    if type(v) == "function" then wFns[#wFns + 1] = k end
                                end
                                table.sort(wFns)
                                results[#results + 1] = "world fns: " .. table.concat(wFns, ", ")
                            end
                            return table.concat(results, " | ")
                        end)
                        if ok then return err end
                        return "ERROR: " .. tostring(err)
                    ]]
                    local ok_d5, r5 = pcall(netNs.dostring_in, envName, code5)
                    if ok_d5 then
                        w("  mission scripting APIs: " .. tostring(r5))
                    else
                        w("  mission scripting APIs ERROR: " .. tostring(r5))
                    end
                end
            end

            -- =====================================================================
            -- 4b. Use net.dostring_in("export") to call LoGetSelfData
            -- This tests whether we can get export data via hooks
            -- =====================================================================
            w("--- net.dostring_in export LoGet calls ---")
            local exportCalls = {
                {"LoGetSelfData", [[
                    local d = LoGetSelfData()
                    if not d then return "nil" end
                    if type(d) ~= "table" then return type(d) end
                    local parts = {}
                    for k, v in pairs(d) do
                        if type(v) == "table" then
                            parts[#parts + 1] = k .. "={...}"
                        else
                            parts[#parts + 1] = k .. "=" .. tostring(v)
                        end
                    end
                    table.sort(parts)
                    return table.concat(parts, ", ")
                ]]},
                {"LoGetRoute", [[
                    local d = LoGetRoute()
                    if not d then return "nil" end
                    if type(d) ~= "table" then return tostring(d) end
                    local parts = {}
                    for k, v in pairs(d) do
                        if type(v) == "table" then
                            local sub = {}
                            for sk, sv in pairs(v) do
                                sub[#sub + 1] = tostring(sk) .. "=" .. tostring(sv)
                            end
                            parts[#parts + 1] = k .. "={" .. table.concat(sub, ",") .. "}"
                        else
                            parts[#parts + 1] = k .. "=" .. tostring(v)
                        end
                    end
                    return table.concat(parts, ", ")
                ]]},
                {"LoGetNavigationInfo", [[
                    local d = LoGetNavigationInfo()
                    if not d then return "nil" end
                    if type(d) ~= "table" then return tostring(d) end
                    local parts = {}
                    for k, v in pairs(d) do
                        if type(v) == "table" then
                            parts[#parts + 1] = k .. "={...}"
                        else
                            parts[#parts + 1] = k .. "=" .. tostring(v)
                        end
                    end
                    table.sort(parts)
                    return table.concat(parts, ", ")
                ]]},
                {"LoGetPayloadInfo", [[
                    local d = LoGetPayloadInfo()
                    if not d then return "nil" end
                    if type(d) ~= "table" then return tostring(d) end
                    local parts = {}
                    for k, v in pairs(d) do
                        if type(v) == "table" then
                            parts[#parts + 1] = k .. "={...}"
                        else
                            parts[#parts + 1] = k .. "=" .. tostring(v)
                        end
                    end
                    table.sort(parts)
                    return table.concat(parts, ", ")
                ]]},
            }
            for _, pair in ipairs(exportCalls) do
                local label, code = pair[1], pair[2]
                local okE, rE = pcall(netNs.dostring_in, "export", code)
                if okE then
                    local rStr = tostring(rE)
                    if #rStr > 500 then rStr = rStr:sub(1, 500) .. "..." end
                    w("  export." .. label .. ": " .. rStr)
                else
                    w("  export." .. label .. " ERROR: " .. tostring(rE))
                end
            end
        end

        -- =====================================================================
        -- 5. Check getMissionOptions for DTC-related settings
        -- =====================================================================
        w("--- getMissionOptions ---")
        local okMo, mo = pcall(DCS.getMissionOptions)
        if okMo and mo and type(mo) == "table" then
            local moKeys = {}
            for k in pairs(mo) do moKeys[#moKeys + 1] = tostring(k) end
            table.sort(moKeys)
            w("getMissionOptions keys: " .. table.concat(moKeys, ", "))
            -- Check each key for DTC relevance
            for k, v in pairs(mo) do
                local kl = tostring(k):lower()
                if kl:find("dtc") or kl:find("cartridge") or kl:find("kneeboard")
                   or kl:find("program") or kl:find("difficulty") then
                    w("  option." .. tostring(k) .. " = " .. tostring(v))
                end
            end
        elseif not okMo then
            w("getMissionOptions ERROR: " .. tostring(mo))
        end

        -- =====================================================================
        -- 6. getUserOptions for DTC settings
        -- =====================================================================
        w("--- getUserOptions ---")
        local okUo, uo = pcall(DCS.getUserOptions)
        if okUo and uo and type(uo) == "table" then
            local uoKeys = {}
            for k in pairs(uo) do uoKeys[#uoKeys + 1] = tostring(k) end
            table.sort(uoKeys)
            w("getUserOptions keys: " .. table.concat(uoKeys, ", "))
        elseif not okUo then
            w("getUserOptions ERROR: " .. tostring(uo))
        end

    end)

    if not ok then w("ERROR: " .. tostring(err)) end
    w("=== HOOKS PROBE END (v8) ===")
    f:close()
end

DCS.setUserCallbacks(p)

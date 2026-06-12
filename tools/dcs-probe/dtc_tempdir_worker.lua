-- =============================================================================
-- DTC Temp Directory Worker - loaded via dofile from hook callback
-- Scans unpacked mission temp directory for DTC data
-- =============================================================================

local PROBE = "TEMPDIR"
local logPath = lfs.writedir() .. "Logs\\dtc_probe_log.txt"

local function w(msg)
    local f = io.open(logPath, "a")
    if not f then return end
    f:write("[" .. os.date("%Y-%m-%d %H:%M:%S") .. "] [" .. PROBE .. "] " .. msg .. "\n")
    f:close()
end

local function readHead(path, maxChars)
    if maxChars == nil then maxChars = 5000 end
    local f = io.open(path, "r")
    if not f then return nil, 0 end
    local content = f:read(maxChars)
    local total = f:seek("end")
    f:close()
    return content, total
end

local dtcStrings = {
    "dtc", "DTC", "cartridge", "Program_1", "Program_2", "Program_3",
    "ADF", "CMDS", "TargetPoint", "WeaponSettings", "SelectedProgram",
    "mirror_ADF", "mirror_NAV", "mirror_Radio",
}

local function scanFileForDTC(path)
    local content = readHead(path, 10000)
    if not content then return {} end
    local hits = {}
    for i = 1, #dtcStrings do
        local s, e = string.find(content, dtcStrings[i])
        if s then
            local lo = math.max(1, s - 30)
            local hi = math.min(#content, e + 30)
            local ctx = content:sub(lo, hi):gsub("\n", "\\n"):gsub("\r", "")
            hits[#hits + 1] = dtcStrings[i] .. " at " .. s .. ": " .. ctx
        end
    end
    return hits
end

-- ==========================================================================
-- Find DCS temp directories
-- ==========================================================================

w("=== TEMPDIR WORKER START ===")

local localApp = os.getenv("LOCALAPPDATA")
if not localApp then
    local profile = os.getenv("USERPROFILE")
    if profile then localApp = profile .. "\\AppData\\Local" end
end

if not localApp then
    w("ERROR: Cannot determine LOCALAPPDATA")
    w("=== TEMPDIR WORKER END ===")
    return
end

local tempCandidates = { localApp .. "\\Temp\\DCS", localApp .. "\\Temp\\DCS.openbeta" }
local tempDirs = {}
for i = 1, #tempCandidates do
    local ok = pcall(lfs.dir, tempCandidates[i])
    if ok then
        tempDirs[#tempDirs + 1] = tempCandidates[i]
        w("Found temp dir: " .. tempCandidates[i])
    end
end

if #tempDirs == 0 then
    w("No DCS temp directories found")
    w("=== TEMPDIR WORKER END ===")
    return
end

-- ==========================================================================
-- Scan directories (breadth-first queue, no recursion)
-- ==========================================================================

for di = 1, #tempDirs do
    local baseDir = tempDirs[di]
    w("Scanning: " .. baseDir)

    -- BFS queue: {path, depth}
    local queue = { {baseDir, 0} }
    local head = 1
    local entries = {}

    while head <= #queue do
        local item = queue[head]
        head = head + 1
        local dirPath = item[1]
        local dirDepth = item[2]

        local ok, iter = pcall(lfs.dir, dirPath)
        if ok then
            for name in iter do
                if name ~= "." and name ~= ".." then
                    local full = dirPath .. "\\" .. name
                    local ok_m, mode = pcall(lfs.attributes, full, "mode")
                    local ftype = ok_m and mode or "unknown"
                    local fsize = 0
                    if ftype == "file" then
                        local ok_s, s = pcall(lfs.attributes, full, "size")
                        if ok_s then fsize = s end
                    end
                    entries[#entries + 1] = {full, name, ftype, fsize}
                    if ftype == "directory" and dirDepth < 3 then
                        queue[#queue + 1] = {full, dirDepth + 1}
                    end
                end
            end
        end
    end

    -- Log tree
    w("Found " .. #entries .. " entries:")
    for i = 1, #entries do
        local e = entries[i]
        local rel = e[1]
        if string.find(rel, baseDir, 1, true) == 1 then
            rel = rel:sub(#baseDir + 1)
        end
        local tag = ""
        if e[3] == "directory" then tag = " [DIR]" end
        if e[3] == "file" then tag = " [" .. e[4] .. "b]" end
        w("  " .. rel .. tag)
    end

    -- Inspect interesting files
    w("--- Inspecting files ---")
    for i = 1, #entries do
        local full = entries[i][1]
        local name = entries[i][2]
        local ftype = entries[i][3]
        local fsize = entries[i][4]
        if ftype ~= "file" then
            -- skip directories
        elseif string.find(name:lower(), "%.dtc$") then
            w("*** .dtc FILE: " .. full .. " ***")
            local c = readHead(full, 5000)
            if c then w(c) end
        elseif name == "mission" then
            w("*** mission FILE: " .. full .. " (" .. fsize .. "b) ***")
            local c, total = readHead(full, 5000)
            if c then
                w("Total size: " .. total .. " bytes")
                w(c)
                local hits = scanFileForDTC(full)
                if #hits > 0 then
                    w("*** DTC STRINGS IN mission ***")
                    for j = 1, #hits do w("  " .. hits[j]) end
                end
            end
        elseif name == "dictionary" then
            w("dictionary: " .. full)
            local c = readHead(full, 2000)
            if c then w(c) end
        elseif name == "options" then
            w("options: " .. full)
            local c = readHead(full, 2000)
            if c then w(c) end
        elseif string.find(name:lower(), "dtc") or string.find(name:lower(), "cartridge") then
            w("*** DTC-NAMED FILE: " .. full .. " ***")
            local c = readHead(full, 5000)
            if c then w(c) end
        elseif string.find(name:lower(), "%.lua$") then
            local hits = scanFileForDTC(full)
            if #hits > 0 then
                w("DTC in Lua: " .. full)
                for j = 1, #hits do w("  " .. hits[j]) end
            end
        elseif string.find(name:lower(), "%.miz$") then
            w("Found .miz: " .. full .. " (" .. fsize .. "b)")
        end
    end
end

-- Check Saved Games for .dtc files
w("--- Saved Games .dtc scan ---")
local wd = lfs.writedir()
local sgPaths = { wd, wd .. "MissionEditor", wd .. "Config" }
for i = 1, #sgPaths do
    local ok, iter = pcall(lfs.dir, sgPaths[i])
    if ok then
        for name in iter do
            if name ~= "." and name ~= ".." and string.find(name:lower(), "%.dtc") then
                local fp = sgPaths[i] .. "\\" .. name
                w("*** .dtc in Saved Games: " .. fp .. " ***")
                local c = readHead(fp, 5000)
                if c then w(c) end
            end
        end
    end
end

-- Mission path from DCS API
if DCS and DCS.getMissionFilename then
    local ok, mf = pcall(DCS.getMissionFilename)
    if ok and mf then w("DCS.getMissionFilename() = " .. tostring(mf)) end
end

w("=== TEMPDIR WORKER END ===")

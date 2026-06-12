-- DTC Temp Dir Listing Test (v10 - fix: io.popen is nil in hooks)
-- v9 crashed because io.popen doesn't exist in DCS hooks context.
-- v10: guard all calls properly, test remaining strategies.

local wd = lfs.writedir()
local logPath = wd .. "Logs\\dtc_probe_log.txt"
local countdown = nil
local done = false
local p = {}

function p.onPlayerChangeSlot()
    if not done then countdown = 2100 end
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
        f:write("[" .. os.date("%Y-%m-%d %H:%M:%S") .. "] [TEMPDIR-v10] " .. msg .. "\n")
    end

    w("=== TEMPDIR LISTING TEST START ===")

    local ok, err = pcall(function()
        local localApp = os.getenv("LOCALAPPDATA")
        if not localApp then
            local profile = os.getenv("USERPROFILE")
            if profile then localApp = profile .. "\\AppData\\Local" end
        end
        if not localApp then
            w("ERROR: Cannot determine LOCALAPPDATA")
            return
        end

        -- Use the known temp dir path (confirmed in round 1)
        local tempBase = localApp .. "\\Temp\\DCS"
        w("Target temp dir: " .. tempBase)

        -- =================================================================
        -- Check what IO functions are available
        -- =================================================================
        w("")
        w("--- IO function availability ---")
        w("io.open: " .. type(io.open))
        w("io.popen: " .. type(io.popen))
        w("os.execute: " .. type(os.execute))
        w("os.remove: " .. type(os.remove))
        w("os.rename: " .. type(os.rename))
        w("os.tmpname: " .. type(os.tmpname))
        w("lfs.dir: " .. type(lfs.dir))
        w("lfs.attributes: " .. type(lfs.attributes))
        w("lfs.currentdir: " .. type(lfs.currentdir))
        w("lfs.chdir: " .. type(lfs.chdir))

        -- =================================================================
        -- STRATEGY A: io.popen (already know it's nil, confirming)
        -- =================================================================
        w("")
        w("--- STRATEGY A: io.popen ---")
        if type(io.popen) ~= "function" then
            w("STRATEGY A: SKIPPED - io.popen is " .. type(io.popen))
        else
            local okP, ph = pcall(io.popen, 'dir /b "' .. tempBase .. '" 2>nul')
            if okP and ph then
                local output = ph:read("*a")
                ph:close()
                if output and #output > 0 then
                    w("STRATEGY A: SUCCESS")
                    for line in output:gmatch("[^\r\n]+") do
                        w("  FILE: " .. line)
                    end
                else
                    w("STRATEGY A: FAILED - empty output")
                end
            else
                w("STRATEGY A: FAILED - " .. tostring(ph))
            end
        end

        -- =================================================================
        -- STRATEGY B: os.execute with redirect to file
        -- =================================================================
        w("")
        w("--- STRATEGY B: os.execute + redirect ---")
        if type(os.execute) ~= "function" then
            w("STRATEGY B: SKIPPED - os.execute is " .. type(os.execute))
        else
            local outputFile = wd .. "Logs\\dtc_dir_listing.txt"
            -- Clean up first
            pcall(os.remove, outputFile)

            local cmdStr = 'dir /b "' .. tempBase .. '" > "' .. outputFile .. '" 2>nul'
            w("Running: " .. cmdStr)
            local okExec, execResult = pcall(os.execute, cmdStr)
            w("os.execute returned: ok=" .. tostring(okExec) .. ", result=" .. tostring(execResult))

            -- Try to read the output
            local fOut = io.open(outputFile, "r")
            if fOut then
                local content = fOut:read("*a")
                fOut:close()
                if content and #content > 0 then
                    w("STRATEGY B: SUCCESS (" .. #content .. " bytes)")
                    for line in content:gmatch("[^\r\n]+") do
                        w("  FILE: " .. line)
                    end
                else
                    w("STRATEGY B: FAILED - output file empty or nil")
                end
                pcall(os.remove, outputFile)
            else
                w("STRATEGY B: FAILED - could not open output file")
            end
        end

        -- =================================================================
        -- STRATEGY C: lfs.dir on the temp base (known to fail, re-confirm)
        -- =================================================================
        w("")
        w("--- STRATEGY C: lfs.dir (expected to fail) ---")
        local okDir, iter = pcall(lfs.dir, tempBase)
        if okDir and iter then
            w("STRATEGY C: SUCCESS (unexpected!)")
            local entries = {}
            for name in iter do
                if name ~= "." and name ~= ".." then
                    entries[#entries + 1] = name
                    w("  FILE: " .. name)
                end
            end
            w("Total: " .. #entries)
        else
            w("STRATEGY C: FAILED - " .. tostring(iter))
        end

        -- =================================================================
        -- STRATEGY D: brute-force io.open on ~trXXXXXXXX.bin/.txt
        -- =================================================================
        w("")
        w("--- STRATEGY D: brute-force io.open ---")
        local foundFiles = {}
        local attempts = 0
        local startTime = os.clock()

        -- Phase 1: 0x0000 to 0x00FF (512 attempts) for timing
        w("Phase 1: timing test 0x0000-0x00FF")
        local p1Start = os.clock()
        for i = 0, 0xFF do
            local hexId = string.format("%08X", i)
            for _, ext in ipairs({".bin", ".txt"}) do
                local path = tempBase .. "\\~tr" .. hexId .. ext
                local fh = io.open(path, "rb")
                attempts = attempts + 1
                if fh then
                    local size = fh:seek("end")
                    fh:close()
                    foundFiles[#foundFiles + 1] = {
                        name = "~tr" .. hexId .. ext,
                        size = size,
                        path = path,
                    }
                    w("  FOUND: ~tr" .. hexId .. ext .. " (" .. size .. " bytes)")
                end
            end
        end
        local p1Time = os.clock() - p1Start
        w("Phase 1: " .. attempts .. " attempts in " .. string.format("%.3f", p1Time) .. "s")
        w("Projected full scan (0x0000-0xFFFF): " .. string.format("%.1f", p1Time * 256) .. "s")

        -- Phase 2: full scan if fast enough (< 2s for phase 1)
        if p1Time < 2.0 then
            w("Phase 2: full scan 0x0100-0xFFFF")
            local p2Start = os.clock()
            for i = 0x100, 0xFFFF do
                local hexId = string.format("%08X", i)
                for _, ext in ipairs({".bin", ".txt"}) do
                    local path = tempBase .. "\\~tr" .. hexId .. ext
                    local fh = io.open(path, "rb")
                    attempts = attempts + 1
                    if fh then
                        local size = fh:seek("end")
                        fh:close()
                        foundFiles[#foundFiles + 1] = {
                            name = "~tr" .. hexId .. ext,
                            size = size,
                            path = path,
                        }
                        w("  FOUND: ~tr" .. hexId .. ext .. " (" .. size .. " bytes)")
                    end
                end
            end
            local p2Time = os.clock() - p2Start
            w("Phase 2: " .. (attempts - 512) .. " attempts in " .. string.format("%.3f", p2Time) .. "s")
        else
            w("Phase 1 too slow, skipping full scan")
        end

        local totalTime = os.clock() - startTime
        w("")
        w("STRATEGY D: " .. #foundFiles .. " files found, " .. attempts .. " attempts, " .. string.format("%.3f", totalTime) .. "s")

        -- =================================================================
        -- DTC file identification test
        -- =================================================================
        w("")
        w("--- DTC file identification ---")
        for _, entry in ipairs(foundFiles) do
            if entry.name:match("%.bin$") then
                local fh = io.open(entry.path, "rb")
                if fh then
                    local header = fh:read(50000)
                    fh:close()
                    if header then
                        local hasMig = header:find("MiG%-29")
                        local hasData = header:find('"data"')
                        if hasMig and hasData then
                            w("DTC FILE: " .. entry.name .. " (" .. entry.size .. " bytes)")
                            local nameMatch = header:match('"name":%s*"([^"]+)"')
                            if nameMatch then
                                w("  DTC profile: " .. nameMatch)
                            end
                        else
                            w("Not DTC: " .. entry.name .. " (MiG29=" .. tostring(hasMig ~= nil) .. ", data=" .. tostring(hasData ~= nil) .. ")")
                        end
                    end
                end
            else
                w("Skipped (txt): " .. entry.name .. " (" .. entry.size .. " bytes)")
            end
        end

        -- =================================================================
        -- SUMMARY
        -- =================================================================
        w("")
        w("=== SUMMARY ===")
        w("io.popen: " .. type(io.popen) .. " (not available)")
        w("os.execute: tested above")
        w("lfs.dir: blocked by VFS")
        w("Brute-force io.open: " .. #foundFiles .. " files in " .. string.format("%.3f", totalTime) .. "s")
    end)

    if not ok then w("ERROR: " .. tostring(err)) end
    w("=== TEMPDIR LISTING TEST END ===")
    f:close()
end

DCS.setUserCallbacks(p)

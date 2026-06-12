-- =============================================================================
-- Export.lua snippet v2 - Add this to the END of your existing Export.lua
-- v2 adds: diagnostic breadcrumb log BEFORE attempting dofile, so we know
-- whether Export.lua itself is being loaded by DCS.
-- =============================================================================

-- DTC Export Probe loader
do
    -- Breadcrumb: confirm this snippet was reached
    local _dtcLogPath
    if lfs and lfs.writedir then
        _dtcLogPath = lfs.writedir() .. "Logs\\dtc_probe_log.txt"
    else
        local _profile = os.getenv("USERPROFILE")
        if _profile then _dtcLogPath = _profile .. "\\Saved Games\\DCS\\Logs\\dtc_probe_log.txt" end
    end
    if _dtcLogPath then
        local _lf = io.open(_dtcLogPath, "a")
        if _lf then
            _lf:write(string.format("[%s] [EXPORT_LOADER] Snippet reached in Export.lua\n", os.date("%Y-%m-%d %H:%M:%S")))
            _lf:close()
        end
    end

    local dtcProbeOk, dtcProbeErr = pcall(function()
        local probePath
        if lfs and lfs.writedir then
            probePath = lfs.writedir() .. "Scripts\\dtc_export_probe.lua"
        else
            local profile = os.getenv("USERPROFILE")
            if profile then
                probePath = profile .. "\\Saved Games\\DCS\\Scripts\\dtc_export_probe.lua"
            end
        end

        if probePath then
            local f = io.open(probePath, "r")
            if f then
                f:close()
                -- Log that we found the file and are about to dofile it
                if _dtcLogPath then
                    local _lf2 = io.open(_dtcLogPath, "a")
                    if _lf2 then
                        _lf2:write(string.format("[%s] [EXPORT_LOADER] Found probe at: %s, executing dofile\n", os.date("%Y-%m-%d %H:%M:%S"), probePath))
                        _lf2:close()
                    end
                end
                dofile(probePath)
            else
                -- Probe file not found - not an error, just means it's uninstalled
                if _dtcLogPath then
                    local _lf3 = io.open(_dtcLogPath, "a")
                    if _lf3 then
                        _lf3:write(string.format("[%s] [EXPORT_LOADER] Probe file not found at: %s\n", os.date("%Y-%m-%d %H:%M:%S"), probePath))
                        _lf3:close()
                    end
                end
            end
        end
    end)

    if not dtcProbeOk then
        if _dtcLogPath then
            local lf = io.open(_dtcLogPath, "a")
            if lf then
                lf:write(string.format("[%s] [EXPORT_LOADER] ERROR loading probe: %s\n", os.date("%Y-%m-%d %H:%M:%S"), tostring(dtcProbeErr)))
                lf:close()
            end
        end
    end
end
-- END DTC Export Probe loader

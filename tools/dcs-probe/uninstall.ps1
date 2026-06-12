# =============================================================================
# DTC Probe - Uninstall Script (v3)
# Removes hook launchers, worker files, and Export.lua snippet
# =============================================================================

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  DTC Access Research Probe - Uninstall" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

$dcsStable = Join-Path $env:USERPROFILE "Saved Games\DCS"
$dcsBeta   = Join-Path $env:USERPROFILE "Saved Games\DCS.openbeta"
$dcsDir = $null

if (Test-Path $dcsStable) { $dcsDir = $dcsStable }
elseif (Test-Path $dcsBeta) { $dcsDir = $dcsBeta }
else { Write-Host "[ERROR] DCS not found." -ForegroundColor Red; exit 1 }

if ((Test-Path $dcsStable) -and (Test-Path $dcsBeta)) {
    Write-Host "  1) $dcsStable  2) $dcsBeta"
    $choice = Read-Host "Which one? (1/2, default=1)"
    if ($choice -eq "2") { $dcsDir = $dcsBeta } else { $dcsDir = $dcsStable }
}

Write-Host "Using: $dcsDir" -ForegroundColor Cyan

# Remove all probe files
$filesToRemove = @(
    (Join-Path $dcsDir "Scripts\Hooks\dtc_hooks_probe.lua"),
    (Join-Path $dcsDir "Scripts\Hooks\dtc_tempdir_probe.lua"),
    (Join-Path $dcsDir "Scripts\dtc_hooks_worker.lua"),
    (Join-Path $dcsDir "Scripts\dtc_tempdir_worker.lua"),
    (Join-Path $dcsDir "Scripts\dtc_export_probe.lua")
)

foreach ($file in $filesToRemove) {
    if (Test-Path $file) {
        Remove-Item $file -Force
        Write-Host "[REMOVED] $file" -ForegroundColor Green
    } else {
        Write-Host "[SKIP] Not found: $file" -ForegroundColor Gray
    }
}

# Clean Export.lua
$exportLua = Join-Path $dcsDir "Scripts\Export.lua"
if (Test-Path $exportLua) {
    $content = Get-Content $exportLua -Raw
    $markerStart = "-- DTC Export Probe loader"
    $markerEnd   = "-- END DTC Export Probe loader"

    if ($content -match [regex]::Escape($markerStart)) {
        $pattern = "(?s)\r?\n?\r?\n?" + [regex]::Escape($markerStart) + ".*?" + [regex]::Escape($markerEnd)
        $cleaned = [regex]::Replace($content, $pattern, "")
        $cleaned = $cleaned.TrimEnd() + "`n"

        if ($cleaned.Trim().Length -eq 0) {
            $backupPath = Join-Path $dcsDir "Scripts\Export.lua.dtcprobe.bak"
            if (Test-Path $backupPath) {
                Copy-Item $backupPath $exportLua -Force
                Remove-Item $backupPath -Force
                Write-Host "[RESTORED] Export.lua from backup" -ForegroundColor Green
            } else {
                Remove-Item $exportLua -Force
                Write-Host "[REMOVED] Export.lua (empty after cleanup)" -ForegroundColor Green
            }
        } else {
            Set-Content -Path $exportLua -Value $cleaned -Encoding UTF8
            Write-Host "[CLEANED] Removed probe snippet from Export.lua" -ForegroundColor Green
            $backupPath = Join-Path $dcsDir "Scripts\Export.lua.dtcprobe.bak"
            if (Test-Path $backupPath) { Remove-Item $backupPath -Force }
        }
    } else {
        Write-Host "[SKIP] Export.lua has no probe snippet" -ForegroundColor Gray
    }
}

# Summary
$logFile = Join-Path $dcsDir "Logs\dtc_probe_log.txt"
Write-Host ""
Write-Host "  Uninstall Complete" -ForegroundColor Green
if (Test-Path $logFile) {
    Write-Host "  Log preserved: $logFile" -ForegroundColor Yellow
}

# =============================================================================
# DTC Probe - Install Script (v4 - round 2)
# v8 hooks/tempdir probes (self-contained), v3 export probe
# Replaces old Export.lua snippet with updated v2 (was skipping before)
# =============================================================================

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  DTC Access Research Probe - Installer" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Locate DCS Saved Games directory
$dcsStable = Join-Path $env:USERPROFILE "Saved Games\DCS"
$dcsBeta   = Join-Path $env:USERPROFILE "Saved Games\DCS.openbeta"
$dcsDir = $null

if (Test-Path $dcsStable) { $dcsDir = $dcsStable }
elseif (Test-Path $dcsBeta) { $dcsDir = $dcsBeta }
else {
    Write-Host "[ERROR] Could not find DCS Saved Games directory." -ForegroundColor Red
    exit 1
}

if ((Test-Path $dcsStable) -and (Test-Path $dcsBeta)) {
    Write-Host "Both DCS stable and open beta found." -ForegroundColor Yellow
    Write-Host "  1) $dcsStable"
    Write-Host "  2) $dcsBeta"
    $choice = Read-Host "Which one? (1/2, default=1)"
    if ($choice -eq "2") { $dcsDir = $dcsBeta } else { $dcsDir = $dcsStable }
}

Write-Host "[OK] Using: $dcsDir" -ForegroundColor Green

$scriptDir = $PSScriptRoot
if (-not $scriptDir) { $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition }

# Ensure directories
$scriptsDir = Join-Path $dcsDir "Scripts"
$hooksDir   = Join-Path $dcsDir "Scripts\Hooks"
$logsDir    = Join-Path $dcsDir "Logs"

foreach ($dir in @($scriptsDir, $hooksDir, $logsDir)) {
    if (-not (Test-Path $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
        Write-Host "[CREATED] $dir" -ForegroundColor Yellow
    }
}

# Clean up old probe log to avoid file-lock issues from stale handles
$oldLog = Join-Path $logsDir "dtc_probe_log.txt"
if (Test-Path $oldLog) {
    $backupLog = Join-Path $logsDir ("dtc_probe_log_backup_" + (Get-Date -Format "yyyyMMdd_HHmmss") + ".txt")
    Move-Item $oldLog $backupLog -Force
    Write-Host "[BACKUP] Old probe log moved to: $backupLog" -ForegroundColor Yellow
}

# --- Remove any old v1/v2 files that might still be present ---
$oldFiles = @(
    (Join-Path $scriptsDir "dtc_export_probe.lua"),
    (Join-Path $hooksDir "dtc_hooks_probe.lua"),
    (Join-Path $hooksDir "dtc_tempdir_probe.lua")
)
foreach ($old in $oldFiles) {
    if (Test-Path $old) {
        Remove-Item $old -Force
        Write-Host "[CLEANED] Removed old: $old" -ForegroundColor Gray
    }
}

# --- Install Hook Probes (v8 round 2: self-contained, deep probing) ---
Write-Host ""
Write-Host "--- Hook probes (into Scripts\Hooks\) ---" -ForegroundColor Cyan

$hookFiles = @("dtc_hooks_probe.lua", "dtc_tempdir_probe.lua")
foreach ($hf in $hookFiles) {
    $src = Join-Path $scriptDir $hf
    $dst = Join-Path $hooksDir $hf
    if (-not (Test-Path $src)) { Write-Host "[ERROR] Missing: $src" -ForegroundColor Red; exit 1 }
    Copy-Item $src $dst -Force
    Write-Host "[INSTALLED] $dst" -ForegroundColor Green
}

# --- Clean up old worker files from v3-v6 ---
$oldWorkers = @(
    (Join-Path $scriptsDir "dtc_hooks_worker.lua"),
    (Join-Path $scriptsDir "dtc_tempdir_worker.lua")
)
foreach ($old in $oldWorkers) {
    if (Test-Path $old) {
        Remove-Item $old -Force
        Write-Host "[CLEANED] Removed old worker: $old" -ForegroundColor Gray
    }
}

# --- Install Export Probe ---
Write-Host ""
Write-Host "--- Export probe ---" -ForegroundColor Cyan

$exportProbeSrc  = Join-Path $scriptDir "dtc_export_probe.lua"
$exportProbeDest = Join-Path $scriptsDir "dtc_export_probe.lua"
Copy-Item $exportProbeSrc $exportProbeDest -Force
Write-Host "[INSTALLED] $exportProbeDest" -ForegroundColor Green

# Handle Export.lua
$exportLua = Join-Path $scriptsDir "Export.lua"
$snippetFile = Join-Path $scriptDir "export_lua_snippet.lua"
$snippetContent = Get-Content $snippetFile -Raw
$markerStart = "-- DTC Export Probe loader"
$markerEnd   = "-- END DTC Export Probe loader"

if (Test-Path $exportLua) {
    $existingContent = Get-Content $exportLua -Raw
    if ($existingContent -match [regex]::Escape($markerStart)) {
        # Replace old snippet with new one (v2 has diagnostic breadcrumbs)
        $backupPath = Join-Path $scriptsDir "Export.lua.dtcprobe.bak"
        Copy-Item $exportLua $backupPath -Force
        Write-Host "[BACKUP] Export.lua -> $backupPath" -ForegroundColor Yellow
        $pattern = [regex]::Escape($markerStart) + "[\s\S]*?" + [regex]::Escape($markerEnd)
        $newContent = [regex]::Replace($existingContent, $pattern, $snippetContent.TrimEnd())
        Set-Content -Path $exportLua -Value $newContent -Encoding UTF8
        Write-Host "[REPLACED] Updated probe snippet in Export.lua (v2)" -ForegroundColor Green
    } else {
        $backupPath = Join-Path $scriptsDir "Export.lua.dtcprobe.bak"
        Copy-Item $exportLua $backupPath -Force
        Write-Host "[BACKUP] Export.lua -> $backupPath" -ForegroundColor Yellow
        $newContent = $existingContent.TrimEnd() + "`n`n" + $snippetContent
        Set-Content -Path $exportLua -Value $newContent -Encoding UTF8
        Write-Host "[UPDATED] Appended probe loader to Export.lua" -ForegroundColor Green
    }
} else {
    Set-Content -Path $exportLua -Value $snippetContent -Encoding UTF8
    Write-Host "[CREATED] Export.lua with probe loader" -ForegroundColor Green
}

# --- Summary ---
Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Installation Complete (v8 round 2)" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Installed files:" -ForegroundColor White
Write-Host "  Hooks:   $hooksDir\dtc_hooks_probe.lua   (self-contained, deferred)"
Write-Host "  Hooks:   $hooksDir\dtc_tempdir_probe.lua  (self-contained, deferred)"
Write-Host "  Scripts: $scriptsDir\dtc_export_probe.lua   (export probe)"
Write-Host "  Export:  $exportLua (snippet appended)"
Write-Host ""
Write-Host "Log output: $(Join-Path $logsDir 'dtc_probe_log.txt')" -ForegroundColor White
Write-Host ""
Write-Host "To uninstall: .\uninstall.ps1" -ForegroundColor Gray

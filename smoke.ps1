$ErrorActionPreference = "Stop"

Write-Host "=== FRBOT SMOKE TEST ===" -ForegroundColor Cyan

$repoRoot = $PSScriptRoot
if (-not $repoRoot) { $repoRoot = (Get-Location).Path }

$diagnosticsDir = Join-Path $repoRoot "diagnostics"

# Ensure diagnostics dir exists for checks.
if (!(Test-Path $diagnosticsDir)) {
    New-Item -ItemType Directory -Path $diagnosticsDir | Out-Null
}

$fatalPath = Join-Path $diagnosticsDir "fatal.log"
$runtimePath = Join-Path $diagnosticsDir "runtime.log"

# 1) REAL MODE: debe ABORTAR y escribir fatal.log
Write-Host "`n[1/2] REAL mode (expect ABORT + fatal.log)" -ForegroundColor Yellow
$env:FRBOT_MODE = "real"

if (Test-Path $fatalPath) { Remove-Item -Force $fatalPath }
if (Test-Path $runtimePath) { Remove-Item -Force $runtimePath }

& python (Join-Path $repoRoot "main.py")
$code = $LASTEXITCODE

if ($code -eq 0) {
    # Real adapters verified -> must produce runtime evidence.
    if (!(Test-Path $runtimePath)) {
        Write-Host "ERROR: runtime.log not found after real-mode success" -ForegroundColor Red
        exit 1
    }
    $runtimeTextReal = Get-Content -Raw $runtimePath
    if ($runtimeTextReal -notmatch "tick_count=") {
        Write-Host "ERROR: runtime.log missing tick_count evidence (real)" -ForegroundColor Red
        exit 1
    }
    Write-Host "OK: real mode ran with verified adapters" -ForegroundColor Green
} else {
    # Real preflight failed -> must produce fatal evidence and NOT create runtime.log.
    if (!(Test-Path $fatalPath)) {
        Write-Host "ERROR: fatal.log not found after real-mode abort" -ForegroundColor Red
        exit 1
    }
    Write-Host "OK: fatal.log present" -ForegroundColor Green

    $fatalText = Get-Content -Raw $fatalPath
    if ($fatalText -notmatch "FATAL: .+") {
        Write-Host "ERROR: fatal.log missing reason string" -ForegroundColor Red
        exit 1
    }

    # runtime.log must exist ONLY if preflight passes. Real aborted => must not exist.
    if (Test-Path $runtimePath) {
        Write-Host "ERROR: runtime.log exists even though real preflight failed" -ForegroundColor Red
        exit 1
    }
}

# 2) MOCK MODE: debe correr limpio y salir 0
Write-Host "`n[2/2] MOCK mode (expect CLEAN run)" -ForegroundColor Yellow
$env:FRBOT_MODE = "mock"

if (Test-Path $fatalPath) { Remove-Item -Force $fatalPath }
if (Test-Path $runtimePath) { Remove-Item -Force $runtimePath }

& python (Join-Path $repoRoot "main.py")
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: mock mode did not exit cleanly" -ForegroundColor Red
    exit 1
}

if (!(Test-Path $runtimePath)) {
    Write-Host "ERROR: runtime.log not found after mock run" -ForegroundColor Red
    exit 1
}

$runtimeText = Get-Content -Raw $runtimePath
if ($runtimeText -notmatch "tick_count=") {
    Write-Host "ERROR: runtime.log missing tick_count evidence" -ForegroundColor Red
    exit 1
}

$matches = Select-String -Path $runtimePath -Pattern "tick_count=(\d+)" -AllMatches
$maxTick = 0
foreach ($m in $matches.Matches) {
    $v = [int]$m.Groups[1].Value
    if ($v -gt $maxTick) { $maxTick = $v }
}
if ($maxTick -lt 1) {
    Write-Host "ERROR: mock did not produce tick_count >= 1" -ForegroundColor Red
    exit 1
}

Write-Host "`n=== SMOKE PASSED ===" -ForegroundColor Green
exit 0

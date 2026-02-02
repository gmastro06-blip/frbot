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

function Get-RuntimeEvents {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    if (!(Test-Path $Path)) {
        return @()
    }

    $events = @()
    foreach ($line in (Get-Content -Path $Path -ErrorAction Stop)) {
        if ([string]::IsNullOrWhiteSpace($line)) { continue }
        try {
            $events += ($line | ConvertFrom-Json -ErrorAction Stop)
        } catch {
            # Keep behavior strict for CI: runtime.log must be valid JSONL.
            throw "runtime.log contains non-JSON line: $line"
        }
    }
    return $events
}

function Get-FatalPayload {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    if (!(Test-Path $Path)) {
        return $null
    }

    $raw = Get-Content -Raw -Path $Path -ErrorAction Stop
    if ([string]::IsNullOrWhiteSpace($raw)) {
        throw "fatal.log is empty"
    }
    return ($raw | ConvertFrom-Json -ErrorAction Stop)
}

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

    $events = Get-RuntimeEvents -Path $runtimePath
    $tickEvents = @($events | Where-Object { $_.event -eq 'tick' })
    if ($tickEvents.Count -lt 1) {
        Write-Host "ERROR: runtime.log missing tick events (real)" -ForegroundColor Red
        exit 1
    }

    $maxTick = 0
    foreach ($evt in $tickEvents) {
        if ($null -ne $evt.tick_count) {
            $v = [int]$evt.tick_count
            if ($v -gt $maxTick) { $maxTick = $v }
        }
    }
    if ($maxTick -lt 1) {
        Write-Host "ERROR: runtime.log missing tick_count >= 1 (real)" -ForegroundColor Red
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

    $fatal = Get-FatalPayload -Path $fatalPath
    if ($null -eq $fatal) {
        Write-Host "ERROR: fatal.log could not be parsed" -ForegroundColor Red
        exit 1
    }
    if ($fatal.level -ne 'FATAL') {
        Write-Host "ERROR: fatal.log payload missing level=FATAL" -ForegroundColor Red
        exit 1
    }
    if ([string]::IsNullOrWhiteSpace([string]$fatal.reason)) {
        Write-Host "ERROR: fatal.log missing reason" -ForegroundColor Red
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

$events = Get-RuntimeEvents -Path $runtimePath
$tickEvents = @($events | Where-Object { $_.event -eq 'tick' })
if ($tickEvents.Count -lt 1) {
    Write-Host "ERROR: runtime.log missing tick events (mock)" -ForegroundColor Red
    exit 1
}

$maxTick = 0
foreach ($evt in $tickEvents) {
    if ($null -ne $evt.tick_count) {
        $v = [int]$evt.tick_count
        if ($v -gt $maxTick) { $maxTick = $v }
    }
}
if ($maxTick -lt 1) {
    Write-Host "ERROR: mock did not produce tick_count >= 1" -ForegroundColor Red
    exit 1
}

Write-Host "`n=== SMOKE PASSED ===" -ForegroundColor Green
exit 0

param(
    [string]$Mode = "cavebot",          # targeting | healing | combat | cavebot | looting | deposit | trade
    [string]$Backend = "real",          # real | mock
    [int]$TimeoutSec = 20,
    [string]$ConfigPath = ""
)

$ErrorActionPreference = "Stop"

$root = $PSScriptRoot
if (-not $root) { $root = (Get-Location).Path }
Set-Location $root

function Resolve-ConfigPath([string]$p) {
    if (-not $p) { return "" }
    if ([System.IO.Path]::IsPathRooted($p)) { return $p }
    return (Join-Path $root $p)
}

function Get-PythonCommand {
    $venvPython = Join-Path $root ".venv\Scripts\python.exe"
    if (Test-Path $venvPython) {
        return @{ File = $venvPython; ArgsPrefix = @() }
    }

    $poetry = Get-Command poetry -ErrorAction SilentlyContinue
    if ($poetry) {
        return @{ File = "poetry"; ArgsPrefix = @("run", "python") }
    }

    return @{ File = "python"; ArgsPrefix = @() }
}

$Mode = [string]$Mode
if (-not $Mode) { $Mode = "cavebot" }
$Mode = $Mode.Trim().ToLowerInvariant()

$Backend = [string]$Backend
if (-not $Backend) { $Backend = "real" }
$Backend = $Backend.Trim().ToLowerInvariant()

$validModes = @("targeting","healing","combat","cavebot","looting","deposit","trade")
if (-not ($validModes -contains $Mode)) {
    Write-Host "ERROR: invalid -Mode '$Mode'" -ForegroundColor Red
    Write-Host "Allowed: $($validModes -join ', ')" -ForegroundColor Yellow
    exit 2
}

if ($Backend -ne "real" -and $Backend -ne "mock") {
    Write-Host "ERROR: invalid -Backend '$Backend' (real|mock)" -ForegroundColor Red
    exit 2
}

$configResolved = Resolve-ConfigPath $ConfigPath

if ($Backend -eq "real" -and (-not $configResolved)) {
    # Hard fail early: real mode requires an ROI config.
    Write-Host "ERROR: real mode requires -ConfigPath (ROI JSON with rois{})" -ForegroundColor Red
    Write-Host "Example: .\calibrate_and_diagnose.ps1 -Mode cavebot -Backend real -ConfigPath .\my_rois.json" -ForegroundColor Yellow
    exit 2
}

# ---- Environment ----
$env:FRBOT_MODE = $Mode
$env:FRBOT_DUMP_FRAMES = "1"

# Backend selection for all gates (safe even if not used).
$env:FRBOT_TARGETING_BACKEND = $Backend
$env:FRBOT_CAVEBOT_BACKEND = $Backend
$env:FRBOT_LOOTING_BACKEND = $Backend
$env:FRBOT_TRADE_BACKEND = $Backend
$env:FRBOT_DEPOSIT_BACKEND = $Backend
$env:FRBOT_HEALING_BACKEND = $Backend
$env:FRBOT_COMBAT_BACKEND = $Backend

# Keep runs short and deterministic.
$env:FRBOT_TARGETING_MAX_TICKS = "1"
$env:FRBOT_HEALING_MAX_TICKS = "1"
$env:FRBOT_COMBAT_MAX_TICKS = "1"
$env:FRBOT_CAVEBOT_MAX_TICKS = "1"
$env:FRBOT_LOOTING_MAX_TICKS = "1"
$env:FRBOT_DEPOSIT_MAX_TICKS = "1"
$env:FRBOT_TRADE_MAX_TICKS = "1"

# ROI config path (required for real).
if ($configResolved) {
    $env:FRBOT_CONFIG_PATH = $configResolved
}

# ---- Clean diagnostics (runtime artifacts only; do NOT delete the diagnostics package) ----
$diag = Join-Path $root "diagnostics"
if (-not (Test-Path $diag)) {
    New-Item -ItemType Directory -Path $diag | Out-Null
}

$fatal = Join-Path $diag "fatal.log"
$runtime = Join-Path $diag "runtime.log"
$frames = Join-Path $diag "frames"

if (Test-Path $fatal) { Remove-Item -Force $fatal }
if (Test-Path $runtime) { Remove-Item -Force $runtime }
if (Test-Path $frames) { Remove-Item -Recurse -Force $frames }

Write-Host "== Fenril Calibration ==" -ForegroundColor Cyan
Write-Host "Mode      : $Mode"
Write-Host "Backend   : $Backend"
Write-Host "Timeout   : ${TimeoutSec}s"
if ($configResolved) {
    Write-Host "Config    : $configResolved"
}
Write-Host ""

$py = Get-PythonCommand
$pyFile = $py.File
$pyPrefix = @()
if ($py.ContainsKey('ArgsPrefix')) {
    if ($py.ArgsPrefix -is [System.Array]) {
        $pyPrefix = @($py.ArgsPrefix | Where-Object { $_ -ne $null -and $_ -ne '' })
    } elseif ($py.ArgsPrefix) {
        $pyPrefix = @([string]$py.ArgsPrefix)
    }
}

function Run-ProcessWithTimeout([string]$file, [object[]]$argumentList, [int]$timeoutSec) {
    $flat = New-Object System.Collections.Generic.List[string]
    foreach ($a in $argumentList) {
        if ($a -eq $null) { continue }
        if ($a -is [System.Array]) {
            foreach ($aa in $a) {
                if ($aa -eq $null) { continue }
                $s = [string]$aa
                if ($s -ne "") { $flat.Add($s) }
            }
            continue
        }
        $s = [string]$a
        if ($s -ne "") { $flat.Add($s) }
    }
    if ($flat.Count -le 0) {
        throw "ArgumentList is empty after sanitization"
    }

    function Quote-Arg([string]$s) {
        if ($null -eq $s) { return '' }
        if ($s -notmatch '[\s\"]') { return $s }
        $escaped = $s -replace '(\\*)\"', '$1$1\"'
        $escaped = $escaped -replace '(\\+)$', '$1$1'
        return '"' + $escaped + '"'
    }

    $argStr = ($flat.ToArray() | ForEach-Object { Quote-Arg $_ }) -join ' '

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = [string]$file
    $psi.Arguments = [string]$argStr
    $psi.WorkingDirectory = [string]$root
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true

    $proc = New-Object System.Diagnostics.Process
    $proc.StartInfo = $psi

    if (-not $proc.Start()) {
        throw "Failed to start process: $file"
    }

    $ok = $proc.WaitForExit([int]($timeoutSec * 1000))
    if (-not $ok) {
        try { $proc.Kill() } catch { }
        return [pscustomobject]@{ ExitCode = 999; TimedOut = $true }
    }

    return [pscustomobject]@{ ExitCode = [int]$proc.ExitCode; TimedOut = $false }
}

# ---- Step 1: capture idle baseline (no input) ----
Write-Host "[1/3] Capturing idle baseline frames..." -ForegroundColor Yellow
$idleArgs = @()
$idleArgs += $pyPrefix
$idleArgs += @("-m", "diagnostics.idle_capture", "--backend", $Backend, "--gate", $Mode)
if ($configResolved) {
    $idleArgs += @("--config", $configResolved)
}
$idleRes = Run-ProcessWithTimeout -file $pyFile -argumentList $idleArgs -timeoutSec ([Math]::Max(5, [Math]::Min($TimeoutSec, 15)))
if ($idleRes.TimedOut) {
    Write-Host "TIMEOUT: idle capture killed" -ForegroundColor Red
}

# ---- Step 2: run selected gate with frame dumps enabled ----
Write-Host "[2/3] Running gate (expect abort for calibration)..." -ForegroundColor Yellow
$mainArgs = @()
$mainArgs += $pyPrefix
$mainArgs += @((Join-Path $root "main.py"))

$runRes = Run-ProcessWithTimeout -file $pyFile -argumentList $mainArgs -timeoutSec $TimeoutSec
if ($runRes.TimedOut) {
    Write-Host "TIMEOUT: main process killed" -ForegroundColor Red
}

$exitCode = [int]$runRes.ExitCode

# ---- Step 3: summarize + optional audit ----
Write-Host ""
Write-Host "== RESULT ==" -ForegroundColor Cyan
Write-Host "Exit code: $exitCode"

if (Test-Path $fatal) {
    Write-Host "ABORT" -ForegroundColor Red
    Write-Host "fatal.log (first 30 lines):" -ForegroundColor DarkGray
    Get-Content $fatal | Select-Object -First 30
} elseif (Test-Path $runtime) {
    Write-Host "RUNTIME OK" -ForegroundColor Green
    Write-Host "runtime.log (last 15 lines):" -ForegroundColor DarkGray
    Get-Content $runtime | Select-Object -Last 15
} else {
    Write-Host "⚠️ No logs produced (unexpected)" -ForegroundColor Yellow
}

if (Test-Path $frames) {
    $count = (Get-ChildItem $frames -Recurse -File | Measure-Object).Count
    Write-Host "Frames dumped: $count"
    Write-Host "Frames path: $frames"
}

# Run evidence-only auditor when frames exist and config is available.
if ((Test-Path $frames) -and $configResolved) {
    Write-Host ""
    Write-Host "== AUDIT (evidence-only) ==" -ForegroundColor Cyan
    $auditArgs = @()
    $auditArgs += $pyPrefix
    $auditArgs += @((Join-Path $root "diagnostics\real_mode_audit.py"), "--frames", $frames, "--config", $configResolved)

    try {
        & $pyFile @auditArgs
    } catch {
        Write-Host "Audit failed: $($_.Exception.Message)" -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "== END ==" -ForegroundColor Cyan
exit $exitCode

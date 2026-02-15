[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)]
  [string]$ObsSource,

  [Parameter(Mandatory = $false)]
  [string]$WindowTitle = "",

  [Parameter(Mandatory = $true)]
  [string]$ConfigPath,

  [Parameter(Mandatory = $false)]
  [string]$ProjectorTitle = "",

  [Parameter(Mandatory = $false)]
  [int]$MaxTicks = 30,

  [Parameter(Mandatory = $false)]
  [int]$GraceSeconds = 10,

  [Parameter(Mandatory = $false)]
  [switch]$StrictSafe,

  [Parameter(Mandatory = $false)]
  [switch]$DumpFrames
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

function Resolve-TibiaWindowTitle {
  param(
    [Parameter(Mandatory = $false)]
    [string]$PreferredTitle = ""
  )

  $windows = @(
    Get-Process |
      Where-Object { $_.MainWindowHandle -ne 0 -and -not [string]::IsNullOrWhiteSpace($_.MainWindowTitle) } |
      Select-Object -ExpandProperty MainWindowTitle
  )

  if (-not [string]::IsNullOrWhiteSpace($PreferredTitle)) {
    $needle = [string]$PreferredTitle
    $hit = $windows | Where-Object { [string]$_ -like ("*" + $needle + "*") } | Select-Object -First 1
    if (-not [string]::IsNullOrWhiteSpace([string]$hit)) {
      return [string]$hit
    }
  }

  $fallback = $windows | Where-Object { [string]$_ -like "Tibia -*" } | Select-Object -First 1
  if (-not [string]::IsNullOrWhiteSpace([string]$fallback)) {
    return [string]$fallback
  }

  return [string]$PreferredTitle
}

Set-Location -LiteralPath $PSScriptRoot

$resolvedConfig = [System.IO.Path]::GetFullPath((Resolve-Path -LiteralPath $ConfigPath).Path)
if (-not (Test-Path -LiteralPath $resolvedConfig)) {
  Write-Host "FINAL DECISION: NOT_OPERATIONAL_REAL"
  Write-Host "reason=config_not_found"
  exit 2
}

if ([string]::IsNullOrWhiteSpace($ProjectorTitle)) {
  $ProjectorTitle = "Proyector en ventana (Fuente) - $ObsSource"
}

$effectiveWindowTitle = Resolve-TibiaWindowTitle -PreferredTitle $WindowTitle
if ([string]::IsNullOrWhiteSpace($effectiveWindowTitle)) {
  Write-Host "FINAL DECISION: NOT_OPERATIONAL_REAL"
  Write-Host "reason=tibia_window_not_found"
  exit 2
}

Write-Host ("TibiaWindowResolved: " + $effectiveWindowTitle)

$ts = Get-Date -Format "yyyyMMdd-HHmmss"
$framesDir = Join-Path $PSScriptRoot ("diagnostics\frames_real\" + $ts)
New-Item -ItemType Directory -Path $framesDir -Force | Out-Null

$env:FRBOT_MODE = "real"
$env:FRBOT_PROFILE = "prod_emergency"
$env:FRBOT_CAPTURE_SOURCE = "obs"
$env:FRBOT_CAPTURE_BACKEND = "meld"
$env:FRBOT_OBS_SOURCE_NAME = [string]$ObsSource
$env:FRBOT_OBS_PROJECTOR_TITLE = [string]$ProjectorTitle
$env:FRBOT_WINDOW_TITLE = [string]$effectiveWindowTitle
$env:FRBOT_CONFIG_PATH = [string]$resolvedConfig
$env:FRBOT_REAL_FRAMES_DIR = [string]$framesDir
$env:FRBOT_DUMP_FRAMES = "0"
if ($DumpFrames.IsPresent) {
  $env:FRBOT_DUMP_FRAMES = "1"
}
$env:FRBOT_PROJECTOR_REQUIRE_FOREGROUND = "0"
$env:FRBOT_PROJECTOR_FOCUS_ON_START = "0"
$env:FRBOT_TRY_ALL_OUTPUTS = "1"
$env:FRBOT_MAX_OUTPUTS = "6"
$env:FRBOT_TARGETING_BACKEND = "real"
$env:FRBOT_HEALING_BACKEND = "real"
$env:FRBOT_CAVEBOT_BACKEND = "real"

if ([string]::IsNullOrWhiteSpace([string]$env:FRBOT_ROUTE_LOCALIZE_MIN_SCORE)) {
  $env:FRBOT_ROUTE_LOCALIZE_MIN_SCORE = "0.55"
}
if ([string]::IsNullOrWhiteSpace([string]$env:FRBOT_CAVEBOT_LOCALIZE_MIN_SCORE)) {
  $env:FRBOT_CAVEBOT_LOCALIZE_MIN_SCORE = "0.55"
}

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
  $python = "python"
}

$stdoutPath = Join-Path $framesDir "runner.stdout.log"
$stderrPath = Join-Path $framesDir "runner.stderr.log"

$scriptPath = Join-Path $PSScriptRoot "tools\validate_core_features.py"
$pythonArgs = @(
  $scriptPath,
  "--projector-title", [string]$ProjectorTitle,
  "--tibia-title", [string]$effectiveWindowTitle,
  "--config", [string]$resolvedConfig,
  "--frames-dir", [string]$framesDir,
  "--max-ticks", [string]$MaxTicks,
  "--grace-seconds", [string]$GraceSeconds
)

if ($StrictSafe.IsPresent) {
  $pythonArgs += "--strict-safe"
}

Push-Location $PSScriptRoot
try {
  & $python @pythonArgs 1> $stdoutPath 2> $stderrPath
  $runnerExit = [int]$LASTEXITCODE
}
finally {
  Pop-Location
}

$summaryPath = Join-Path $framesDir "validate_summary.json"
$finalDecision = "NOT_OPERATIONAL_REAL"
if (Test-Path -LiteralPath $summaryPath) {
  try {
    $summary = Get-Content -LiteralPath $summaryPath -Raw | ConvertFrom-Json
    $fd = [string]$summary.final_decision
    if ($fd -eq "OPERATIONAL_REAL") {
      $finalDecision = "OPERATIONAL_REAL"
    }
  }
  catch {
  }
}

Write-Host "Artifacts: $framesDir"
Write-Host "RunnerExit: $runnerExit"
Write-Host "Stdout: $stdoutPath"
Write-Host "Stderr: $stderrPath"
Write-Host ("FINAL DECISION: " + $finalDecision)

if ($finalDecision -eq "OPERATIONAL_REAL" -and $runnerExit -eq 0) {
  exit 0
}

exit 1
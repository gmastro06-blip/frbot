[Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSAvoidAssignmentToAutomaticVariable', '', Justification='False positive from analyzer')]
[CmdletBinding()]
param(
  [Parameter(Mandatory = $false)]
  [ValidateSet("mock", "real")]
  [string]$Mode = "mock",

  [Parameter(Mandatory = $false)]
  [string]$ObsSource,

  [Parameter(Mandatory = $false)]
  [string]$WindowTitle,

  [Parameter(Mandatory = $false)]
  [string]$WindowHwnd,

  [Parameter(Mandatory = $false)]
  [string]$ConfigPath,

  [Parameter(Mandatory = $false)]
  [switch]$TryFocus
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

  return ""
}

Set-Location -LiteralPath $PSScriptRoot

try {
  # Isolar entorno FRBOT para no contaminar pytest u otras ejecuciones.
  $toClear = Get-ChildItem Env: | Where-Object { $_.Name -like 'FRBOT_*' } | ForEach-Object { $_.Name }
  foreach ($name in @($toClear)) {
    Remove-Item -Path ("Env:" + $name) -ErrorAction SilentlyContinue
  }

  if ($Mode -eq "mock") {
    $env:FRBOT_VALIDATE_MODE = "mock"
    $env:FRBOT_MODE = "mock"
    $env:FRBOT_PROFILE = "prod_emergency"
  }
  else {
    $effectiveWindowTitle = ""
    $effectiveProjectorTitle = ""

    $env:FRBOT_VALIDATE_MODE = "real"
    $env:FRBOT_MODE = "prod_full"
    $env:FRBOT_PROFILE = "prod_full"
    $env:FRBOT_CAPTURE_SOURCE = "obs_source"

    if ([string]::IsNullOrWhiteSpace($ObsSource)) { throw "real_missing_obs_source" }
    if ([string]::IsNullOrWhiteSpace($ConfigPath)) { throw "real_missing_config_path" }
    $effectiveProjectorTitle = [string]$env:FRBOT_OBS_PROJECTOR_TITLE
    if ([string]::IsNullOrWhiteSpace($effectiveProjectorTitle)) {
      $effectiveProjectorTitle = "Proyector en ventana (Fuente) - " + [string]$ObsSource
    }

    if ([string]::IsNullOrWhiteSpace($WindowHwnd)) {
      $effectiveWindowTitle = Resolve-TibiaWindowTitle -PreferredTitle $WindowTitle
      if ([string]::IsNullOrWhiteSpace($effectiveWindowTitle)) { throw "real_missing_window_selector" }
      Write-Host ("TibiaWindowResolved: " + $effectiveWindowTitle)
    }

    $env:FRBOT_OBS_SOURCE_NAME = [string]$ObsSource
    $env:FRBOT_CONFIG_PATH = [string]$ConfigPath
    $env:FRBOT_ALLOW_BACKGROUND_INPUT = "1"
    $env:FRBOT_INPUT_METHOD = "postmessage"
    $env:FRBOT_COMBO_METHOD = "postmessage"
    if (-not [string]::IsNullOrWhiteSpace($effectiveWindowTitle)) {
      $env:FRBOT_WINDOW_TITLE = [string]$effectiveWindowTitle
    }
    elseif (-not [string]::IsNullOrWhiteSpace($WindowTitle)) {
      $env:FRBOT_WINDOW_TITLE = [string]$WindowTitle
    }
    if (-not [string]::IsNullOrWhiteSpace($WindowHwnd)) { $env:FRBOT_WINDOW_HWND = [string]$WindowHwnd }
    $env:FRBOT_TRY_FOCUS = "1"
  }

  $poetryCli = @("run", "python", ".\tools\validate_core_features.py")
  if ($Mode -eq "real") {
    $resolvedTitle = [string]$env:FRBOT_WINDOW_TITLE
    if ([string]::IsNullOrWhiteSpace($resolvedTitle)) { $resolvedTitle = [string]$WindowTitle }
    if ([string]::IsNullOrWhiteSpace($resolvedTitle)) { throw "real_missing_window_selector" }
    $projectorTitle = [string]$effectiveProjectorTitle
    if ([string]::IsNullOrWhiteSpace($projectorTitle)) { throw "real_missing_projector_title" }

    $poetryCli += @(
      "--projector-title", [string]$projectorTitle,
      "--tibia-title", [string]$resolvedTitle,
      "--config", [string]$ConfigPath,
      "--strict-safe",
      "--max-ticks", "30",
      "--grace-seconds", "10"
    )
  }
  else {
    $poetryCli += @("--mode", [string]$Mode)
  }

  $out = & poetry @poetryCli 2>&1
  $code = [int]$LASTEXITCODE

  $outLines = @($out | ForEach-Object { [string]$_ })
  $line = ($outLines | Where-Object { $_ -match "^FINAL_DECISION:\s*(OPERATIONAL_REAL|OPERATIONAL|NOT_OPERATIONAL_REAL(?::.*)?|NOT_OPERATIONAL:.*)" } | Select-Object -Last 1)

  if (-not $line) {
    Write-Output "VALIDATE_NO_GO:internal_error"
    exit 2
  }

  if ($line -match "FINAL_DECISION:\s*(OPERATIONAL|OPERATIONAL_REAL)") {
    Write-Output "VALIDATE_GO"
    exit 0
  }

  $reason = ($line -replace "^FINAL_DECISION:\s*", "").Trim()
  if ($reason -like "NOT_OPERATIONAL:*") {
    $reason = ($reason -replace "^NOT_OPERATIONAL:", "").Trim()
  }
  elseif ($reason -eq "NOT_OPERATIONAL_REAL") {
    $reason = "not_operational_real"
  }
  elseif ($reason -like "NOT_OPERATIONAL_REAL:*") {
    $reason = ($reason -replace "^NOT_OPERATIONAL_REAL:", "").Trim()
  }
  if ([string]::IsNullOrWhiteSpace($reason)) { $reason = "unknown" }
  Write-Output ("VALIDATE_NO_GO:" + $reason)
  if ($code -eq 0) { exit 2 }
  exit $code
}
catch {
  Write-Output "VALIDATE_NO_GO:internal_error"
  exit 2
}

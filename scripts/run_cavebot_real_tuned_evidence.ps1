[CmdletBinding()]
param(
  [Parameter(Mandatory = $false)]
  [string]$ObsSourceName = "Tibia_Fuente",

  [Parameter(Mandatory = $false)]
  [string]$ConfigPath = "config/rois_prod_full.json",

  [Parameter(Mandatory = $false)]
  [string]$WindowTitle = "Tibia - Onniwabanshu",

  [Parameter(Mandatory = $false)]
  [string]$WindowHwnd = "0x709d0",

  [Parameter(Mandatory = $false)]
  [switch]$TryFocus,

  [Parameter(Mandatory = $false)]
  [string]$AutoRouteDxPx = "2",

  [Parameter(Mandatory = $false)]
  [string]$AutoRouteRadiusPx = "3",

  [Parameter(Mandatory = $false)]
  [string]$AutoRouteMaxTicks = "40",

  [Parameter(Mandatory = $false)]
  [string]$StuckWindow = "10"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location -LiteralPath $repoRoot

$ts = Get-Date -Format 'yyyyMMdd_HHmmss'
$ev = Join-Path $repoRoot ("diagnostics/validate_real_cavebot_tuned_" + $ts)
New-Item -ItemType Directory -Force -Path $ev | Out-Null

$env:FRBOT_MODE = 'real'
$env:FRBOT_PROFILE = 'prod_full'
$env:FRBOT_CAPTURE_SOURCE = 'obs_source'
$env:FRBOT_OBS_SOURCE_NAME = [string]$ObsSourceName
$env:FRBOT_CONFIG_PATH = [string]$ConfigPath
$env:FRBOT_WINDOW_TITLE = [string]$WindowTitle
$env:FRBOT_WINDOW_HWND = [string]$WindowHwnd
$env:FRBOT_REAL_FRAMES_DIR = [string]$ev
$env:FRBOT_CAVEBOT_FULL_BACKEND = 'real'
$env:FRBOT_CAVEBOT_AUTO_ROUTE = '1'
$env:FRBOT_CAVEBOT_AUTO_ROUTE_DX_PX = [string]$AutoRouteDxPx
$env:FRBOT_CAVEBOT_AUTO_ROUTE_RADIUS_PX = [string]$AutoRouteRadiusPx
$env:FRBOT_CAVEBOT_AUTO_ROUTE_MAX_TICKS = [string]$AutoRouteMaxTicks
$env:FRBOT_CAVEBOT_STUCK_WINDOW = [string]$StuckWindow
$env:FRBOT_TRY_FOCUS = $(if ($TryFocus.IsPresent) { '1' } else { '0' })

poetry run python -c "from cavebot_full_entrypoint import run_cavebot_full_only; raise SystemExit(run_cavebot_full_only())"
$code = [int]$LASTEXITCODE

Write-Output ("RUN_EXIT=" + $code)
Write-Output ("EVIDENCE_DIR=" + $ev)
Get-ChildItem -Path $ev | Select-Object Name,Length | Sort-Object Name | Format-Table -AutoSize

exit $code

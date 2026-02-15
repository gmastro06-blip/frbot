[CmdletBinding()]
param(
  [Parameter(Mandatory = $false)]
  [string]$ScriptPath = "Waypoints/script_base_15_right_magic5_loop.json",

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
  [int]$RadiusPx = 3,

  [Parameter(Mandatory = $false)]
  [int]$MaxTicksPerWp = 40
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location -LiteralPath $repoRoot

$scriptAbs = (Resolve-Path -LiteralPath $ScriptPath).Path
$cfgAbs = (Resolve-Path -LiteralPath $ConfigPath).Path

$ts = Get-Date -Format 'yyyyMMdd_HHmmss'
$ev = Join-Path $repoRoot ("diagnostics/validate_real_cavebot_script15_" + $ts)
New-Item -ItemType Directory -Force -Path $ev | Out-Null

$data = Get-Content -Raw -Path $scriptAbs | ConvertFrom-Json
$wps = @()
$idx = 0
foreach ($wp in $data.waypoints) {
  if ($null -ne $wp.enabled -and $wp.enabled -eq $false) { continue }
  $wps += @{
    waypoint_id = ("loop_" + $idx)
    x = [int]$wp.x
    y = [int]$wp.y
    z = [int]$wp.z
    radius_px = [int]$RadiusPx
    max_ticks = [int]$MaxTicksPerWp
  }
  $idx++
}

$env:FRBOT_MODE = 'real'
$env:FRBOT_PROFILE = 'prod_full'
$env:FRBOT_CAPTURE_SOURCE = 'obs_source'
$env:FRBOT_OBS_SOURCE_NAME = [string]$ObsSourceName
$env:FRBOT_CONFIG_PATH = [string]$cfgAbs
$env:FRBOT_WINDOW_TITLE = [string]$WindowTitle
$env:FRBOT_WINDOW_HWND = [string]$WindowHwnd
$env:FRBOT_REAL_FRAMES_DIR = [string]$ev
$env:FRBOT_CAVEBOT_FULL_BACKEND = 'real'
$env:FRBOT_CAVEBOT_AUTO_ROUTE = '0'
$env:FRBOT_CAVEBOT_WAYPOINTS = ($wps | ConvertTo-Json -Compress)
$env:FRBOT_TRY_FOCUS = $(if ($TryFocus.IsPresent) { '1' } else { '0' })

poetry run python -c "from cavebot_full_entrypoint import run_cavebot_full_only; raise SystemExit(run_cavebot_full_only())"
$code = [int]$LASTEXITCODE

Write-Output ("RUN_EXIT=" + $code)
Write-Output ("SCRIPT_USED=" + $scriptAbs)
Write-Output ("WAYPOINTS_EMITTED=" + $idx)
Write-Output ("EVIDENCE_DIR=" + $ev)
Get-ChildItem -Path $ev | Select-Object Name,Length | Sort-Object Name | Format-Table -AutoSize

exit $code

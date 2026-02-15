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
  [int]$StartX = 62,

  [Parameter(Mandatory = $false)]
  [int]$StartY = 38,

  [Parameter(Mandatory = $false)]
  [int]$StepPx = 1,

  [Parameter(Mandatory = $false)]
  [int]$RadiusPx = 2,

  [Parameter(Mandatory = $false)]
  [int]$MaxTicksPerWp = 35

  ,
  [Parameter(Mandatory = $false)]
  [int]$PostMoveDelayMs = 260

  ,
  [Parameter(Mandatory = $false)]
  [int]$RealMoveSettleMs = 420

  ,
  [Parameter(Mandatory = $false)]
  [int]$RealMovePollMs = 35

  ,
  [Parameter(Mandatory = $false)]
  [int]$TickHz = 14
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location -LiteralPath $repoRoot

$scriptAbs = (Resolve-Path -LiteralPath $ScriptPath).Path
$cfgAbs = (Resolve-Path -LiteralPath $ConfigPath).Path

$ts = Get-Date -Format 'yyyyMMdd_HHmmss'
$ev = Join-Path $repoRoot ("diagnostics/validate_real_cavebot_script15_moves_" + $ts)
New-Item -ItemType Directory -Force -Path $ev | Out-Null

$data = Get-Content -Raw -Path $scriptAbs | ConvertFrom-Json
$wps = @()
$idx = 0
$x = [int]$StartX
$y = [int]$StartY

foreach ($wp in $data.waypoints) {
  if ($null -ne $wp.enabled -and $wp.enabled -eq $false) { continue }
  if ([string]$wp.type -ne 'single_move') { continue }

  $dir = ''
  if ($null -ne $wp.options -and $null -ne $wp.options.direction) {
    $dir = ([string]$wp.options.direction).Trim().ToLowerInvariant()
  }

  switch ($dir) {
    'east'  { $x += [int]$StepPx }
    'west'  { $x -= [int]$StepPx }
    'north' { $y -= [int]$StepPx }
    'south' { $y += [int]$StepPx }
    default { continue }
  }

  if ($x -lt 1) { $x = 1 }
  if ($x -gt 106) { $x = 106 }
  if ($y -lt 1) { $y = 1 }
  if ($y -gt 113) { $y = 113 }

  $wps += @{
    waypoint_id = ("move_" + $idx)
    x = [int]$x
    y = [int]$y
    z = 7
    radius_px = [int]$RadiusPx
    max_ticks = [int]$MaxTicksPerWp
  }
  $idx++
}

if ($idx -le 0) {
  throw "no_single_move_waypoints_found"
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
$env:FRBOT_POST_MOVE_DELAY_MS = [string]$PostMoveDelayMs
$env:FRBOT_REAL_MOVE_SETTLE_MS = [string]$RealMoveSettleMs
$env:FRBOT_REAL_MOVE_POLL_MS = [string]$RealMovePollMs
$env:FRBOT_TICK_HZ = [string]$TickHz

poetry run python -c "from cavebot_full_entrypoint import run_cavebot_full_only; raise SystemExit(run_cavebot_full_only())"
$code = [int]$LASTEXITCODE

Write-Output ("RUN_EXIT=" + $code)
Write-Output ("SCRIPT_USED=" + $scriptAbs)
Write-Output ("WAYPOINTS_EMITTED=" + $idx)
Write-Output ("EVIDENCE_DIR=" + $ev)
Get-ChildItem -Path $ev | Select-Object Name,Length | Sort-Object Name | Format-Table -AutoSize

exit $code

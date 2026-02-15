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
  [string]$StartXList = "",

  [Parameter(Mandatory = $false)]
  [string]$StartYList = "31,34,38,42,45",

  [Parameter(Mandatory = $false)]
  [string]$RadiusList = "2,4,8",

  [Parameter(Mandatory = $false)]
  [int]$StepPx = 1,

  [Parameter(Mandatory = $false)]
  [int]$MaxTicksPerWp = 60,

  [Parameter(Mandatory = $false)]
  [int]$StuckWindow = 20,

  [Parameter(Mandatory = $false)]
  [int]$PostMoveDelayMs = 260,

  [Parameter(Mandatory = $false)]
  [int]$RealMoveSettleMs = 420,

  [Parameter(Mandatory = $false)]
  [int]$RealMovePollMs = 35,

  [Parameter(Mandatory = $false)]
  [int]$TickHz = 14,

  [Parameter(Mandatory = $false)]
  [switch]$StopOnFirstSuccess,

  [Parameter(Mandatory = $false)]
  [switch]$RequireAutocalibSuccess,

  [Parameter(Mandatory = $false)]
  [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location -LiteralPath $repoRoot

if ($DryRun.IsPresent) {
  Write-Host "DRY_RUN: ejecutando solo autocalibración en dry-run" -ForegroundColor Yellow
  & "./scripts/run_cavebot_real_script15_autocalib.ps1" `
    -ScriptPath $ScriptPath `
    -ObsSourceName $ObsSourceName `
    -ConfigPath $ConfigPath `
    -WindowTitle $WindowTitle `
    -WindowHwnd $WindowHwnd `
    -TryFocus:$TryFocus.IsPresent `
    -StartX $StartX `
    -StartXList $StartXList `
    -StartYList $StartYList `
    -RadiusList $RadiusList `
    -StepPx $StepPx `
    -MaxTicksPerWp $MaxTicksPerWp `
    -StuckWindow $StuckWindow `
    -PostMoveDelayMs $PostMoveDelayMs `
    -RealMoveSettleMs $RealMoveSettleMs `
    -RealMovePollMs $RealMovePollMs `
    -TickHz $TickHz `
    -StopOnFirstSuccess:$StopOnFirstSuccess.IsPresent `
    -DryRun
  exit [int]$LASTEXITCODE
}

$beforeDirs = @()
if (Test-Path -LiteralPath "diagnostics") {
  $beforeDirs = @(Get-ChildItem -LiteralPath "diagnostics" -Directory | Where-Object { $_.Name -like 'autocalib_script15_*' } | ForEach-Object { $_.FullName })
}

Write-Host "[1/2] Ejecutando autocalibración..." -ForegroundColor Cyan
& "./scripts/run_cavebot_real_script15_autocalib.ps1" `
  -ScriptPath $ScriptPath `
  -ObsSourceName $ObsSourceName `
  -ConfigPath $ConfigPath `
  -WindowTitle $WindowTitle `
  -WindowHwnd $WindowHwnd `
  -TryFocus:$TryFocus.IsPresent `
  -StartX $StartX `
  -StartXList $StartXList `
  -StartYList $StartYList `
  -RadiusList $RadiusList `
  -StepPx $StepPx `
  -MaxTicksPerWp $MaxTicksPerWp `
  -StuckWindow $StuckWindow `
  -PostMoveDelayMs $PostMoveDelayMs `
  -RealMoveSettleMs $RealMoveSettleMs `
  -RealMovePollMs $RealMovePollMs `
  -TickHz $TickHz `
  -StopOnFirstSuccess:$StopOnFirstSuccess.IsPresent
$autoExit = [int]$LASTEXITCODE

$afterDirs = @()
if (Test-Path -LiteralPath "diagnostics") {
  $afterDirs = @(Get-ChildItem -LiteralPath "diagnostics" -Directory | Where-Object { $_.Name -like 'autocalib_script15_*' } | ForEach-Object { $_.FullName })
}

$newDirs = @($afterDirs | Where-Object { $_ -notin $beforeDirs })
$autocalibDir = ""
if ($newDirs.Count -gt 0) {
  $autocalibDir = ($newDirs | Sort-Object | Select-Object -Last 1)
} elseif ($afterDirs.Count -gt 0) {
  $autocalibDir = ($afterDirs | Sort-Object | Select-Object -Last 1)
}

if ([string]::IsNullOrWhiteSpace($autocalibDir)) {
  throw "autocalib_dir_not_found"
}

$bestPath = Join-Path $autocalibDir "best_config.json"
if (-not (Test-Path -LiteralPath $bestPath)) {
  throw "best_config_not_found: $bestPath"
}

$best = Get-Content -Raw -Path $bestPath | ConvertFrom-Json
$bestStartX = [int]$best.start_x
$bestStartY = [int]$best.start_y
$bestRadius = [int]$best.radius_px
$bestStep = [int]$best.step_px
$bestMaxTicks = [int]$best.max_ticks_per_wp
$bestReason = [string]$best.evidence_reason
$bestOk = [bool]$best.ok
$bestExit = [int]$best.run_exit

Write-Host "AUTOCALIB_DIR=$autocalibDir" -ForegroundColor Yellow
Write-Host "AUTOCALIB_BEST=$bestPath" -ForegroundColor Yellow
Write-Host ("AUTOCALIB_PICK: StartX={0} StartY={1} RadiusPx={2} StepPx={3} MaxTicks={4} Exit={5} Ok={6} Reason={7}" -f $bestStartX, $bestStartY, $bestRadius, $bestStep, $bestMaxTicks, $bestExit, $bestOk, $bestReason) -ForegroundColor Green

if ($RequireAutocalibSuccess.IsPresent -and -not $bestOk -and $bestExit -ne 0) {
  Write-Host "Autocalibración sin éxito; abortado por -RequireAutocalibSuccess" -ForegroundColor Red
  exit 1
}

Write-Host "[2/2] Ejecutando corrida final con mejor configuración..." -ForegroundColor Cyan
$env:FRBOT_CAVEBOT_STUCK_WINDOW = [string]$StuckWindow
& "./scripts/run_cavebot_real_script15_moves_only_evidence.ps1" `
  -ScriptPath $ScriptPath `
  -ObsSourceName $ObsSourceName `
  -ConfigPath $ConfigPath `
  -WindowTitle $WindowTitle `
  -WindowHwnd $WindowHwnd `
  -TryFocus:$TryFocus.IsPresent `
  -StartX $bestStartX `
  -StartY $bestStartY `
  -StepPx $bestStep `
  -RadiusPx $bestRadius `
  -MaxTicksPerWp $bestMaxTicks `
  -PostMoveDelayMs $PostMoveDelayMs `
  -RealMoveSettleMs $RealMoveSettleMs `
  -RealMovePollMs $RealMovePollMs `
  -TickHz $TickHz

$finalExit = [int]$LASTEXITCODE
Write-Host ("FINAL_EXIT={0}" -f $finalExit) -ForegroundColor Yellow
exit $finalExit

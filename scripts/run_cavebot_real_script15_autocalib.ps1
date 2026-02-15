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
  [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Parse-IntList {
  param([Parameter(Mandatory = $true)][string]$Raw)
  $items = @()
  foreach ($part in ($Raw -split ',')) {
    $t = [string]$part
    $t = $t.Trim()
    if ([string]::IsNullOrWhiteSpace($t)) { continue }
    $items += [int]$t
  }
  return ,$items
}

function Reason-Rank {
  param(
    [Parameter(Mandatory = $true)][bool]$Ok,
    [Parameter(Mandatory = $true)][string]$Reason,
    [Parameter(Mandatory = $true)][int]$ExitCode
  )

  if ($Ok -or $ExitCode -eq 0) { return 0 }

  switch ($Reason) {
    "cavebot_wrong_direction" { return 1 }
    "cavebot_stuck_detected" { return 2 }
    "max_ticks_reached" { return 3 }
    default { return 4 }
  }
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location -LiteralPath $repoRoot

$ys = Parse-IntList -Raw $StartYList
$rs = Parse-IntList -Raw $RadiusList
$xs = @([int]$StartX)
if (-not [string]::IsNullOrWhiteSpace([string]$StartXList)) {
  $xs = Parse-IntList -Raw $StartXList
}
if ($xs.Count -le 0) { throw "StartXList_empty" }
if ($ys.Count -le 0) { throw "StartYList_empty" }
if ($rs.Count -le 0) { throw "RadiusList_empty" }

$ts = Get-Date -Format 'yyyyMMdd_HHmmss'
$summaryDir = Join-Path $repoRoot ("diagnostics/autocalib_script15_" + $ts)
New-Item -ItemType Directory -Force -Path $summaryDir | Out-Null
$summaryFile = Join-Path $summaryDir "autocalib_summary.json"
$bestFile = Join-Path $summaryDir "best_config.json"

$results = @()
$runIndex = 0

foreach ($sx in $xs) {
  foreach ($sy in $ys) {
    foreach ($rad in $rs) {
    $runIndex++
    Write-Host ("RUN #{0}: StartX={1} StartY={2} Radius={3}" -f $runIndex, $sx, $sy, $rad) -ForegroundColor Cyan

    if ($DryRun.IsPresent) {
      $results += [pscustomobject]@{
        run_index = [int]$runIndex
        start_x = [int]$sx
        start_y = [int]$sy
        radius_px = [int]$rad
        step_px = [int]$StepPx
        max_ticks_per_wp = [int]$MaxTicksPerWp
        stuck_window = [int]$StuckWindow
        post_move_delay_ms = [int]$PostMoveDelayMs
        real_move_settle_ms = [int]$RealMoveSettleMs
        real_move_poll_ms = [int]$RealMovePollMs
        tick_hz = [int]$TickHz
        run_exit = -999
        ok = $false
        evidence_reason = "dry_run"
        actions_sent = 0
        evidence_dir = ""
        rank = 9
      }
      continue
    }

    $env:FRBOT_CAVEBOT_STUCK_WINDOW = [string]$StuckWindow

    $runOutputRaw = (& "./scripts/run_cavebot_real_script15_moves_only_evidence.ps1" `
      -ScriptPath $ScriptPath `
      -ObsSourceName $ObsSourceName `
      -ConfigPath $ConfigPath `
      -WindowTitle $WindowTitle `
      -WindowHwnd $WindowHwnd `
      -StartX $sx `
      -StartY $sy `
      -StepPx $StepPx `
      -RadiusPx $rad `
      -MaxTicksPerWp $MaxTicksPerWp `
      -PostMoveDelayMs $PostMoveDelayMs `
      -RealMoveSettleMs $RealMoveSettleMs `
      -RealMovePollMs $RealMovePollMs `
      -TickHz $TickHz `
      -TryFocus:$TryFocus.IsPresent 2>&1 | Out-String)

    $runOutput = @()
    if ($null -ne $runOutputRaw) {
      $runOutput = @([string]$runOutputRaw -split "`r?`n")
    }

    $exitCode = [int]$LASTEXITCODE
    foreach ($line in $runOutput) { Write-Host ([string]$line) }

    $evDir = ""
    foreach ($line in $runOutput) {
      $s = [string]$line
      if ($s -match '^EVIDENCE_DIR=(.+)$') {
        $evDir = [string]$Matches[1]
      }
    }

    $ok = $false
    $reason = "unknown"
    $actionsSent = 0
    if (-not [string]::IsNullOrWhiteSpace($evDir)) {
      $lastResult = Join-Path $evDir "cavebot_full_last_result.json"
      if (Test-Path -LiteralPath $lastResult) {
        try {
          $obj = Get-Content -Raw -Path $lastResult | ConvertFrom-Json
          $ok = [bool]$obj.ok
          $reason = [string]$obj.evidence_reason
          $actionsSent = [int]$obj.actions_sent
        } catch {
          $reason = "parse_error"
        }
      } else {
        $reason = "missing_last_result"
      }
    } else {
      $reason = "missing_evidence_dir"
    }

    $rank = Reason-Rank -Ok:$ok -Reason:$reason -ExitCode $exitCode

    $results += [pscustomobject]@{
      run_index = [int]$runIndex
      start_x = [int]$sx
      start_y = [int]$sy
      radius_px = [int]$rad
      step_px = [int]$StepPx
      max_ticks_per_wp = [int]$MaxTicksPerWp
      stuck_window = [int]$StuckWindow
      post_move_delay_ms = [int]$PostMoveDelayMs
      real_move_settle_ms = [int]$RealMoveSettleMs
      real_move_poll_ms = [int]$RealMovePollMs
      tick_hz = [int]$TickHz
      run_exit = [int]$exitCode
      ok = [bool]$ok
      evidence_reason = [string]$reason
      actions_sent = [int]$actionsSent
      evidence_dir = [string]$evDir
      rank = [int]$rank
    }

    if ($StopOnFirstSuccess.IsPresent -and ($ok -or $exitCode -eq 0)) {
      Write-Host "StopOnFirstSuccess activado; terminando barrido." -ForegroundColor Green
      break
    }
  }
  }

  if ($StopOnFirstSuccess.IsPresent) {
    $anySuccess = $results | Where-Object { [bool]$_.ok -or [int]$_.run_exit -eq 0 }
    if ($null -ne $anySuccess -and @($anySuccess).Count -gt 0) { break }
  }
}

if (@($results).Count -le 0) {
  throw "autocalib_no_runs"
}

$ordered = @(
  $results |
    Sort-Object -Property @{Expression = { [int]$_.rank }; Ascending = $true }, @{Expression = { -[int]$_.actions_sent }; Ascending = $true }, @{Expression = { [int]$_.run_index }; Ascending = $true }
)
$best = $ordered[0]

$results | ConvertTo-Json -Depth 6 | Set-Content -Encoding UTF8 -Path $summaryFile
$best | ConvertTo-Json -Depth 6 | Set-Content -Encoding UTF8 -Path $bestFile

Write-Host "" 
Write-Host "AUTOCALIB_SUMMARY_DIR=$summaryDir" -ForegroundColor Yellow
Write-Host "AUTOCALIB_SUMMARY_FILE=$summaryFile" -ForegroundColor Yellow
Write-Host "AUTOCALIB_BEST_FILE=$bestFile" -ForegroundColor Yellow
Write-Host ("BEST: StartX={0} StartY={1} RadiusPx={2} Exit={3} Ok={4} Reason={5} Actions={6}" -f $best.start_x, $best.start_y, $best.radius_px, $best.run_exit, $best.ok, $best.evidence_reason, $best.actions_sent) -ForegroundColor Green
Write-Host "" 
Write-Host "Recomendado para siguiente corrida:" -ForegroundColor Cyan
Write-Host ("./scripts/run_cavebot_real_script15_moves_only_evidence.ps1 -TryFocus -StartX {0} -StartY {1} -RadiusPx {2} -StepPx {3} -MaxTicksPerWp {4}" -f $best.start_x, $best.start_y, $best.radius_px, $best.step_px, $best.max_ticks_per_wp)

if ([int]$best.run_exit -eq 0 -or [bool]$best.ok) {
  exit 0
}

exit 1

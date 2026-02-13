[CmdletBinding()]
param(
  # Shared capture/input selectors
  [Parameter(Mandatory = $false)]
  [string]$ObsSourceName = "Tibia_Fuente",

  [Parameter(Mandatory = $false)]
  [string]$WindowHwnd = "",

  [Parameter(Mandatory = $false)]
  [string]$WindowTitle = "",

  [Parameter(Mandatory = $false)]
  [string]$InputMethod = "",

  # Evidence
  [Parameter(Mandatory = $false)]
  [switch]$DumpFrames,

  [Parameter(Mandatory = $false)]
  [switch]$PostProcessEvidence,

  # Combat (exactly 1 input)
  [Parameter(Mandatory = $false)]
  [string]$CombatAction = "attack_key",

  [Parameter(Mandatory = $false)]
  [string]$AttackKey = "SPACE",

  [Parameter(Mandatory = $false)]
  [string]$CombatClickXY = "",

  # Loot (exactly 1 input)
  [Parameter(Mandatory = $false)]
  [switch]$LootAutoScan,

  [Parameter(Mandatory = $false)]
  [string]$LootClickXY = "",

  [Parameter(Mandatory = $false)]
  [string]$LootScanCenterXY = "",

  # Small delay to let corpse appear / UI settle
  [Parameter(Mandatory = $false)]
  [int]$SleepAfterCombatMs = 450,

  # Optional explicit configs (recommended to avoid FRBOT_CONFIG_PATH overrides)
  [Parameter(Mandatory = $false)]
  [string]$CombatConfigPath = "",

  [Parameter(Mandatory = $false)]
  [string]$LootConfigPath = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Push-Location $RepoRoot

try {
  $combatScript = Join-Path $RepoRoot "scripts" "run_combat_basic_real_obs_source.ps1"
  $lootScript = Join-Path $RepoRoot "scripts" "run_looting_basic_real_obs_source.ps1"

  if (-not (Test-Path $combatScript)) { throw "Missing script: $combatScript" }
  if (-not (Test-Path $lootScript)) { throw "Missing script: $lootScript" }

  if ([string]::IsNullOrWhiteSpace($CombatConfigPath)) {
    $CombatConfigPath = (Join-Path $RepoRoot "rois_prod_emergency_combat_basic.json")
  }
  if ([string]::IsNullOrWhiteSpace($LootConfigPath)) {
    $LootConfigPath = (Join-Path $RepoRoot "rois_prod_emergency_looting_basic.json")
  }

  # ----------------
  # 1) combat_basic
  # ----------------
  $combatParams = @{
    ObsSourceName = $ObsSourceName
    ConfigPath = $CombatConfigPath
    Action = $CombatAction
    AttackKey = $AttackKey
  }
  if (-not [string]::IsNullOrWhiteSpace($WindowHwnd)) { $combatParams.WindowHwnd = $WindowHwnd }
  if (-not [string]::IsNullOrWhiteSpace($WindowTitle)) { $combatParams.WindowTitle = $WindowTitle }
  if (-not [string]::IsNullOrWhiteSpace($InputMethod)) { $combatParams.InputMethod = $InputMethod }
  if (-not [string]::IsNullOrWhiteSpace($CombatClickXY)) { $combatParams.ClickXY = $CombatClickXY }
  if ($DumpFrames) { $combatParams.DumpFrames = $true }
  if ($PostProcessEvidence) { $combatParams.PostProcessEvidence = $true }

  Write-Host "Step 1/2: combat_basic (1 input)" -ForegroundColor Cyan
  & $combatScript @combatParams
  $combatExit = $LASTEXITCODE
  if ($combatExit -ne 0) {
    Write-Host "combat_basic failed (exit=$combatExit); skipping loot." -ForegroundColor Yellow
    exit $combatExit
  }

  if ($SleepAfterCombatMs -gt 0) {
    Start-Sleep -Milliseconds $SleepAfterCombatMs
  }

  # ----------------
  # 2) looting_basic
  # ----------------
  $resolvedCenter = $LootScanCenterXY
  if ([string]::IsNullOrWhiteSpace($resolvedCenter)) {
    if (-not [string]::IsNullOrWhiteSpace($LootClickXY)) {
      $resolvedCenter = $LootClickXY
    } elseif (-not [string]::IsNullOrWhiteSpace($CombatClickXY)) {
      $resolvedCenter = $CombatClickXY
    }
  }

  $lootParams = @{
    ObsSourceName = $ObsSourceName
    ConfigPath = $LootConfigPath
  }
  if (-not [string]::IsNullOrWhiteSpace($WindowHwnd)) { $lootParams.WindowHwnd = $WindowHwnd }
  if (-not [string]::IsNullOrWhiteSpace($WindowTitle)) { $lootParams.WindowTitle = $WindowTitle }
  if ($DumpFrames) { $lootParams.DumpFrames = $true }
  if ($PostProcessEvidence) { $lootParams.PostProcessEvidence = $true }

  if ($LootAutoScan) {
    $lootParams.AutoScan = $true
    if (-not [string]::IsNullOrWhiteSpace($resolvedCenter)) {
      $lootParams.ScanCenterXY = $resolvedCenter
    }
  } else {
    if (-not [string]::IsNullOrWhiteSpace($LootClickXY)) {
      $lootParams.ClickXY = $LootClickXY
    } elseif (-not [string]::IsNullOrWhiteSpace($resolvedCenter)) {
      $lootParams.ClickXY = $resolvedCenter
    }
  }

  Write-Host "Step 2/2: looting_basic (1 input)" -ForegroundColor Cyan
  if ($LootAutoScan -and -not [string]::IsNullOrWhiteSpace($resolvedCenter)) {
    Write-Host "Loot scan center: $resolvedCenter" -ForegroundColor DarkCyan
  }

  & $lootScript @lootParams
  exit $LASTEXITCODE
} finally {
  Pop-Location
}

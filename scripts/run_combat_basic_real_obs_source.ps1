[CmdletBinding()]
param(
  # OBS source name (OBS WebSocket v5 source identity)
  [Parameter(Mandatory = $false)]
  [string]$ObsSourceName = "Tibia_Fuente",

  # ROI config JSON with {"frame": {"width":..,"height":..}, "rois": {...}}
  [Parameter(Mandatory = $false)]
  [string]$ConfigPath = "",

  # Tibia input window selector (HWND preferred, title substring supported)
  [Parameter(Mandatory = $false)]
  [string]$WindowHwnd = "",

  [Parameter(Mandatory = $false)]
  [string]$WindowTitle = "",

  # ROIs (names in config)
  [Parameter(Mandatory = $false)]
  [string]$MinimapRoi = "minimap",

  [Parameter(Mandatory = $false)]
  [string]$TargetFrameRoi = "target_frame",

  [Parameter(Mandatory = $false)]
  [string]$TargetHpBarRoi = "target_hp_bar",

  [Parameter(Mandatory = $false)]
  [string]$CombatCooldownRoi = "combat_cooldown",

  [Parameter(Mandatory = $false)]
  [string]$CombatFeedbackRoi = "combat_feedback",

  # Attack key (single key press)
  [Parameter(Mandatory = $false)]
  [string]$AttackKey = "SPACE",

  # Exactly-one-input action spec:
  #   - attack_key[:KEY]         (e.g. attack_key:PageUp)
  #   - battle_list_click
  #   - monster_right_click
  # Back-compat aliases like "key" and "battlelist_click" are accepted.
  [Parameter(Mandatory = $false)]
  [string]$Action = "attack_key",

  # Optional click point override (frame/client pixels): "x,y"
  [Parameter(Mandatory = $false)]
  [string]$ClickXY = "",

  # Auto-scan multiple click points (runs multiple gate attempts, 1 input each, stops on first SUCCESS)
  [Parameter(Mandatory = $false)]
  [switch]$AutoScan,

  # Optional scan center (defaults to -ClickXY if provided)
  [Parameter(Mandatory = $false)]
  [string]$ScanCenterXY = "",

  # Optional explicit scan points list (overrides center-based generation)
  [Parameter(Mandatory = $false)]
  [string[]]$ScanClickXY = @(),

  # Optional click ROI name (for ClickRel). Defaults to battle_list ROI.
  [Parameter(Mandatory = $false)]
  [string]$ClickRoi = "",

  # Optional click point override (relative within ClickRoi): "0.55,0.15" (0..1 ratios)
  [Parameter(Mandatory = $false)]
  [string]$ClickRel = "",

  # Input injection method for Win32 HWND adapter (optional)
  [Parameter(Mandatory = $false)]
  [string]$InputMethod = "",

  # Evidence threshold
  [Parameter(Mandatory = $false)]
  [double]$TargetHpDecreaseMin = 0.02,

  # Preflight smoothing (no inputs sent during waits)
  [Parameter(Mandatory = $false)]
  [double]$WaitForTargetLockSec = 8,

  [Parameter(Mandatory = $false)]
  [double]$WaitForCooldownClearSec = 4,

  [Parameter(Mandatory = $false)]
  [double]$WaitForHpReadableSec = 2,

  [Parameter(Mandatory = $false)]
  [double]$PreflightPollSec = 0.15,

  # Post-input evidence sampling window (REAL only)
  [Parameter(Mandatory = $false)]
  [int]$AfterWindowMs = 900,

  [Parameter(Mandatory = $false)]
  [int]$AfterPollMs = 120,

  # Enable frame dumps on failure/success
  [Parameter(Mandatory = $false)]
  [switch]$DumpFrames,

  # After a run, auto-generate ROI overlays/crops/diff for newest frames.
  [Parameter(Mandatory = $false)]
  [switch]$PostProcessEvidence
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Push-Location $RepoRoot

function _RotateRuntimeLog {
  try {
    $diagDir = Join-Path $RepoRoot "diagnostics"
    $log = Join-Path $diagDir "runtime.log"
    if (Test-Path $log) {
      $stamp = (Get-Date).ToString("yyyyMMdd-HHmmss")
      $dst = Join-Path $diagDir ("runtime_" + $stamp + ".log")
      Move-Item -Force $log $dst
    }
  } catch {
    # non-fatal
  }
}

function _RotateFatalLog {
  try {
    $diagDir = Join-Path $RepoRoot "diagnostics"
    $log = Join-Path $diagDir "fatal.log"
    if (Test-Path $log) {
      $stamp = (Get-Date).ToString("yyyyMMdd-HHmmss")
      $dst = Join-Path $diagDir ("fatal_" + $stamp + ".log")
      Move-Item -Force $log $dst
    }
  } catch {
    # non-fatal
  }
}

function _ParseHwnd {
  param(
    [Parameter(Mandatory = $true)]
    [string]$Raw
  )

  $s = ($Raw | ForEach-Object { $_.Trim() })
  if ([string]::IsNullOrWhiteSpace($s)) { return [IntPtr]::Zero }
  try {
    if ($s -match "^0x[0-9a-fA-F]+$") {
      $n = [Convert]::ToInt64($s.Substring(2), 16)
      return [IntPtr]$n
    }
    $n2 = [Int64]$s
    return [IntPtr]$n2
  } catch {
    return [IntPtr]::Zero
  }
}

function _EnsureTibiaForeground {
  param(
    [Parameter(Mandatory = $false)]
    [string]$Title = "",

    [Parameter(Mandatory = $false)]
    [string]$Hwnd = "",

    [Parameter(Mandatory = $false)]
    [int]$RetryMs = 1200
  )

  $t = ($Title | ForEach-Object { $_.Trim() })
  $hWanted = _ParseHwnd -Raw $Hwnd

  if ([string]::IsNullOrWhiteSpace($t) -and $hWanted -eq [IntPtr]::Zero) { return }

  try {
    if (-not [string]::IsNullOrWhiteSpace($t)) {
      $ws = New-Object -ComObject WScript.Shell
      [void]$ws.AppActivate($t)
    }
  } catch {
    # best-effort
  }

  try {
    if (-not ("Win32.User32" -as [type])) {
      Add-Type -Namespace Win32 -Name User32 -MemberDefinition @"
[DllImport(\"user32.dll\")] public static extern bool SetForegroundWindow(IntPtr hWnd);
[DllImport(\"user32.dll\")] public static extern bool ShowWindowAsync(IntPtr hWnd, int nCmdShow);
[DllImport(\"user32.dll\")] public static extern IntPtr GetForegroundWindow();
"@
    }

    $h = [IntPtr]::Zero
    if ($hWanted -ne [IntPtr]::Zero) {
      $h = $hWanted
    } else {
      $p = Get-Process |
        Where-Object { $_.MainWindowHandle -ne 0 -and $_.MainWindowTitle -like ("*" + $t + "*") } |
        Select-Object -First 1
      if ($p -and $p.MainWindowHandle -ne 0) {
        $h = [IntPtr]$p.MainWindowHandle
      }
    }

    if ($h -ne [IntPtr]::Zero) {
      $deadline = [DateTime]::UtcNow.AddMilliseconds([Math]::Max(0, $RetryMs))
      while ($true) {
        # SW_RESTORE = 9
        [void][Win32.User32]::ShowWindowAsync($h, 9)
        Start-Sleep -Milliseconds 60
        [void][Win32.User32]::SetForegroundWindow($h)
        Start-Sleep -Milliseconds 60

        $fg = [Win32.User32]::GetForegroundWindow()
        if ($fg -eq $h) { break }
        if ([DateTime]::UtcNow -ge $deadline) { break }
      }
    }
  } catch {
    # best-effort
  }
}

function _WaitForTibiaForeground {
  param(
    [Parameter(Mandatory = $false)]
    [string]$Title = "",

    [Parameter(Mandatory = $false)]
    [string]$Hwnd = "",

    [Parameter(Mandatory = $false)]
    [int]$TimeoutMs = 60000,

    [Parameter(Mandatory = $false)]
    [int]$PollMs = 100
  )

  $t = ($Title | ForEach-Object { $_.Trim() })
  $hWanted = _ParseHwnd -Raw $Hwnd

  if ([string]::IsNullOrWhiteSpace($t) -and $hWanted -eq [IntPtr]::Zero) { return $true }

  try {
    if (-not ("Win32.User32" -as [type])) {
      Add-Type -Namespace Win32 -Name User32 -MemberDefinition @"
[DllImport(\"user32.dll\")] public static extern IntPtr GetForegroundWindow();
"@
    }
  } catch {
    # If we cannot query foreground, don't block execution here.
    return $true
  }

  $h = [IntPtr]::Zero
  if ($hWanted -ne [IntPtr]::Zero) {
    $h = $hWanted
  } elseif (-not [string]::IsNullOrWhiteSpace($t)) {
    try {
      $p = Get-Process |
        Where-Object { $_.MainWindowHandle -ne 0 -and $_.MainWindowTitle -like ("*" + $t + "*") } |
        Select-Object -First 1
      if ($p -and $p.MainWindowHandle -ne 0) {
        $h = [IntPtr]$p.MainWindowHandle
      }
    } catch {
      # best-effort
    }
  }

  if ($h -eq [IntPtr]::Zero) {
    # Cannot resolve target hwnd; don't block.
    return $true
  }

  $deadline = [DateTime]::UtcNow.AddMilliseconds([Math]::Max(0, $TimeoutMs))
  while ($true) {
    try {
      $fg = [Win32.User32]::GetForegroundWindow()
      if ($fg -eq $h) { return $true }
    } catch {
      return $true
    }
    if ([DateTime]::UtcNow -ge $deadline) { break }
    Start-Sleep -Milliseconds ([Math]::Max(25, $PollMs))
  }

  return $false
}

function _MakeScanPoints {
  param(
    [Parameter(Mandatory = $true)]
    [string]$CenterXY
  )

  $pt = $CenterXY.Trim()
  if ([string]::IsNullOrWhiteSpace($pt)) { return @() }
  $bits = $pt -split "[,;]" | ForEach-Object { $_.Trim() } | Where-Object { $_ -ne "" }
  if ($bits.Count -ne 2) { return @() }
  $cx = [int]([double]$bits[0])
  $cy = [int]([double]$bits[1])

  # Small grid around the center (tuned for the around-character 'X' zone)
  $dx = 40
  $dy = 40
  return @(
    "$cx,$cy",
    "$($cx - $dx),$cy",
    "$($cx + $dx),$cy",
    "$cx,$($cy - $dy)",
    "$cx,$($cy + $dy)",
    "$($cx - $dx),$($cy - $dy)",
    "$($cx + $dx),$($cy - $dy)",
    "$($cx - $dx),$($cy + $dy)",
    "$($cx + $dx),$($cy + $dy)"
  )
}

function _TryPostProcessEvidence {
  param(
    [Parameter(Mandatory = $true)]
    [string]$AbsConfigPath
  )

  try {
    $framesDir = Join-Path $RepoRoot "diagnostics\frames"
    if (-not (Test-Path $framesDir)) {
      return
    }

    $before = Get-ChildItem -Path $framesDir -File |
      Where-Object {
        $_.Name -like "combat_basic*_before.ppm" -and
        $_.Name -notlike "*_before_minimap.ppm" -and
        ($_.Name -like "combat_basic_preflight*_before.ppm" -or $_.Name -like "combat_basic_*_before.ppm")
      } |
      Sort-Object -Property LastWriteTime -Descending |
      Select-Object -First 1

    if (-not $before) {
      return
    }

    $beforePath = $before.FullName
    $afterPath = $beforePath -replace "_before\.ppm$", "_after.ppm"
    $hasAfter = Test-Path $afterPath

    Write-Host "Post-processing evidence from: $beforePath" -ForegroundColor DarkCyan

    $roiArgs = @(
      "--roi", "target_hp_bar",
      "--roi", "target_frame",
      "--roi", "combat_cooldown",
      "--roi", "combat_feedback",
      "--roi", "battle_list",
      "--roi", "minimap",
      "--roi", "hp_mp"
    )

    poetry run python tools/draw_rois_on_ppm.py --ppm $beforePath --config $AbsConfigPath --out diagnostics/roi_overlays @roiArgs | Out-Host
    poetry run python tools/dump_roi_crops_from_ppm.py --ppm $beforePath --config $AbsConfigPath --out-dir diagnostics/roi_crops @roiArgs | Out-Host

    if ($hasAfter) {
      Write-Host "Found after-frame: $afterPath" -ForegroundColor DarkCyan
      poetry run python tools/dump_ppm_diff_overlay.py --before $beforePath --after $afterPath --out diagnostics/diff_overlays | Out-Host
      poetry run python tools/draw_rois_on_ppm.py --ppm $afterPath --config $AbsConfigPath --out diagnostics/roi_overlays @roiArgs | Out-Host
      poetry run python tools/dump_roi_crops_from_ppm.py --ppm $afterPath --config $AbsConfigPath --out-dir diagnostics/roi_crops @roiArgs | Out-Host
    }
  }
  catch {
    Write-Host "Post-process evidence failed: $($_.Exception.Message)" -ForegroundColor Yellow
  }
}

function _RunPoetryPythonToEvidenceFile {
  param(
    [Parameter(Mandatory = $true)]
    [string[]]$PyArgs,

    [Parameter(Mandatory = $true)]
    [string]$OutPath
  )

  $stdoutPath = ($OutPath + ".stdout.txt")
  $stderrPath = ($OutPath + ".stderr.txt")

  try { if (Test-Path $stdoutPath) { Remove-Item -Force $stdoutPath } } catch {}
  try { if (Test-Path $stderrPath) { Remove-Item -Force $stderrPath } } catch {}
  try { if (Test-Path $OutPath) { Remove-Item -Force $OutPath } } catch {}

  $args = @("run", "python") + $PyArgs
  $p = Start-Process -FilePath "poetry" -ArgumentList $args -NoNewWindow -PassThru -Wait -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath
  $code = [int]$p.ExitCode

  $out = ""
  if (Test-Path $stdoutPath) {
    try { $out += (Get-Content -Raw -Path $stdoutPath) } catch {}
  }
  if (Test-Path $stderrPath) {
    try {
      $err = (Get-Content -Raw -Path $stderrPath)
      if (-not [string]::IsNullOrWhiteSpace($err)) {
        if (-not [string]::IsNullOrWhiteSpace($out)) { $out += "`r`n" }
        $out += "[stderr]`r`n" + $err
      }
    } catch {}
  }

  try {
    Set-Content -Path $OutPath -Value $out -Encoding UTF8
  } catch {
    # best-effort
  }

  return $code
}

try {
  _RotateRuntimeLog
  _RotateFatalLog
  $stamp = (Get-Date).ToString("yyyyMMdd-HHmmss")
  $diagDir = Join-Path $RepoRoot "diagnostics"
  if (-not (Test-Path $diagDir)) {
    New-Item -ItemType Directory -Force -Path $diagDir | Out-Null
  }

  if ([string]::IsNullOrWhiteSpace($ConfigPath)) {
    if (-not [string]::IsNullOrWhiteSpace($env:FRBOT_CONFIG_PATH)) {
      $ConfigPath = $env:FRBOT_CONFIG_PATH
    } else {
      $ConfigPath = (Join-Path $RepoRoot "rois_prod_emergency_combat_basic.json")
    }
  }
  if (-not (Test-Path $ConfigPath)) {
    throw "ConfigPath not found: $ConfigPath"
  }
  $absConfig = Resolve-Path $ConfigPath

  if ([string]::IsNullOrWhiteSpace($WindowHwnd) -and -not [string]::IsNullOrWhiteSpace($env:FRBOT_WINDOW_HWND)) {
    $WindowHwnd = $env:FRBOT_WINDOW_HWND
  }
  if ([string]::IsNullOrWhiteSpace($WindowTitle) -and -not [string]::IsNullOrWhiteSpace($env:FRBOT_WINDOW_TITLE)) {
    $WindowTitle = $env:FRBOT_WINDOW_TITLE
  }
  if ([string]::IsNullOrWhiteSpace($WindowHwnd) -and [string]::IsNullOrWhiteSpace($WindowTitle)) {
    throw "Provide -WindowHwnd or -WindowTitle, or set FRBOT_WINDOW_HWND/FRBOT_WINDOW_TITLE."
  }

  # PROD-EMERGENCY profile is enforced by main.py too, but we set it for tools.
  $env:FRBOT_PROFILE = "prod_emergency"

  # Feature flag: explicitly enable the isolated combat_basic gate.
  # Default is OFF in PROD_EMERGENCY.
  $env:FRBOT_FEATURE_COMBAT_BASIC = "1"

  # This runner is intended to certify combat_basic in isolation.
  # Don't fail the emergency audit on looting inventory OCR readiness.
  $env:FRBOT_AUDIT_SKIP_LOOTING = "1"

  # IMPORTANT: tools/audit_emergency.py only supports FRBOT_MODE=real|mock.
  # main.py will be run with FRBOT_MODE=combat_basic afterwards.
  $env:FRBOT_MODE = "real"

  # CaptureAuthority: OBS source identity
  $env:FRBOT_CAPTURE_SOURCE = "obs_source"
  $env:FRBOT_OBS_SOURCE_NAME = $ObsSourceName

  # Config
  $env:FRBOT_CONFIG_PATH = $absConfig.Path
  $env:FRBOT_MINIMAP_ROI = $MinimapRoi

  # InputAuthority
  if (-not [string]::IsNullOrWhiteSpace($WindowHwnd)) { $env:FRBOT_WINDOW_HWND = $WindowHwnd }
  if (-not [string]::IsNullOrWhiteSpace($WindowTitle)) { $env:FRBOT_WINDOW_TITLE = $WindowTitle }

  # combat_basic ROIs
  $env:FRBOT_TARGET_FRAME_ROI = $TargetFrameRoi
  $env:FRBOT_TARGET_HP_BAR_ROI = $TargetHpBarRoi
  $env:FRBOT_COMBAT_COOLDOWN_ROI = $CombatCooldownRoi
  $env:FRBOT_COMBAT_FEEDBACK_ROI = $CombatFeedbackRoi

  # combat_basic action
  $env:FRBOT_ATTACK_KEY = $AttackKey
  $env:FRBOT_COMBAT_BASIC_ACTION = $Action
  if (-not [string]::IsNullOrWhiteSpace($ClickXY)) { $env:FRBOT_COMBAT_BASIC_CLICK_XY = $ClickXY }
  if (-not [string]::IsNullOrWhiteSpace($ClickRoi)) { $env:FRBOT_COMBAT_BASIC_CLICK_ROI = $ClickRoi }
  if (-not [string]::IsNullOrWhiteSpace($ClickRel)) { $env:FRBOT_COMBAT_BASIC_CLICK_REL = $ClickRel }
  $env:FRBOT_COMBAT_BASIC_TARGET_HP_DECREASE_MIN = [string]$TargetHpDecreaseMin

  # combat_basic preflight waits (still strict: must become valid within timeout)
  $env:FRBOT_COMBAT_BASIC_WAIT_FOR_TARGET_LOCK_S = [string]$WaitForTargetLockSec
  $env:FRBOT_COMBAT_BASIC_WAIT_FOR_COOLDOWN_CLEAR_S = [string]$WaitForCooldownClearSec
  $env:FRBOT_COMBAT_BASIC_WAIT_FOR_HP_READABLE_S = [string]$WaitForHpReadableSec
  $env:FRBOT_COMBAT_BASIC_PREFLIGHT_POLL_S = [string]$PreflightPollSec

  $env:FRBOT_COMBAT_BASIC_AFTER_WINDOW_MS = [string]$AfterWindowMs
  $env:FRBOT_COMBAT_BASIC_AFTER_POLL_MS = [string]$AfterPollMs

  if ($DumpFrames.IsPresent) {
    $env:FRBOT_DUMP_FRAMES = "1"
  } else {
    if (-not $env:FRBOT_DUMP_FRAMES) { $env:FRBOT_DUMP_FRAMES = "1" }
  }

  # PROD_EMERGENCY requires Tibia to be foreground. Startup guards won't focus windows,
  # but this runner can best-effort activate it to reduce window_not_foreground failures.
  if ((-not [string]::IsNullOrWhiteSpace($WindowTitle)) -or (-not [string]::IsNullOrWhiteSpace($WindowHwnd))) {
    _EnsureTibiaForeground -Title $WindowTitle -Hwnd $WindowHwnd
  }

  if (-not [string]::IsNullOrWhiteSpace($InputMethod)) {
    $env:FRBOT_INPUT_METHOD = $InputMethod
  }

  if ($AutoScan.IsPresent) {
    $points = @()
    if ($ScanClickXY -and $ScanClickXY.Count -gt 0) {
      $points = $ScanClickXY
    } else {
      if ([string]::IsNullOrWhiteSpace($ScanCenterXY)) {
        $ScanCenterXY = $ClickXY
      }
      $points = _MakeScanPoints -CenterXY $ScanCenterXY
    }

    if (-not $points -or $points.Count -eq 0) {
      throw "AutoScan requires -ScanClickXY or -ScanCenterXY (or -ClickXY as fallback)."
    }

    $lastCode = 1
    $winner = ""
    foreach ($p in $points) {
      $env:FRBOT_COMBAT_BASIC_CLICK_XY = $p

      $tryStamp = (Get-Date).ToString("yyyyMMdd-HHmmss")
      $auditOut = Join-Path $diagDir ("evidence_" + $tryStamp + ".audit_emergency_combat_basic.out")
      $gateOut = Join-Path $diagDir ("evidence_" + $tryStamp + ".combat_basic.out")

      Write-Host "combat_basic (PROD_EMERGENCY) pre-audit..." -ForegroundColor Cyan
      # NOTE: audit_emergency will only run the combat_basic audit when the flag is enabled.
      if ((-not [string]::IsNullOrWhiteSpace($WindowTitle)) -or (-not [string]::IsNullOrWhiteSpace($WindowHwnd))) {
        _EnsureTibiaForeground -Title $WindowTitle -Hwnd $WindowHwnd
      }

      # Critical: don't start audit until Tibia is already foreground.
      Start-Sleep -Milliseconds 350
      $fgOk = _WaitForTibiaForeground -Title $WindowTitle -Hwnd $WindowHwnd -TimeoutMs 60000 -PollMs 100
      if (-not $fgOk) {
        Write-Host "NOT_READY: Tibia is not foreground at audit start. Focus Tibia and re-run." -ForegroundColor Red
        exit 2
      }

      $auditCode = _RunPoetryPythonToEvidenceFile -PyArgs @("tools/audit_emergency.py") -OutPath $auditOut
      if ($auditCode -ne 0) {
        Write-Host "Wrote audit evidence: $auditOut" -ForegroundColor DarkCyan
        Write-Host "NOT_READY: audit_emergency failed (code=$auditCode)." -ForegroundColor Red
        Write-Host "Check diagnostics/frames_emergency/ and diagnostics/fatal.log" -ForegroundColor Yellow
        exit $auditCode
      }

      Write-Host "Wrote audit evidence: $auditOut" -ForegroundColor DarkCyan

      Write-Host "Running combat_basic gate (1 input) ClickXY=$p ..." -ForegroundColor Cyan
      $env:FRBOT_MODE = "combat_basic"
      if ((-not [string]::IsNullOrWhiteSpace($WindowTitle)) -or (-not [string]::IsNullOrWhiteSpace($WindowHwnd))) {
        _EnsureTibiaForeground -Title $WindowTitle -Hwnd $WindowHwnd
      }

      # Critical: PROD_EMERGENCY forbids focus stealing in Python; ensure Tibia is
      # already foreground before gate startup (prevents VS Code/terminal being foreground).
      Start-Sleep -Milliseconds 350
      $fgOk = _WaitForTibiaForeground -Title $WindowTitle -Hwnd $WindowHwnd -TimeoutMs 60000 -PollMs 100
      if (-not $fgOk) {
        Write-Host "NOT_READY: Tibia is not foreground at gate start. Focus Tibia and re-run." -ForegroundColor Red
        exit 2
      }

      $gateCode = _RunPoetryPythonToEvidenceFile -PyArgs @("main.py") -OutPath $gateOut
      $lastCode = $gateCode

      Write-Host "Wrote gate evidence: $gateOut" -ForegroundColor DarkCyan

      $doPost = $PostProcessEvidence.IsPresent -or $DumpFrames.IsPresent
      if ($doPost) {
        _TryPostProcessEvidence -AbsConfigPath $absConfig.Path
      }

      if ($gateCode -eq 0) {
        $winner = $p
        break
      }
    }

    if (-not [string]::IsNullOrWhiteSpace($winner)) {
      Write-Host "SUCCESS winner ClickXY=$winner" -ForegroundColor Green
    } else {
      Write-Host "No SUCCESS in AutoScan points." -ForegroundColor Yellow
    }
    exit $lastCode
  }

  Write-Host "combat_basic (PROD_EMERGENCY) pre-audit..." -ForegroundColor Cyan
  # NOTE: audit_emergency will only run the combat_basic audit when the flag is enabled.
  $auditOut = Join-Path $diagDir ("evidence_" + $stamp + ".audit_emergency_combat_basic.out")
  $gateOut = Join-Path $diagDir ("evidence_" + $stamp + ".combat_basic.out")

  if ((-not [string]::IsNullOrWhiteSpace($WindowTitle)) -or (-not [string]::IsNullOrWhiteSpace($WindowHwnd))) {
    _EnsureTibiaForeground -Title $WindowTitle -Hwnd $WindowHwnd
  }

  # Critical: don't start audit until Tibia is already foreground.
  Start-Sleep -Milliseconds 350
  $fgOk = _WaitForTibiaForeground -Title $WindowTitle -Hwnd $WindowHwnd -TimeoutMs 60000 -PollMs 100
  if (-not $fgOk) {
    Write-Host "NOT_READY: Tibia is not foreground at audit start. Focus Tibia and re-run." -ForegroundColor Red
    exit 2
  }

  $auditCode = _RunPoetryPythonToEvidenceFile -PyArgs @("tools/audit_emergency.py") -OutPath $auditOut
  if ($auditCode -ne 0) {
    Write-Host "Wrote audit evidence: $auditOut" -ForegroundColor DarkCyan
    Write-Host "NOT_READY: audit_emergency failed (code=$auditCode)." -ForegroundColor Red
    Write-Host "Check diagnostics/frames_emergency/ and diagnostics/fatal.log" -ForegroundColor Yellow
    exit $auditCode
  }

  Write-Host "Wrote audit evidence: $auditOut" -ForegroundColor DarkCyan

  Write-Host "Running combat_basic gate (1 input)..." -ForegroundColor Cyan
  $env:FRBOT_MODE = "combat_basic"
  if ((-not [string]::IsNullOrWhiteSpace($WindowTitle)) -or (-not [string]::IsNullOrWhiteSpace($WindowHwnd))) {
    _EnsureTibiaForeground -Title $WindowTitle -Hwnd $WindowHwnd
  }

  # Critical: don't start main.py until Tibia is already foreground.
  Start-Sleep -Milliseconds 350
  $fgOk = _WaitForTibiaForeground -Title $WindowTitle -Hwnd $WindowHwnd -TimeoutMs 60000 -PollMs 100
  if (-not $fgOk) {
    Write-Host "NOT_READY: Tibia is not foreground at gate start. Focus Tibia and re-run." -ForegroundColor Red
    exit 2
  }

  $gateCode = _RunPoetryPythonToEvidenceFile -PyArgs @("main.py") -OutPath $gateOut

  Write-Host "Wrote gate evidence: $gateOut" -ForegroundColor DarkCyan

  $doPost = $PostProcessEvidence.IsPresent -or $DumpFrames.IsPresent
  if ($doPost) {
    _TryPostProcessEvidence -AbsConfigPath $absConfig.Path
  }

  exit $gateCode
}
finally {
  Pop-Location
}

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
  [string]$InventoryTextRoi = "inventory_text",

  # Quick loot key (single key press)
  [Parameter(Mandatory = $false)]
  [string]$QuickLootKey = "R",

  # Loot gesture to emit for REAL runs. Must correspond to Tibia configuration.
  # - shift_rmb: Shift + Right Click (default)
  # - rmb: Right Click
  # - shift_rmb_cursor: Shift + Right Click at current cursor position (no ClickXY)
  # - rmb_cursor: Right Click at current cursor position (no ClickXY)
  # - key: Press -QuickLootKey
  # - alt_q: Press Alt+Q (hotkey quick loot; no ClickXY)
  [Parameter(Mandatory = $false)]
  [ValidateSet("shift_rmb", "rmb", "shift_rmb_cursor", "rmb_cursor", "key", "alt_q")]
  [string]$LootGesture = "shift_rmb",

  # Strict certification action override.
  # - alt_q: emits exactly one Alt+Q input action; no click/autoscan; no sleeps/retries around the gate.
  [Parameter(Mandatory = $false)]
  [ValidateSet("", "alt_q")]
  [string]$Action = "",

  # Coordinate space for -ClickXY:
  # - screen: absolute screen pixels (recommended for OBS display capture)
  # - frame: capture-frame pixels (legacy)
  [Parameter(Mandatory = $false)]
  [ValidateSet("screen", "frame")]
  [string]$FrameCoordSpace = "screen",

  # Enable frame dumps on failure/success
  [Parameter(Mandatory = $false)]
  [switch]$DumpFrames,

  # After a run, auto-generate ROI overlays/crops/diff for newest frames.
  [Parameter(Mandatory = $false)]
  [switch]$PostProcessEvidence,

  # Mandatory click point for REAL loot (defaults to SCREEN pixels): "x,y"
  [Parameter(Mandatory = $false)]
  [string]$ClickXY,

  # If present, set FRBOT_VALIDATE_QUICK_LOOT=1 so failures become quick_loot_not_effective
  # (instead of looting_no_inventory_delta) for quick-loot gestures.
  [Parameter(Mandatory = $false)]
  [switch]$ValidateQuickLoot,

  # Auto-scan multiple click points (runs multiple gate attempts, 1 input each, stops on first SUCCESS)
  [Parameter(Mandatory = $false)]
  [switch]$AutoScan,

  # If present, try to bring Tibia to foreground automatically (best-effort).
  # Note: this is NOT a game input action; it only affects window focus.
  [Parameter(Mandatory = $false)]
  [switch]$AutoForeground,

  # How long to wait for Tibia to be foreground (ms).
  # - Set >0 to allow an operator to focus Tibia within the timeout.
  # - Set 0 to fail fast if Tibia is not already foreground.
  [Parameter(Mandatory = $false)]
  [int]$WaitForegroundMs = 60000,

  # Optional scan center (defaults to -ClickXY)
  [Parameter(Mandatory = $false)]
  [string]$ScanCenterXY = "",

  # Optional explicit scan points list (overrides center-based generation)
  [Parameter(Mandatory = $false)]
  [string[]]$ScanClickXY = @()
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

function _ParseXY {
  param(
    [Parameter(Mandatory = $true)]
    [string]$Raw
  )

  $pt = ($Raw | ForEach-Object { $_.Trim() })
  if ([string]::IsNullOrWhiteSpace($pt)) { return $null }
  $bits = $pt -split "[,;]" | ForEach-Object { $_.Trim() } | Where-Object { $_ -ne "" }
  if ($bits.Count -ne 2) { return $null }
  try {
    $x = [int]([double]$bits[0])
    $y = [int]([double]$bits[1])
    return @{ x = $x; y = $y }
  } catch {
    return $null
  }
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

  # 3x3 grid around the center (Tibia tiles are typically ~32px).
  $dx = 32
  $dy = 32
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

function _TryGetFrameCenterXYFromConfig {
  param(
    [Parameter(Mandatory = $true)]
    [string]$AbsConfigPath
  )

  try {
    if (-not (Test-Path $AbsConfigPath)) { return "" }
    $j = Get-Content -Raw -Path $AbsConfigPath | ConvertFrom-Json
    if (-not $j) { return "" }
    $w = [int]$j.frame.width
    $h = [int]$j.frame.height
    if ($w -le 0 -or $h -le 0) { return "" }
    $cx = [int]([Math]::Floor($w / 2))
    $cy = [int]([Math]::Floor($h / 2))
    return "$cx,$cy"
  } catch {
    return ""
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
    [DllImport(\"user32.dll\")] public static extern bool IsIconic(IntPtr hWnd);
[DllImport(\"user32.dll\")] public static extern IntPtr GetForegroundWindow();
[DllImport(\"user32.dll\")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint lpdwProcessId);
[DllImport(\"user32.dll\")] public static extern bool AttachThreadInput(uint idAttach, uint idAttachTo, bool fAttach);
[DllImport(\"user32.dll\")] public static extern bool BringWindowToTop(IntPtr hWnd);
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
        try {
          $fg0 = [Win32.User32]::GetForegroundWindow()
          $pidTmp = [uint32]0
          $fgTid = [Win32.User32]::GetWindowThreadProcessId($fg0, [ref]$pidTmp)
          $pidTmp2 = [uint32]0
          $targetTid = [Win32.User32]::GetWindowThreadProcessId($h, [ref]$pidTmp2)

          if ($fgTid -ne 0 -and $targetTid -ne 0 -and $fgTid -ne $targetTid) {
            [void][Win32.User32]::AttachThreadInput($fgTid, $targetTid, $true)
          }

          # Only restore if minimized; avoid toggling maximized->normal.
          if ([Win32.User32]::IsIconic($h)) {
            [void][Win32.User32]::ShowWindowAsync($h, 9)
          }
          [void][Win32.User32]::BringWindowToTop($h)
          [void][Win32.User32]::SetForegroundWindow($h)

          if ($fgTid -ne 0 -and $targetTid -ne 0 -and $fgTid -ne $targetTid) {
            [void][Win32.User32]::AttachThreadInput($fgTid, $targetTid, $false)
          }
        } catch {
          # best-effort
          if ([Win32.User32]::IsIconic($h)) {
            [void][Win32.User32]::ShowWindowAsync($h, 9)
          }
          [void][Win32.User32]::SetForegroundWindow($h)
        }

        Start-Sleep -Milliseconds 40

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
    return $false
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

function _RunPoetryPythonToEvidenceFile {
  param(
    [Parameter(Mandatory = $true)]
    [string[]]$PyArgs,

    [Parameter(Mandatory = $true)]
    [string]$OutPath
  )

  try { if (Test-Path $OutPath) { Remove-Item -Force $OutPath } } catch {}

  $out = ""
  try {
    $out = (& poetry run python @PyArgs 2>&1 | Out-String -Width 200)
  } catch {
    $out = ("FAILED to run poetry python: " + $_.Exception.Message)
  }
  $code = [int]$LASTEXITCODE

  # Some modes intentionally print nothing on success; keep evidence files explicit.
  if ($code -eq 0 -and [string]::IsNullOrWhiteSpace($out)) {
    $out = "REAL_CAVEBOT_OK`r`n"
  }

  try { Set-Content -Path $OutPath -Value $out -Encoding UTF8 } catch {}
  return $code
}

function _TryPostProcessLootingEvidence {
  param(
    [Parameter(Mandatory = $true)]
    [string]$AbsConfigPath
  )

  try {
    $framesLeaf = "frames"
    if (("$env:FRBOT_PROFILE" | ForEach-Object { $_.Trim().ToLower() }) -eq "prod_emergency") {
      $framesLeaf = "frames_emergency"
    }
    $framesDir = Join-Path $RepoRoot ("diagnostics\" + $framesLeaf)
    if (-not (Test-Path $framesDir)) {
      return
    }

    $before = Get-ChildItem -Path $framesDir -File |
      Where-Object { $_.Name -like "looting_basic*_before.ppm" -and $_.Name -notlike "*_before_minimap.ppm" } |
      Sort-Object -Property LastWriteTime -Descending |
      Select-Object -First 1

    if (-not $before) {
      return
    }

    $beforePath = $before.FullName
    $afterPath = $beforePath -replace "_before\.ppm$", "_after.ppm"
    $hasAfter = Test-Path $afterPath

    Write-Host "Post-processing looting evidence from: $beforePath" -ForegroundColor DarkCyan

    $roiArgs = @(
      "--roi", "inventory_text",
      "--roi", "loot_corpse",
      "--roi", "minimap",
      "--roi", "hp_mp",
      "--roi", "battle_list",
      "--roi", "combat_feedback",
      "--roi", "chat_loot_area"
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
    Write-Host "Post-process looting evidence failed: $($_.Exception.Message)" -ForegroundColor Yellow
  }
}

try {
  _RotateRuntimeLog
  _RotateFatalLog

  $isStrictAltQ = ($Action -eq "alt_q")
  if ($isStrictAltQ) {
    $LootGesture = "alt_q"
  }

  $stamp = (Get-Date).ToString("yyyyMMdd-HHmmss")
  $diagDir = Join-Path $RepoRoot "diagnostics"
  if (-not (Test-Path $diagDir)) {
    New-Item -ItemType Directory -Force -Path $diagDir | Out-Null
  }

  if ([string]::IsNullOrWhiteSpace($ConfigPath)) {
    # Deterministic default: do NOT inherit FRBOT_CONFIG_PATH, because other
    # runners (e.g. combat_basic) may have set it to a different config.
    $ConfigPath = (Join-Path $RepoRoot "rois_prod_emergency_looting_basic.json")
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

  # Certification contract: explicit loot gesture confirmation.
  $env:FRBOT_TIBIA_LOOT_GESTURE = $LootGesture
  if ($isStrictAltQ) {
    $env:FRBOT_LOOTING_BASIC_ACTION = "alt_q"
  }

  if ($ValidateQuickLoot.IsPresent) {
    $env:FRBOT_VALIDATE_QUICK_LOOT = "1"
  }

  # Coordinate mapping: screen pixels are recommended for OBS display capture.
  $env:FRBOT_FRAME_COORD_SPACE = $FrameCoordSpace

  # IMPORTANT: tools/audit_emergency.py only supports FRBOT_MODE=real|mock.
  # main.py will be run with FRBOT_MODE=looting_basic afterwards.
  $env:FRBOT_MODE = "real"

  # CaptureAuthority: OBS source identity
  $env:FRBOT_CAPTURE_SOURCE = "obs_source"
  $env:FRBOT_OBS_SOURCE_NAME = $ObsSourceName

  # Evidence manifest: overwrite with current capture identity (best-effort).
  try {
    $framesDir = Join-Path $RepoRoot "diagnostics\frames_emergency"
    if (-not (Test-Path $framesDir)) {
      New-Item -ItemType Directory -Force -Path $framesDir | Out-Null
    }
    $manifestPath = Join-Path $framesDir "evidence_manifest.json"
    $payload = @{
      capture_source = "obs_source"
      obs_source_name = [string]$ObsSourceName
      obs_projector_title = ""
      ts = [int][DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    }
    ($payload | ConvertTo-Json -Depth 6) | Set-Content -Path $manifestPath -Encoding UTF8
  } catch {
    # non-fatal
  }

  # Config
  $env:FRBOT_CONFIG_PATH = $absConfig.Path
  $env:FRBOT_MINIMAP_ROI = $MinimapRoi

  # InputAuthority
  if (-not [string]::IsNullOrWhiteSpace($WindowHwnd)) {
    $env:FRBOT_WINDOW_HWND = $WindowHwnd
    $env:FRBOT_WINDOW_TITLE = ""
  }
  if (-not [string]::IsNullOrWhiteSpace($WindowTitle)) {
    $env:FRBOT_WINDOW_TITLE = $WindowTitle
    if ([string]::IsNullOrWhiteSpace($WindowHwnd)) {
      $env:FRBOT_WINDOW_HWND = ""
    }
  }

  # Allow operator time to re-focus Tibia (IDE terminals may start in foreground).
  if ($WaitForegroundMs -le 0) {
    # Fail fast in fully-automatic mode.
    if ($AutoForeground.IsPresent) {
      # Best-effort: allow a short stabilization window since focus changes can race
      # with PowerShell/Poetry process startup.
      $env:FRBOT_FOREGROUND_RETRIES = "40"
      $env:FRBOT_FOREGROUND_RETRY_DELAY_MS = "50"
    } else {
      $env:FRBOT_FOREGROUND_RETRIES = "1"
      $env:FRBOT_FOREGROUND_RETRY_DELAY_MS = "50"
    }
  } else {
    if (-not $env:FRBOT_FOREGROUND_RETRIES) { $env:FRBOT_FOREGROUND_RETRIES = "600" }
    if (-not $env:FRBOT_FOREGROUND_RETRY_DELAY_MS) { $env:FRBOT_FOREGROUND_RETRY_DELAY_MS = "100" }
  }

  # looting_basic
  $env:FRBOT_INVENTORY_TEXT_ROI = $InventoryTextRoi
  $env:FRBOT_QUICK_LOOT_KEY = $QuickLootKey

  # Unattended strict mode: allow background execution from VS Code terminals.
  # This switches Alt+Q injection to a hybrid strategy:
  # - Prefer SendInput (most reliable for games) when foreground can be acquired.
  # - Fall back to HWND-targeted PostMessage syskeys when foreground cannot be acquired.
  # Also relaxes the prod_emergency foreground preflight check.
  if ($isStrictAltQ -and $AutoForeground.IsPresent) {
    $env:FRBOT_ALLOW_BACKGROUND_INPUT = "1"
    $env:FRBOT_COMBO_METHOD = "hybrid"
    # Pixel-only verification: allow any delta in loot/chat ROI without requiring
    # pre-calibrated hash patterns.
    $env:FRBOT_CHAT_LOOT_ALLOW_ANY_DELTA = "1"
  }

  # Post-input verification sampling (REAL only): keep exactly-one-input contract,
  # but allow a slightly longer UI update window by default.
  if ($isStrictAltQ) {
    $env:FRBOT_LOOTING_BASIC_VERIFY_ATTEMPTS = "1"
    $env:FRBOT_LOOTING_BASIC_VERIFY_DELAY_MS = "0"
  } else {
    if (-not $env:FRBOT_LOOTING_BASIC_VERIFY_ATTEMPTS) {
      if ($LootGesture -eq "alt_q" -or $LootGesture -eq "key") {
        $env:FRBOT_LOOTING_BASIC_VERIFY_ATTEMPTS = "7"
      } else {
        $env:FRBOT_LOOTING_BASIC_VERIFY_ATTEMPTS = "6"
      }
    }
    if (-not $env:FRBOT_LOOTING_BASIC_VERIFY_DELAY_MS) {
      if ($LootGesture -eq "alt_q" -or $LootGesture -eq "key") {
        $env:FRBOT_LOOTING_BASIC_VERIFY_DELAY_MS = "350"
      } else {
        $env:FRBOT_LOOTING_BASIC_VERIFY_DELAY_MS = "200"
      }
    }
  }

  if ($DumpFrames.IsPresent) {
    $env:FRBOT_DUMP_FRAMES = "1"
  } else {
    if (-not $env:FRBOT_DUMP_FRAMES) { $env:FRBOT_DUMP_FRAMES = "1" }
  }

  # Foreground invariant (default): in prod_emergency we do not steal focus.
  # - Non-strict: allow operator time to focus and do best-effort focusing.
  # - Strict (-Action alt_q):
  #   - default: require Tibia already foreground (or operator focuses it if WaitForegroundMs > 0)
  #   - with -AutoForeground: attempt best-effort focusing (still no extra game inputs)
  if ($WaitForegroundMs -gt 0) {
    if ((-not $isStrictAltQ) -or (-not $AutoForeground.IsPresent)) {
      Write-Host "ACTION REQUIRED: Focus Tibia now (waiting up to ${WaitForegroundMs}ms)..." -ForegroundColor Yellow
      $fgPromptOk = _WaitForTibiaForeground -Title $WindowTitle -Hwnd $WindowHwnd -TimeoutMs $WaitForegroundMs -PollMs 100
      if (-not $fgPromptOk) {
        throw "window_not_foreground"
      }
    }
  }

  # Best-effort focusing (may still be blocked by Windows policies).
  if (((-not $isStrictAltQ) -or $AutoForeground.IsPresent) -and ((-not [string]::IsNullOrWhiteSpace($WindowTitle)) -or (-not [string]::IsNullOrWhiteSpace($WindowHwnd)))) {
    _EnsureTibiaForeground -Title $WindowTitle -Hwnd $WindowHwnd
    _EnsureTibiaForeground -Title $WindowTitle -Hwnd $WindowHwnd
  }
  $fgOk = $true
  if ($isStrictAltQ) {
    if ($WaitForegroundMs -gt 0 -and (-not $AutoForeground.IsPresent)) {
      $fgOk = _WaitForTibiaForeground -Title $WindowTitle -Hwnd $WindowHwnd -TimeoutMs $WaitForegroundMs -PollMs 100
    } else {
      $fgOk = _WaitForTibiaForeground -Title $WindowTitle -Hwnd $WindowHwnd -TimeoutMs 0 -PollMs 100
    }
  } elseif ($WaitForegroundMs -gt 0) {
    $fgOk = _WaitForTibiaForeground -Title $WindowTitle -Hwnd $WindowHwnd -TimeoutMs $WaitForegroundMs -PollMs 100
  }
  if (-not $fgOk) {
    throw "window_not_foreground"
  }

  if (-not $isStrictAltQ) {
    Write-Host "looting_basic (PROD_EMERGENCY) pre-audit..." -ForegroundColor Cyan
  }
  $auditOut = Join-Path $diagDir ("evidence_" + $stamp + ".audit_emergency_looting_basic.out")
  $auditCode = 2
  if ($isStrictAltQ) {
    # Pre-audit is readiness-only; looting_basic certification evidence is produced by the gate run.
    $env:FRBOT_AUDIT_SKIP_LOOTING = "1"
    if ($AutoForeground.IsPresent) {
      _EnsureTibiaForeground -Title $WindowTitle -Hwnd $WindowHwnd
    }
    $auditCode = _RunPoetryPythonToEvidenceFile -PyArgs @("tools/audit_emergency.py") -OutPath $auditOut
  } else {
    $env:FRBOT_AUDIT_SKIP_LOOTING = "1"
    $auditDeadline = [DateTime]::UtcNow.AddSeconds(60)
    $attempt = 0
    while ([DateTime]::UtcNow -lt $auditDeadline) {
      $attempt += 1
      $auditCode = _RunPoetryPythonToEvidenceFile -PyArgs @("tools/audit_emergency.py") -OutPath $auditOut
      if ($auditCode -eq 0) { break }

      $outTxt = ""
      try { $outTxt = (Get-Content -Raw -Path $auditOut) } catch { $outTxt = "" }
      if ($outTxt -match "window_not_foreground") {
        Write-Host "audit_emergency attempt #${attempt}: window_not_foreground (focus Tibia) ..." -ForegroundColor Yellow
        _EnsureTibiaForeground -Title $WindowTitle -Hwnd $WindowHwnd
        continue
      }

      break
    }
  }
  if ($auditCode -ne 0) {
    Write-Host "Wrote audit evidence: $auditOut" -ForegroundColor DarkCyan
    Write-Host "NOT_READY: audit_emergency failed (code=$auditCode)." -ForegroundColor Red
    Write-Host "Check diagnostics/frames_emergency/ and diagnostics/fatal.log" -ForegroundColor Yellow
    exit $auditCode
  }

  Write-Host "Wrote audit evidence: $auditOut" -ForegroundColor DarkCyan

  if ($AutoScan.IsPresent) {
    if ($LootGesture -eq "alt_q" -or $LootGesture -eq "key") {
      throw "autoscan_not_supported_for_non_click_gesture"
    }
    $points = @()
    if ($ScanClickXY -and $ScanClickXY.Count -gt 0) {
      $points = $ScanClickXY
    } else {
      if ([string]::IsNullOrWhiteSpace($ScanCenterXY)) {
        if (-not [string]::IsNullOrWhiteSpace($ClickXY)) { $ScanCenterXY = $ClickXY }
      }
      if ([string]::IsNullOrWhiteSpace($ScanCenterXY)) {
        $cfgCenter = _TryGetFrameCenterXYFromConfig -AbsConfigPath $absConfig.Path
        if (-not [string]::IsNullOrWhiteSpace($cfgCenter)) { $ScanCenterXY = $cfgCenter }
      }
      $points = _MakeScanPoints -CenterXY $ScanCenterXY
    }

    if (-not $points -or $points.Count -eq 0) { throw "AutoScan requires -ScanClickXY or -ScanCenterXY." }

    $lastCode = 1
    $winner = ""
    foreach ($p in $points) {
      if ($WaitForegroundMs -gt 0) {
        Write-Host "ACTION REQUIRED: Focus Tibia for next attempt ($p) (waiting up to ${WaitForegroundMs}ms)..." -ForegroundColor Yellow
        [void](_WaitForTibiaForeground -Title $WindowTitle -Hwnd $WindowHwnd -TimeoutMs $WaitForegroundMs -PollMs 100)
      }

      $xy = _ParseXY -Raw $p
      if (-not $xy) { continue }
      $env:FRBOT_LOOTING_BASIC_LOOT_X = [string]$xy.x
      $env:FRBOT_LOOTING_BASIC_LOOT_Y = [string]$xy.y

      $tryStamp = (Get-Date).ToString("yyyyMMdd-HHmmss")
      $gateOut = Join-Path $diagDir ("evidence_" + $tryStamp + ".looting_basic.out")

      Write-Host "Running looting_basic gate (1 input) ClickXY=$p ..." -ForegroundColor Cyan
      $env:FRBOT_MODE = "looting_basic"
      if (((-not $isStrictAltQ) -or $AutoForeground.IsPresent) -and ((-not [string]::IsNullOrWhiteSpace($WindowTitle)) -or (-not [string]::IsNullOrWhiteSpace($WindowHwnd)))) {
        _EnsureTibiaForeground -Title $WindowTitle -Hwnd $WindowHwnd
      }
      $fgOk2 = $true
      if ($isStrictAltQ) {
        if ($WaitForegroundMs -gt 0 -and (-not $AutoForeground.IsPresent)) {
          $fgOk2 = _WaitForTibiaForeground -Title $WindowTitle -Hwnd $WindowHwnd -TimeoutMs $WaitForegroundMs -PollMs 100
        } else {
          $fgOk2 = _WaitForTibiaForeground -Title $WindowTitle -Hwnd $WindowHwnd -TimeoutMs 0 -PollMs 100
        }
      } elseif ($WaitForegroundMs -gt 0) {
        $fgOk2 = _WaitForTibiaForeground -Title $WindowTitle -Hwnd $WindowHwnd -TimeoutMs $WaitForegroundMs -PollMs 100
      }
      if (-not $fgOk2) {
        throw "window_not_foreground"
      }

      $gateCode = _RunPoetryPythonToEvidenceFile -PyArgs @("main.py") -OutPath $gateOut
      $lastCode = $gateCode
      Write-Host "Wrote gate evidence: $gateOut" -ForegroundColor DarkCyan

      # Persist logs per attempt (main.py overwrites diagnostics/fatal.log and runtime.log).
      try {
        $fatalPath = Join-Path $diagDir "fatal.log"
        if (Test-Path $fatalPath) {
          $fatalDst = Join-Path $diagDir ("fatal_" + $tryStamp + ".log")
          Copy-Item -Force -Path $fatalPath -Destination $fatalDst
        }
      } catch {}
      try {
        $runtimePath = Join-Path $diagDir "runtime.log"
        if (Test-Path $runtimePath) {
          $runtimeDst = Join-Path $diagDir ("runtime_" + $tryStamp + ".log")
          Copy-Item -Force -Path $runtimePath -Destination $runtimeDst
        }
      } catch {}

      # Persist binary inventory candidate evidence per attempt.
      try {
        $candPath = Join-Path $RepoRoot "diagnostics\frames_emergency\emergency_inventory_binary_beef_candidates.json"
        if (Test-Path $candPath) {
          $candDst = Join-Path $RepoRoot ("diagnostics\frames_emergency\emergency_inventory_binary_beef_candidates_" + $tryStamp + ".json")
          Copy-Item -Force -Path $candPath -Destination $candDst
        }
      } catch {}

      $doPost = $PostProcessEvidence.IsPresent -or $DumpFrames.IsPresent
      if ($doPost) {
        _TryPostProcessLootingEvidence -AbsConfigPath $absConfig.Path
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

  if ($LootGesture -eq "shift_rmb_cursor" -or $LootGesture -eq "rmb_cursor") {
    if (-not [string]::IsNullOrWhiteSpace($ClickXY)) {
      Write-Host "-ClickXY is ignored for cursor-click gestures ($LootGesture). Pre-position the mouse over the corpse before the run." -ForegroundColor Yellow
    }
    if ($AutoScan) {
      throw "autoscan_not_supported_for_non_click_gesture"
    }
  }

  if ($LootGesture -eq "shift_rmb" -or $LootGesture -eq "rmb") {
    if ([string]::IsNullOrWhiteSpace($ClickXY)) {
      throw "looting_click_point_missing"
    }
    $xy0 = _ParseXY -Raw $ClickXY
    if (-not $xy0) {
      throw "looting_click_point_missing"
    }
    $env:FRBOT_LOOTING_BASIC_LOOT_X = [string]$xy0.x
    $env:FRBOT_LOOTING_BASIC_LOOT_Y = [string]$xy0.y
  }

  if (-not $isStrictAltQ) {
    Write-Host "Running looting_basic gate (1 input)..." -ForegroundColor Cyan
  }
  $env:FRBOT_MODE = "looting_basic"
  if (((-not $isStrictAltQ) -or $AutoForeground.IsPresent) -and ((-not [string]::IsNullOrWhiteSpace($WindowTitle)) -or (-not [string]::IsNullOrWhiteSpace($WindowHwnd)))) {
    _EnsureTibiaForeground -Title $WindowTitle -Hwnd $WindowHwnd
  }
  if ($isStrictAltQ -and $AutoForeground.IsPresent) {
    # Reduce chance of losing foreground between the initial check and gate execution.
    _EnsureTibiaForeground -Title $WindowTitle -Hwnd $WindowHwnd
  }
  $fgOk2 = $true
  if ($isStrictAltQ) {
    if ($WaitForegroundMs -gt 0 -and (-not $AutoForeground.IsPresent)) {
      $fgOk2 = _WaitForTibiaForeground -Title $WindowTitle -Hwnd $WindowHwnd -TimeoutMs $WaitForegroundMs -PollMs 100
    } else {
      $fgOk2 = _WaitForTibiaForeground -Title $WindowTitle -Hwnd $WindowHwnd -TimeoutMs 0 -PollMs 100
    }
  } else {
    $fgOk2 = _WaitForTibiaForeground -Title $WindowTitle -Hwnd $WindowHwnd -TimeoutMs 60000 -PollMs 100
  }
  if (-not $fgOk2) {
    throw "window_not_foreground"
  }

  $gateOut = Join-Path $diagDir ("evidence_" + $stamp + ".looting_basic.out")
  $gateCode = _RunPoetryPythonToEvidenceFile -PyArgs @("main.py") -OutPath $gateOut
  Write-Host "Wrote gate evidence: $gateOut" -ForegroundColor DarkCyan

  # Persist binary inventory candidate evidence for this run.
  try {
    $candPath = Join-Path $RepoRoot "diagnostics\frames_emergency\emergency_inventory_binary_beef_candidates.json"
    if (Test-Path $candPath) {
      $candDst = Join-Path $RepoRoot ("diagnostics\frames_emergency\emergency_inventory_binary_beef_candidates_" + $stamp + ".json")
      Copy-Item -Force -Path $candPath -Destination $candDst
    }
  } catch {}

  $doPost = $PostProcessEvidence.IsPresent -or $DumpFrames.IsPresent
  if ($doPost) {
    _TryPostProcessLootingEvidence -AbsConfigPath $absConfig.Path
  }

  # Final certification audit: looting_basic is REQUIRED here.
  try { Remove-Item Env:FRBOT_AUDIT_SKIP_LOOTING -ErrorAction SilentlyContinue } catch {}
  $env:FRBOT_MODE = "real"
  $finalAuditOut = Join-Path $diagDir ("evidence_" + $stamp + ".audit_emergency_final.out")
  $finalCode = _RunPoetryPythonToEvidenceFile -PyArgs @("tools/audit_emergency.py") -OutPath $finalAuditOut
  Write-Host "Wrote final audit evidence: $finalAuditOut" -ForegroundColor DarkCyan

  if ($finalCode -ne 0) { exit $finalCode }
  exit $gateCode

}
catch {
  $msg = "" + $_.Exception.Message
  if ($msg -eq "autoscan_not_supported_for_non_click_gesture") {
    Write-Host "AutoScan is only supported for click-based gestures (shift_rmb/rmb). Run a single attempt for -LootGesture alt_q, -LootGesture key, -LootGesture shift_rmb_cursor, or -LootGesture rmb_cursor." -ForegroundColor Red
    exit 2
  }
  if ($msg -eq "looting_click_point_missing") {
    Write-Host "looting_click_point_missing: provide -ClickXY 'x,y' (required for -LootGesture shift_rmb/rmb; not required for -LootGesture alt_q/key/shift_rmb_cursor/rmb_cursor)" -ForegroundColor Red
    exit 2
  }
  if ($msg -eq "inventory_overlay_missing") {
    Write-Host "inventory_overlay_missing: OBS source is missing the 0xBEEF binary inventory overlay" -ForegroundColor Red
    exit 4
  }
  if ($msg -eq "looting_action_not_configured") {
    Write-Host "looting_action_not_configured: configured loot gesture did not trigger Quick Loot" -ForegroundColor Red
    exit 5
  }
  if ($msg -eq "window_not_foreground") {
    Write-Host "window_not_foreground" -ForegroundColor Red
    exit 3
  }

  Write-Host ("FAILED: " + $msg) -ForegroundColor Red
  exit 1
}
finally {
  Pop-Location
}

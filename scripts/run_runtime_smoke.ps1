[CmdletBinding()]
param(
  [Parameter(Mandatory = $false)]
  [double]$Seconds = 10.0,

  # ROI config to use (runtime schema: {"rois": {...}})
  [Parameter(Mandatory = $false)]
  [string]$ConfigPath = "obs_projector_rois_runtime.json",

  [Parameter(Mandatory = $false)]
  [string]$MinimapRoi = "minimap",

  # OBS Projector window title substring for strict foreground binding.
  [Parameter(Mandatory = $false)]
  [string]$ProjectorWindowTitle = "Proyector en ventana (Fuente) - Tibia_Fuente",

  # Player marker detection defaults (adjust if your minimap marker differs).
  [Parameter(Mandatory = $false)]
  [string]$PlayerMarkerRgb = "255,255,0",

  [Parameter(Mandatory = $false)]
  [int]$PlayerMarkerTol = 10,

  [Parameter(Mandatory = $false)]
  [int]$PlayerMarkerMinPixels = 3,

  # dxcam output probing (recommended for multi-monitor setups)
  [Parameter(Mandatory = $false)]
  [switch]$TryAllOutputs,

  [Parameter(Mandatory = $false)]
  [int]$MaxOutputs = 6,

  # Optional: precheck the projector window exists before running Python.
  [Parameter(Mandatory = $false)]
  [switch]$PrecheckProjectorWindow,

  # Optional: best-effort focus attempt during precheck.
  [Parameter(Mandatory = $false)]
  [switch]$PrecheckTryFocus

  ,
  # Optional: wait for projector to be foreground (reduces window_binding_lost failures).
  [Parameter(Mandatory = $false)]
  [double]$WaitForForegroundSeconds = 5.0
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# Run from repo root deterministically.
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Push-Location $RepoRoot

try {
  $Bound = $PSBoundParameters

  # Core: run REAL pipeline via OBS Projector.
  $env:FRBOT_MODE = "real"
  $env:FRBOT_CAPTURE_BACKEND = "obs-projector"
  $env:FRBOT_CAPTURE_TARGET = "projector"

  function Get-Env([string]$Name) {
    return [Environment]::GetEnvironmentVariable($Name, "Process")
  }

  function Set-Env([string]$Name, [string]$Value) {
    [Environment]::SetEnvironmentVariable($Name, $Value, "Process")
  }

  function Set-EnvIfMissing([string]$Name, [string]$Value) {
    $cur = Get-Env $Name
    if (-not $cur -or [string]::IsNullOrWhiteSpace($cur)) {
      Set-Env $Name $Value
    }
  }

  function Set-EnvIfExplicit([string]$Name, [string]$Value, [string]$ParamKey) {
    if ($Bound.ContainsKey($ParamKey)) {
      Set-Env $Name $Value
      return
    }
    Set-EnvIfMissing -Name $Name -Value $Value
  }

  Set-EnvIfExplicit -Name "FRBOT_CONFIG_PATH" -Value $ConfigPath -ParamKey "ConfigPath"
  Set-EnvIfExplicit -Name "FRBOT_MINIMAP_ROI" -Value $MinimapRoi -ParamKey "MinimapRoi"

  # Strict foreground invariant preserved (binding.verify/assert_bound enforce it).
  Set-EnvIfExplicit -Name "FRBOT_PROJECTOR_WINDOW_TITLE" -Value $ProjectorWindowTitle -ParamKey "ProjectorWindowTitle"
  Set-EnvIfMissing -Name "FRBOT_PROJECTOR_REQUIRE_FOREGROUND" -Value "1"

  # Best-effort focusing (preflight uses this to satisfy verify()).
  Set-EnvIfMissing -Name "FRBOT_PROJECTOR_FOCUS_ON_START" -Value "1"
  if (-not $env:FRBOT_PROJECTOR_FOCUS_TIMEOUT_S) {
    $env:FRBOT_PROJECTOR_FOCUS_TIMEOUT_S = "5"
  }

  # Output probing: keep deterministic for CI by enabling explicitly.
  $tryAllAlreadySet = -not [string]::IsNullOrWhiteSpace((Get-Env 'FRBOT_TRY_ALL_OUTPUTS'))
  $maxOutputsAlreadySet = -not [string]::IsNullOrWhiteSpace((Get-Env 'FRBOT_MAX_OUTPUTS'))
  if ($TryAllOutputs.IsPresent) {
    $env:FRBOT_TRY_ALL_OUTPUTS = "1"
    $env:FRBOT_MAX_OUTPUTS = [string]$MaxOutputs
  } elseif (-not $tryAllAlreadySet) {
    # Turnkey default: dxcam can pick the wrong output on multi-monitor rigs.
    # Enabling probing here makes real runs much more reliable.
    $env:FRBOT_TRY_ALL_OUTPUTS = "1"
    if (-not $maxOutputsAlreadySet) {
      $env:FRBOT_MAX_OUTPUTS = [string]$MaxOutputs
    }
    Write-Host "dxcam: auto-enabled output probing (FRBOT_TRY_ALL_OUTPUTS=1)" -ForegroundColor DarkCyan
  }

  # Marker detection
  Set-EnvIfExplicit -Name "FRBOT_PLAYER_MARKER_RGB" -Value $PlayerMarkerRgb -ParamKey "PlayerMarkerRgb"
  Set-EnvIfExplicit -Name "FRBOT_PLAYER_MARKER_TOL" -Value ([string]$PlayerMarkerTol) -ParamKey "PlayerMarkerTol"
  Set-EnvIfExplicit -Name "FRBOT_PLAYER_MARKER_MIN_PIXELS" -Value ([string]$PlayerMarkerMinPixels) -ParamKey "PlayerMarkerMinPixels"

  Write-Host "runtime_smoke: seconds=$Seconds config=$(Get-Env 'FRBOT_CONFIG_PATH') minimap_roi=$(Get-Env 'FRBOT_MINIMAP_ROI')" -ForegroundColor Cyan
  Write-Host "projector_title='$(Get-Env 'FRBOT_PROJECTOR_WINDOW_TITLE')' try_all_outputs=$(Get-Env 'FRBOT_TRY_ALL_OUTPUTS') max_outputs=$(Get-Env 'FRBOT_MAX_OUTPUTS')" -ForegroundColor Cyan
  Write-Host "marker_rgb=$(Get-Env 'FRBOT_PLAYER_MARKER_RGB') tol=$(Get-Env 'FRBOT_PLAYER_MARKER_TOL') min_pixels=$(Get-Env 'FRBOT_PLAYER_MARKER_MIN_PIXELS')" -ForegroundColor Cyan

  if ($PrecheckProjectorWindow.IsPresent) {
    $title = (Get-Env 'FRBOT_PROJECTOR_WINDOW_TITLE')
    if (-not $title -or [string]::IsNullOrWhiteSpace($title)) {
      Write-Host "precheck: FRBOT_PROJECTOR_WINDOW_TITLE is empty" -ForegroundColor Red
      exit 2
    }

    # Use the existing repo tool (same Win32 implementation) to avoid duplicating user32 window enumeration in PS.
    $json = & poetry run python tools/list_windows_visible.py --filter $title --json 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $json) {
      Write-Host "precheck: failed to query windows via tools/list_windows_visible.py" -ForegroundColor Red
      exit 2
    }

    try {
      $parsed = $json | ConvertFrom-Json
    } catch {
      Write-Host "precheck: invalid JSON from list_windows_visible" -ForegroundColor Red
      exit 2
    }

    $wins = @($parsed.windows)
    if ($wins.Count -eq 0) {
      Write-Host "precheck: no visible window matched title substring: '$title'" -ForegroundColor Red
      exit 2
    }

    $w0 = $wins[0]
    Write-Host "precheck: matched hwnd=$($w0.hwnd_hex) minimized=$($w0.minimized) foreground=$($w0.foreground)" -ForegroundColor DarkCyan

    if ($PrecheckTryFocus.IsPresent -and -not $w0.foreground) {
      # Best-effort focus; actual strict enforcement remains in runtime.
      & poetry run python -c "import sys; from adapters.windows import win32 as w32; hwnd=int(sys.argv[1],0); w32.try_focus_window(hwnd, timeout_s=1.0)" $w0.hwnd_hex 2>$null | Out-Null
      Start-Sleep -Milliseconds 150
      $json2 = & poetry run python tools/list_windows_visible.py --filter $title --json 2>$null
      try { $parsed2 = $json2 | ConvertFrom-Json } catch { $parsed2 = $null }
      if ($parsed2 -and @($parsed2.windows).Count -gt 0 -and @($parsed2.windows)[0].foreground) {
        Write-Host "precheck: focus acquired" -ForegroundColor DarkCyan
      } else {
        Write-Host "precheck: focus not acquired (will rely on runtime focus + strict verify)" -ForegroundColor Yellow
      }
    }
  }

  # Best-effort wait for strict foreground binding to be satisfied.
  $title = (Get-Env 'FRBOT_PROJECTOR_WINDOW_TITLE')
  if ($title -and -not [string]::IsNullOrWhiteSpace($title) -and $WaitForForegroundSeconds -gt 0) {
    $deadline = (Get-Date).AddSeconds($WaitForForegroundSeconds)
    $isForeground = $false
    while ((Get-Date) -lt $deadline) {
      $json = & poetry run python tools/list_windows_visible.py --filter $title --json 2>$null
      if ($LASTEXITCODE -eq 0 -and $json) {
        try {
          $parsed = $json | ConvertFrom-Json
          $wins = @($parsed.windows)
          if ($wins.Count -gt 0 -and $wins[0].foreground) {
            $isForeground = $true
            break
          }
        } catch {
          # ignore transient JSON parse issues
        }
      }
      Start-Sleep -Milliseconds 100
    }

    if (-not $isForeground) {
      Write-Host "projector_not_foreground: bring the OBS Projector window to the foreground and retry." -ForegroundColor Red
      Write-Host "Tip: re-run with -PrecheckProjectorWindow -PrecheckTryFocus to debug title matching/focus." -ForegroundColor Yellow
      exit 2
    }
  }

  poetry run python tools/run_runtime_smoke.py --seconds $Seconds
  $code = $LASTEXITCODE
  exit $code
}
finally {
  Pop-Location
}

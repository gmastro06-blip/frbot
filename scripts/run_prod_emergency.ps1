[CmdletBinding()]
param(
  # OBS Projector window title substring for strict foreground binding.
  [Parameter(Mandatory = $false)]
  [string]$ProjectorWindowTitle = "Proyector en ventana (Fuente) - Tibia_Fuente",

  # Minimal ROI config (runtime schema: {"rois": {...}})
  [Parameter(Mandatory = $false)]
  [string]$ConfigPath = "rois_prod.json",

  [Parameter(Mandatory = $false)]
  [string]$MinimapRoi = "minimap",

  # Player marker detection defaults (adjust if your minimap marker differs).
  [Parameter(Mandatory = $false)]
  [string]$PlayerMarkerRgb = "255,255,0",

  [Parameter(Mandatory = $false)]
  [int]$PlayerMarkerTol = 10,

  [Parameter(Mandatory = $false)]
  [int]$PlayerMarkerMinPixels = 3,

  # Session guardrails
  [Parameter(Mandatory = $false)]
  [int]$MaxTicks = 300,

  [Parameter(Mandatory = $false)]
  [double]$SessionSeconds = 15.0,

  # dxcam output probing (recommended)
  [Parameter(Mandatory = $false)]
  [switch]$TryAllOutputs,

  [Parameter(Mandatory = $false)]
  [int]$MaxOutputs = 6
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# Run from repo root deterministically.
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Push-Location $RepoRoot

try {
  # Emergency profile (hard cut features + bounded session).
  $env:FRBOT_PROFILE = "prod_emergency"

  # Real + OBS Projector backend.
  $env:FRBOT_MODE = "real"
  $env:FRBOT_CAPTURE_BACKEND = "obs-projector"
  $env:FRBOT_CAPTURE_TARGET = "projector"

  $env:FRBOT_CONFIG_PATH = $ConfigPath
  $env:FRBOT_MINIMAP_ROI = $MinimapRoi

  $env:FRBOT_PROJECTOR_WINDOW_TITLE = $ProjectorWindowTitle
  $env:FRBOT_PROJECTOR_REQUIRE_FOREGROUND = "1"
  $env:FRBOT_PROJECTOR_FOCUS_ON_START = "1"
  $env:FRBOT_PROJECTOR_FOCUS_TIMEOUT_S = "5"

  # Recommended dxcam probing for multi-monitor setups.
  if ($TryAllOutputs.IsPresent) {
    $env:FRBOT_TRY_ALL_OUTPUTS = "1"
    $env:FRBOT_MAX_OUTPUTS = [string]$MaxOutputs
  } else {
    # Turnkey default: probe unless user explicitly manages outputs.
    if (-not $env:FRBOT_TRY_ALL_OUTPUTS) { $env:FRBOT_TRY_ALL_OUTPUTS = "1" }
    if (-not $env:FRBOT_MAX_OUTPUTS) { $env:FRBOT_MAX_OUTPUTS = [string]$MaxOutputs }
  }

  # Marker detection
  $env:FRBOT_PLAYER_MARKER_RGB = $PlayerMarkerRgb
  $env:FRBOT_PLAYER_MARKER_TOL = [string]$PlayerMarkerTol
  $env:FRBOT_PLAYER_MARKER_MIN_PIXELS = [string]$PlayerMarkerMinPixels

  # Emergency session guardrails
  $env:FRBOT_MAX_TICKS = [string]$MaxTicks
  $env:FRBOT_SESSION_SECONDS = [string]$SessionSeconds

  Write-Host "PROD-EMERGENCY: auditing live preflight + minimal ROIs..." -ForegroundColor Cyan
  poetry run python tools/audit_emergency.py
  $auditCode = $LASTEXITCODE
  if ($auditCode -ne 0) {
    Write-Host "NOT_READY: audit_emergency failed (code=$auditCode)." -ForegroundColor Red
    Write-Host "Evidence dumps (if any): diagnostics/frames_emergency/" -ForegroundColor Yellow
    exit $auditCode
  }

  Write-Host "READY: starting bounded real session (safe)" -ForegroundColor Cyan
  Write-Host "- FRBOT_MAX_TICKS=$env:FRBOT_MAX_TICKS FRBOT_SESSION_SECONDS=$env:FRBOT_SESSION_SECONDS" -ForegroundColor DarkCyan
  poetry run python main.py
  exit $LASTEXITCODE
}
finally {
  Pop-Location
}

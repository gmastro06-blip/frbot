[CmdletBinding()]
param(
  # Nombre exacto del Source en OBS (requerido).
  [Parameter(Mandatory = $false)]
  [string]$ObsSource,

  # Selector de ventana Tibia (requerido: WindowHwnd o WindowTitle).
  [Parameter(Mandatory = $false)]
  [string]$WindowTitle,

  [Parameter(Mandatory = $false)]
  [string]$WindowHwnd
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Push-Location $RepoRoot
try {
  try { [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false) } catch {}

  if ($ObsSource) { $env:FRBOT_OBS_SOURCE_NAME = $ObsSource }
  if ($WindowTitle) { $env:FRBOT_WINDOW_TITLE = $WindowTitle }
  if ($WindowHwnd) { $env:FRBOT_WINDOW_HWND = $WindowHwnd }

  # Set required REAL/release flags.
  $env:FRBOT_PROFILE = "prod_full"
  $env:FRBOT_MODE = "prod_full"
  $env:FRBOT_CAPTURE_SOURCE = "obs_source"
  $env:FRBOT_DUMP_FRAMES = "1"

  # Set a per-release frames directory: diagnostics/frames_full/<timestamp>
  $ts = (Get-Date).ToUniversalTime().ToString("yyyyMMdd_HHmmss")
  $framesDir = (Join-Path $RepoRoot (Join-Path "diagnostics\frames_full" $ts))
  New-Item -ItemType Directory -Force -Path $framesDir | Out-Null
  $env:FRBOT_REAL_FRAMES_DIR = $framesDir

  # Delegate orchestration to Python. Capture any output and normalize to a single final line.
  $out = & poetry run python (Join-Path $RepoRoot "tools\release_prod_full.py") -ObsSource $env:FRBOT_OBS_SOURCE_NAME -WindowTitle $env:FRBOT_WINDOW_TITLE -WindowHwnd $env:FRBOT_WINDOW_HWND 2>&1
  $code = $LASTEXITCODE

  if ($out -match "\bRELEASE_GO\b") {
    Write-Output "RELEASE_GO"
    exit 0
  }

  if ($out -match "\bRELEASE_NO_GO\b") {
    Write-Output "RELEASE_NO_GO"
    exit 1
  }

  if ($code -eq 0) {
    Write-Output "RELEASE_GO"
    exit 0
  }
  Write-Output "RELEASE_NO_GO"
  exit 1
} finally {
  Pop-Location
}

[CmdletBinding()]
param(
  # Nombre exacto del Source en OBS (requerido para identidad estricta).
  [Parameter(Mandatory = $false)]
  [string]$ObsSourceName,

  # Selector de ventana Tibia (al menos uno de los dos).
  [Parameter(Mandatory = $false)]
  [string]$WindowTitle,

  [Parameter(Mandatory = $false)]
  [string]$WindowHwnd,

  # Overrides opcionales.
  [Parameter(Mandatory = $false)]
  [string]$ConfigPath,

  [Parameter(Mandatory = $false)]
  [string]$FramesDir
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Push-Location $RepoRoot
try {
  try { [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false) } catch {}

  # Precondiciones REAL (no silenciosas): el auditor validará y reportará blockers.
  $env:FRBOT_PROFILE = "prod_full"
  $env:FRBOT_CAPTURE_SOURCE = "obs_source"

  if ($ObsSourceName) { $env:FRBOT_OBS_SOURCE_NAME = $ObsSourceName }
  if ($WindowTitle) { $env:FRBOT_WINDOW_TITLE = $WindowTitle }
  if ($WindowHwnd) { $env:FRBOT_WINDOW_HWND = $WindowHwnd }

  if ($ConfigPath) {
    $cp = $ConfigPath
    if (-not [System.IO.Path]::IsPathRooted($cp)) {
      $cp = (Join-Path $RepoRoot $cp)
    }
    $env:FRBOT_CONFIG_PATH = $cp
  }

  if ($FramesDir) {
    $fd = $FramesDir
    if (-not [System.IO.Path]::IsPathRooted($fd)) {
      $fd = (Join-Path $RepoRoot $fd)
    }
    $env:FRBOT_REAL_FRAMES_DIR = $fd
  }

  & poetry run python (Join-Path $RepoRoot "tools\audit_repo_status.py")
  $code = $LASTEXITCODE

  Write-Host ("Reporte: {0}" -f (Join-Path $RepoRoot "diagnostics\status_repo.json"))
  Write-Host ("Diagnostico ventanas: {0}" -f (Join-Path $RepoRoot "diagnostics\window_diagnostics.json"))
  exit $code
} finally {
  Pop-Location
}

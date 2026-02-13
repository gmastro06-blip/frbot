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

  ,[Parameter(Mandatory = $false)]
  [string]$InputMethod
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$InformationPreference = "SilentlyContinue"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Push-Location $RepoRoot
try {
  try { [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false) } catch {}

  if ($ObsSource) { $env:FRBOT_OBS_SOURCE_NAME = $ObsSource }
  if ($WindowTitle) { $env:FRBOT_WINDOW_TITLE = $WindowTitle }
  if ($WindowHwnd) { $env:FRBOT_WINDOW_HWND = $WindowHwnd }
  if ($InputMethod) { $env:FRBOT_INPUT_METHOD = $InputMethod }

  # Set required REAL/release flags.
  $env:FRBOT_PROFILE = "prod_full"
  $env:FRBOT_MODE = "prod_full"
  $env:FRBOT_CAPTURE_SOURCE = "obs_source"
  if (-not $env:FRBOT_INPUT_METHOD) { $env:FRBOT_INPUT_METHOD = "postmessage" }
  $env:FRBOT_DUMP_FRAMES = "1"

  $defaultCfg = (Join-Path $RepoRoot "config\rois_prod_full.json")
  if (Test-Path -LiteralPath $defaultCfg) {
    $env:FRBOT_CONFIG_PATH = $defaultCfg
  }

  # Set a per-release frames directory: diagnostics/frames_full/<timestamp>
  $ts = (Get-Date).ToUniversalTime().ToString("yyyyMMdd_HHmmss")
  $framesDir = (Join-Path $RepoRoot (Join-Path "diagnostics\frames_full" $ts))
  New-Item -ItemType Directory -Force -Path $framesDir | Out-Null
  $env:FRBOT_REAL_FRAMES_DIR = $framesDir

  # Delegate orchestration to Python.
  $poetryArgs = @(
    "run",
    "python",
    (Join-Path $RepoRoot "tools\release_prod_full.py"),
    "-ObsSource",
    [string]$env:FRBOT_OBS_SOURCE_NAME,
    "-WindowTitle",
    [string]$env:FRBOT_WINDOW_TITLE
  )
  if ([string]::IsNullOrWhiteSpace([string]$env:FRBOT_WINDOW_HWND) -eq $false) {
    $poetryArgs += @("-WindowHwnd", [string]$env:FRBOT_WINDOW_HWND)
  }
  $out = & poetry @poetryArgs 2>&1
  $code = [int]$LASTEXITCODE

  # Normalize output to strings (avoid formatting non-string objects).
  $outLines = @($out | ForEach-Object { [string]$_ })

  # Contract: print a single RELEASE_* line.
  $line = ($outLines | Where-Object { $_ -match "^(RELEASE_GO|RELEASE_NO_GO:)" } | Select-Object -Last 1)
  if (-not $line) {
    Write-Output "RELEASE_NO_GO:internal_error"
    exit 2
  }

  Write-Output ($line.Trim())
  if ($code -in 0, 1, 2) { exit $code }
  exit 2
} finally {
  Pop-Location
}

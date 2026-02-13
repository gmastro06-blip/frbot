[CmdletBinding()]
param(
  [Parameter(Mandatory = $false)]
  [string]$ObsSource,

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

Set-Location -LiteralPath $PSScriptRoot

try {
  try { [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false) } catch {}

  if ($ObsSource) { $env:FRBOT_OBS_SOURCE_NAME = $ObsSource }
  if ($WindowTitle) { $env:FRBOT_WINDOW_TITLE = $WindowTitle }
  if ($WindowHwnd) { $env:FRBOT_WINDOW_HWND = $WindowHwnd }
  if ($InputMethod) { $env:FRBOT_INPUT_METHOD = $InputMethod }

  if (-not $env:FRBOT_PROFILE) { $env:FRBOT_PROFILE = "prod_full" }
  if (-not $env:FRBOT_MODE) { $env:FRBOT_MODE = "prod_full" }
  if (-not $env:FRBOT_CAPTURE_SOURCE) { $env:FRBOT_CAPTURE_SOURCE = "obs_source" }
  if (-not $env:FRBOT_INPUT_METHOD) { $env:FRBOT_INPUT_METHOD = "postmessage" }

  if (-not $env:FRBOT_REAL_FRAMES_DIR) {
    $env:FRBOT_REAL_FRAMES_DIR = (Join-Path $PSScriptRoot "diagnostics\frames_full")
  }

  $prodFullCfg = (Join-Path $PSScriptRoot "rois_prod_full.json")
  if (Test-Path -LiteralPath $prodFullCfg) {
    $env:FRBOT_CONFIG_PATH = $prodFullCfg
  }

  $hwndRaw = [string]($env:FRBOT_WINDOW_HWND)
  $hwndRawTrim = $hwndRaw.Trim()
  $hwndInvalid = $false
  if ($hwndRawTrim -ne "") {
    if ($hwndRawTrim -match "[Xx]") {
      $hwndInvalid = $true
    } elseif ($hwndRawTrim -notmatch "^(0x[0-9a-fA-F]+|[0-9]+)$") {
      $hwndInvalid = $true
    }
  }
  if ($hwndInvalid) {
    Remove-Item Env:FRBOT_WINDOW_HWND -ErrorAction SilentlyContinue
  }

  New-Item -ItemType Directory -Force -Path $env:FRBOT_REAL_FRAMES_DIR | Out-Null

  $poetryArgs = @(
    "run",
    "python",
    (Join-Path $PSScriptRoot "tools\release_prod_full.py"),
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

  $outLines = @($out | ForEach-Object { [string]$_ })
  $line = ($outLines | Where-Object { $_ -match "^(RELEASE_GO|RELEASE_NO_GO:)" } | Select-Object -Last 1)
  if (-not $line) {
    Write-Output "RELEASE_NO_GO:internal_error"
    exit 2
  }

  Write-Output ($line.Trim())
  if ($code -in 0, 1, 2) { exit $code }
  exit 2
}
catch {
  Write-Output "RELEASE_NO_GO:internal_error"
  exit 2
}

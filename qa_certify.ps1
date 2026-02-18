[CmdletBinding()]
param(
  [Parameter(Mandatory = $false)]
  [switch]$Real
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$InformationPreference = "SilentlyContinue"

Set-Location -LiteralPath $PSScriptRoot

try {
  try { [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false) } catch {}

  if (-not $env:FRBOT_PROFILE) { $env:FRBOT_PROFILE = "prod_full" }
  if (-not $env:FRBOT_MODE) { $env:FRBOT_MODE = "prod_full" }
  if (-not $env:FRBOT_CAPTURE_SOURCE) { $env:FRBOT_CAPTURE_SOURCE = "obs_source" }

  $repoRoot = $PSScriptRoot
  if (-not $env:FRBOT_REAL_FRAMES_DIR) {
    $env:FRBOT_REAL_FRAMES_DIR = (Join-Path $repoRoot "diagnostics\frames_full")
  }

  if (-not $env:FRBOT_CONFIG_PATH) {
    $cfg = Join-Path $repoRoot "rois_prod_full.json"
    if (Test-Path -LiteralPath $cfg) {
      $env:FRBOT_CONFIG_PATH = $cfg
    }
  }

  if ($Real) {
    $missing = New-Object System.Collections.Generic.List[string]

    if ([string]::IsNullOrWhiteSpace([string]$env:FRBOT_CAPTURE_SOURCE)) { $missing.Add("FRBOT_CAPTURE_SOURCE") }
    if ([string]::IsNullOrWhiteSpace([string]$env:FRBOT_OBS_SOURCE_NAME)) { $missing.Add("FRBOT_OBS_SOURCE_NAME") }
    if ([string]::IsNullOrWhiteSpace([string]$env:FRBOT_CONFIG_PATH)) { $missing.Add("FRBOT_CONFIG_PATH") }
    if ([string]::IsNullOrWhiteSpace([string]$env:FRBOT_REAL_FRAMES_DIR)) { $missing.Add("FRBOT_REAL_FRAMES_DIR") }

    $hasTitle = -not [string]::IsNullOrWhiteSpace([string]$env:FRBOT_WINDOW_TITLE)
    $hasHwnd = -not [string]::IsNullOrWhiteSpace([string]$env:FRBOT_WINDOW_HWND)
    if (-not ($hasTitle -or $hasHwnd)) { $missing.Add("FRBOT_WINDOW_TITLE|FRBOT_WINDOW_HWND") }

    if ([string]$env:FRBOT_CAPTURE_SOURCE -ne "obs_source") { $missing.Add("FRBOT_CAPTURE_SOURCE=obs_source") }

    if ($missing.Count -gt 0) {
      $env:FRBOT_QA_REQUIRED_ENV_MISSING = [string]::Join(",", $missing)
    }
    else {
      Remove-Item Env:FRBOT_QA_REQUIRED_ENV_MISSING -ErrorAction SilentlyContinue
    }
  }

  $qaArgs = @("run", "python", (Join-Path $repoRoot "tools\qa_certify.py"))
  if ($Real) { $qaArgs += "--real" }

  & poetry @qaArgs
  $code = [int]$LASTEXITCODE

  if ($code -eq 0) { exit 0 }
  if ($code -eq 1) { exit 1 }
  exit 2
}
catch {
  Write-Output "QA_NO_GO:internal_error"
  exit 2
}

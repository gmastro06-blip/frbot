[CmdletBinding()]
param(
  # OBS source name (OBS WebSocket v5 source identity)
  [Parameter(Mandatory = $false)]
  [string]$ObsSourceName = "Tibia_Fuente",

  # ROI config JSON with {"frame": {"width":..,"height":..}, "rois": {...}}
  # Must include all ROIs required by prod_full gates.
  [Parameter(Mandatory = $false)]
  [string]$ConfigPath = "",

  # Tibia input window selector (HWND preferred, title substring supported)
  [Parameter(Mandatory = $false)]
  [string]$WindowHwnd = "",

  [Parameter(Mandatory = $false)]
  [string]$WindowTitle = "",

  # Input injection method for Win32 HWND adapter (optional)
  [Parameter(Mandatory = $false)]
  [string]$InputMethod = "",

  # Enable frame dumps (forced ON for prod profiles)
  [Parameter(Mandatory = $false)]
  [switch]$DumpFrames
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Push-Location $RepoRoot

function _RotateLog {
  param(
    [Parameter(Mandatory = $true)]
    [string]$Name
  )
  try {
    $diagDir = Join-Path $RepoRoot "diagnostics"
    $log = Join-Path $diagDir $Name
    if (Test-Path $log) {
      $stamp = (Get-Date).ToString("yyyyMMdd-HHmmss")
      $dst = Join-Path $diagDir ($Name.Replace(".log", "") + "_" + $stamp + ".log")
      Move-Item -Force $log $dst
    }
  } catch {
    # non-fatal
  }
}

function _RunPoetryPython {
  param(
    [Parameter(Mandatory = $true)]
    [string[]]$PyArgs,

    [Parameter(Mandatory = $true)]
    [string]$OutPath
  )

  $stdoutPath = ($OutPath + ".stdout")
  $stderrPath = ($OutPath + ".stderr")

  if (Test-Path $stdoutPath) { Remove-Item -Force $stdoutPath }
  if (Test-Path $stderrPath) { Remove-Item -Force $stderrPath }

  $cmd = @("poetry", "run", "python") + $PyArgs
  # Invoke safely: PowerShell can treat arrays as a single string otherwise.
  & $cmd[0] @($cmd[1..($cmd.Length - 1)]) 1> $stdoutPath 2> $stderrPath
  $code = $LASTEXITCODE

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
  _RotateLog -Name "runtime.log"
  _RotateLog -Name "fatal.log"

  $stamp = (Get-Date).ToString("yyyyMMdd-HHmmss")
  $diagDir = Join-Path $RepoRoot "diagnostics"
  if (-not (Test-Path $diagDir)) {
    New-Item -ItemType Directory -Force -Path $diagDir | Out-Null
  }

  if ([string]::IsNullOrWhiteSpace($ConfigPath)) {
    if (-not [string]::IsNullOrWhiteSpace($env:FRBOT_CONFIG_PATH)) {
      $ConfigPath = $env:FRBOT_CONFIG_PATH
      # Some terminals/README snippets use placeholders like "C:\...\file.json".
      # If env points to a non-existent path, fall back to the repo default.
      if (-not (Test-Path $ConfigPath)) {
        $ConfigPath = ""
      }
    } else {
        $defaultCfg = Join-Path $RepoRoot "config\rois_prod_full.json"
        $legacyCfg = Join-Path $RepoRoot "rois_prod_full.json"
        if (Test-Path $defaultCfg) {
          $ConfigPath = $defaultCfg
        } elseif (Test-Path $legacyCfg) {
          $ConfigPath = $legacyCfg
        } else {
        throw "Provide -ConfigPath (prod_full requires a unified ROI config). Missing default: $defaultCfg"
      }
    }
  }
  if ([string]::IsNullOrWhiteSpace($ConfigPath)) {
    $defaultCfg = Join-Path $RepoRoot "config\rois_prod_full.json"
    $legacyCfg = Join-Path $RepoRoot "rois_prod_full.json"
    if (Test-Path $defaultCfg) {
      $ConfigPath = $defaultCfg
    } elseif (Test-Path $legacyCfg) {
      $ConfigPath = $legacyCfg
    } else {
      throw "Provide -ConfigPath (prod_full requires a unified ROI config). Missing default: $defaultCfg"
    }
  }
  if (-not (Test-Path $ConfigPath)) {
    throw "ConfigPath not found: $ConfigPath"
  }
  $absConfig = Resolve-Path $ConfigPath

  if ([string]::IsNullOrWhiteSpace($WindowHwnd) -and -not [string]::IsNullOrWhiteSpace($env:FRBOT_WINDOW_HWND)) {
    $candidateHwnd = [string]$env:FRBOT_WINDOW_HWND
    $trim = $candidateHwnd.Trim()
    # Ignore common placeholder values like 0xXXXXXXXX.
    if ($trim -match '^(?i)0xX+$') {
      $WindowHwnd = ""
    } else {
      $WindowHwnd = $candidateHwnd
    }
  }
  if ([string]::IsNullOrWhiteSpace($WindowTitle) -and -not [string]::IsNullOrWhiteSpace($env:FRBOT_WINDOW_TITLE)) {
    $WindowTitle = $env:FRBOT_WINDOW_TITLE
  }
  if ([string]::IsNullOrWhiteSpace($WindowHwnd) -and [string]::IsNullOrWhiteSpace($WindowTitle)) {
    throw "Provide -WindowHwnd or -WindowTitle, or set FRBOT_WINDOW_HWND/FRBOT_WINDOW_TITLE."
  }

  # Evidence dir per run (keeps runs isolated)
  $framesDir = Join-Path $diagDir ("frames_full\\evidence_" + $stamp)
  if (-not (Test-Path $framesDir)) {
    New-Item -ItemType Directory -Force -Path $framesDir | Out-Null
  }

  # Profile + capture authority
  $env:FRBOT_PROFILE = "prod_full"
  $env:FRBOT_CAPTURE_SOURCE = "obs_source"
  $env:FRBOT_OBS_SOURCE_NAME = $ObsSourceName

  # Config + evidence location
  $env:FRBOT_CONFIG_PATH = $absConfig.Path
  $env:FRBOT_REAL_FRAMES_DIR = $framesDir

  # Input authority
  if (-not [string]::IsNullOrWhiteSpace($WindowHwnd)) { $env:FRBOT_WINDOW_HWND = $WindowHwnd }
  if (-not [string]::IsNullOrWhiteSpace($WindowTitle)) { $env:FRBOT_WINDOW_TITLE = $WindowTitle }
  if (-not [string]::IsNullOrWhiteSpace($InputMethod)) { $env:FRBOT_INPUT_METHOD = $InputMethod }

  # Ensure evidence dumping is enabled.
  if ($DumpFrames.IsPresent) {
    $env:FRBOT_DUMP_FRAMES = "1"
  } else {
    if (-not $env:FRBOT_DUMP_FRAMES) { $env:FRBOT_DUMP_FRAMES = "1" }
  }

  # Operational robustness: give operator a short window to focus Tibia
  # after starting the script (no focus stealing; guards just wait).
  if (-not $env:FRBOT_FOREGROUND_RETRIES) { $env:FRBOT_FOREGROUND_RETRIES = "40" }
  if (-not $env:FRBOT_FOREGROUND_DELAY_MS) { $env:FRBOT_FOREGROUND_DELAY_MS = "150" }

  # ----------------
  # 1) Precheck (no gate execution)
  # ----------------
  Write-Host "prod_full precheck..." -ForegroundColor Cyan
  $env:FRBOT_MODE = "prod_full"

  $preOut = Join-Path $diagDir ("evidence_" + $stamp + ".prod_full.precheck.out")
  $preCode = _RunPoetryPython -PyArgs @(
    "-c",
    @'
import os
import json
import time
from pathlib import Path
from runtime.startup_guards import enforce_prod_emergency_real_startup_guards
from runtime.config_loader import load_rois
from contracts.runtime import RuntimeConfig, RuntimeContext, RuntimeState, RuntimeStatus, RuntimeTelemetry

# Startup guards (HWND + foreground + platform) for prod profiles.
enforce_prod_emergency_real_startup_guards(write_fatal_on_fail=True)

# Config schema + prod_full allowlist enforcement.
cfg = RuntimeConfig(mode="real", config_path=os.environ.get("FRBOT_CONFIG_PATH",""))
ctx = RuntimeContext(config=cfg, status=RuntimeStatus(state=RuntimeState.INIT), telemetry=RuntimeTelemetry())
load_rois(ctx)

# Always record capture identity into the evidence directory.
frames_dir = Path(os.environ.get("FRBOT_REAL_FRAMES_DIR", "") or ".")
try:
  frames_dir.mkdir(parents=True, exist_ok=True)
  payload = {
    "capture_source": ("obs_source" if (os.environ.get("FRBOT_CAPTURE_SOURCE", "") or "").strip().lower() == "obs_source" else "client"),
    "obs_source_name": str((os.environ.get("FRBOT_OBS_SOURCE_NAME", "") or "").strip()),
    "obs_projector_title": str((os.environ.get("FRBOT_OBS_PROJECTOR_TITLE", "") or "").strip()),
    "ts": int(time.time()),
  }
  (frames_dir / "evidence_manifest.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
except Exception:
  pass
print("PRECHECK_OK")
'@
  ) -OutPath $preOut

  if ($preCode -ne 0) {
    Write-Host "Precheck failed (exit=$preCode)." -ForegroundColor Red
    Write-Host "See: $preOut" -ForegroundColor Yellow
    exit $preCode
  }

  # ----------------
  # 2) Run pipeline (targeting_full -> healing_full -> combat_full -> cavebot_full -> looting_full -> deposit_full -> trade_full)
  # ----------------
  Write-Host "Running prod_full pipeline..." -ForegroundColor Cyan
  $env:FRBOT_MODE = "prod_full"
  $gateOut = Join-Path $diagDir ("evidence_" + $stamp + ".prod_full.run.out")
  $gateCode = _RunPoetryPython -PyArgs @("main.py") -OutPath $gateOut
  if ($gateCode -ne 0) {
    Write-Host "Pipeline failed (exit=$gateCode)." -ForegroundColor Yellow
    Write-Host "Evidence dir: $framesDir" -ForegroundColor DarkCyan
    Write-Host "See: $gateOut" -ForegroundColor DarkCyan
    exit $gateCode
  }

  # ----------------
  # 3) Final audit (auditor is authority)
  # ----------------
  Write-Host "Final audit (tools/audit_prod_full.py)..." -ForegroundColor Cyan
  $env:FRBOT_MODE = "real"
  $auditOut = Join-Path $diagDir ("evidence_" + $stamp + ".audit_prod_full.out")
  $auditCode = _RunPoetryPython -PyArgs @("tools/audit_prod_full.py") -OutPath $auditOut

  Write-Host "Evidence dir: $framesDir" -ForegroundColor DarkCyan
  Write-Host "Audit output: $auditOut" -ForegroundColor DarkCyan

  exit $auditCode
} finally {
  Pop-Location
}

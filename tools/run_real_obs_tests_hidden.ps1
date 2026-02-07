param(
  [Parameter(Mandatory=$false)]
  [string]$PythonExe = "python",

  # ROI config JSON with {"frame": {"width":..,"height":..}, "rois": {...}}
  [Parameter(Mandatory=$true)]
  [string]$ConfigPath,

  # Tibia input window selector (HWND preferred, title substring supported)
  [Parameter(Mandatory=$false)]
  [string]$WindowHwnd = "",

  [Parameter(Mandatory=$false)]
  [string]$WindowTitle = "",

  # OBS source name (OBS WebSocket v5 source identity)
  [Parameter(Mandatory=$false)]
  [string]$ObsSourceName = "Tibia_Fuente",

  # Absolute output evidence directory
  [Parameter(Mandatory=$false)]
  [string]$FramesDir = "",

  # Give operator time to focus Tibia before verification.
  [Parameter(Mandatory=$false)]
  [int]$DelaySeconds = 3,

  [Parameter(Mandatory=$false)]
  [string]$LogDir = ""
)

$ErrorActionPreference = "Stop"

# Runs the REAL OBS evidence harness without creating a visible console window.
# Rationale: starting from an interactive console often steals foreground focus,
# which correctly causes PROD-EMERGENCY startup guards to abort.

$repoRoot = Split-Path -Parent $PSScriptRoot
$scriptPath = Join-Path $repoRoot "tools\run_real_obs_tests.py"

if (-not (Test-Path -LiteralPath $scriptPath)) {
  Write-Error "Missing harness script: $scriptPath"
  exit 2
}

function Resolve-PythonExe([string]$Requested) {
  $req = ($Requested | ForEach-Object { $_.ToString().Trim() })
  if (-not $req) { $req = 'python' }

  if ($req -ne 'python') {
    return $req
  }

  $poetry = Get-Command poetry -ErrorAction SilentlyContinue
  if ($null -ne $poetry) {
    try {
      $venv = (& poetry env info -p 2>$null | Out-String).Trim()
      if ($venv) {
        $pyWin = Join-Path $venv 'Scripts\python.exe'
        if (Test-Path -LiteralPath $pyWin) { return $pyWin }
        $pyPosix = Join-Path $venv 'bin\python'
        if (Test-Path -LiteralPath $pyPosix) { return $pyPosix }
      }
    } catch {
      # Ignore and fall back.
    }
  }

  return 'python'
}

$absConfig = Resolve-Path -LiteralPath $ConfigPath

if ([string]::IsNullOrWhiteSpace($WindowHwnd) -and -not [string]::IsNullOrWhiteSpace($env:FRBOT_WINDOW_HWND)) {
  $WindowHwnd = $env:FRBOT_WINDOW_HWND
}
if ([string]::IsNullOrWhiteSpace($WindowTitle) -and -not [string]::IsNullOrWhiteSpace($env:FRBOT_WINDOW_TITLE)) {
  $WindowTitle = $env:FRBOT_WINDOW_TITLE
}
if ([string]::IsNullOrWhiteSpace($WindowHwnd) -and [string]::IsNullOrWhiteSpace($WindowTitle)) {
  Write-Error "Provide -WindowHwnd or -WindowTitle, or set FRBOT_WINDOW_HWND/FRBOT_WINDOW_TITLE."
  exit 2
}

$ts = (Get-Date).ToString('yyyyMMdd-HHmmss')
$effectiveLogDir = if ($LogDir) { $LogDir } else { (Join-Path $repoRoot "diagnostics\_hidden_runs") }
if (-not (Test-Path -LiteralPath $effectiveLogDir)) {
  New-Item -ItemType Directory -Path $effectiveLogDir | Out-Null
}

if ([string]::IsNullOrWhiteSpace($FramesDir)) {
  $FramesDir = Join-Path $repoRoot ("diagnostics\frames_real_obs_hidden_" + $ts)
}

$absFramesDir = Resolve-Path -LiteralPath (New-Item -ItemType Directory -Force -Path $FramesDir).FullName

# Set process environment (inherited by child process).
$env:FRBOT_PROFILE = "prod_emergency"
$env:FRBOT_MODE = "real"
$env:FRBOT_CAPTURE_SOURCE = "obs_source"
$env:FRBOT_OBS_SOURCE_NAME = $ObsSourceName
$env:FRBOT_CONFIG_PATH = $absConfig.Path
$env:FRBOT_REAL_FRAMES_DIR = $absFramesDir.Path
$env:FRBOT_DUMP_FRAMES = "1"

if (-not [string]::IsNullOrWhiteSpace($WindowHwnd)) { $env:FRBOT_WINDOW_HWND = $WindowHwnd }
if (-not [string]::IsNullOrWhiteSpace($WindowTitle)) { $env:FRBOT_WINDOW_TITLE = $WindowTitle }

if ($DelaySeconds -gt 0) {
  Write-Host ("Focus Tibia now (you have {0}s)..." -f $DelaySeconds)
  Start-Sleep -Seconds $DelaySeconds
}

$py = Resolve-PythonExe $PythonExe
$stdout = Join-Path $effectiveLogDir ("real_obs_tests_{0}.stdout.log" -f $ts)
$stderr = Join-Path $effectiveLogDir ("real_obs_tests_{0}.stderr.log" -f $ts)

Write-Host ("Starting hidden REAL OBS harness. Frames: {0}" -f $absFramesDir.Path)
Write-Host ("Logs: {0} , {1}" -f $stdout, $stderr)

Push-Location $repoRoot
try {
  $argList = @(
    "-u",
    $scriptPath
  )
  $proc = Start-Process -FilePath $py -ArgumentList $argList -WindowStyle Hidden -PassThru -Wait -RedirectStandardOutput $stdout -RedirectStandardError $stderr
  $code = $proc.ExitCode
  if ($null -eq $code) { $code = 1 }
  Write-Host ("Exit code: {0}" -f $code)
  exit $code
} finally {
  Pop-Location
}

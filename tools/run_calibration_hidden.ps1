param(
  [Parameter(Mandatory=$false)]
  [string]$PythonExe = "python",

  [Parameter(Mandatory=$false)]
  [ValidateSet('calibrate','bootstrap')]
  [string]$Mode = 'calibrate',

  [Parameter(Mandatory=$false)]
  [string[]]$Version = @(),

  [Parameter(Mandatory=$false)]
  [int]$DelaySeconds = 3,

  [Parameter(Mandatory=$false)]
  [string]$PowerShellExe = 'powershell',

  [Parameter(Mandatory=$false)]
  [string]$LogDir = ''
)

$ErrorActionPreference = "Stop"

# Runs calibration without creating a visible console window.
# Rationale: starting from an interactive console often steals foreground focus,
# which correctly causes calibration to HARD STOP (foreground mismatch).

$repoRoot = Split-Path -Parent $PSScriptRoot
$calibratorPath = Join-Path $repoRoot "tools\calibrate_all_real.py"
$bootstrapPath = Join-Path $repoRoot "tools\bootstrap_real_evidence.ps1"

$ts = (Get-Date).ToString('yyyyMMdd-HHmmss')
$effectiveLogDir = if ($LogDir) { $LogDir } else { (Join-Path $repoRoot "diagnostics\_hidden_runs") }
if (-not (Test-Path -LiteralPath $effectiveLogDir)) {
  New-Item -ItemType Directory -Path $effectiveLogDir | Out-Null
}

function Resolve-PythonExe([string]$Requested) {
  $req = ($Requested | ForEach-Object { $_.ToString().Trim() })
  if (-not $req) { $req = 'python' }

  # If user provided an explicit path (or non-default command), prefer it.
  if ($req -ne 'python') {
    return $req
  }

  # Best-effort: detect Poetry virtualenv python.
  $poetry = Get-Command poetry -ErrorAction SilentlyContinue
  if ($null -ne $poetry) {
    try {
      $venv = (& poetry env info -p 2>$null | Out-String).Trim()
      if ($venv) {
        $pyWin = Join-Path $venv 'Scripts\python.exe'
        if (Test-Path -LiteralPath $pyWin) {
          return $pyWin
        }
        $pyPosix = Join-Path $venv 'bin\python'
        if (Test-Path -LiteralPath $pyPosix) {
          return $pyPosix
        }
      }
    } catch {
      # Ignore and fall back.
    }
  }

  return 'python'
}

function New-LogPaths([string]$prefix) {
  $safePrefix = ($prefix -replace '[^a-zA-Z0-9._-]', '_')
  $out = Join-Path $effectiveLogDir ("{0}_{1}.stdout.log" -f $safePrefix, $ts)
  $err = Join-Path $effectiveLogDir ("{0}_{1}.stderr.log" -f $safePrefix, $ts)
  return @{ out = $out; err = $err }
}

# Required preconditions are enforced by:
# - calibrate_all_real.py (FRBOT_REAL_FRAMES_DIR/FRBOT_CONFIG_PATH/selector)
# - bootstrap_real_evidence.ps1 (selector + config + output path)

if ($Mode -eq 'calibrate') {
  if (-not (Test-Path $calibratorPath)) {
    Write-Error "Missing calibrator script: $calibratorPath"
    exit 2
  }

  $argList = @(
    "-u",
    $calibratorPath
  )

  $logs = New-LogPaths "calibrate"
  Write-Host ("Starting hidden calibrator. Logs: {0} , {1}" -f $logs.out, $logs.err)
  $PythonExe = Resolve-PythonExe $PythonExe
  $proc = Start-Process -FilePath $PythonExe -ArgumentList $argList -WindowStyle Hidden -PassThru -Wait -RedirectStandardOutput $logs.out -RedirectStandardError $logs.err
  $code = $proc.ExitCode
  if ($null -eq $code) { $code = 1 }
  exit $code
}

if ($Mode -eq 'bootstrap') {
  if (-not $Version -or $Version.Count -eq 0) {
    Write-Error "Missing -Version for bootstrap mode (15.x or 15.y). You can pass multiple: -Version 15.x,15.y"
    exit 2
  }
  if (-not (Test-Path $bootstrapPath)) {
    Write-Error "Missing bootstrap script: $bootstrapPath"
    exit 2
  }

  $allowed = @('15.x','15.y','15x','15y')
  $versionsToRun = @()
  foreach ($v0 in $Version) {
    if ($null -eq $v0) { continue }
    foreach ($piece in ($v0 -split '[,;]')) {
      $vv = ($piece).Trim()
      if (-not $vv) { continue }
      $versionsToRun += $vv
    }
  }

  if ($versionsToRun.Count -eq 0) {
    Write-Error "Missing -Version for bootstrap mode (15.x or 15.y)."
    exit 2
  }

  foreach ($vv in $versionsToRun) {
    if ($allowed -notcontains $vv) {
      Write-Error "Invalid -Version '$vv'. Allowed: $($allowed -join ', ')"
      exit 2
    }
  }

  foreach ($v in $versionsToRun) {
    if ($DelaySeconds -gt 0) {
      Write-Host ("Focus Tibia now for version {0} (you have {1}s)..." -f $v, $DelaySeconds)
      Start-Sleep -Seconds $DelaySeconds
    }

    $psArgs = @(
      "-NoProfile",
      "-ExecutionPolicy", "Bypass",
      "-File", $bootstrapPath,
      "-Version", $v,
      "-PythonExe", $PythonExe
    )

    $logs = New-LogPaths ("bootstrap_{0}" -f $v)
    Write-Host ("Starting hidden bootstrap for {0}. Logs: {1} , {2}" -f $v, $logs.out, $logs.err)
    $proc = Start-Process -FilePath $PowerShellExe -ArgumentList $psArgs -WindowStyle Hidden -PassThru -Wait -RedirectStandardOutput $logs.out -RedirectStandardError $logs.err
    $code = $proc.ExitCode
    if ($null -eq $code) { $code = 1 }
    if ($code -ne 0) {
      Write-Host ("Bootstrap failed for {0} with exit {1}. See logs: {2} , {3}" -f $v, $code, $logs.out, $logs.err)
      exit $code
    }
  }

  exit 0
}

Write-Error "Invalid -Mode: $Mode"
exit 2

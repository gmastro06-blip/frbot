param(
  [Parameter(Mandatory = $true)]
  [string]$OldDir,

  [Parameter(Mandatory = $true)]
  [string]$NewDir,

  [Parameter(Mandatory = $true)]
  [string]$ConfigPath,

  # Optional explicit Python executable (recommended: Poetry venv python.exe).
  [Parameter(Mandatory = $false)]
  [string]$PythonExe = 'python'
)

$ErrorActionPreference = 'Stop'

function HardStop([string]$Reason, [hashtable]$Extra = @{}) {
  $payload = @{ reason = $Reason; ts = (Get-Date).ToString('o') }
  foreach ($k in $Extra.Keys) { $payload[$k] = $Extra[$k] }
  Write-Host ($payload | ConvertTo-Json -Depth 6)
  exit 2
}

if ($env:FRBOT_PROFILE -and $env:FRBOT_PROFILE.Trim().ToLower() -eq 'prod_emergency') {
  HardStop 'feature_disabled' @{ tool = 'run_phase1_guard.ps1'; profile = $env:FRBOT_PROFILE }
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location -Path $repoRoot

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

try {
  $oldAbs = (Resolve-Path -LiteralPath $OldDir).Path
} catch {
  HardStop 'invalid_old_dir' @{ value = $OldDir }
}

try {
  $newAbs = (Resolve-Path -LiteralPath $NewDir).Path
} catch {
  HardStop 'invalid_new_dir' @{ value = $NewDir }
}

try {
  $cfgAbs = (Resolve-Path -LiteralPath $ConfigPath).Path
} catch {
  HardStop 'invalid_config_path' @{ value = $ConfigPath }
}

$env:FRBOT_REAL_FRAMES_DIR_OLD = $oldAbs
$env:FRBOT_REAL_FRAMES_DIR_NEW = $newAbs
$env:FRBOT_CONFIG_PATH = $cfgAbs

$PythonExe = Resolve-PythonExe $PythonExe
if ($PythonExe -match '[\\/]' -and -not (Test-Path -LiteralPath $PythonExe)) {
  HardStop 'python_exe_not_found' @{ python = $PythonExe }
}

$guard = Join-Path $repoRoot 'tools\phase1_guard.py'

& $PythonExe $guard
exit $LASTEXITCODE

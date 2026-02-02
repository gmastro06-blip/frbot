param(
  [Parameter(Mandatory = $true)]
  [string]$Image,

  [Parameter(Mandatory = $true)]
  [string]$Name,

  [Parameter(Mandatory = $false)]
  [string]$ConfigPath = '',

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

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location -Path $repoRoot

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
      # ignore
    }
  }

  return 'python'
}

try {
  $imgAbs = (Resolve-Path -LiteralPath $Image).Path
} catch {
  HardStop 'invalid_image_path' @{ value = $Image }
}

$cfgAbs = ''
if ($ConfigPath) {
  try {
    $cfgAbs = (Resolve-Path -LiteralPath $ConfigPath).Path
  } catch {
    HardStop 'invalid_config_path' @{ value = $ConfigPath }
  }
}

$PythonExe = Resolve-PythonExe $PythonExe
if ($PythonExe -match '[\\/]' -and -not (Test-Path -LiteralPath $PythonExe)) {
  HardStop 'python_exe_not_found' @{ python = $PythonExe }
}

$tool = Join-Path $repoRoot 'tools\roi_pick.py'

$cmd = @($tool, '--image', $imgAbs, '--name', $Name)
if ($cfgAbs) {
  $cmd += @('--config', $cfgAbs)
}

& $PythonExe @cmd
exit $LASTEXITCODE

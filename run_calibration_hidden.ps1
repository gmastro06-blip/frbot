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

$ErrorActionPreference = 'Stop'

# Wrapper kept at repo root to match operational runbook.
$repoRoot = Split-Path -Parent $PSScriptRoot
$inner = Join-Path $PSScriptRoot 'tools\run_calibration_hidden.ps1'

if (-not (Test-Path -LiteralPath $inner)) {
  Write-Error "Missing: $inner"
  exit 2
}

& $inner -PythonExe $PythonExe -Mode $Mode -Version $Version -DelaySeconds $DelaySeconds -PowerShellExe $PowerShellExe -LogDir $LogDir
exit $LASTEXITCODE

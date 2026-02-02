param(
  [Parameter(Mandatory = $true)]
  [string]$ConfigPath,

  [Parameter(Mandatory = $false)]
  [string]$PythonExe = 'python'
)

$ErrorActionPreference = 'Stop'

$repoRoot = (Resolve-Path $PSScriptRoot).Path
$tool = Join-Path $repoRoot 'tools\run_roi_guard.ps1'

if (-not (Test-Path -LiteralPath $tool)) {
  Write-Host (@{ reason = 'missing_tool'; tool = $tool } | ConvertTo-Json -Depth 4)
  exit 2
}

& $tool -ConfigPath $ConfigPath -PythonExe $PythonExe
exit $LASTEXITCODE

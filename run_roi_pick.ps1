param(
  [Parameter(Mandatory = $true)]
  [string]$Image,

  [Parameter(Mandatory = $true)]
  [string]$Name,

  [Parameter(Mandatory = $false)]
  [string]$ConfigPath = '',

  [Parameter(Mandatory = $false)]
  [string]$PythonExe = 'python'
)

$ErrorActionPreference = 'Stop'

$repoRoot = (Resolve-Path $PSScriptRoot).Path
$tool = Join-Path $repoRoot 'tools\run_roi_pick.ps1'

if (-not (Test-Path -LiteralPath $tool)) {
  Write-Host (@{ reason = 'missing_tool'; tool = $tool } | ConvertTo-Json -Depth 4)
  exit 2
}

& $tool -Image $Image -Name $Name -ConfigPath $ConfigPath -PythonExe $PythonExe
exit $LASTEXITCODE

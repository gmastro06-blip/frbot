param(
  [string]$Hwnd = "",
  [string]$Title = "",
  [int]$WaitSeconds = 60,
  [switch]$Focus,
  [switch]$ListWindows,
  [string]$Filter = "tibia"
)

$ErrorActionPreference = 'Stop'

$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Py = Join-Path $RepoRoot '.venv\Scripts\python.exe'

if (-not (Test-Path $Py)) {
  Write-Host "ERROR: .venv Python not found at $Py"
  Write-Host "Run: poetry install" 
  exit 2
}

if ($ListWindows) {
  & $Py (Join-Path $RepoRoot 'test_capture_real.py') --list-windows --filter $Filter
  exit $LASTEXITCODE
}

$env:FRBOT_CAPTURE_BACKEND = 'meld'

if ($Hwnd -ne "") {
  $env:FRBOT_WINDOW_HWND = $Hwnd
}
if ($Title -ne "") {
  $env:FRBOT_WINDOW_TITLE_SUBSTRING = $Title
}

$argsList = @('--wait-seconds', $WaitSeconds)
if ($Focus) {
  $argsList += '--focus'
}

& $Py (Join-Path $RepoRoot 'test_capture_real.py') @argsList
exit $LASTEXITCODE
